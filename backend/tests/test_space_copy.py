import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, ConversationInput, MessageInput, EventInput
from memory_slice.space_copy import SpaceCopier, CopyInput
from memory_slice.errors import MemoryError
from memory_slice.web import create_app
from memory_slice.store import Store
from memory_slice.integrations.routing import alias_id, ChatRouting


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.core=MemoryService(self.temp.name,seed=False)
        self.store=self.core.store
        self.a=self.core.add_space(SpaceInput(name='A'))['id'];self.b=self.core.add_space(SpaceInput(name='B'))['id']
        self.copier=SpaceCopier(self.core)

    def tearDown(self):self.temp.cleanup()

    def event(self,sid=None,**kw):
        return self.core.create_event(sid or self.a,EventInput(**(dict(title='面包',body='烤面包外面糊了里面没熟')|kw)))

    def perform(self,source=None,target=None,**kw):
        source=source or self.a;target=target or self.b
        plan=self.copier.preview(source,CopyInput(target_space_id=target))
        return self.copier.copy(source,CopyInput(target_space_id=target,preview_token=plan['preview_token'],**kw))

    def test_missing_reinforcement_copy_history_merge_and_reexport(self):
        from memory_slice.schemas import MergeInput
        from memory_slice.transfer import Transfers,ImportInput
        events=[self.event(title='第一件'),self.event(title='第二件')]
        for index,event in enumerate(events):
            event['reinforcements']=[{'message_id':f'missing_{index}','at':event['learned_at']}]
            self.store.put('event',event,self.a)
            # Save a historical snapshot which also contains the now-missing source.
            self.store.put('event',dict(event,body=event['body']+' 后续'),self.a)
        before=self.snapshot(self.a)
        result=self.perform()
        self.assertEqual(before,self.snapshot(self.a))
        ids=list(result['event_map'].values())
        for index,eid in enumerate(ids):
            copied=self.store.get('event',eid,self.b)
            self.assertIsNone(copied['reinforcements'][0]['message_id'])
            self.assertIn(copied['reinforcements'][0]['unavailable_message_id'],{'missing_0','missing_1'})
            histories=self.store.revisions(eid,self.b)
            refs=[r for v in histories for r in v['event'].get('reinforcements',[])]
            self.assertTrue(refs)
            self.assertTrue(all(r['message_id'] is None and r['unavailable_message_id'] for r in refs))
        self.assertEqual(self.store.list('message',self.b),[])
        # Repeating a copy does not duplicate reinforcement history.
        self.perform(conflict_policy='overwrite')
        self.core.merge(self.b,MergeInput(event_ids=ids,target_id=ids[0],reason='回归测试'))
        merged=self.store.get('event',ids[0],self.b)
        self.assertEqual({r['unavailable_message_id'] for r in merged['reinforcements']},{'missing_0','missing_1'})
        self.assertEqual(len(merged['reinforcements']),2)
        transfer=Transfers(self.core);package=transfer.export(self.b)
        body=ImportInput(package=package,operation_id='reinforcement-roundtrip-test',new_space_name='再次迁移')
        plan=transfer.preview(body)
        outcome=transfer.commit(body.model_copy(update={'preview_token':plan['preview_token']}))
        final=[e for e in self.store.list('event',outcome['space_id']) if e['status']!='archived'][0]
        self.assertEqual(len(final['reinforcements']),2)

    def test_reinforcement_merge_deduplicates_identity_not_null(self):
        from memory_slice.reinforcements import merge_reinforcements
        missing=lambda mid:dict(message_id=None,unavailable_message_id=mid,at='2026-01-01T00:00:00Z')
        rows=[missing('a'),missing('b'),missing('a')]
        self.assertEqual(len(merge_reinforcements(rows)),2)
        live=dict(message_id='a',at='2026-01-01T00:00:00Z')
        self.assertEqual(merge_reinforcements([live,missing('a')]),[live])
        self.assertEqual(merge_reinforcements([missing('a'),live]),[live])

    def snapshot(self,sid):
        with self.store.connect() as con:
            return (list(con.execute('SELECT * FROM records WHERE space_id=? ORDER BY kind,id',(sid,))),
                    list(con.execute('SELECT * FROM versions WHERE space_id=? ORDER BY id',(sid,))),
                    list(con.execute('SELECT * FROM copy_identity WHERE space_id=? ORDER BY kind,origin_id',(sid,))))

    def alias(self,sid,platform,uid,pid):
        self.store.put('astrbot_person_alias',dict(id=alias_id(sid,platform,uid),platform=platform,uid=uid,person_id=pid),sid)

    def test_full_copy_preserves_source_and_rewrites_graph(self):
        pid=self.core.persons(self.a)[0]['id']
        cid=self.core.add_conversation(self.a,ConversationInput(person_id=pid))['id']
        conv=self.store.get('conversation',cid);self.store.put('conversation',dict(conv,external_thread_id='host-thread',integration='codex'),self.a)
        user=self.core.post_message(self.a,cid,MessageInput(role='user',content='[[literal_chat_text]]',sender_id=pid,mention_ids=[pid]))
        answer=self.core.post_message(self.a,cid,MessageInput(role='assistant',content='下次再试',in_reply_to=user['id']))
        self.store.put('message',dict(user,processed=True,maintenance_offset=5),self.a)
        first=self.event(person_ids=[pid],source_ids=[user['id']])
        second=self.event(title='第二次',body='接着 [['+first['id']+']] 再试',source_ids=[answer['id']])
        first=self.core.freeze_event(self.a,first['id'],True)
        self.store.put('astrbot_scope',dict(id='route',space_id=self.a,conversation_id=cid),self.a)
        self.store.put('integration',dict(id='cursor',space_id=self.a,position=123),self.a)
        source=self.snapshot(self.a);cfg=self.core.config_for(self.b)
        result=self.perform()
        self.assertEqual(self.snapshot(self.a),source)
        self.assertEqual(len(self.core.spaces()),2)
        self.assertEqual(self.core.config_for(self.b),cfg)
        copied=self.store.get('event',result['event_map'][first['id']],self.b)
        self.assertNotEqual(copied['id'],first['id']);self.assertEqual(copied['origin_id'],first['id']);self.assertTrue(copied['frozen'])
        e2=self.store.get('event',result['event_map'][second['id']],self.b)
        self.assertIn('[['+copied['id']+']]',e2['body']);self.assertEqual(e2['links'][0]['target_id'],copied['id'])
        self.assertIn(copied['id'],self.store.search_events(self.b,'面包'))
        self.assertTrue(self.store.revisions(copied['id'],self.b))
        messages=self.store.list('message',self.b)
        cm=next(m for m in messages if m['role']=='user');ca=next(m for m in messages if m['role']=='assistant')
        self.assertEqual(cm['content'],'[[literal_chat_text]]');self.assertTrue(cm['processed']);self.assertEqual(cm['maintenance_offset'],5)
        self.assertEqual(ca['in_reply_to'],cm['id']);self.assertEqual(cm['mention_ids'],[copied['person_ids'][0]])
        self.assertEqual(copied['source_ids'],[cm['id']])
        self.assertNotIn('external_thread_id',self.store.list('conversation',self.b)[0])
        self.assertEqual(self.store.list('astrbot_scope',self.b),[]);self.assertEqual(self.store.list('integration',self.b),[])

    def test_repeat_skip_overwrite_and_independent_revisions(self):
        a=self.event();result=self.perform();bid=result['event_map'][a['id']]
        self.core.update_event(self.b,bid,EventInput(title='B 的修改',body='目标自己写的内容'))
        before=self.store.get('event',bid);history=self.store.revisions(bid,self.b)
        result=self.perform()
        self.assertEqual(result['counts']['skip'],1);self.assertEqual(self.store.get('event',bid),before)
        self.assertEqual(self.store.revisions(bid,self.b),history)
        for i in range(5):self.core.update_event(self.a,a['id'],EventInput(title='源更新',body='源内容 '+str(i)))
        result=self.perform(event_choices={a['id']:'overwrite'})
        after=self.store.get('event',bid)
        self.assertEqual(after['id'],bid);self.assertEqual(after['version'],before['version']+1)
        self.assertEqual(after['origin_id'],a['id']);self.assertEqual(after['body'],'源内容 4')
        self.assertTrue(any(v['event']['body']=='目标自己写的内容' for v in self.store.revisions(bid,self.b)))
        self.assertEqual(len(self.store.list('event',self.b)),1)
        count=len(self.store.revisions(bid,self.b))
        self.perform(conflict_policy='overwrite')
        self.assertEqual(len(self.store.revisions(bid,self.b)),count+1) # only local snapshot, source history deduped
        self.core.delete_event(self.b,bid)
        self.assertEqual(self.store.get('event',a['id'])['body'],'源内容 4')
        result=self.perform();self.assertNotEqual(result['event_map'][a['id']],bid)

    def test_split_copy_has_distinct_origins_and_mixed_conflict_choices(self):
        from memory_slice.schemas import SplitInput, SplitPart
        original=self.event(body='第一件。第二件。')
        copied=self.perform()['event_map'][original['id']]
        children=self.core.split(self.b,copied,SplitInput(reason='分开记录两件事',parts=[
            SplitPart(title='第一件',body='第一件。'), SplitPart(title='第二件',body='第二件。')]))['events']
        self.assertEqual(len({e['origin_id'] for e in children}),2)
        self.assertTrue(all(e['origin_id']==e['id'] for e in children))
        c=self.core.add_space(SpaceInput(name='C'))['id']
        result=self.perform(self.b,c)
        self.assertEqual(result['counts']['create'],3)
        self.core.update_event(self.b,children[0]['id'],EventInput(title='更新第一件',body='新的第一件。'))
        another=self.event(self.b,title='第三件',body='还有第三件。')
        result2=self.perform(self.b,c,event_choices={children[0]['id']:'overwrite'})
        self.assertEqual(result2['counts']['create'],1)
        self.assertEqual(result2['counts']['overwrite'],1)
        self.assertEqual(result2['counts']['skip'],2)
        self.assertEqual(self.store.get('event',result['event_map'][children[0]['id']])['body'],'新的第一件。')
        self.assertIn(another['id'],result2['event_map'])

    def test_transitive_copy_and_copy_back(self):
        a=self.event();first=self.perform();bid=first['event_map'][a['id']]
        c=self.core.add_space(SpaceInput(name='C'))['id']
        third=self.perform(self.b,c)['event_map'][bid]
        self.assertEqual(self.store.get('event',third)['origin_id'],a['id'])
        result=self.perform(self.a,c)
        self.assertEqual(result['event_map'][a['id']],third);self.assertEqual(result['counts']['skip'],1)
        self.assertEqual(self.perform(self.b,self.a)['event_map'][bid],a['id'])
        self.assertEqual(len(self.store.list('event',self.a)),1)

    def test_alias_identity_and_transitive_person_dedup(self):
        pa=self.core.persons(self.a)[0]['id'];pb=self.core.persons(self.b)[0]['id']
        self.alias(self.a,'simulation','test-user',pa);self.alias(self.b,'simulation','test-user',pb)
        a=self.event(person_ids=[pa]);self.perform()
        self.assertEqual(len(self.core.persons(self.b)),1)
        c=self.core.add_space(SpaceInput(name='C'))['id']
        self.perform(self.b,c);people=len(self.core.persons(c));self.perform(self.a,c)
        self.assertEqual(len(self.core.persons(c)),people)
        self.assertEqual(len(self.store.list('event',c)),1)
        self.assertEqual(ChatRouting(self.core).person(self.b,'simulation','test-user'),pb)
        self.assertEqual(self.store.get('event',a['id'])['person_ids'],[pa])

    def test_messages_dedup_and_local_changes_not_overwritten(self):
        cid=self.core.add_conversation(self.a,ConversationInput())['id']
        message=self.core.post_message(self.a,cid,MessageInput(role='user',content='一条消息'))
        self.perform();copied=self.store.list('message',self.b)[0]
        self.store.put('message',dict(copied,processed=True,content='',content_cleared_at='now'),self.b)
        self.perform(conflict_policy='overwrite')
        self.assertEqual(len(self.store.list('message',self.b)),1)
        self.assertEqual(self.store.list('message',self.b)[0]['content'],'')
        self.assertEqual(self.store.get('message',message['id'])['content'],'一条消息')

    def test_frozen_target_rejects_overwrite_atomically(self):
        a=self.event();bid=self.perform()['event_map'][a['id']]
        self.core.freeze_event(self.b,bid,True);before=self.snapshot(self.b)
        with self.assertRaisesRegex(MemoryError,'已冻结'):self.perform(conflict_policy='overwrite')
        self.assertEqual(self.snapshot(self.b),before)
        self.assertEqual(self.perform()['counts']['skip'],1)

    def test_pins_opt_in_and_overwrite_budget(self):
        a=self.event();self.store.put('astrbot_pins',dict(id=self.a,event_ids=[a['id']]),self.a)
        result=self.perform();bid=result['event_map'][a['id']]
        self.assertEqual(self.store.list('astrbot_pins',self.b),[])
        self.perform(pinned_event_ids=[a['id']]);self.assertEqual(self.store.get('astrbot_pins',self.b)['event_ids'],[bid])
        self.core.update_event(self.a,a['id'],EventInput(title='长事件',body='长'*2000))
        self.copier.pin_budget=1000;before=self.snapshot(self.b)
        with self.assertRaisesRegex(MemoryError,'字符上限'):self.perform(conflict_policy='overwrite')
        self.assertEqual(self.snapshot(self.b),before)
        self.perform(conflict_policy='overwrite',pinned_event_ids=[])
        self.assertEqual(self.store.get('astrbot_pins',self.b)['event_ids'],[])

    def test_stale_preview_rollback_and_lock_timeout(self):
        a=self.event();plan=self.copier.preview(self.a,CopyInput(target_space_id=self.b))
        self.event();before=self.snapshot(self.b)
        with self.assertRaisesRegex(MemoryError,'已变化'):
            self.copier.copy(self.a,CopyInput(target_space_id=self.b,preview_token=plan['preview_token']))
        self.assertEqual(self.snapshot(self.b),before)
        with patch.object(self.store,'_index_event',side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):self.perform()
        self.assertEqual(self.snapshot(self.b),before)
        body=CopyInput(target_space_id=self.b,preview_token=self.copier.preview(self.a,CopyInput(target_space_id=self.b))['preview_token'])
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            with self.assertRaises(MemoryError) as error:self.copier.copy(self.a,body)
            self.assertEqual(error.exception.status_code,503)
        self.assertEqual(self.snapshot(self.b),before)

    def test_compatible_vectors_only_and_active_jobs(self):
        a=self.event();emb=self.core.embeddings;chunk=next(emb.chunks([a]))
        self.store.put('embedding_vector',dict(id=chunk['id'],event_id=a['id'],hash=chunk['hash'],signature=emb.identity(emb.settings(self.b)),vector=[1.,0.]),self.a)
        self.perform();self.assertEqual(emb.status(self.b)['indexed_chunks'],1)
        self.store.put('job',dict(id='active-job',status='running'),self.a)
        with self.assertRaisesRegex(MemoryError,'整理任务'):self.perform()


