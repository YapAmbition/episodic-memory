"""Portable snapshots; imports are planned in isolation and committed atomically."""
import copy
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .errors import MemoryError
from .schemas import EventInput, PersonInput, SpaceInput
from .service import MemoryService
from .space_copy import SpaceCopier, CopyInput, REFERENCE_KINDS
from .store import iso

MAX_BYTES = 32 * 1024 * 1024
from .migration_compensation import KINDS, RECOVERY, OPAQUE, compensate, recovery_record, copy_audit_evidence
TABLES = ('records', 'versions', 'copy_identity')

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def formatted_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')

def digest(value): return hashlib.sha256(encoded(value)).hexdigest()

def rows(con, sid):
    return {table:[dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE space_id=? ORDER BY '+('kind,rowid' if table=='records' else 'id' if table=='versions' else 'kind,origin_id'), (sid,))] for table in TABLES}


def backup_filename(action, label, identity):
    # Portable on Windows/macOS; timestamp is explicitly UTC, suffix prevents collisions.
    safe=re.sub(r'[^\w-]+','-',label,flags=re.UNICODE).strip('-_')[:32] or 'database'
    ident=re.sub(r'[^A-Za-z0-9_-]+','-',identity)[:60]
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return f'before-{action}-{stamp}-{safe}-{ident}-{uuid.uuid4().hex[:8]}.sqlite3'


def verified_backup(source, destination):
    """Use SQLite backup (including WAL), verify, fsync, then publish the file."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix('.partial')
    try:
        fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(part) as backup:
            started=time.monotonic()
            def progress(*_):
                if time.monotonic()-started>30:raise MemoryError(503,'备份超过 30 秒，已停止，请稍后重试')
            source.backup(backup,pages=1024,progress=progress,sleep=0.05)
            if backup.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise MemoryError(500, '备份完整性检查失败，导入已停止')
        with part.open('rb') as file: os.fsync(file.fileno())
        os.replace(part, destination)
        if hasattr(os,'O_DIRECTORY'):
            fd=os.open(destination.parent,os.O_RDONLY | os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:
        part.unlink(missing_ok=True)


class ImportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package: dict
    operation_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,100}$')
    target_space_id: str | None = None
    new_space_name: str | None = Field(None, min_length=1, max_length=80)
    preview_token: str | None = None
    conflict_policy: Literal['skip','overwrite'] = 'skip'
    event_choices: dict[str, Literal['skip','overwrite']] = Field(default_factory=dict)
    pinned_event_ids: list[str] | None = Field(None, max_length=6)


class Transfers:
    def __init__(self, core, pin_budget=None):
        self.core, self.store, self.pin_budget = core, core.store, pin_budget

    def export(self, sid, include_messages=True):
        if not include_messages:raise MemoryError(422,'已移除省略聊天正文的选项，请导出完整迁移包')
        with self.store.connect() as con:
            con.execute('BEGIN')
            space=self.store.get('space',sid,con=con)
            if con.execute('SELECT count(*) FROM records WHERE space_id=?',(sid,)).fetchone()[0]>100000:
                raise MemoryError(413,'空间超过 100000 条记录，当前迁移包暂不支持')
            records=[]
            for kind in KINDS:
                for item in self.store.list(kind,sid,con):
                    value=copy.deepcopy(item)
                    records.append({'kind':kind,'value':value})
            revisions=[dict(r) for r in con.execute('SELECT * FROM versions WHERE space_id=? ORDER BY id',(sid,))]
            for rev in revisions: rev['data']=json.loads(rev['data'])
            records.extend(copy_audit_evidence(sid,self.store.list('space_copy',sid,con)))
            origins=[dict(r) for r in con.execute('SELECT * FROM copy_identity WHERE space_id=? ORDER BY kind,origin_id',(sid,))]
        records,revisions=compensate(records,revisions,sid)
        available={(r['kind'],r['value']['id']) for r in records}|{('revision',r['id']) for r in revisions}
        records.extend(recovery_record(sid,'origin',o,['来源映射的记录不存在，保留原映射'],'pending') for o in origins if (o['kind'],o['record_id']) not in available)
        origins=[o for o in origins if (o['kind'],o['record_id']) in available]
        payload=dict(space={k:space.get(k,'') for k in ('id','name','description')},exported_at=iso(),include_messages=include_messages,records=records,revisions=revisions,origins=origins)
        package=dict(format='memory-slice',format_version=2,payload=payload,sha256=digest(payload))
        if len(formatted_json(package))>MAX_BYTES:raise MemoryError(413,'完整迁移包超过 32 MiB，当前版本暂不支持；请保留原库，不会省略内容')
        self.validate(package)
        return package

    def validate(self, package):
        try:
            if len(encoded(package))>MAX_BYTES:raise ValueError('文件超过 32 MiB')
            if set(package)!={'format','format_version','payload','sha256'} or package['format']!='memory-slice' or type(package['format_version']) is not int or package['format_version'] not in (1,2):
                raise ValueError('不支持的迁移包格式或版本，请升级程序')
            p=copy.deepcopy(package['payload'])
            if digest(p)!=package['sha256']:raise ValueError('文件校验失败，内容可能损坏')
            if set(p)!={'space','exported_at','include_messages','records','revisions','origins'}:raise ValueError('包结构不完整')
            SpaceInput.model_validate({k:v for k,v in p['space'].items() if k!='id'})
            if p['include_messages'] is not True:raise ValueError('这是旧版省略聊天正文的包，请在源机器重新导出完整迁移包')
            from .migration_compensation import valid_id, valid_time
            sid=p['space']['id']
            if not valid_id(sid) or not valid_time(p['exported_at']):raise ValueError('缺少源空间 ID 或导出时间')
            if any(not isinstance(p[k],list) for k in ('records','revisions','origins')):raise ValueError('记录必须为列表')
            if sum(len(p[k]) for k in ('records','revisions','origins'))>100000:raise ValueError('记录超过 100000 条')
            p['records'],p['revisions']=compensate(p['records'],p['revisions'],sid)
            available={(r['kind'],r['value']['id']) for r in p['records']}|{('revision',r['id']) for r in p['revisions']}
            origins=[];seen=set()
            for o in p['origins']:
                if not isinstance(o,dict) or o.get('space_id')!=sid or not valid_id(o.get('origin_id')) or not valid_id(o.get('record_id')):
                    raise ValueError('来源映射损坏')
                key=(o['kind'],o['origin_id'])
                if key in seen:raise ValueError('来源映射重复')
                seen.add(key)
                if (o['kind'],o['record_id']) in available:origins.append(o)
                else:
                    p['records'].append(recovery_record(sid,'origin',o,['来源映射的记录不存在，保留原映射'],'pending'))
            p['origins']=origins
            return p
        except (KeyError,TypeError,ValueError,AttributeError,RecursionError,ValidationError) as exc:
            raise MemoryError(422,'迁移包校验失败：'+str(exc)[:300]) from exc

    def _state(self, con, target):
        return rows(con,target) if target else [dict(r) for r in con.execute("SELECT * FROM records WHERE kind='space' ORDER BY id")]

    def _stamp(self, con, target):
        # Store updates increment record versions. Rowids also detect replacement
        # with a fresh row/version; version history is append-only. Recall trace
        # payloads may change without a version, but imports never modify them.
        if not target:
            return [tuple(r) for r in con.execute("SELECT rowid,id,version FROM records WHERE kind='space' ORDER BY id")]
        return [
            [tuple(r) for r in con.execute('SELECT rowid,kind,id,version FROM records WHERE space_id=? ORDER BY kind,rowid',(target,))],
            [tuple(r) for r in con.execute('SELECT id,event_id,saved_at FROM versions WHERE space_id=? ORDER BY id',(target,))],
            [tuple(r) for r in con.execute('SELECT kind,origin_id,record_id FROM copy_identity WHERE space_id=? ORDER BY kind,origin_id',(target,))],
        ]

    def _idle(self, sid):
        if not sid:return
        with self.store.connect() as con:
            self.store.get('space',sid,con=con)
            if any(j['status'] in ('queued','running') for j in self.store.list('job',sid,con)):raise MemoryError(409,'请等待目标空间整理完成再导入')
            cids={c['id'] for c in self.store.list('conversation',sid,con)}
        if any(lock.locked() for cid,lock in self.core.chat_locks.items() if cid in cids):raise MemoryError(409,'目标空间正在回复，请稍后导入')

    @contextmanager
    def _stage(self, body, payload):
        self._idle(body.target_space_id)
        with tempfile.TemporaryDirectory(prefix='memory-import-') as directory:
            # Stage only the selected space, not the entire user's database.
            scratch=MemoryService(directory,seed=False)
            with self.store.connect() as source:
                source.execute('BEGIN')
                baseline=self._state(source,body.target_space_id)
                stamp=self._stamp(source,body.target_space_id)
                space_rows=[dict(r) for r in source.execute("SELECT * FROM records WHERE kind='space' ORDER BY rowid")]
                with scratch.store.connect() as con:
                    if body.target_space_id:
                        for table in TABLES:
                            for row in baseline[table]:
                                keys=list(row)
                                con.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",tuple(row[k] for k in keys))
                    for row in space_rows:
                        if row['id']!=body.target_space_id:
                            con.execute('INSERT INTO records VALUES(?,?,?,?,?)',tuple(row[k] for k in ('kind','id','space_id','data','version')))
            sid='import_source_'+digest(payload)[:24]
            if any(s['id']==sid for s in scratch.spaces()):raise MemoryError(409,'导入暂存 ID 冲突')
            # Deterministic staging IDs isolate untrusted IDs from all live records.
            maps={kind:{r['value']['id']:'stage_'+hashlib.sha256((sid+kind+r['value']['id']).encode()).hexdigest()[:32] for r in payload['records'] if r['kind']==kind} for kind in KINDS}
            maps['revision']={r['id']:'stage_rev_'+hashlib.sha256((sid+r['id']).encode()).hexdigest()[:32] for r in payload['revisions']}
            def rewrite(value,key=''):
                if key in OPAQUE:return copy.deepcopy(value)
                if isinstance(value,dict):return {k:rewrite(v,k) for k,v in value.items()}
                if isinstance(value,list):return [rewrite(v,key) for v in value]
                if key in REFERENCE_KINDS and isinstance(value,str):return maps[REFERENCE_KINDS[key]].get(value,value)
                if key in ('body','content') and isinstance(value,str):return re.sub(r'\[\[([A-Za-z0-9_-]+)\]\]',lambda m:'[['+maps['event'].get(m[1],m[1])+']]',value)
                if key=='space_id':return sid
                return value
            with scratch.store.connect() as con:
                scratch.store.put('space',dict(id=sid,name=payload['space']['name'],description='迁移暂存'),sid,con=con)
                for row in payload['records']:
                    kind,old=row['kind'],row['value'];v=copy.deepcopy(old) if kind==RECOVERY else rewrite(old)
                    v.update(id=maps[kind][old['id']],space_id=sid,origin_id=old.get('origin_id',old['id']))
                    if kind=='message':v['content']=old['content']
                    con.execute('INSERT INTO records VALUES(?,?,?,?,?)',(kind,v['id'],sid,json.dumps(v,ensure_ascii=False),old.get('version',1)))
                for rev in payload['revisions']:
                    value=rewrite(rev['data']);value['id']=maps['event'][rev['event_id']]
                    con.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(maps['revision'][rev['id']],maps['event'][rev['event_id']],sid,json.dumps(value,ensure_ascii=False),rev['saved_at']))
                    con.execute('INSERT INTO copy_identity VALUES(?,?,?,?)',(sid,'revision',rev['id'],maps['revision'][rev['id']]))
                for o in payload['origins']:
                    con.execute('INSERT OR REPLACE INTO copy_identity VALUES(?,?,?,?)',(sid,o['kind'],o['origin_id'],maps[o['kind']][o['record_id']]))
            target=body.target_space_id
            if not target:
                name=(body.new_space_name or payload['space']['name']).strip()
                existing={s['name'] for s in scratch.spaces()}
                base=name
                if name in existing:name=base[:76]+'（导入）'
                n=2
                while name in existing:
                    suffix=f'（导入 {n}）';name=base[:80-len(suffix)]+suffix;n+=1
                target=scratch.add_space(SpaceInput(name=name[:80]))['id']
            copier=SpaceCopier(scratch,self.pin_budget)
            plan=copier.preview(sid,CopyInput(target_space_id=target))
            token=digest([stamp,body.package['sha256'],body.target_space_id,body.new_space_name,body.operation_id])
            yield scratch,copier,sid,target,plan,token,baseline,stamp

    def preview(self, body):
        payload=self.validate(body.package)
        with self._stage(body,payload) as (_,_,_,_,plan,token,_,_):
            return dict(plan,preview_token=token,operation_id=body.operation_id,creating_space=not body.target_space_id)

    def operation(self, operation_id):
        return self.store.get('import_operation','import_'+operation_id)['result']

    def commit(self, body):
        payload=self.validate(body.package)
        request_hash=digest(body.model_dump(exclude={'preview_token'}))
        operation='import_'+body.operation_id
        def completed(con):
            row=con.execute("SELECT data FROM records WHERE kind='import_operation' AND id=?",(operation,)).fetchone()
            if row:
                old=json.loads(row['data'])
                if old['request_hash']!=request_hash:raise MemoryError(409,'此操作编号已用于不同的导入，请重新预览')
                return old['result']
        with self.store.connect() as con:
            result=completed(con)
            if result:return result
        try:
            with self._stage(body,payload) as (scratch,copier,sid,target,plan,token,baseline,stamp):
                if not body.preview_token or body.preview_token!=token:raise MemoryError(409,'目标数据已变化，请重新预览')
                result=copier.copy(sid,CopyInput(target_space_id=target,preview_token=plan['preview_token'],conflict_policy=body.conflict_policy,event_choices=body.event_choices,pinned_event_ids=body.pinned_event_ids))
                with scratch.store.connect() as con: output=rows(con,target)
                # Build the patch before taking the writer lock. Leave existing
                # message rowids untouched, including equal-timestamp messages.
                current=baseline if body.target_space_id else {table:[] for table in TABLES}
                primary={'records':('kind','id'),'versions':('id',),'copy_identity':('space_id','kind','origin_id')}
                mutations=[]
                for table in TABLES:
                    pk=primary[table]
                    before={tuple(row[k] for k in pk):row for row in current[table]}
                    after={tuple(row[k] for k in pk):row for row in output[table]}
                    where=' AND '.join(k+'=?' for k in pk)
                    for key in before.keys()-after.keys():mutations.append((f'DELETE FROM {table} WHERE '+where,key))
                    for key,row in after.items():
                        if before.get(key)==row:continue
                        if key in before:
                            changed=[k for k in row if k not in pk]
                            mutations.append((f'UPDATE {table} SET '+','.join(k+'=?' for k in changed)+' WHERE '+where,tuple(row[k] for k in changed)+key))
                        else:
                            keys=list(row)
                            mutations.append((f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",tuple(row[k] for k in keys)))
                before_events={r['id']:r for r in current['records'] if r['kind']=='event'}
                changed_events=[r for r in output['records'] if r['kind']=='event' and before_events.get(r['id'])!=r]
                # Snapshot backup uses a WAL read transaction. It never holds
                # SQLite's writer lock; concurrent writes to other spaces continue.
                backup=self.store.directory/'backups'/backup_filename('import',plan['target']['name'],body.operation_id)
                with self.store.connect() as reader:
                    reader.execute('BEGIN')
                    done=completed(reader)
                    if done:return done
                    if self._stamp(reader,body.target_space_id)!=stamp:raise MemoryError(409,'目标数据已变化，请重新预览')
                    verified_backup(reader,backup)
                with self.store.connect() as con:
                    con.execute('PRAGMA busy_timeout=1000');con.execute('BEGIN IMMEDIATE')
                    done=completed(con)
                    if done:return done
                    self._idle(body.target_space_id)
                    if self._stamp(con,body.target_space_id)!=stamp:raise MemoryError(409,'目标数据已变化，请重新预览')
                    for statement,parameters in mutations:
                        con.execute(statement,parameters)
                    for row in changed_events:
                        self.store._index_event(con,json.loads(row['data']),row['version'])
                    result.update(operation_id=body.operation_id,backup_path=str(backup.resolve()),space_name=plan['target']['name'])
                    self.store.put('import_operation',dict(id=operation,request_hash=request_hash,result=result,at=iso()),target,con=con)
                self.core.embeddings.state(target)['generation']+=1
                return result
        except (OSError,sqlite3.Error) as exc:
            raise MemoryError(503,'导入未完成，数据库写入已回滚；请检查磁盘空间或稍后重试（'+type(exc).__name__+'）') from exc
