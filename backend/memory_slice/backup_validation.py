"""Read-only validation for whole-database recovery, never performs migrations."""
import json
import sqlite3
from datetime import datetime
from .migrations import SCHEMA_VERSION
from .schemas import ConversationInput, EventInput, MemoryConfig, ModelInput, PersonInput

COLUMNS={
    'records':(('kind','TEXT',1),('id','TEXT',2),('space_id','TEXT',0),('data','TEXT',0),('version','INTEGER',0)),
    'versions':(('id','TEXT',1),('event_id','TEXT',0),('space_id','TEXT',0),('data','TEXT',0),('saved_at','TEXT',0)),
    'copy_identity':(('space_id','TEXT',1),('kind','TEXT',2),('origin_id','TEXT',3),('record_id','TEXT',0)),
}

def timestamp(value, key, required=False):
    raw=value.get(key)
    if raw is None and not required:return
    if not isinstance(raw,str) or not raw.strip():raise ValueError('无效或缺失的时间字段：'+key)
    datetime.fromisoformat(raw.replace('Z','+00:00'))


def validate_database(con):
    try:
        version=con.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA_VERSION:raise ValueError('备份来自更高版本，请升级程序后恢复')
        if [r[0] for r in con.execute('PRAGMA integrity_check')]!=['ok']:raise ValueError('备份完整性检查失败')
        objects={r[0]:(r[1],r[2]) for r in con.execute("SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        if any(v[0] in ('trigger','view') for v in objects.values()):raise ValueError('备份包含不支持的触发器或视图')
        required=('records','versions','copy_identity') if version>=1 else ('records','versions')
        for table in required:
            if table not in objects or objects[table][0]!='table':raise ValueError('备份缺少表：'+table)
            columns=tuple((r[1],r[2].upper(),r[5]) for r in con.execute('PRAGMA table_info('+table+')'))
            if columns!=COLUMNS[table]:raise ValueError('备份表结构不匹配：'+table)
        search=objects.get('event_search')
        if not search or 'using fts5' not in (search[1] or '').lower():raise ValueError('备份缺少正确的记忆搜索索引')
        spaces={r[0] for r in con.execute("SELECT id FROM records WHERE kind='space'")}
        configs=set();models=set();events=set()
        conversations={(r[0],r[1]) for r in con.execute("SELECT space_id,id FROM records WHERE kind='conversation'")}
        for kind,rid,sid,raw,rev in con.execute('SELECT kind,id,space_id,data,version FROM records'):
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('id')!=rid or type(rev) is not int or rev<1:raise ValueError('备份包含无效记录：'+str(rid))
            if kind in ('event','person','conversation','message','fragment','config','model','space') and sid not in spaces:raise ValueError('备份包含不属于任何空间的数据')
            if value.get('space_id',sid)!=sid:raise ValueError('备份空间归属不一致')
            if kind=='space':
                if rid!=sid or not isinstance(value.get('name'),str) or not value['name']:raise ValueError('无效空间')
            elif kind=='config':
                if rid!=sid:raise ValueError('无效配置 ID')
                MemoryConfig.model_validate({k:v for k,v in value.items() if k not in ('id','version')});configs.add(sid)
            elif kind=='model':
                if rid!=sid:raise ValueError('无效模型配置 ID')
                ModelInput.model_validate({k:v for k,v in value.items() if k in ModelInput.model_fields});models.add(sid)
            elif kind=='event':
                EventInput.model_validate({k:v for k,v in value.items() if k in EventInput.model_fields});events.add((sid,rid))
            elif kind=='person':PersonInput.model_validate({k:v for k,v in value.items() if k in PersonInput.model_fields})
            elif kind=='conversation':
                if 'title' not in value:raise ValueError('会话缺少标题')
                ConversationInput.model_validate({k:v for k,v in value.items() if k in ConversationInput.model_fields})
                timestamp(value,'created_at',required=True)
                timestamp(value,'updated_at')
            elif kind=='fragment':
                if not isinstance(value.get('content'),str):raise ValueError('无效片段正文')
                timestamp(value,'recorded_at',required=True)
                if not isinstance(value.get('source_ids'),list) or any(not isinstance(x,str) for x in value['source_ids']):raise ValueError('无效片段来源')
            elif kind=='message':
                if value.get('role') not in ('user','assistant') or not isinstance(value.get('content'),str) or not isinstance(value.get('conversation_id'),str):raise ValueError('无效聊天记录')
                timestamp(value,'at',required=True)
                for key in ('processed_at','content_cleared_at','expires_at'):timestamp(value,key)
                if (sid,value['conversation_id']) not in conversations:raise ValueError('聊天记录指向缺失或其他空间的会话')
                if 'processed' in value and type(value['processed']) is not bool:raise ValueError('无效消息处理状态')
                if 'maintenance_offset' in value and (type(value['maintenance_offset']) is not int or value['maintenance_offset']<0):raise ValueError('无效消息处理位置')
        if configs!=spaces or models!=spaces:raise ValueError('备份缺少空间配置或模型配置')
        for eid,sid,raw in con.execute('SELECT event_id,space_id,data FROM versions'):
            value=json.loads(raw)
            if (sid,eid) not in events or not isinstance(value,dict) or value.get('id')!=eid:raise ValueError('备份包含无效历史版本')
            EventInput.model_validate({k:v for k,v in value.items() if k in EventInput.model_fields})
        for sid,raw in con.execute("SELECT space_id,data FROM records WHERE kind='fragment'"):
            if (sid,json.loads(raw).get('event_id')) not in events:raise ValueError('备份片段指向缺失的事件')
    except (sqlite3.Error,TypeError,KeyError,json.JSONDecodeError) as exc:
        raise ValueError('不是完整有效的记忆切片数据库：'+str(exc)[:160]) from exc