class MigrationTests(unittest.TestCase):
    def test_legacy_backup_preservation_and_future_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp)
            with store.connect() as con:
                con.execute('DROP TABLE copy_identity');con.execute('PRAGMA user_version=0')
                legacy=dict(id='old-person',name='我',space_id='old-space')
                con.execute('INSERT INTO records VALUES(?,?,?,?,?)',('person','old-person','old-space',json.dumps(legacy),7))
            store=Store(temp)
            self.assertEqual(store.get('person','old-person'),dict(legacy,version=7))
            backups=list((Path(temp)/'backups').glob('*.sqlite3'));self.assertEqual(len(backups),1)
            with sqlite3.connect(backups[0]) as backup:self.assertEqual(backup.execute('PRAGMA user_version').fetchone()[0],0)
            Store(temp);self.assertEqual(len(list((Path(temp)/'backups').glob('*.sqlite3'))),1)
            with store.connect() as con:con.execute('PRAGMA user_version=999')
            with self.assertRaisesRegex(RuntimeError,'高于本程序'):Store(temp)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_copy_api_and_retired_move(self):
        with tempfile.TemporaryDirectory() as temp:
            core=MemoryService(temp,seed=False)
            a=core.add_space(SpaceInput(name='A'))['id'];b=core.add_space(SpaceInput(name='B'))['id']
            app=create_app(service=core)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                retired=await client.post(f'/api/v1/spaces/{a}/merge',json={'target_space_id':b})
                self.assertEqual(retired.status_code,410)
                response=await client.post(f'/api/v1/spaces/{a}/copy/preview',json={'target_space_id':b})
                self.assertEqual(response.status_code,200,response.text)
                response=await client.post(f'/api/v1/spaces/{a}/copy',json={'target_space_id':b,'preview_token':response.json()['preview_token']})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(len((await client.get('/api/v1/spaces')).json()),2)
            await core.close()
