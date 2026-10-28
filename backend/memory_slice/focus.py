"""Single-person attention, bounded ingestion buffer and explainable person cues."""
import re
from collections import defaultdict
from datetime import timedelta
from .errors import MemoryError as HTTPException
from .store import dt, iso, utcnow

def person_score(event, config, speaker=None):
    focus = config.get('focus_person_id')
    if not focus or focus not in event.get('person_ids', []):
        return 0.0
    others = set(config.get('_interaction_ids', [])) | ({speaker} if speaker else set())
    others.discard(focus)
    return .25 + (.75 * len(others.intersection(event['person_ids'])) / len(others) if others else 0)

def named_focus(text, focus, people):
    target = next((p for p in people if p['id'] == focus), {})
    names = [target.get('name', ''), *target.get('aliases', [])]
    for name in names:
        if len(name) < 2 or name in {'自己', '本人', '用户', '助手'}:
            continue
        if any(p['id'] != focus and name in [p['name'], *p.get('aliases', [])] for p in people):
            continue
        # Longer registered names must not be mistaken for their shorter prefix.
        cleaned = text
        for p in people:
            if p['id'] != focus:
                for other in [p['name'], *p.get('aliases', [])]:
                    if name in other and name != other:
                        cleaned = cleaned.replace(other, '')
        if (re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', cleaned)
                if name.isascii() else name in cleaned):
            return True
    return False

def select_messages(store, sid, messages, config):
    focus = config.get('focus_person_id')
    if not focus:
        return messages, {'enabled': False}
    people = store.list('person', sid)
    all_messages = {m['id']: m for m in store.list('message', sid)}
    groups = defaultdict(list)
    for m in messages:
        groups[m['conversation_id']].append(m)
    selected = []
    anchors = 0
    for group in groups.values():
        hits = {}
        for i, m in enumerate(group):
            if m['role'] != 'user':
                continue
            parent = all_messages.get(m.get('in_reply_to'), {})
            reason = ('speaker' if m.get('person_id') == focus else
                      'mention' if focus in m.get('mention_ids', []) else
                      'reply' if parent.get('role') == 'user' and parent.get('person_id') == focus else
                      'name' if named_focus(m['content'], focus, people) else None)
            if reason and not re.fullmatch(r'[\s哈呵嘿嗯哦啊好呀是的!！。.，,👍😂🤣]+', m['content']):
                hits[i] = reason
        anchors += len(hits)
        for i, m in enumerate(group):
            nearby = any(abs(i-j) <= 2 and abs((dt(m['at'])-dt(group[j]['at'])).total_seconds()) <= 1800 for j in hits)
            parent_hit = any(m.get('in_reply_to') == group[j]['id'] for j in hits)
            if i in hits or nearby or parent_hit:
                selected.append(dict(m, focus_reason=hits.get(i, 'context')))
    selected_ids = {m['id'] for m in selected}
    selected_by_id = {m['id']: m for m in selected}
    return [selected_by_id[m['id']] for m in messages if m['id'] in selected_ids], {
        'enabled': True, 'scanned': len(messages), 'anchors': anchors,
        'candidates': len(selected), 'waiting': len(messages)-len(selected),
    }

def focus_prompt(config):
    if not config.get('focus_person_id'):
        return ''
    return ('\n本空间只长期记录与关注对象 ' + config['focus_person_id'] + ' 有关的事情。'
            'focus_reason 是程序候选线索，不代表事件一定相关。context 消息仅用于理解前后文。'
            '不要把群成员、围观者或仅发哈哈/表情的人认定为参与者。'
            '每件相关事件 person_ids 必须包含关注对象；其他 person_ids 只填有依据的相关人。'
            '用正文明确谁参与、谁转述、谁补充，以及尚未确认的信息。'
            '每件事件必须给 focus_evidence:{message_id:本轮消息ID,quote:其中逐字原话}，说明与关注对象的关系。'
            '无关内容返回 ignored_messages；关系不确定返回 deferred_messages。不要为通过校验编造关联。')

def validate_focus(plan, messages, config):
    focus = config.get('focus_person_id')
    if not focus:
        return
    by_id = {m['id']: m for m in messages}
    for entry in plan.events:
        evidence = entry.focus_evidence
        source = by_id.get(evidence.message_id) if evidence else None
        if (focus not in entry.person_ids or not source or source['id'] not in entry.source_ids
                or source.get('focus_reason') not in {'speaker', 'mention', 'reply', 'name'}
                or evidence.quote not in source['content']):
            raise HTTPException(502, '关注对象关联缺少本轮原话证据，未写入事件')
    for operation in [*plan.merges, *plan.splits]:
        if not any(by_id.get(mid, {}).get('focus_reason') in {'speaker', 'mention', 'reply', 'name'}
                   for mid in operation.source_ids):
            raise HTTPException(502, '合并或拆分缺少与关注对象相关的本轮消息依据')

def cleanup_buffer(store, sid, config):
    """Only new, explicitly temporary messages; legacy and referenced records are protected."""
    with store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if any(j['status'] in ('queued', 'running') for j in store.list('job', sid, con)):
            return 0
        for trace in store.list('recall', sid, con):
            if trace.get('expires_at') and dt(trace['expires_at']) <= utcnow():
                store.delete('recall', trace['id'], sid, con)
        messages = store.list('message', sid, con)
        protected = set()
        for kind in ('event', 'fragment'):
            for record in store.list(kind, sid, con):
                protected.update(record.get('source_ids', []))
        import json
        for row in con.execute('SELECT data FROM versions WHERE space_id=?', (sid,)):
            protected.update(json.loads(row[0]).get('source_ids', []))
        protected.update(m['id'] for m in messages if m.get('retention') != 'buffer' or (m.get('maintenance_offset', 0) and not m.get('processed')))
        by_id = {m['id']: m for m in messages}
        for mid in list(protected):
            seen = set()
            while mid in by_id and mid not in seen:
                seen.add(mid)
                mid = by_id[mid].get('in_reply_to')
                if mid:
                    protected.add(mid)
        temporary = [m for m in messages if m.get('retention') == 'buffer' and m['id'] not in protected]
        for m in messages:
            if m['id'] in protected and m.get('retention') == 'buffer' and m.get('processed'):
                m.update(retention='permanent', expires_at=None)
                store.put('message', m, sid, con=con)
        excess = max(0, len(temporary) - config.get('focus_buffer_limit', 2000))
        removed = 0
        for i, m in enumerate(temporary):
            if i < excess or dt(m['expires_at']) <= utcnow():
                store.delete('message', m['id'], sid, con)
                removed += 1
        return removed
