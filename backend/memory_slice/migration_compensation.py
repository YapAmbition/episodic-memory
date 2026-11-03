"""Detached, deterministic migration repair. Originals never become runtime data.

Recovery records are opaque evidence: their payloads must never have IDs rewritten,
be summarized, or be removed by message retention. Every lossy repair keeps its input.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

from .schemas import EventInput, PersonInput
from .reinforcements import portable_event
from .message_identity import portable_message

RECOVERY = 'migration_recovery'
KINDS = ('person', 'conversation', 'message', 'event', 'fragment', 'astrbot_pins', 'astrbot_person_alias', RECOVERY)
COMMON = {'id', 'space_id', 'version', 'origin_id', 'migration', 'migration_extra'}
FIELDS = {
    'person': set(PersonInput.model_fields),
    'conversation': {'person_id','kind','title','created_at','updated_at'},
    'message': {'person_id','conversation_id','role','content','at','received_at','sender_id','in_reply_to','unavailable_in_reply_to','mention_ids','processed','processed_at','maintenance_offset','content_sha256','content_chars','content_cleared_at','retention','expires_at','fixture','source'},
    'event': set(EventInput.model_fields) | {'frozen','reinforcements','updated_at','split_from','split_into','merged_into','archive_conversation_id','origin'},
    'fragment': {'event_id','kind','content','source_ids','recorded_at','source_ranges'},
    'astrbot_pins': {'event_ids'},
    'astrbot_person_alias': {'platform','uid','person_id'},
    RECOVERY: {'status','record_kind','payload','issues'},
}
OPAQUE = {'migration', 'migration_extra', 'payload', 'source'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def clean(kind, value):
    result = deepcopy({k:v for k,v in value.items() if k in COMMON | FIELDS[kind]})
    extra = {k:v for k,v in value.items() if k not in COMMON | FIELDS[kind]}
    if extra:
        # Preserve unrecognized historical fields without activating host bindings.
        previous = result.get('migration_extra', {})
        result['migration_extra'] = {**(previous if isinstance(previous, dict) else {'previous': previous}), **deepcopy(extra)}
    return result


def valid_id(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 300


def valid_time(value):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def recovery_record(sid, kind, raw, reasons, status='compensated'):
    key='recovery_'+fingerprint([sid,kind,raw,status])[:40]
    return {'kind':RECOVERY,'value':dict(id=key,space_id=sid,origin_id=key,
        status=status,record_kind=kind,payload=deepcopy(raw),issues=list(dict.fromkeys(reasons)),version=1)}


def copy_audit_evidence(sid, audits):
    # Older copies kept overwritten fragment snapshots only in their audit log.
    # Carry those snapshots too, without activating them as current fragments.
    return [recovery_record(sid,'fragment',fragment,['覆盖前的历史片段原文'])
            for audit in audits for fragment in audit.get('previous_fragments',[])]


def summary(records):
    evidence = [r['value'] for r in records if r['kind'] == RECOVERY]
    return dict(compensated=sum(r['status']=='compensated' for r in evidence),
                pending=sum(r['status']=='pending' for r in evidence),
                issues=[{'id':r.get('origin_id',r['id']), 'kind':r['record_kind'], 'status':r['status'], 'reasons':r['issues']} for r in evidence[:50]], truncated=len(evidence)>50)


def compensate(records, revisions, sid):
    """Return safe records, safe history, and complete recoverable evidence.

    Works on copies, is idempotent, and uses no clock, network, model, or DB writes.
    Structural package errors remain errors; damaged individual records are retained.
    """
    records, revisions = deepcopy(records), deepcopy(revisions)
    recovery = {}
    active = []
    originals = {}
    problems = {}

    def preserve(kind, raw, reasons, status='compensated'):
        record=recovery_record(sid,kind,raw,reasons,status)
        recovery[record['value']['id']]=record
        return record['value']['id']

    counts = Counter((r.get('kind'), r.get('value', {}).get('id')) for r in records
                     if isinstance(r,dict) and isinstance(r.get('value'),dict) and valid_id(r['value'].get('id')))
    for position, row in enumerate(records):
        if not isinstance(row, dict) or row.get('kind') not in KINDS or not isinstance(row.get('value'),dict):
            raise ValueError('未知记录类型或记录结构损坏')
        kind, raw = row['kind'], row['value']
        if kind == RECOVERY:
            if not valid_id(raw.get('id')) or raw.get('status') not in ('pending','compensated') or not isinstance(raw.get('issues'),list) or not all(isinstance(x,str) for x in raw['issues']) or 'payload' not in raw or not isinstance(raw.get('record_kind'),str):
                raise ValueError('恢复记录结构损坏')
            key = raw.get('origin_id', raw['id'])
            if not valid_id(key):raise ValueError('恢复资料来源 ID 无效')
            if key in recovery:
                if any(recovery[key]['value'].get(k)!=raw.get(k) for k in ('payload','record_kind','status','issues')):
                    preserve(RECOVERY,raw,['恢复资料身份冲突：原始资料完整保留'],'pending')
                continue
            repaired=deepcopy(row)
            if counts[(kind,raw['id'])]>1:
                repaired['value']['id']='recovery_id_'+fingerprint([key,raw])[:40]
                metadata=repaired['value'].get('migration')
                if not isinstance(metadata,dict):metadata={}
                repaired['value']['migration']=dict(metadata,original_recovery_id=raw['id'])
            recovery[key] = repaired
            continue
        if valid_id(raw.get('id')) and counts[(kind,raw['id'])] > 1:
            preserve(kind, raw, ['记录 ID 重复，无法判断引用归属'], 'pending')
            continue
        v = clean(kind, raw)
        issues = []
        if not valid_id(v.get('id')):
            v['id'] = 'recovered_' + fingerprint([sid,kind,position,raw])[:32]
            issues.append('缺失或无效 ID：生成稳定的新 ID')
        if v.get('space_id',sid) != sid:
            preserve(kind,raw,['记录空间归属不一致'],'pending');continue
        v['space_id'] = sid
        if not valid_id(v.get('origin_id',v['id'])):
            v['origin_id'] = v['id'];issues.append('来源 ID 无效：保留原值并使用记录 ID')
        if type(v.get('version',1)) is not int or v.get('version',1)<1:
            v['version']=1;issues.append('版本号无效：使用初始版本，原值保留')
        if 'migration' in v and not isinstance(v['migration'],dict):
            v['migration']={};issues.append('迁移标记无效：原标记保留')
        if isinstance(v.get('migration'),dict) and not isinstance(v['migration'].get('unavailable_references',[]),list):
            v['migration']['unavailable_references']=[];issues.append('失效引用标记格式无效，原标记留存')
        originals[(kind,v['id'])]=raw
        problems[(kind,v['id'])]=issues
        active.append({'kind':kind,'value':v})

    def reject(row, reason):
        kind,v=row['kind'],row['value']
        preserve(kind, originals[(kind,v['id'])], problems[(kind,v['id'])]+[reason], 'pending')

    def date(v, key, alternatives, issues, required=False):
        if valid_time(v.get(key)):return True
        candidate=next((x for x in alternatives if valid_time(x)),None)
        if candidate:
            v[key]=candidate;issues.append(key+' 缺失或无效：由已有时间推断')
        elif v.get(key) is not None or required:
            v[key]=None;issues.append(key+' 缺失或无效：时间未知')
        return candidate is not None

    message_times={}
    for row in active:
        if row['kind']=='message' and isinstance(row['value'].get('conversation_id'),str):
            message_times.setdefault(row['value']['conversation_id'],[]).append(row['value'].get('at'))
    people={r['value']['id'] for r in active if r['kind']=='person'}
    conversations={r['value']['id']:r['value'] for r in active if r['kind']=='conversation'}

    # Normalize intrinsic fields before resolving references, so references cannot
    # accidentally point at a record subsequently quarantined for missing content.
    safe=[]
    for row in active:
        kind,v=row['kind'],row['value'];issues=problems[(kind,v['id'])]
        try:
            if kind=='person':
                if not isinstance(v.get('name'),str) or not v['name'].strip():v['name']='身份未知';issues.append('人物名称未知')
                for key,default in [('relation','日常对话'),('platform',''),('external_uid',''),('aliases',[])]:v.setdefault(key,deepcopy(default))
                if bool(v['platform']) != bool(v['external_uid']):
                    v['platform']=v['external_uid']='';issues.append('平台身份不完整：原平台和 UID 留存，暂停身份匹配')
                PersonInput.model_validate({k:x for k,x in v.items() if k in PersonInput.model_fields})
            elif kind=='conversation':
                if not isinstance(v.get('title'),str) or not v['title'].strip():v['title']='恢复的会话';issues.append('会话标题缺失')
                if len(v['title'])>100:v['title']=v['title'][:100];issues.append('会话标题超长：原文保留，展示限制为 100 字')
                if v.get('kind') not in ('direct','group'):v['kind']='group';issues.append('会话类型未知：不推断私聊人物')
                # Child message timestamps are evidence for a recovered conversation.
                times=message_times.get(v['id'],[])
                if not date(v,'created_at',[v.get('updated_at'),*times],issues,True):raise ValueError('会话时间未知，暂存待恢复')
                v.setdefault('person_id',None)
            elif kind=='message':
                if v.get('role') not in ('user','assistant'):raise ValueError('说话角色未知，不参与整理')
                if not isinstance(v.get('content'),str):raise ValueError('聊天正文缺失，无法重建')
                cleared=bool(v.get('content_cleared_at') and isinstance(v.get('content_sha256'),str) and type(v.get('content_chars')) is int)
                if not v['content'].strip() and not cleared and not (v.get('processed') is True and v.get('content_cleared_at')):raise ValueError('聊天正文缺失，且没有正常清理标记')
                if type(v.get('processed')) is not bool:raise ValueError('消息消费状态未知，暂停整理及清理')
                offset=v.get('maintenance_offset',0)
                if type(offset) is not int or offset<0 or (not cleared and offset>len(v['content'])):raise ValueError('消息消费位置无效，暂停整理及清理')
                if not date(v,'at',[v.get('received_at')],issues,True):raise ValueError('消息时间未知，暂存待恢复')
                if 'person_id' not in v:
                    person=v.get('sender_id')
                    conversation=conversations.get(v.get('conversation_id')) if isinstance(v.get('conversation_id'),str) else None
                    if not person and conversation and conversation.get('kind','direct')=='direct':person=conversation.get('person_id')
                    v['person_id']=person if isinstance(person,str) and person in people else None
                v.setdefault('sender_id',None);v.setdefault('in_reply_to',None)
                if v['in_reply_to'] is not None and not valid_id(v['in_reply_to']):
                    v['in_reply_to']=None;issues.append('回复引用无效：原值保留')
                v.setdefault('mention_ids',[]);v.setdefault('fixture',False)
                if type(v['fixture']) is not bool:raise ValueError('消息来源标记无效')
                if v.get('retention') not in ('permanent','buffer') or (v.get('retention')=='buffer' and not valid_time(v.get('expires_at'))):
                    v['retention']='permanent';issues.append('保留策略未知：保留正文')
            elif kind=='event':
                if not isinstance(v.get('body'),str) or not v['body'].strip():raise ValueError('事件正文缺失，无法重建')
                if not isinstance(v.get('title'),str) or not v['title'].strip():v['title']=v['body'].strip()[:80] or '未命名';issues.append('标题从正文补全')
                defaults=EventInput(title=v['title'],body=v['body']).model_dump(mode='json')
                for k,x in defaults.items():
                    if k not in v:v[k]=x
                if not date(v,'learned_at',[v.get('created_at'),v.get('updated_at'),v.get('occurred_at')],issues,True):raise ValueError('记忆时间未知，暂存待恢复，避免按今天计算淡忘')
                date(v,'occurred_at',[],issues)
                v.setdefault('reinforcements',[]);v.setdefault('frozen',False)
                if type(v['frozen']) is not bool:raise ValueError('冻结状态无效，不能猜测是否允许修改')
                EventInput.model_validate_json(json.dumps({k:x for k,x in v.items() if k in EventInput.model_fields}),strict=True)
                if not isinstance(v['reinforcements'],list):raise ValueError('强化记录格式损坏')
                good=[]
                for x in v['reinforcements']:
                    if isinstance(x,dict) and valid_id(x.get('message_id') or x.get('unavailable_message_id')) and valid_time(x.get('at')):good.append(x)
                    else:issues.append('无效强化记录保留在恢复资料中，不参与权重计算')
                v['reinforcements']=good
            elif kind=='fragment':
                if not isinstance(v.get('content'),str) or not v['content'].strip():raise ValueError('片段正文缺失')
                v.setdefault('source_ids',[]);v.setdefault('source_ranges',[]);v.setdefault('kind','baseline')
            elif kind=='astrbot_pins':v.setdefault('event_ids',[])
            elif kind=='astrbot_person_alias':
                if not valid_id(v.get('platform')) or not valid_id(v.get('uid')):raise ValueError('人物平台身份缺失')
            # Nullable optional dates are safe; malformed ones are not used.
            for key in ('updated_at','processed_at','expires_at','content_cleared_at','received_at'):
                if key in v:date(v,key,[],issues)
            for key in ('person_ids','source_ids','mention_ids','event_ids','split_into'):
                if key in v and (not isinstance(v[key],list) or any(not valid_id(x) for x in v[key])):
                    raise ValueError(key+' 格式无效，保留原记录待恢复')
            safe.append(row)
        except (ValueError,TypeError,KeyError) as exc:reject(row,str(exc))

    indexes={k:{r['value']['id']:r['value'] for r in safe if r['kind']==k} for k in KINDS}
    result=[]
    generated={}
    for row in safe:
        kind,v=row['kind'],row['value'];issues=problems[(kind,v['id'])]
        missing=[]
        def ref(key, target, multi=False):
            values=v.get(key,[] if multi else None)
            if multi:
                keep=[]
                for x in values:
                    if x in indexes[target]:keep.append(x)
                    else:missing.append({'field':key,'original_id':x})
                v[key]=keep
            elif values is not None and (not isinstance(values,str) or values not in indexes[target]):
                missing.append({'field':key,'original_id':values});v[key]=None
        try:
            if kind=='conversation':ref('person_id','person')
            elif kind=='message':
                cid=v.get('conversation_id')
                if not isinstance(cid,str) or cid not in indexes['conversation']:
                    group=cid if valid_id(cid) else 'unknown-'+v['id']
                    rid='recovered_conv_'+fingerprint([sid,group])[:32]
                    if rid not in generated:
                        generated[rid]=dict(id=rid,space_id=sid,origin_id=rid,title='恢复的会话',kind='group',person_id=None,created_at=v['at'],updated_at=v['at'],migration={'original_conversation_id':cid},version=1)
                    v['conversation_id']=rid;issues.append('原会话缺失：归入独立的恢复会话')
                for key in ('person_id','sender_id'):ref(key,'person')
                ref('mention_ids','person',True)
                v.update(portable_message(v,indexes['message']))
            elif kind=='event':
                for key,target in [('person_ids','person'),('source_ids','message'),('split_into','event')]:
                    if key in v:ref(key,target,True)
                for key,target in [('split_from','event'),('merged_into','event'),('archive_conversation_id','conversation')]:ref(key,target)
                links=[]
                for link in v['links']:
                    if link['target_id'] in indexes['event']:links.append(link)
                    else:missing.append({'field':'links','original':deepcopy(link)})
                v['links']=links
                for eid in re.findall(r'\[\[([A-Za-z0-9_-]+)\]\]',v['body']):
                    if eid not in indexes['event']:missing.append({'field':'body','original_id':eid})
                v.update(portable_event(v,indexes['message']))
            elif kind=='fragment':
                if v.get('event_id') not in indexes['event']:raise ValueError('片段所属事件缺失，全文暂存待恢复')
                ref('source_ids','message',True)
                if not date(v,'recorded_at',[indexes['event'][v['event_id']].get('learned_at')],issues,True):raise ValueError('片段时间未知')
                ranges=[]
                if not isinstance(v['source_ranges'],list):v['source_ranges']=[];issues.append('来源位置格式无效，停用定位')
                for x in v['source_ranges']:
                    mid=x.get('message_id') if isinstance(x,dict) else None
                    msg=indexes['message'].get(mid) if isinstance(mid,str) else None
                    if not msg or type(x.get('start')) is not int or type(x.get('end')) is not int or not 0<=x['start']<=x['end'] or (msg.get('content') and x['end']>len(msg['content'])):
                        issues.append('来源位置失效，保留原值并停用定位')
                    else:ranges.append(x)
                v['source_ranges']=ranges
            elif kind=='astrbot_pins':
                ref('event_ids','event',True)
                v['event_ids']=list(dict.fromkeys(e for e in v['event_ids'] if indexes['event'][e].get('status')!='archived'))
            elif kind=='astrbot_person_alias':
                if v.get('person_id') not in indexes['person']:raise ValueError('人物身份映射指向缺失人物，保留平台及 UID 待恢复')
            if missing:
                meta=v.setdefault('migration',{})
                meta['unavailable_references']=meta.get('unavailable_references',[])+[x for x in missing if x not in meta.get('unavailable_references',[])]
                issues.append('关联目标缺失：原引用留存，不作为有效关系')
            raw=originals[(kind,v['id'])]
            if v != raw:
                preserve(kind,raw,issues or ['补齐默认字段或保留旧版扩展字段'])
            result.append({'kind':kind,'value':v})
        except (ValueError,TypeError,KeyError) as exc:reject(row,str(exc))
    result += [{'kind':'conversation','value':v} for v in generated.values()]

    # History remains a faithful snapshot, not a new current event. Keep original
    # body/IDs verbatim in evidence; mapped working snapshots only use safe refs.
    safe_history=[]
    events={r['value']['id'] for r in result if r['kind']=='event'}
    seen=Counter(r.get('id') for r in revisions if isinstance(r,dict) and isinstance(r.get('id'),str))
    for rev in revisions:
        if not isinstance(rev,dict):raise ValueError('历史版本结构损坏')
        if rev.get('event_id') not in events or not isinstance(rev.get('data'),dict) or not valid_time(rev.get('saved_at')) or not valid_id(rev.get('id')) or seen.get(rev.get('id'),0)>1:
            preserve('revision',rev,['历史版本归属、身份或时间不明：原样保留'],'pending');continue
        raw=deepcopy(rev)
        data=clean('event',rev['data'])
        try:EventInput.model_validate({k:x for k,x in data.items() if k in EventInput.model_fields})
        except (ValueError,TypeError):
            preserve('revision',raw,['历史内容不完整：原样保留'],'pending');continue
        # Rewriting only known live references later is safe; missing ones stay as
        # original text/IDs in historical snapshots, which never enter recall.
        exposures=data.get('reinforcements',[])
        data['reinforcements']=[x for x in exposures if isinstance(x,dict) and valid_id(x.get('message_id') or x.get('unavailable_message_id')) and valid_time(x.get('at'))] if isinstance(exposures,list) else []
        data=portable_event(data,indexes['message'])
        rev['data']=data
        if not isinstance(data.get('migration'),dict):data['migration']={}
        evidence_id=data['migration'].get('snapshot_evidence_id')
        if not isinstance(evidence_id,str) or evidence_id not in recovery:
            evidence_id=preserve('revision',raw,['历史原始快照：保留迁移前身份与全文'])
        data['migration']['snapshot_evidence_id']=evidence_id
        safe_history.append(rev)
    result += list(recovery.values())
    return result,safe_history
