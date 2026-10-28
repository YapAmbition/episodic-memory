import asyncio
import copy
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from memory_slice.engine import build_context, memory_strength
from app.main import create_app
from memory_slice.schemas import MemoryConfig


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def event(eid="e1", salience=.8, age=30, body="生日那天第一次烤面包，叫作陨石一号。", links=None):
    return {"id": eid, "space_id": "space", "title": body[:30], "body": body,
            "occurred_at": (NOW-timedelta(days=age)).isoformat(), "learned_at": (NOW-timedelta(days=age)).isoformat(),
            "person_ids": ["person"], "salience": salience, "salience_reason": "共同玩笑",
            "anchors": {"time": ["生日"], "details": ["陨石一号"], "scene": [], "structure": [], "triggers": []},
            "links": links or [], "source_ids": [], "reinforcements": [], "status": "closed"}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.config = MemoryConfig().model_dump()

    def test_elapsed_time_fades_but_salience_changes_retention(self):
        e = event(salience=.4)
        self.assertGreater(memory_strength(e,NOW,self.config)["strength"], memory_strength(e,NOW+timedelta(days=90),self.config)["strength"])
        self.assertGreater(memory_strength(event(salience=.95,age=180),NOW,self.config)["strength"],memory_strength(event(salience=.1,age=1),NOW,self.config)["strength"])

    def test_retention_uses_learning_time_not_occurrence(self):
        e=event(age=3650)
        e["learned_at"]=NOW.isoformat()
        self.assertEqual(memory_strength(e,NOW,self.config)["age_days"],0)
        self.assertGreater(memory_strength(e,NOW,self.config)["strength"],.8)

    def test_future_learning_cannot_leak_into_context(self):
        e=event(age=-3)
        result=build_context([e],"person","生日",NOW,self.config)
        self.assertEqual(result["selected"],[])

    def test_reads_and_graph_expansion_do_not_reinforce(self):
        e=event()
        original=copy.deepcopy(e)
        for _ in range(5):
            build_context([e],"person","生日",NOW,self.config)
        self.assertEqual(e,original)

    def test_strong_cue_revives_old_event(self):
        e=event(salience=.1,age=1500)
        absent=build_context([e],"person","周末想散步",NOW,self.config)
        recalled=build_context([e],"person","陨石一号",NOW,self.config)
        self.assertEqual(absent["selected"],[])
        self.assertEqual(recalled["selected"][0]["event_id"],"e1")

    def test_graph_cycles_terminate_and_dormant_neighbor_does_not_bypass_decay(self):
        a=event("a",age=0,links=[{"target_id":"b","strength":1,"relation":"续篇"}])
        b=event("b",salience=.01,age=2000,body="普通买菜记录",links=[{"target_id":"a","strength":1,"relation":"引用"}])
        b["anchors"]={"time":[],"details":[],"scene":[],"structure":[],"triggers":[]}
        result=build_context([a,b],"person","陨石一号",NOW,self.config)
        ids=[x["event_id"] for x in result["selected"]]
        self.assertEqual(ids,["a"])

    def test_budget_omits_whole_event_instead_of_truncating(self):
        e=event(body="陨石一号"*1000)
        cfg=dict(self.config,context_char_budget=1000)
        result=build_context([e],"person","陨石一号",NOW,cfg)
        self.assertEqual(result["context"],"")
        self.assertEqual(result["omitted"][0]["event_id"],"e1")

    def test_dated_future_plan_can_be_known_today(self):
        e=event(age=0)
        e["occurred_at"]=(NOW+timedelta(days=20)).isoformat()
        e["status"]="planned"
        self.assertEqual(build_context([e],"person","生日",NOW,self.config)["selected"][0]["event_id"],"e1")

    def test_event_strength_changes_only_after_real_exposure_time(self):
        e=event(age=100)
        before=memory_strength(e,NOW,self.config)["strength"]
        e["reinforcements"]=[{"message_id":"m","at":(NOW+timedelta(days=1)).isoformat()}]
        self.assertEqual(before,memory_strength(e,NOW,self.config)["strength"])
        self.assertGreater(memory_strength(e,NOW+timedelta(days=1),self.config)["strength"],before)

    def test_weights_are_normalized_and_all_zero_is_valid(self):
        cfg=dict(self.config,cue_weight=1,retention_weight=1,person_weight=1)
        result=build_context([event(age=0)],"person","生日",NOW,cfg)
        self.assertLessEqual(result['candidates'][0]['score'],1)
        cfg.update(cue_weight=0,retention_weight=0,person_weight=0)
        self.assertEqual(build_context([event(age=0)],"person","生日",NOW,cfg)['selected'],[])


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(self.temp.name,seed=False)
        self.client_context=TestClient(self.app)
        self.client=self.client_context.__enter__()
        self.space=self.client.post('/api/v1/spaces',json={"name":"测试空间"}).json()["id"]
        self.prefix=f'/api/v1/spaces/{self.space}'
        self.person=self.client.get(self.prefix+'/persons').json()[0]["id"]
        self.cid=self.client.post(self.prefix+'/conversations',json={"person_id":self.person}).json()["id"]

    def tearDown(self):
        self.client_context.__exit__(None,None,None)
        self.temp.cleanup()

    def create_event(self, **changes):
        body={"title":"第一次烤面包", "body":"用户说这是生日那天的陨石一号。", "person_ids":[self.person], "anchors":{"time":["生日"],"details":["陨石一号"]},"salience":.9,"learned_at":(NOW-timedelta(days=10)).isoformat()}
        body.update(changes)
        response=self.client.post(self.prefix+'/events',json=body)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def post_message(self,text="今天又想烤面包了",client_id=None):
        return self.client.post(self.prefix+f'/conversations/{self.cid}/messages',json={"role":"user","content":text,"client_id":client_id,"at":NOW.isoformat()})

    def await_job(self,jid):
        for _ in range(100):
            job=self.client.get(self.prefix+f'/jobs/{jid}').json()
            if job["status"] not in ('queued','running'):
                return job
            time.sleep(.01)
        self.fail('job did not finish')

    def test_scope_isolation_and_bad_links(self):
        other=self.client.post('/api/v1/spaces',json={"name":"另一个空间"}).json()["id"]
        e=self.create_event()
        self.assertEqual(self.client.get(f'/api/v1/spaces/{other}/events/{e["id"]}').status_code,404)
        bad=self.client.post(self.prefix+'/events',json={"title":"坏引用","body":"正文","links":[{"target_id":"missing"}]})
        self.assertEqual(bad.status_code,404)
        self.assertEqual(len(self.client.get(self.prefix+'/events').json()),1)

    def test_record_is_idempotent_and_rejects_conflicting_reuse(self):
        first=self.post_message(client_id='stable').json()
        second=self.post_message(client_id='stable').json()
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(self.post_message('不同内容',client_id='stable').status_code,409)

    def test_event_version_conflict_and_full_body(self):
        e=self.create_event()
        body={k:v for k,v in e.items() if k in ('title','body','person_ids','anchors','salience','learned_at')}
        body.update(body='更新后的完整正文',expected_version=1)
        self.assertEqual(self.client.put(self.prefix+f'/events/{e["id"]}',json=body).status_code,200)
        self.assertEqual(self.client.put(self.prefix+f'/events/{e["id"]}',json=body).status_code,409)
        self.assertEqual(len(self.client.get(self.prefix+f'/events/{e["id"]}/versions').json()),1)

    def test_local_maintenance_archives_sources_and_second_run_is_noop(self):
        self.post_message()
        jid=self.client.post(self.prefix+'/maintenance',json={"conversation_id":self.cid}).json()['id']
        job=self.await_job(jid)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['method'],'local_archive')
        events=self.client.get(self.prefix+'/events').json()
        self.assertIn('今天又想烤面包了',events[0]['body'])
        jid2=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
        self.assertEqual(self.await_job(jid2)['message_count'],0)
        self.assertEqual(len(self.client.get(self.prefix+'/events').json()),1)

    def test_bad_model_output_does_not_advance_cursor(self):
        self.post_message()
        self.client.put(self.prefix+'/model',json={"mode":"llm","model":"test-model","api_key":"test-secret"})
        async def invalid(*args,**kwargs):
            return '{"events":[{"title":"编造事件","body":"没有来源","source_ids":["missing"]}]}'
        with patch('memory_slice.model.complete',invalid):
            jid=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
            self.assertEqual(self.await_job(jid)['status'],'failed')
        self.assertEqual(self.client.get(self.prefix+'/stats').json()['pending_messages'],1)
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])
        self.assertNotIn('test-secret',self.client.get(self.prefix+'/model').text)

    def test_valid_model_maintenance_preserves_source_and_full_body(self):
        import json
        message=self.post_message('第一次烤面包失败，我给它取名陨石一号。').json()
        self.client.put(self.prefix+'/model',json={"mode":"llm","model":"test-model"})
        async def valid(*args,**kwargs):
            return json.dumps({"events":[{"title":"陨石一号","body":"用户第一次烤面包失败，自己取名「陨石一号」。这是用户原话。","person_ids":[self.person],"source_ids":[message['id']],"anchors":{"details":["陨石一号"]},"salience":.8}]},ensure_ascii=False)
        with patch('memory_slice.model.complete',valid):
            jid=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
            job=self.await_job(jid)
            self.assertEqual(job['status'],'completed',job)
        result=self.client.get(self.prefix+'/events').json()[0]
        self.assertIn('这是用户原话',result['body'])
        self.assertEqual(result['source_ids'],[message['id']])
        self.assertEqual(result['learned_at'],message['at'])

    def test_multi_event_transaction_rolls_back_on_invalid_reference(self):
        import json
        message=self.post_message().json()
        self.client.put(self.prefix+'/model',json={"mode":"llm","model":"test-model"})
        async def partly_bad(*args,**kwargs):
            return json.dumps({"events":[{"title":"有效事件","body":"保留消息","source_ids":[message['id']]},{"title":"无效事件","body":"另一个片段","source_ids":[message['id']],"links":[{"target_id":"missing"}]}]})
        with patch('memory_slice.model.complete',partly_bad):
            jid=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
            self.assertEqual(self.await_job(jid)['status'],'failed')
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])
        self.assertEqual(self.client.get(self.prefix+'/stats').json()['pending_messages'],1)

    def test_chat_retry_returns_same_message_and_reply(self):
        path=self.prefix+f'/conversations/{self.cid}/chat'
        request={"content":"今天想起陨石一号","at":NOW.isoformat(),"client_id":"retry-chat"}
        one=self.client.post(path,json=request)
        two=self.client.post(path,json=request)
        self.assertEqual(one.status_code,200,one.text)
        self.assertEqual(one.json()['user']['id'],two.json()['user']['id'])
        self.assertEqual(one.json()['assistant']['id'],two.json()['assistant']['id'])
        self.assertEqual(len(self.client.get(self.prefix+f'/conversations/{self.cid}/messages').json()),2)

    def test_delete_cleans_links_and_recall_snapshots(self):
        a=self.create_event()
        b=self.create_event(title='后续',links=[{"target_id":a['id']}],body='前次经历 [['+a['id']+']]')
        self.client.post(self.prefix+'/context',json={"person_id":self.person,"query":"陨石一号","at":NOW.isoformat()})
        self.assertTrue(self.client.get(self.prefix+'/recalls').json())
        self.assertEqual(self.client.delete(self.prefix+f'/events/{a["id"]}').status_code,200)
        updated=self.client.get(self.prefix+f'/events/{b["id"]}').json()
        self.assertEqual(updated['links'],[])
        self.assertNotIn(a['id'],updated['body'])
        self.assertFalse(self.client.get(self.prefix+'/recalls').json())

    def test_context_is_read_only_for_strength(self):
        e=self.create_event()
        for _ in range(3):
            self.assertEqual(self.client.post(self.prefix+'/context',json={"person_id":self.person,"query":"生日","at":NOW.isoformat()}).status_code,200)
        updated=self.client.get(self.prefix+f'/events/{e["id"]}').json()
        self.assertEqual(updated['reinforcements'],[])
        self.assertEqual(updated['version'],e['version'])

    def test_reinforcement_requires_user_message_and_deduplicates(self):
        e=self.create_event()
        m=self.post_message().json()
        path=self.prefix+f'/events/{e["id"]}/reinforce'
        for _ in range(2):
            self.assertEqual(self.client.post(path,json={"message_id":m['id']}).status_code,200)
        updated=self.client.get(self.prefix+f'/events/{e["id"]}').json()
        self.assertEqual(len(updated['reinforcements']),1)

    def test_api_token_is_checked(self):
        with patch.dict(os.environ,{"MEMORY_API_TOKEN":"local-test-token"}):
            self.assertEqual(self.client.get('/api/v1/spaces').status_code,401)
            self.assertEqual(self.client.get('/api/v1/spaces',headers={'Authorization':'Bearer local-test-token'}).status_code,200)

    def test_invalid_config_is_rejected(self):
        config=self.client.get(self.prefix+'/config').json()
        config['half_life_days']=0
        self.assertEqual(self.client.put(self.prefix+'/config',json=config).status_code,422)
        config=self.client.get(self.prefix+'/config').json()
        config['timezone']='Invalid/MadeUp'
        self.assertEqual(self.client.put(self.prefix+'/config',json=config).status_code,422)

    def test_export_contains_full_markdown_and_no_api_key(self):
        import io,zipfile
        self.create_event()
        self.client.put(self.prefix+'/model',json={"api_key":"never-export-this"})
        data=self.client.get(self.prefix+'/export').content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertTrue(any(name.endswith('.md') for name in z.namelist()))
            self.assertNotIn('never-export-this',''.join(z.read(name).decode() for name in z.namelist()))


