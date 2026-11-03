"""Independent, repeatable space copies with stable provenance and atomic writes."""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from .errors import MemoryError
from .integrations.routing import alias_id
from .pinned_context import pin_size
from .store import iso, new_id
from .reinforcements import portable_event

CONTENT_KINDS = ('person', 'conversation', 'message', 'event')
REFERENCE_KINDS = {
    'person_id': 'person', 'person_ids': 'person', 'sender_id': 'person',
    'mention_ids': 'person', 'reply_to_person_id': 'person',
    'conversation_id': 'conversation', 'archive_conversation_id': 'conversation',
    'source_ids': 'message', 'message_id': 'message', 'in_reply_to': 'message',
    'event_id': 'event', 'event_ids': 'event', 'target_id': 'event',
    'split_from': 'event', 'split_into': 'event', 'merged_into': 'event',
}


def omitted_message(message):
    # Legacy export-without-body used this exact shape. Retention cleanup always
    # writes content_sha256/content_chars; those tombstones must stay cleared.
    return bool(message and not message.get('content') and message.get('processed') is True
                and message.get('content_cleared_at') and message.get('maintenance_offset') == 0
                and 'content_sha256' not in message and 'content_chars' not in message
                and message.get('origin_id') and message['origin_id'] != message['id'])


class CopyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_space_id: str
    preview_token: str | None = None
    conflict_policy: Literal['skip', 'overwrite'] = 'skip'
    event_choices: dict[str, Literal['skip', 'overwrite']] = Field(default_factory=dict)
    pinned_event_ids: list[str] | None = Field(None, max_length=6)


