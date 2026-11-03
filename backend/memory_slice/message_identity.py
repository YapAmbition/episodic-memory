"""Recover message identity omitted by early portable snapshots."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
import uuid


def complete_person_ids(messages, conversations, persons):
    """Only fill absent fields; never replace an explicitly recorded identity/null."""
    def valid(pid, sid):
        return pid if isinstance(pid, str) and (sid, pid) in persons else None

    changed=[]
    for message in messages.values():
        if 'person_id' in message:continue
        sid=message.get('space_id')
        person=valid(message.get('sender_id'),sid)
        conversation=conversations.get((sid,message.get('conversation_id')), {})
        if person is None and conversation.get('kind','direct')=='direct':
            person=valid(conversation.get('person_id'),sid)
        # Group assistant replies can refer to a known user; do not guess from mentions.
        if person is None and message.get('role')=='assistant':
            parent=messages.get((sid,message.get('in_reply_to')), {})
            if parent.get('role')=='user' and parent.get('conversation_id')==message.get('conversation_id'):
                person=valid(parent.get('person_id') or parent.get('sender_id'),sid)
        message['person_id']=person
        changed.append(message)
    return changed


def repair_imported_messages(store):
    with store.connect() as con:
        missing=con.execute("SELECT 1 FROM records WHERE kind='message' AND json_type(data,'$.person_id') IS NULL LIMIT 1").fetchone()
        if not missing:return 0
        # Startup holds the database lifecycle lock. Keep a full snapshot before repair.
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        directory=store.directory/'backups';directory.mkdir(exist_ok=True)
        path=directory/f'before-message-identity-repair-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3'
        with path.open('xb'):pass
        path.chmod(0o600)
        with closing(sqlite3.connect(path)) as backup:
            con.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise RuntimeError('消息身份修复前备份校验失败')
        con.execute('BEGIN IMMEDIATE')
        data={kind:store.list(kind,con=con) for kind in ('message','conversation','person')}
        messages={(m['space_id'],m['id']):m for m in data['message']}
        conversations={(c['space_id'],c['id']):c for c in data['conversation']}
        persons={(p['space_id'],p['id']) for p in data['person']}
        changed=complete_person_ids(messages,conversations,persons)
        for message in changed:store.put('message',message,message['space_id'],con=con)
        return len(changed)