class SchedulerTests(unittest.TestCase):
    def test_interval_job_runs_and_restart_does_not_repeat_slot(self):
        from memory_slice.seed import create_space
        from memory_slice.store import iso, new_id, utcnow
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,seed=False)
            store=app.state.store
            space=create_space(store,'自动整理测试')
            sid=space['id']
            space['created_at']=iso(utcnow()-timedelta(minutes=3))
            store.put('space',space,sid)
            config=store.get('config',sid,sid)
            config.update(schedule_enabled=True,schedule_interval_minutes=1)
            store.put('config',config,sid)
            person=store.put('person',{'id':new_id('person'),'space_id':sid,'name':'测试者','relation':'测试'},sid)
            cid=new_id('conv')
            store.put('conversation',{'id':cid,'space_id':sid,'person_id':person['id'],'title':'测试','created_at':iso()},sid)
            store.put('message',{'id':new_id('msg'),'space_id':sid,'conversation_id':cid,'person_id':person['id'],'role':'user','content':'这是一段待整理的真实测试消息。','at':iso(),'processed':False,'fixture':False,'client_id':None},sid)
            with TestClient(app) as client:
                for _ in range(100):
                    jobs=client.get(f'/api/v1/spaces/{sid}/jobs').json()
                    if jobs and jobs[0]['status']=='completed':
                        break
                    time.sleep(.01)
                self.assertEqual(len(jobs),1)
                self.assertEqual(jobs[0]['trigger'],'scheduled')
                self.assertEqual(jobs[0]['message_count'],1)
            with TestClient(app) as client:
                self.assertEqual(len(client.get(f'/api/v1/spaces/{sid}/jobs').json()),1)


if __name__ == '__main__':
    unittest.main()