class SpaceCopier:
    def __init__(self, core, pin_budget=None):
        self.core, self.store, self.pin_budget = core, core.store, pin_budget

    def _plan(self, source, target, con):
        if source == target:
            raise MemoryError(422, '请选择不同的源空间与目标空间')
        spaces = [self.store.get('space', sid, con=con) for sid in (source, target)]
        for sid in (source, target):
            if any(j['status'] in ('queued', 'running') for j in self.store.list('job', sid, con)):
                raise MemoryError(409, '请等待两个空间的整理任务完成后再复制')
        rows = [dict(r) for r in con.execute('SELECT * FROM records WHERE space_id IN (?,?) ORDER BY kind,rowid', (source,target))]
        revisions = [dict(r) for r in con.execute('SELECT * FROM versions WHERE space_id IN (?,?) ORDER BY id', (source,target))]
        provenance = [dict(r) for r in con.execute('SELECT * FROM copy_identity WHERE space_id IN (?,?) ORDER BY space_id,kind,origin_id', (source,target))]
        token = hashlib.sha256(json.dumps([rows,revisions,provenance],sort_keys=True).encode()).hexdigest()
        data = {sid: {} for sid in (source,target)}
        for row in rows:
            data[row['space_id']].setdefault(row['kind'], {})[row['id']] = dict(json.loads(row['data']),version=row['version'])
        cids = set(data[source].get('conversation', {})) | set(data[target].get('conversation', {}))
        if any(lock.locked() for cid,lock in self.core.chat_locks.items() if cid in cids):
            raise MemoryError(409, '这两个空间中有回复正在生成，请稍后复制')
        aliases = {}
        for row in provenance:
            aliases.setdefault((row['space_id'],row['kind'],row['record_id']),set()).add(row['origin_id'])
        def origins(sid,kind,item):
            return {item.get('origin_id',item['id'])} | aliases.get((sid,kind,item['id']),set())
        index = {}
        for kind in CONTENT_KINDS:
            for item in data[target].get(kind, {}).values():
                for origin in origins(target,kind,item):
                    key=(kind,origin)
                    if key in index and index[key] != item['id']:
                        raise MemoryError(409, '目标空间有重复来源记录，请先核对：' + kind + '/' + origin)
                    index[key]=item['id']
        mapping = {kind:{} for kind in CONTENT_KINDS}
        for kind in CONTENT_KINDS[1:]:
            assigned = {}
            for item in data[source].get(kind, {}).values():
                roots=origins(source,kind,item)
                hits={index[(kind,o)] for o in roots if (kind,o) in index}
                if len(hits)>1 or any(o in assigned for o in roots):
                    raise MemoryError(409, '存在歧义的同源记录，请先核对：' + item['id'])
                mapping[kind][item['id']] = next(iter(hits)) if hits else new_id('msg' if kind=='message' else 'conv' if kind=='conversation' else kind)
                for origin in roots:assigned[origin]=item['id']

        # Confirmed platform aliases and provenance both identify people. A
        # bridge between two distinct target people is ambiguous and rejected.
        people = {sid:data[sid].get('person',{}) for sid in (source,target)}
        parent={pid:pid for group in people.values() for pid in group}
        def root(pid):
            while parent[pid]!=pid:
                parent[pid]=parent[parent[pid]];pid=parent[pid]
            return pid
        identities={}
        for sid in (target,source):
            accounts=[]
            for p in people[sid].values():
                accounts += [(('origin',o),p['id']) for o in origins(sid,'person',p)]
                if p.get('platform') and p.get('external_uid'):
                    accounts.append((('account',p['platform'],p['external_uid']),p['id']))
            accounts += [(('account',a['platform'],a['uid']),a['person_id']) for a in data[sid].get('astrbot_person_alias',{}).values()]
            for key,pid in accounts:
                if pid not in people[sid]:
                    raise MemoryError(409, '人物身份映射指向不存在的人物，请检查旧映射')
                if key in identities:parent[root(pid)]=root(identities[key])
                identities[key]=pid
        groups={}
        for pid in parent:groups.setdefault(root(pid),[]).append(pid)
        for group in groups.values():
            targets=[pid for pid in group if pid in people[target]]
            if len(targets)>1:
                raise MemoryError(409, '已有身份映射同时关联目标空间中的多个人物：'+'、'.join(f"{people[target][pid]['name']}（{pid}）" for pid in targets))
            dest=targets[0] if targets else new_id('person')
            for pid in group:
                if pid in people[source]:mapping['person'][pid]=dest
        conflicts=[]
        for eid,event in data[source].get('event',{}).items():
            dest=data[target].get('event',{}).get(mapping['event'][eid])
            if dest:
                conflicts.append(dict(source_id=eid,target_id=dest['id'],origin_id=event.get('origin_id',eid),
                    source_title=event['title'],target_title=dest['title'],target_frozen=bool(dest.get('frozen')),
                    source_version=event['version'],target_version=dest['version']))
        target_pins=[eid for p in data[target].get('astrbot_pins',{}).values() for eid in p['event_ids']]
        candidates=[]
        for sid in (target,source):
            for row in data[sid].get('astrbot_pins',{}).values():
                for eid in row['event_ids']:
                    event=data[sid].get('event',{}).get(eid)
                    if event:candidates.append(dict(id=eid,title=event['title'],from_source=sid==source))
        budget=self.pin_budget if self.pin_budget is not None else self.core.config_for(target)['context_char_budget']
        plan=dict(source=spaces[0],target=spaces[1],preview_token=token,
            counts={kind:len(data[source].get(kind,{})) for kind in CONTENT_KINDS},
            conflicts=conflicts,new_events=len(mapping['event'])-len(conflicts),
            restorable_messages=sum(bool(m.get('content')) and omitted_message(data[target].get('message',{}).get(mapping['message'][mid])) for mid,m in data[source].get('message',{}).items()),
            reused_people=sum(pid in people[target] for pid in mapping['person'].values()),
            pinned_events=candidates,selected_pins=target_pins,pin_limit=budget)
        return plan,data,mapping,revisions,origins

    def preview(self, source, body):
        with self.store.connect() as con:
            con.execute('BEGIN')
            return self._plan(source,body.target_space_id,con)[0]

    def copy(self, source, body):
        target=body.target_space_id
        try:
            with self.store.connect() as con:
                con.execute('PRAGMA busy_timeout=1000');con.execute('BEGIN IMMEDIATE')
                plan,data,mapping,revisions,origins=self._plan(source,target,con)
                if not body.preview_token or body.preview_token!=plan['preview_token']:
                    raise MemoryError(409,'空间数据已变化，请重新预览并确认')
                conflict_ids={c['source_id'] for c in plan['conflicts']}
                if any(eid not in conflict_ids for eid in body.event_choices):
                    raise MemoryError(422,'逐件选择包含不属于本次冲突的事件')
                actions={eid:(body.event_choices.get(eid,body.conflict_policy) if eid in conflict_ids else 'create') for eid in mapping['event']}
                for conflict in plan['conflicts']:
                    if actions[conflict['source_id']]=='overwrite' and conflict['target_frozen']:
                        raise MemoryError(409,'目标事件已冻结，不能覆盖，请先解冻或选择跳过：'+conflict['target_title'])

                def rewrite(value,key='',strict=False):
                    if isinstance(value,dict):
                        return {k:rewrite(v,k,strict) for k,v in value.items()}
                    if isinstance(value,list):
                        return [v for v in (rewrite(v,key,strict) for v in value) if v is not None]
                    if key=='space_id' and value==source:return target
                    if key in REFERENCE_KINDS and isinstance(value,str):
                        mapped=mapping[REFERENCE_KINDS[key]].get(value)
                        if not mapped and strict and key not in ('split_from','split_into','merged_into','archive_conversation_id'):
                            raise MemoryError(422,'源记录引用了缺失的数据，无法完整复制：'+value)
                        return mapped
                    if key in ('body','content') and isinstance(value,str):
                        def link(match):
                            eid=match[1]
                            if eid in mapping['event']:return '[['+mapping['event'][eid]+']]'
                            if strict:raise MemoryError(422,'源事件正文引用了缺失事件：'+eid)
                            return '[未复制的历史事件]'
                        # Chat content is preserved literally; only event/fragment bodies call this.
                        return re.sub(r'\[\[([A-Za-z0-9_-]+)\]\]',link,value)
                    return value

                def remember(kind,item,dest):
                    for origin in origins(source,kind,item):
                        con.execute('INSERT INTO copy_identity VALUES(?,?,?,?) ON CONFLICT(space_id,kind,origin_id) DO UPDATE SET record_id=excluded.record_id', (target,kind,origin,dest))

                event_results={}
                for eid,event in data[source].get('event',{}).items():
                    dest=mapping['event'][eid]
                    if actions[eid]=='skip':event_results[dest]=data[target]['event'][dest];continue
                    portable=portable_event(event,data[source].get('message',{}))
                    value=rewrite({k:v for k,v in portable.items() if k!='copied_from'},strict=True)
                    value.update(id=dest,space_id=target,origin_id=event.get('origin_id',eid),updated_at=iso(),
                        copied_from=dict(space_id=source,event_id=eid,version=event['version']))
                    value['version']=data[target].get('event',{}).get(dest,{}).get('version',0)+1
                    event_results[dest]=value
                allowed={p['id'] for p in plan['pinned_events']}
                selected=plan['selected_pins'] if body.pinned_event_ids is None else body.pinned_event_ids
                if len(set(selected))!=len(selected) or any(e not in allowed for e in selected):
                    raise MemoryError(422,'请从预览列出的常驻事件中选择')
                pins=list(dict.fromkeys(mapping['event'].get(e,e) for e in selected))
                if len(pins)>3:raise MemoryError(422,'最多选择 3 件常驻事件')
                pin_events=[event_results.get(e) or data[target].get('event',{}).get(e) for e in pins]
                if any(not e or e.get('status')=='archived' for e in pin_events):
                    raise MemoryError(422,'常驻事件不存在或已归档，请调整选择')
                if pin_size(pin_events)>plan['pin_limit']:
                    raise MemoryError(422,'复制后的常驻内容超过字符上限，请减少选择或调整事件')

                written={kind:set(data[target].get(kind,{})) for kind in CONTENT_KINDS}
                counts=Counter()
                for kind in CONTENT_KINDS[:3]:
                    for rid,item in data[source].get(kind,{}).items():
                        dest=mapping[kind][rid]
                        if dest not in written[kind]:
                            value=rewrite(item)
                            value.update(id=dest,space_id=target,origin_id=item.get('origin_id',rid))
                            # Copies are ordinary local conversations, never live host bindings.
                            if kind=='conversation':
                                value.pop('external_thread_id',None);value.pop('integration',None)
                                if value.get('title','').startswith('agent-session:'):value['title']='副本 · '+value['title']
                            if kind=='message':
                                value['content']=item['content'];value.pop('recall_id',None)
                            self.store.put(kind,value,target,con=con)
                            written[kind].add(dest);counts[kind]+=1
                        elif kind=='message' and item.get('content') and omitted_message(data[target].get('message',{}).get(dest)):
                            value=dict(data[target]['message'][dest],content=item['content'])
                            for field in ('content_cleared_at','processed_at','maintenance_offset','retention','expires_at','processed'):
                                value.pop(field,None)
                                if field in item:value[field]=item[field]
                            self.store.put('message',value,target,con=con)
                            counts['restored_messages']+=1
                        remember(kind,item,dest)
                # Preserve all explicit account identities after people are deduplicated.
                accounts=[(p['platform'],p['external_uid'],p['id']) for p in data[source].get('person',{}).values() if p.get('platform') and p.get('external_uid')]
                accounts += [(a['platform'],a['uid'],a['person_id']) for a in data[source].get('astrbot_person_alias',{}).values()]
                for platform,uid,pid in accounts:
                    aid=alias_id(target,platform,uid)
                    existing=data[target].get('astrbot_person_alias',{}).get(aid)
                    if not existing:
                        self.store.put('astrbot_person_alias',dict(id=aid,platform=platform,uid=uid,person_id=mapping['person'][pid]),target,con=con)
                overwritten_fragments=[]
                for eid,event in data[source].get('event',{}).items():
                    dest=mapping['event'][eid]
                    remember('event',event,dest)
                    counts[actions[eid]]+=1
                    if actions[eid]=='skip':continue
                    value=event_results[dest]
                    self.store.put('event',value,target,con=con,allow_freeze_change=True)
                    if actions[eid]=='overwrite':
                        previous=[f for f in data[target].get('fragment',{}).values() if f['event_id']==dest]
                        overwritten_fragments.extend(previous)
                        con.execute("DELETE FROM records WHERE kind='fragment' AND space_id=? AND json_extract(data,'$.event_id')=?",(target,dest))
                        con.execute("DELETE FROM records WHERE kind='embedding_vector' AND space_id=? AND json_extract(data,'$.event_id')=?",(target,dest))
                    fragments=[f for f in data[source].get('fragment',{}).values() if f['event_id']==eid]
                    for f in fragments:
                        value=rewrite(f)
                        fid=new_id('fragment')
                        value.update(id=fid,space_id=target,origin_id=f.get('origin_id',f['id']))
                        self.store.put('fragment',value,target,con=con)
                    if not fragments:
                        from .maintenance import ensure_baseline
                        ensure_baseline(self.store,target,event_results[dest],con)
                    # Source history is supplemental; local revision numbers remain independent.
                    for rev in revisions:
                        if rev['space_id']!=source or rev['event_id']!=eid:continue
                        history_origins=origins(source,'revision',{'id':rev['id']})
                        found=con.execute("SELECT 1 FROM copy_identity c JOIN versions v ON v.id=c.record_id AND v.space_id=c.space_id WHERE c.space_id=? AND c.kind='revision' AND c.origin_id IN ("+','.join('?' for _ in history_origins)+')',(target,*history_origins)).fetchone()
                        if found:continue
                        revision_id=new_id('rev');snapshot=rewrite(portable_event(json.loads(rev['data']),data[source].get('message',{})))
                        snapshot.update(id=dest,space_id=target,origin_id=event.get('origin_id',eid))
                        con.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(revision_id,dest,target,json.dumps(snapshot,ensure_ascii=False),rev['saved_at']))
                        remember('revision',{'id':rev['id']},revision_id)
                    # Compatible vectors can be reused; changed links/text are re-indexed normally.
                    old_chunks=list(self.core.embeddings.chunks([event]))
                    new_chunks=list(self.core.embeddings.chunks([event_results[dest]]))
                    signature=self.core.embeddings.identity(self.core.embeddings.settings(target))
                    vectors=data[source].get('embedding_vector',{})
                    for old_chunk,new_chunk in zip(old_chunks,new_chunks):
                        vector=vectors.get(old_chunk['id'])
                        if vector and vector.get('signature')==signature and vector.get('hash')==old_chunk['hash']==new_chunk['hash']:
                            self.store.put('embedding_vector',dict(vector,id=new_chunk['id'],event_id=dest),target,con=con)
                # Keep target pins by default. Selecting copied pins is an explicit opt-in.
                if pins!=plan['selected_pins']:
                    self.store.put('astrbot_pins',dict(id=target,event_ids=pins),target,con=con)
                self.store.put('space_copy',dict(id=new_id('copy'),source_space_id=source,target_space_id=target,at=iso(),
                    counts=dict(counts),event_choices=actions,event_map=mapping['event'],previous_fragments=overwritten_fragments),target,con=con)
            self.core.embeddings.state(target)['generation']+=1
            return dict(space_id=target,source_space_id=source,counts=dict(counts),event_map=mapping['event'],pinned_event_ids=pins)
        except sqlite3.OperationalError as exc:
            if getattr(exc,'sqlite_errorcode',0)&255 not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):raise
            raise MemoryError(503,'数据库暂时忙，复制未完成，原数据未改动，请稍后重试') from exc
