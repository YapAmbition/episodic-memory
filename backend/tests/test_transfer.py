import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, EventInput, ConversationInput, MessageInput
from memory_slice.transfer import Transfers, ImportInput, digest, rows
from memory_slice.errors import MemoryError

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.a=tempfile.TemporaryDirectory();self.b=tempfile.TemporaryDirectory()
        self.source=MemoryService(self.a.name,seed=False);self.target=MemoryService(self.b.name,seed=False)
        self.sid=self.source.add_space(SpaceInput(name='snowluma'))['id']
        self.tid=self.target.add_space(SpaceInput(name='snowluma'))['id']
        self.exporter=Transfers(self.source);self.importer=Transfers(self.target)
        pid=self.source.persons(self.sid)[0]['id']
        cid=self.source.add_conversation(self.sid,ConversationInput(person_id=pid))['id']
        self.message=self.source.post_message(self.sid,cid,MessageInput(role='user',content='明天一起去看海',sender_id=pid))
        self.event=self.source.create_event(self.sid,EventInput(title='看海计划',body='明天去看海',person_ids=[pid],source_ids=[self.message['id']]))
        self.source.store.put('astrbot_pins',dict(id=self.sid,event_ids=[self.event['id']]),self.sid)
        self.pkg=self.exporter.export(self.sid)
        self.n=0
    def tearDown(self):self.a.cleanup();self.b.cleanup()
    def body(self,**kw):
        self.n+=1
        return ImportInput(package=self.pkg,operation_id=f'test-operation-{self.n:06}',target_space_id=self.tid,**kw)
    def preview(self,b):
        p=self.importer.preview(b);return b.model_copy(update={'preview_token':p['preview_token']}),p
    def state(self):
        with self.target.store.connect() as c:
            return {t:[tuple(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in ('records','versions','copy_identity','event_search')}
    def assert_can_prepare_imported_messages(self, core, sid):
        from memory_slice.maintenance_context import prepare
        from memory_slice.store import utcnow
        from memory_slice.focus import select_messages
        messages=core.store.list('message',sid)
        person=messages[0]['person_id']
        self.assertIsNotNone(person)
        self.assertEqual(core.store.get('person',person)['space_id'],sid)
        config=core.config_for(sid)
        selected,_=select_messages(core.store,sid,messages,dict(config,focus_person_id=person))
        self.assertEqual(len(selected),len(messages))
        result=prepare(core.store,sid,messages,core.model_for(sid)[0],config,utcnow(),'test')
        self.assertIsNotNone(result)

    def test_new_and_legacy_exports_can_continue_maintenance(self):
        self.assertEqual(next(r['value'] for r in self.pkg['payload']['records'] if r['kind']=='message')['person_id'],self.message['person_id'])
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                package=copy.deepcopy(self.pkg)
                if legacy:
                    for row in package['payload']['records']:
                        if row['kind']=='message':row['value'].pop('person_id',None)
                    package['sha256']=digest(package['payload'])
                original=copy.deepcopy(package)
                body=ImportInput(package=package,operation_id=f'identity-roundtrip-{legacy}',new_space_name='身份测试')
                body,_=self.preview(body)
                result=self.importer.commit(body)
                self.assertEqual(package,original)
                self.assert_can_prepare_imported_messages(self.target,result['space_id'])
                self.assertNotEqual(self.target.store.list('message',result['space_id'])[0]['person_id'],self.message['person_id'])

    def test_startup_repairs_previous_imports_once_with_backup(self):
        body,_=self.preview(self.body());self.importer.commit(body)
        with self.target.store.connect() as con:
            con.execute("UPDATE records SET data=json_remove(data,'$.person_id') WHERE kind='message'")
        previous=self.target.store.list('message',self.tid)[0]
        restarted=MemoryService(self.b.name,seed=False)
        self.assert_can_prepare_imported_messages(restarted,self.tid)
        after=restarted.store.list('message',self.tid)[0]
        self.assertEqual(after['content'],previous['content'])
        self.assertEqual(after['processed'],previous['processed'])
        backups=list((Path(self.b.name)/'backups').glob('before-message-identity-repair-*.sqlite3'))
        self.assertEqual(len(backups),1)
        with sqlite3.connect(backups[0]) as con:
            self.assertNotIn('person_id',json.loads(con.execute("SELECT data FROM records WHERE kind='message'").fetchone()[0]))
        MemoryService(self.b.name,seed=False)
        self.assertEqual(len(list((Path(self.b.name)/'backups').glob('before-message-identity-repair-*.sqlite3'))),1)

    def test_identity_repair_does_not_guess_group_or_replace_existing(self):
        from memory_slice.message_identity import complete_person_ids
        people={('s','p')};conversations={('s','c'):{'kind':'group','person_id':'p'}}
        messages={('s','u'):{'id':'u','space_id':'s','role':'user','conversation_id':'c'},
                  ('s','a'):{'id':'a','space_id':'s','role':'assistant','conversation_id':'c','sender_id':'p','person_id':None},
                  ('s','v'):{'id':'v','space_id':'s','role':'user','conversation_id':'c','sender_id':'p'},
                  ('s','r'):{'id':'r','space_id':'s','role':'assistant','conversation_id':'c','in_reply_to':'v'}}
        complete_person_ids(messages,conversations,people)
        self.assertIsNone(messages[('s','u')]['person_id'])
        self.assertIsNone(messages[('s','a')]['person_id'])
        self.assertEqual(messages[('s','v')]['person_id'],'p')
        self.assertEqual(messages[('s','r')]['person_id'],'p')

    def test_missing_reinforcement_source_roundtrips_without_fake_messages(self):
        from memory_slice.schemas import ReinforceInput
        message=self.source.post_message(self.sid,self.message['conversation_id'],MessageInput(role='user',content='又想到看海',sender_id=self.message['person_id']))
        event=self.source.reinforce(self.sid,self.event['id'],ReinforceInput(message_id=message['id']))
        self.source.store.delete('message',message['id'],self.sid)
        original=self.source.store.get('event',event['id'])
        self.pkg=self.exporter.export(self.sid)
        self.assertEqual(self.source.store.get('event',event['id']),original)
        b,_=self.preview(self.body());result=self.importer.commit(b)
        imported=self.target.store.get('event',result['event_map'][next(iter(result['event_map']))])
        reinforcement=imported['reinforcements'][0]
        self.assertIsNone(reinforcement['message_id'])
        self.assertEqual(reinforcement['unavailable_message_id'],message['id'])
        self.assertEqual(reinforcement['at'],event['reinforcements'][0]['at'])
        self.assertEqual(len(self.target.store.list('message',self.tid)),1)
        self.importer.export(self.tid)

    def test_buffer_cleanup_protects_reinforcement_messages(self):
        from memory_slice.schemas import ReinforceInput
        from memory_slice.focus import cleanup_buffer
        message=self.source.post_message(self.sid,self.message['conversation_id'],MessageInput(role='user',content='又想到看海',sender_id=self.message['person_id']))
        self.source.reinforce(self.sid,self.event['id'],ReinforceInput(message_id=message['id']))
        self.source.store.put('message',dict(message,retention='buffer',expires_at='2000-01-01T00:00:00+00:00',processed=True),self.sid)
        self.assertEqual(cleanup_buffer(self.source.store,self.sid,self.source.config_for(self.sid)),0)
        self.assertEqual(self.source.store.get('message',message['id'])['retention'],'permanent')
        self.exporter.export(self.sid)

    def test_roundtrip_independent_repeat_and_backup(self):
        before=self.state();b,p=self.preview(self.body())
        self.assertEqual(before,self.state())
        b=b.model_copy(update={'pinned_event_ids':[v['id'] for v in p['pinned_events']]})
        result=self.importer.commit(b)
        copied=self.target.store.list('event',self.tid)[0]
        self.assertNotEqual(copied['id'],self.event['id']);self.assertEqual(copied['origin_id'],self.event['id'])
        self.assertEqual(self.target.store.list('message',self.tid)[0]['content'],self.message['content'])
        self.assertEqual(self.target.store.get('astrbot_pins',self.tid)['event_ids'],[copied['id']])
        with sqlite3.connect(result['backup_path']) as backup:
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(backup.execute("SELECT count(*) FROM records WHERE kind='event'").fetchone()[0],0)
        state=self.state();self.assertEqual(self.importer.commit(b),result);self.assertEqual(state,self.state())
        self.assertEqual(self.importer.operation(b.operation_id),result)
        repeat,plan=self.preview(self.body());self.assertEqual(len(plan['conflicts']),1)
        self.assertEqual(self.importer.commit(repeat)['counts']['skip'],1)
        self.assertEqual(len(self.target.store.list('message',self.tid)),1)
        self.target.update_event(self.tid,copied['id'],EventInput(title='本地新内容',body='本地修改要保留'))
        overwrite,_=self.preview(self.body(conflict_policy='overwrite'))
        self.importer.commit(overwrite)
        self.assertTrue(any(v['event']['body']=='本地修改要保留' for v in self.target.store.revisions(copied['id'],self.tid)))
        self.assertEqual(self.source.store.get('event',self.event['id'])['body'],'明天去看海')
    def test_new_same_name_and_same_database_ids(self):
        # Import to the original database: all source IDs already exist.
        self.importer=Transfers(self.source)
        body=ImportInput(package=self.pkg,operation_id='new-space-operation',new_space_name='snowluma')
        b,p=self.preview(body);self.assertEqual(p['target']['name'],'snowluma（导入）')
        result=self.importer.commit(b)
        self.assertNotEqual(result['space_id'],self.sid)
        self.assertEqual(len(self.source.spaces()),2)
        self.assertEqual(self.source.store.get('event',self.event['id'])['space_id'],self.sid)
    def test_corruption_unknown_version_missing_reference_rejected(self):
        original=self.state()
        for change,rehash in ((lambda p:p.update(format_version=999),False),
            (lambda p:p['payload']['space'].update(name='tampered'),False),
            (lambda p:p['payload']['records'].append({'kind':'model','value':{'id':'bad','api_key':'secret'}}),True),
            (lambda p:next(r['value'] for r in p['payload']['records'] if r['kind']=='event').update(person_ids=['missing']),True)):
            package=copy.deepcopy(self.pkg);change(package)
            if rehash:package['sha256']=digest(package['payload'])
            with self.assertRaises(MemoryError):self.importer.preview(self.body().model_copy(update={'package':package}))
            self.assertEqual(original,self.state())
    def test_failed_backup_and_write_rollback(self):
        b,_=self.preview(self.body());before=self.state()
        from memory_slice.transfer import verified_backup
        def backup(source,dest):
            if 'before-import' in str(dest):raise OSError('disk full')
            return verified_backup(source,dest)
        with patch('memory_slice.transfer.verified_backup',side_effect=backup):
            with self.assertRaises(MemoryError):self.importer.commit(b)
        self.assertEqual(before,self.state())
        with patch.object(self.target.store,'_index_event',side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(MemoryError):self.importer.commit(b)
        self.assertEqual(before,self.state())
        self.importer.commit(b)
    def test_stale_preview_and_operation_reuse(self):
        b,_=self.preview(self.body());self.target.create_event(self.tid,EventInput(title='新事件',body='预览后新写入'))
        before=self.state()
        with self.assertRaises(MemoryError):self.importer.commit(b)
        self.assertEqual(before,self.state())
        b,_=self.preview(b);self.importer.commit(b)
        with self.assertRaises(MemoryError):self.importer.commit(b.model_copy(update={'conflict_policy':'overwrite'}))
    def test_freeze_and_transitive_origins(self):
        self.pkg=self.exporter.export(self.sid)
        b,_=self.preview(self.body());self.importer.commit(b)
        message=self.target.store.list('message',self.tid)[0]
        self.assertEqual(message['content'],self.message['content'])
        event=self.target.store.list('event',self.tid)[0]
        self.target.freeze_event(self.tid,event['id'],True)
        b,_=self.preview(self.body(conflict_policy='overwrite'));before=self.state()
        with self.assertRaises(MemoryError):self.importer.commit(b)
        self.assertEqual(before,self.state())
        repack=Transfers(self.target).export(self.tid)
        back=Transfers(self.source)
        b=ImportInput(package=repack,operation_id='copy-back-operation',target_space_id=self.sid)
        p=back.preview(b)
        self.assertEqual(len(p['conflicts']),1)
    def test_export_excludes_secrets_and_connections(self):
        self.source.store.put('model',dict(id=self.sid,api_key='DO_NOT_EXPORT'),self.sid)
        self.source.store.put('astrbot_scope',dict(id='external',secret='DO_NOT_EXPORT'),self.sid)
        exported=json.dumps(self.exporter.export(self.sid))
        self.assertNotIn('DO_NOT_EXPORT',exported)

    def test_restore_keeps_before_restore_snapshot(self):
        import importlib.util
        script=Path(__file__).resolve().parents[2]/'scripts/restore_memory_backup.py'
        spec=importlib.util.spec_from_file_location('restore_test',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        b,_=self.preview(self.body());result=self.importer.commit(b)
        saved=module.restore(result['backup_path'],self.target.store.path)
        self.assertEqual(len(self.target.store.list('event',self.tid)),0)
        with sqlite3.connect(saved) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM records WHERE kind='event'").fetchone()[0],1)
        corrupt=Path(self.b.name)/'corrupt.sqlite3';corrupt.write_bytes(b'bad database')
        before=self.state()
        with self.assertRaises(ValueError):module.restore(corrupt,self.target.store.path)
        self.assertEqual(before,self.state())

    def test_concurrent_change_between_plan_and_commit(self):
        b,_=self.preview(self.body())
        from memory_slice.space_copy import SpaceCopier
        original=SpaceCopier.copy
        def mutate(copier,*args,**kw):
            result=original(copier,*args,**kw)
            self.target.create_event(self.tid,EventInput(title='并发新消息',body='这个改动必须保留'))
            return result
        with patch.object(SpaceCopier,'copy',mutate):
            with self.assertRaises(MemoryError):self.importer.commit(b)
        events=self.target.store.list('event',self.tid)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['title'],'并发新消息')

    def test_parallel_same_operation_commits_once(self):
        from concurrent.futures import ThreadPoolExecutor
        b,_=self.preview(self.body())
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _:self.importer.commit(b),range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(len(self.target.store.list('event',self.tid)),1)

    def test_process_crash_leaves_original_database_intact(self):
        import subprocess,sys,os
        b,_=self.preview(ImportInput(package=self.pkg,operation_id='crash-import-operation'))
        request=Path(self.b.name)/'request.json';request.write_text(b.model_dump_json())
        before=self.state()
        code="""
import json,os,sys
from memory_slice.service import MemoryService
from memory_slice.transfer import Transfers,ImportInput
core=MemoryService(sys.argv[1],seed=False)
core.store._index_event=lambda *a,**kw:os._exit(72)
Transfers(core).commit(ImportInput.model_validate(json.load(open(sys.argv[2]))))
"""
        process=subprocess.run([sys.executable,'-c',code,self.b.name,str(request)],env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1])),timeout=15)
        self.assertEqual(process.returncode,72)
        self.assertEqual(self.state(),before)
        with self.target.store.connect() as con:self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertTrue(list((Path(self.b.name)/'backups').glob('before-import-*')))
        self.importer.commit(b)

    def test_deleted_historical_link_does_not_block_migration(self):
        second=self.source.create_event(self.sid,EventInput(title='后续',body='继续 [['+self.event['id']+']] 的计划'))
        self.source.delete_event(self.sid,self.event['id'])
        self.source.store.put('astrbot_pins',dict(id=self.sid,event_ids=[]),self.sid)
        self.pkg=self.exporter.export(self.sid)
        b,_=self.preview(self.body());self.importer.commit(b)
        events=self.target.store.list('event',self.tid)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['origin_id'],second['id'])

    def test_existing_message_ingestion_order_is_untouched(self):
        cid=self.target.add_conversation(self.tid,ConversationInput())['id']
        for text in ('第一句','第二句','第三句'):
            self.target.post_message(self.tid,cid,MessageInput(role='user',content=text,at='2026-11-03T00:00:00Z'))
        with self.target.store.connect() as con:
            before=[tuple(r) for r in con.execute("SELECT rowid,id,data,version FROM records WHERE kind='message' ORDER BY rowid")]
        b,_=self.preview(self.body());self.importer.commit(b)
        with self.target.store.connect() as con:
            after=[tuple(r) for r in con.execute("SELECT rowid,id,data,version FROM records WHERE kind='message' AND json_extract(data,'$.conversation_id')=? ORDER BY rowid",(cid,))]
        self.assertEqual(before,after)

    def test_export_always_complete_and_rejects_legacy_partial_package(self):
        with self.assertRaises(MemoryError):self.exporter.export(self.sid,False)
        package=copy.deepcopy(self.pkg);package['payload']['include_messages']=False
        package['sha256']=digest(package['payload'])
        with self.assertRaises(MemoryError):self.importer.validate(package)
        before=self.source.store.get('message',self.message['id'])
        self.assertEqual(next(r['value'] for r in self.exporter.export(self.sid)['payload']['records'] if r['kind']=='message')['content'],before['content'])
        self.assertEqual(self.source.store.get('message',self.message['id']),before)

    def test_refill_legacy_omission_but_preserve_retention_cleanup(self):
        b,_=self.preview(self.body());self.importer.commit(b)
        message=self.target.store.list('message',self.tid)[0]
        old=dict(message,content='',processed=True,maintenance_offset=0,content_cleared_at='2026-11-03T00:00:00+00:00',retention='permanent')
        self.target.store.put('message',old,self.tid)
        b,plan=self.preview(self.body())
        self.assertEqual(plan['restorable_messages'],1)
        result=self.importer.commit(b)
        restored=self.target.store.get('message',message['id'])
        self.assertEqual(result['counts']['restored_messages'],1)
        self.assertEqual(restored['content'],self.message['content'])
        self.assertEqual(restored.get('processed'),self.message.get('processed'))
        self.assertNotIn('content_cleared_at',restored)
        from memory_slice.message_retention import content_hash
        cleaned=dict(old,content_sha256=content_hash(message['content']),content_chars=len(message['content']))
        self.target.store.put('message',cleaned,self.tid)
        b,plan=self.preview(self.body());self.assertEqual(plan['restorable_messages'],0)
        self.importer.commit(b)
        self.assertEqual(self.target.store.get('message',message['id'])['content'],'')

    def test_deleted_and_legacy_stale_pins_export_valid_package(self):
        self.source.delete_event(self.sid,self.event['id'])
        self.assertEqual(self.source.store.get('astrbot_pins',self.sid)['event_ids'],[])
        # Old releases may have already left a stale selection. Export is read-only.
        self.source.store.put('astrbot_pins',dict(id=self.sid,event_ids=[self.event['id']]),self.sid)
        package=self.exporter.export(self.sid);self.importer.validate(package)
        self.assertEqual(next(r['value']['event_ids'] for r in package['payload']['records'] if r['kind']=='astrbot_pins'),[])
        self.assertEqual(self.source.store.get('astrbot_pins',self.sid)['event_ids'],[self.event['id']])

    def test_restore_rejects_impostor_and_invalid_business_data(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('restore_check',Path(__file__).resolve().parents[2]/'scripts/restore_memory_backup.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        before=self.state()
        fake=Path(self.b.name)/'fake.sqlite3'
        with sqlite3.connect(fake) as c:c.execute('CREATE TABLE records (unrelated TEXT)')
        with self.assertRaises(ValueError):module.restore(fake,self.target.store.path)
        self.assertEqual(before,self.state())
        fake.unlink()
        with self.target.store.connect() as source,sqlite3.connect(fake) as dest:source.backup(dest)
        with sqlite3.connect(fake) as c:c.execute("DELETE FROM records WHERE kind='config'")
        with self.assertRaises(ValueError):module.restore(fake,self.target.store.path)
        self.assertEqual(before,self.state())

    def test_restore_rejects_invalid_conversations_messages_and_fragments(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('restore_fields',Path(__file__).resolve().parents[2]/'scripts/restore_memory_backup.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        before=self.state()
        cases=[('conversation','created_at',None),('conversation','created_at','bad-date'),
               ('conversation','title',None),('message','at',None),
               ('message','conversation_id','missing'),('message','processed','yes'),
               ('fragment','recorded_at',None),('fragment','content',None)]
        for index,(kind,key,value) in enumerate(cases):
            with self.subTest(kind=kind,key=key,value=value):
                backup=Path(self.b.name)/f'invalid-{index}.sqlite3'
                with self.source.store.connect() as source,sqlite3.connect(backup) as dest:source.backup(dest)
                with sqlite3.connect(backup) as con:
                    row=con.execute('SELECT id,data FROM records WHERE kind=?',(kind,)).fetchone()
                    self.assertIsNotNone(row)
                    data=json.loads(row[1])
                    if value is None:data.pop(key,None)
                    else:data[key]=value
                    con.execute('UPDATE records SET data=? WHERE kind=? AND id=?',(json.dumps(data),kind,row[0]))
                with self.assertRaises(ValueError):module.restore(backup,self.target.store.path)
                self.assertEqual(before,self.state())
                self.assertFalse((Path(self.b.name)/'backups').exists())

    def test_backup_names_are_identifiable_portable_and_unique(self):
        from memory_slice.transfer import backup_filename
        name=backup_filename('import','日常记忆 / a:b?*','test-operation')
        self.assertRegex(name,r'^before-import-\d{8}T\d{12}Z-日常记忆-a-b-test-operation-[0-9a-f]{8}\.sqlite3$')
        self.assertNotEqual(name,backup_filename('import','日常记忆 / a:b?*','test-operation'))
        b,_=self.preview(self.body())
        result=self.importer.commit(b)
        self.assertIn('-snowluma-',Path(result['backup_path']).name)

    def test_restore_service_exclusion_both_directions(self):
        import asyncio,importlib.util
        from memory_slice.database_guard import database_guard
        spec=importlib.util.spec_from_file_location('restore_lock',Path(__file__).resolve().parents[2]/'scripts/restore_memory_backup.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        b,_=self.preview(self.body());result=self.importer.commit(b)
        async def check_running():
            await self.target.start()
            try:
                before=self.state()
                with self.assertRaisesRegex(ValueError,'仍在运行'):module.restore(result['backup_path'],self.target.store.path)
                self.assertEqual(before,self.state())
            finally:await self.target.close()
        asyncio.run(check_running())
        with database_guard(self.target.store.path):
            with self.assertRaisesRegex(RuntimeError,'正在恢复'):MemoryService(self.b.name,seed=False)
        module.restore(result['backup_path'],self.target.store.path)

    def test_preview_does_not_clone_other_spaces_or_make_backups(self):
        other=self.target.add_space(SpaceInput(name='其他空间'))['id']
        self.target.store.put('integration',dict(id='big-extra',payload='x'*2000000),other)
        b=self.body()
        with patch('memory_slice.transfer.verified_backup',side_effect=AssertionError('preview must not back up entire DB')):
            with self.importer._stage(b,self.importer.validate(b.package)) as (scratch,*_):
                self.assertEqual(scratch.store.list('integration',other),[])
                self.assertLess(scratch.store.path.stat().st_size,1000000)
            self.preview(b)

    def test_backup_allows_concurrent_write_to_other_space(self):
        other=self.target.add_space(SpaceInput(name='其他空间'))['id']
        b,_=self.preview(self.body())
        from memory_slice.transfer import verified_backup
        def backup(source,destination):
            if 'before-import' in str(destination):
                with sqlite3.connect(self.target.store.path,timeout=0.1) as writer:
                    writer.execute('INSERT INTO records VALUES(?,?,?,?,1)',('integration','parallel-write',other,json.dumps(dict(id='parallel-write',progress=1))))
            return verified_backup(source,destination)
        with patch('memory_slice.transfer.verified_backup',side_effect=backup):self.importer.commit(b)
        self.assertEqual(self.target.store.get('integration','parallel-write')['progress'],1)
