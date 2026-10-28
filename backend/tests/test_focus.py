import json
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_memory as fixtures
from memory_slice.focus import cleanup_buffer, named_focus, person_score, select_messages
from memory_slice.engine import build_context
from memory_slice.schemas import MemoryConfig
from memory_slice.store import iso, utcnow

class FocusTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    await_job = fixtures.APITests.await_job
    create_event = fixtures.APITests.create_event

    def configure(self, focus=True):
        cfg = self.client.get(self.prefix+'/config').json()
        cfg['focus_person_id'] = self.person if focus else None
        r = self.client.put(self.prefix+'/config', json=cfg)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def group(self):
        self.client.put(self.prefix+'/persons/'+self.person, json={'name':'张三','platform':'chat','external_uid':'100'})
        self.other = self.client.post(self.prefix+'/persons', json={'name':'李四','platform':'chat','external_uid':'101'}).json()['id']
        self.cid = self.client.post(self.prefix+'/conversations', json={'kind':'group','title':'测试群'}).json()['id']

    def message(self, text, **kwargs):
        return self.client.post(self.prefix+'/conversations/'+self.cid+'/messages', json=dict(role='user',content=text,**kwargs))

    def run_plan(self, reply):
        self.client.put(self.prefix+'/model',json={'mode':'llm','model':'fixture','base_url':'http://fixture/v1'})
        with patch('memory_slice.model.complete',reply):
            job=self.client.post(self.prefix+'/maintenance',json={'conversation_id':self.cid}).json()
            return self.await_job(job['id'])

    def test_identity_unique_uid_but_duplicate_names_allowed(self):
        self.group()
        self.assertEqual(self.client.post(self.prefix+'/persons',json={'name':'同名','platform':'chat','external_uid':'100'}).status_code,409)
        self.assertEqual(self.client.post(self.prefix+'/persons',json={'name':'张三','platform':'other','external_uid':'100'}).status_code,201)
        self.assertEqual(self.client.post(self.prefix+'/persons',json={'name':'孤立UID','external_uid':'100'}).status_code,422)

    def test_group_sender_reply_and_idempotency_are_verified(self):
        self.group()
        self.assertEqual(self.message('没有身份').status_code,422)
        first=self.message('张三说的话',sender_id=self.person).json()
        second=self.message('李四回复',sender_id=self.other,in_reply_to=first['id'],client_id='external-2').json()
        self.assertEqual(second['person_id'],self.other)
        self.assertEqual(second['in_reply_to'],first['id'])
        self.assertEqual(self.message('李四回复',sender_id=self.person,in_reply_to=first['id'],client_id='external-2').status_code,409)
        separate=self.client.post(self.prefix+'/conversations',json={'person_id':self.person}).json()['id']
        r=self.client.post(self.prefix+'/conversations/'+separate+'/messages',json={'role':'user','content':'跨会话回复','in_reply_to':first['id']})
        self.assertEqual(r.status_code,422)

    def test_unconfigured_recall_needs_no_person_and_has_no_person_lane(self):
        self.create_event()
        result=self.client.post(self.prefix+'/context',json={'query':'陨石一号','trace':True}).json()
        self.assertFalse(result['person_context']['enabled'])
        self.assertNotIn('person_salience',result['retrieval']['lane_candidates'])
        event=fixtures.event(age=0)
        cfg=MemoryConfig().model_dump()
        a=build_context([event],'person','生日',fixtures.NOW,dict(cfg,person_weight=1))
        b=build_context([event],None,'生日',fixtures.NOW,dict(cfg,person_weight=0))
        self.assertEqual(a['candidates'][0]['score'],b['candidates'][0]['score'])

    def test_relationship_pair_scores_above_merely_involving_focus(self):
        cfg={'focus_person_id':'a','_interaction_ids':['b']}
        self.assertGreater(person_score({'person_ids':['a','b']},cfg,'a'), person_score({'person_ids':['a','c']},cfg,'a'))
        self.assertEqual(person_score({'person_ids':['a','b']},{},'a'),0)

    def test_ambiguous_names_do_not_anchor_but_uid_mentions_do(self):
        self.group();cfg=self.configure()
        self.client.post(self.prefix+'/persons',json={'name':'张三'})
        m=self.message('张三昨天烤面包了',sender_id=self.other).json()
        selected,_=select_messages(self.app.state.store,self.space,[m],cfg)
        self.assertEqual(selected,[])
        m['mention_ids']=[self.person]
        self.assertEqual(select_messages(self.app.state.store,self.space,[m],cfg)[0][0]['focus_reason'],'mention')

    def test_reply_pronoun_and_nearby_context_are_candidates(self):
        self.group();cfg=self.configure()
        a=self.message('我昨天烤面包失败了',sender_id=self.person).json()
        b=self.message('是你把温度调高了',sender_id=self.other,in_reply_to=a['id']).json()
        selected,summary=select_messages(self.app.state.store,self.space,[a,b],cfg)
        self.assertEqual([m['focus_reason'] for m in selected],['speaker','reply'])
        self.assertEqual(summary['anchors'],2)

    def test_no_model_defers_instead_of_archiving_unverified_events(self):
        self.group();self.configure()
        self.message('昨天李四自己去旅游了',sender_id=self.person)
        job=self.await_job(self.client.post(self.prefix+'/maintenance',json={}).json()['id'])
        self.assertEqual(job['status'],'deferred')
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])

    def test_unrelated_group_never_calls_model(self):
        self.group();self.configure()
        self.message('李四今天自己去旅游',sender_id=self.other)
        async def forbidden(*a,**kw): self.fail('unrelated input called model')
        job=self.run_plan(forbidden)
        self.assertEqual(job['status'],'completed')
        self.assertEqual(job['focus_summary']['candidates'],0)

    def test_reported_event_is_recorded_with_evidence_in_one_call(self):
        self.group();self.configure()
        source=self.message('昨天我和张三烤面包失败了',sender_id=self.other).json()
        calls=[]
        async def reply(settings,key,request,**kw):
            data=json.loads(request[1]['content']);calls.append(data)
            self.assertIn(self.person,[p['id'] for p in data['persons']])
            return json.dumps({'events':[{'title':'一起烤面包','body':'李四转述：他和张三烤面包失败，尚未由张三确认。','person_ids':[self.person,self.other],'source_ids':[source['id']], 'focus_evidence':{'message_id':source['id'],'quote':'我和张三烤面包失败了'}}]})
        job=self.run_plan(reply)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(len(calls),1)
        self.assertEqual(self.app.state.store.get('message',source['id'],self.space)['retention'],'permanent')
        self.assertEqual(job['focus_summary']['retained'],1)

    def test_unrelated_or_fabricated_evidence_rejected_atomically(self):
        self.group();self.configure()
        source=self.message('张三昨天下雨没出门',sender_id=self.other).json()
        async def reply(*a,**kw):
            return json.dumps({'events':[{'title':'编造关联','body':'不成立','person_ids':[self.person],'source_ids':[source['id']],'focus_evidence':{'message_id':source['id'],'quote':'张三去旅游'}}]})
        job=self.run_plan(reply)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])
        self.assertFalse(self.app.state.store.get('message',source['id'],self.space)['processed'])

    def test_buffer_cleanup_keeps_legacy_sources_and_removes_expired_ignored(self):
        self.group()
        legacy=self.message('旧原文永久保留',sender_id=self.other).json()
        cfg=self.configure()
        source=self.message('张三去烤面包',sender_id=self.other).json()
        ignored=self.message('无关消息',sender_id=self.other).json()
        self.create_event(source_ids=[source['id']])
        for m in (source,ignored):
            m.update(expires_at=iso(utcnow()-timedelta(hours=1)),processed=True,maintenance_offset=len(m['content']))
            self.app.state.store.put('message',m,self.space)
        self.assertEqual(cleanup_buffer(self.app.state.store,self.space,cfg),1)
        ids={m['id'] for m in self.app.state.store.list('message',self.space)}
        self.assertIn(legacy['id'],ids);self.assertIn(source['id'],ids);self.assertNotIn(ignored['id'],ids)

    def test_focus_config_rejects_foreign_person(self):
        cfg=self.client.get(self.prefix+'/config').json();cfg['focus_person_id']='person_unknown'
        self.assertEqual(self.client.put(self.prefix+'/config',json=cfg).status_code,404)

    def test_group_user_reply_is_not_mistaken_for_cached_assistant(self):
        self.group()
        first=self.message('我昨天烤面包失败了',sender_id=self.person,client_id='retry-chat').json()
        self.message('我可以帮你',sender_id=self.other,in_reply_to=first['id'])
        result=self.client.post(self.prefix+'/conversations/'+self.cid+'/chat',json={
            'content':first['content'],'sender_id':self.person,'client_id':'retry-chat','use_memory':False})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['assistant']['role'],'assistant')

    def test_reply_to_processed_message_includes_readonly_original(self):
        self.group();self.configure()
        parent=self.message('我昨天烤面包失败了',sender_id=self.person).json()
        parent.update(processed=True)
        self.app.state.store.put('message',parent,self.space)
        source=self.message('是你把温度调高了',sender_id=self.other,in_reply_to=parent['id']).json()
        async def reply(settings,key,request,**kw):
            data=json.loads(request[1]['content'])
            self.assertEqual(len(data['new_messages']),1)
            context=data['new_messages'][0]['reply_context']
            self.assertEqual(context['content'],parent['content'])
            self.assertTrue(context['read_only'])
            return json.dumps({'ignored_messages':[{'message_id':source['id'],'reason':'测试忽略'}]})
        job=self.run_plan(reply)
        self.assertEqual(job['status'],'completed',job)

    def test_focus_batches_continue_across_conversations(self):
        self.group();self.configure()
        self.message('我今天烤面包了',sender_id=self.person)
        self.cid=self.client.post(self.prefix+'/conversations',json={'kind':'group'}).json()['id']
        self.message('我今天做蛋糕了',sender_id=self.person)
        self.client.put(self.prefix+'/model',json={'mode':'llm','model':'fixture','base_url':'http://fixture/v1'})
        calls=[]
        async def reply(settings,key,request,**kw):
            data=json.loads(request[1]['content']);calls.append(data)
            return json.dumps({'ignored_messages':[{'message_id':m['id'],'reason':'测试忽略'} for m in data['new_messages']]})
        with patch('memory_slice.model.complete',reply):
            job=self.await_job(self.client.post(self.prefix+'/maintenance',json={}).json()['id'])
            self.assertEqual(job['status'],'completed',job)
            child=self.await_job(job['continuation_job_id'])
            self.assertEqual(child['status'],'completed',child)
        self.assertEqual(len(calls),2)
