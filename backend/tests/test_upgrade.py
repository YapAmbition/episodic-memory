import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import test_memory as fixtures
from app.main import create_app
from memory_slice.model import complete
from memory_slice.schemas import MemoryConfig
from memory_slice.store import Store, iso, new_id, utcnow


class UpgradeTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    create_event = fixtures.APITests.create_event
    post_message = fixtures.APITests.post_message
    await_job = fixtures.APITests.await_job

    def model_mode(self):
        self.client.put(self.prefix + '/model', json={'mode':'llm', 'model':'test', 'max_retries':0})

    def test_concurrent_record_retries_create_one_message(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses=list(pool.map(lambda _: self.post_message(client_id='concurrent-client-id'),range(12)))
        self.assertTrue(all(response.status_code==201 for response in responses))
        self.assertEqual(len({response.json()['id'] for response in responses}),1)

    def run_plan(self, plan):
        async def reply(*args, **kwargs):
            return json.dumps(plan, ensure_ascii=False)
        self.model_mode()
        with patch('memory_slice.model.complete', reply):
            jid = self.client.post(self.prefix + '/maintenance', json={}).json()['id']
            return self.await_job(jid)

    def test_automatic_update_keeps_details_sources_and_immutable_fragments(self):
        old_message = self.post_message('第一次外焦内生，但起名陨石一号。').json()
        old = self.create_event(body='原始细节：外焦内生；双方说烤箱像航天设备。', source_ids=[old_message['id']])
        self.app.state.store.put('message', dict(old_message, processed=True), self.space)
        new_message = self.post_message('第一次烤面包的后续：第二次降低了温度，这次能吃了。').json()
        job = self.run_plan({'events':[{'id':old['id'], 'title':old['title'], 'body':'降低温度后能吃了。', 'source_ids':[new_message['id']], 'person_ids':[self.person]}]})
        self.assertEqual(job['status'],'completed',job)
        event = self.client.get(self.prefix + '/events/' + old['id']).json()
        self.assertIn('烤箱像航天设备',event['body'])
        self.assertIn('降低温度后能吃了',event['body'])
        self.assertEqual(set(event['source_ids']),{old_message['id'],new_message['id']})
        fragments = self.client.get(self.prefix + '/events/' + old['id'] + '/fragments').json()
        self.assertEqual(fragments[0]['content'],old['body'])
        self.assertEqual(fragments[-1]['content'],'降低温度后能吃了。')

    def test_corrections_append_explicitly_instead_of_erasing_history(self):
        old = self.create_event(body='当时以为是温度过高。')
        message = self.post_message('第一次烤面包的更正，是时间太长。').json()
        job = self.run_plan({'events':[{'id':old['id'],'title':old['title'],'body':'更正：烤制时间太长。','change_type':'correction','source_ids':[message['id']]}]})
        self.assertEqual(job['status'],'completed',job)
        current = self.client.get(self.prefix + '/events/' + old['id']).json()
        self.assertIn('当时以为是温度过高',current['body'])
        self.assertIn('后述更正优先',current['body'])

    def test_same_batch_new_events_can_link_by_local_alias(self):
        message = self.post_message().json()
        job = self.run_plan({'events':[
            {'local_id':'new_first','title':'第一件事','body':'第一次尝试','source_ids':[message['id']]},
            {'local_id':'new_second','title':'第二件事','body':'后续 [[new_first]]','links':[{'target_id':'new_first'}],'source_ids':[message['id']]}
        ]})
        self.assertEqual(job['status'],'completed',job)
        events = self.client.get(self.prefix+'/events').json()
        first = next(e for e in events if e['title']=='第一件事')
        second = next(e for e in events if e['title']=='第二件事')
        self.assertEqual(second['links'][0]['target_id'],first['id'])
        self.assertNotIn('[[new_first]]',second['body'])

    def test_merge_preserves_bodies_archives_alias_and_redirects_references(self):
        first = self.create_event(body='原始细节甲。')
        second = self.create_event(body='原始细节乙。')
        linked = self.create_event(body='回看 [['+second['id']+']]',links=[{'target_id':second['id']}])
        response = self.client.post(self.prefix+'/events/merge',json={'event_ids':[first['id'],second['id']],'target_id':first['id'],'reason':'同一事情的重复记录'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('原始细节甲',response.json()['event']['body'])
        self.assertIn('原始细节乙',response.json()['event']['body'])
        self.assertEqual(self.client.get(self.prefix+'/events/'+second['id']).json()['status'],'archived')
        self.assertEqual(self.client.get(self.prefix+'/events/'+linked['id']).json()['links'][0]['target_id'],first['id'])

    def test_split_requires_lossless_partition_and_updates_links(self):
        original = self.create_event(body='第一件事。第二件事。')
        linked = self.create_event(body='来源 [['+original['id']+']]',links=[{'target_id':original['id']}])
        path = self.prefix+'/events/'+original['id']+'/split'
        bad = self.client.post(path,json={'reason':'拆开','parts':[{'title':'甲','body':'第一件事。'},{'title':'乙','body':'遗漏内容。'}]})
        self.assertEqual(bad.status_code,422)
        self.assertEqual(self.client.get(self.prefix+'/events/'+original['id']).json()['status'],'closed')
        good = self.client.post(path,json={'reason':'两件独立的事','parts':[{'title':'甲','body':'第一件事。'},{'title':'乙','body':'第二件事。'}]})
        self.assertEqual(good.status_code,200,good.text)
        self.assertEqual(len(self.client.get(self.prefix+'/events/'+linked['id']).json()['links']),2)
        self.assertEqual(self.client.get(self.prefix+'/events/'+original['id']).json()['body'],original['body'])

    def test_merge_is_transactional_and_space_scoped(self):
        old = self.create_event()
        response = self.client.post(self.prefix+'/events/merge',json={'event_ids':[old['id'],'missing'],'target_id':old['id'],'reason':'测试'})
        self.assertEqual(response.status_code,404)
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['version'],old['version'])

    def test_auto_remention_validates_quote_and_deduplicates(self):
        old = self.create_event()
        message = self.post_message('又想起陨石一号了。').json()
        job = self.run_plan({'reinforcements':[{'event_id':old['id'],'message_id':message['id'],'quote':'陨石一号','reason':'用户明确重提旧梗','confidence':.95}]})
        self.assertEqual(job['status'],'completed',job)
        current = self.client.get(self.prefix+'/events/'+old['id']).json()
        self.assertEqual(len(current['reinforcements']),1)
        self.assertEqual(current['reinforcements'][0]['at'],message['at'])
        self.assertEqual(self.run_plan({'events':[]})['message_count'],0)

    def test_fake_remention_rolls_back_and_does_not_process_source(self):
        old = self.create_event()
        message = self.post_message('今天下雨。').json()
        job = self.run_plan({'reinforcements':[{'event_id':old['id'],'message_id':message['id'],'quote':'陨石一号','reason':'没有依据','confidence':.95}]})
        self.assertEqual(job['status'],'failed',job)
        self.assertEqual(self.client.get(self.prefix+'/stats').json()['pending_messages'],1)
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['reinforcements'],[])

    def test_full_text_index_includes_tail_and_updates_and_deletes(self):
        old = self.create_event(body='普通记录。'*500+'末尾特别细节蓝鲸钥匙。',anchors={})
        self.assertIn(old['id'],self.app.state.store.search_events(self.space,'蓝鲸钥匙'))
        body = {'title':old['title'],'body':'改为松鼠书签。','person_ids':[self.person]}
        self.client.put(self.prefix+'/events/'+old['id'],json=body)
        self.assertNotIn(old['id'],self.app.state.store.search_events(self.space,'蓝鲸钥匙'))
        self.assertIn(old['id'],self.app.state.store.search_events(self.space,'松鼠书签'))
        self.client.delete(self.prefix+'/events/'+old['id'])
        self.assertNotIn(old['id'],self.app.state.store.search_events(self.space,'松鼠书签'))

    def test_removed_llm_recall_rejected_without_provider_calls(self):
        self.model_mode()
        with patch('memory_slice.model.complete', side_effect=AssertionError('unexpected call')):
            response=self.client.post(self.prefix+'/context',json={'query':'旧事','retrieval_mode':'llm'})
        self.assertEqual(response.status_code,422)

    def test_monitoring_exports_aggregates_and_normalized_routes(self):
        old=self.create_event()
        self.client.get(self.prefix+'/events/'+old['id'])
        self.client.post(self.prefix+'/context',json={'person_id':self.person,'query':'陨石一号','at':fixtures.NOW.isoformat()})
        text=self.client.get('/metrics').text
        self.assertIn('memory_http_requests_total',text)
        self.assertIn('/api/v1/spaces/{sid}/events/{eid}',text)
        self.assertNotIn(old['id'],text)
        self.assertNotIn('陨石一号',text)
        overview=self.client.get('/api/v1/monitoring').json()
        self.assertEqual(overview['storage']['records']['event'],1)
        self.assertTrue(overview['storage']['database_bytes']>0)
        self.assertTrue(any(m['name']=='memory_recalls' for m in overview['metrics']))

    def test_metrics_token_is_separate_and_json_uses_api_token(self):
        with patch.dict(os.environ,{'MEMORY_API_TOKEN':'api-secret','MEMORY_METRICS_TOKEN':'metrics-secret'}):
            self.assertEqual(self.client.get('/metrics').status_code,401)
            self.assertEqual(self.client.get('/metrics',headers={'Authorization':'Bearer api-secret'}).status_code,401)
            self.assertEqual(self.client.get('/metrics',headers={'Authorization':'Bearer metrics-secret'}).status_code,200)
            self.assertEqual(self.client.get('/api/v1/monitoring',headers={'Authorization':'Bearer metrics-secret'}).status_code,401)
            self.assertEqual(self.client.get('/api/v1/monitoring',headers={'Authorization':'Bearer api-secret'}).status_code,200)

    def test_batches_continue_without_dropping_or_duplicating_messages(self):
        config=self.client.get(self.prefix+'/config').json()
        config['maintenance_batch_chars']=20000
        self.client.put(self.prefix+'/config',json=config)
        for index in range(3):
            self.post_message(str(index)+'记'*14999)
        first=self.client.post(self.prefix+'/maintenance',json={}).json()
        self.assertEqual(self.await_job(first['id'])['status'],'completed')
        for _ in range(200):
            if self.client.get(self.prefix+'/stats').json()['pending_messages']==0:
                break
            time.sleep(.01)
        self.assertEqual(self.client.get(self.prefix+'/stats').json()['pending_messages'],0)
        events=self.client.get(self.prefix+'/events').json()
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['body'].count('记'),44997)

    def test_failed_job_can_retry_without_advancing_progress(self):
        self.post_message()
        failed=self.run_plan({'events':[]})
        self.assertEqual(failed['status'],'failed')
        self.client.put(self.prefix+'/model',json={'mode':'local'})
        retry=self.client.post(self.prefix+'/jobs/'+failed['id']+'/retry')
        self.assertEqual(retry.status_code,202,retry.text)
        job=self.await_job(retry.json()['id'])
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['retry_of'],failed['id'])

    def test_future_plan_can_be_recalled_today_and_on_plan_date(self):
        old=self.create_event(title='下周见小林',body='计划下周见小林，尚未见面。',status='planned',occurred_at=(fixtures.NOW+timedelta(days=7)).isoformat(),anchors={})
        today=self.client.post(self.prefix+'/context',json={'person_id':self.person,'query':'小林','at':fixtures.NOW.isoformat()}).json()
        self.assertIn(old['id'],[e['event_id'] for e in today['selected']])
        future=self.client.post(self.prefix+'/context',json={'person_id':self.person,'query':'今天有什么事情','at':(fixtures.NOW+timedelta(days=7)).isoformat()}).json()
        self.assertIn(old['id'],[e['event_id'] for e in future['selected']])
        self.assertIn('这是计划，不代表已发生',future['context'])
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['status'],'planned')

    def test_plan_update_requires_explicit_user_confirmation(self):
        old=self.create_event(status='planned',body='计划下周去海边。')
        message=self.post_message('计划下周去海边，我还在考虑带什么。').json()
        job=self.run_plan({'events':[{'id':old['id'],'title':old['title'],'body':'还在考虑带什么。','source_ids':[message['id']],'status':'closed'}]})
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['status'],'planned')
        confirmation=self.post_message('已经去过海边了。').json()
        job=self.run_plan({'events':[{'id':old['id'],'title':old['title'],'body':'用户确认已经去了海边。','source_ids':[confirmation['id']],'status':'closed','status_evidence':'已经去过海边了'}]})
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['status'],'closed')

    def test_connection_test_checks_json_mode(self):
        self.model_mode()
        async def invalid(*args,**kwargs):
            self.assertTrue(kwargs.get('json_mode'))
            return '连接成功'
        with patch('memory_slice.model.complete',invalid):
            self.assertEqual(self.client.post(self.prefix+'/model/test').status_code,502)

    def test_feedback_and_evaluations_do_not_reinforce(self):
        old=self.create_event()
        trace=self.client.post(self.prefix+'/context',json={'person_id':self.person,'query':'陨石一号','at':fixtures.NOW.isoformat()}).json()
        path=self.prefix+'/recalls/'+trace['id']+'/feedback'
        body={'useful_ids':[old['id']]}
        self.assertEqual(self.client.put(path,json=body).status_code,200)
        self.assertEqual(self.client.put(path,json=body).status_code,200)
        self.assertIn('memory_recall_feedback_total{verdict="useful"} 1.0',self.client.get('/metrics').text)
        report=self.client.post(self.prefix+'/evaluations',json={'cases':[{'person_id':self.person,'query':'陨石一号','at':fixtures.NOW.isoformat(),'expected_ids':[old['id']]}]})
        self.assertEqual(report.status_code,200,report.text)
        self.assertEqual(report.json()['passed'],1)
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['reinforcements'],[])


class RuntimeTests(unittest.TestCase):
    def test_incomplete_job_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,seed=False)
            with TestClient(app) as client:
                sid=client.post('/api/v1/spaces',json={'name':'恢复测试'}).json()['id']
                pid=client.get('/api/v1/spaces/'+sid+'/persons').json()[0]['id']
                cid=client.post('/api/v1/spaces/'+sid+'/conversations',json={'person_id':pid}).json()['id']
                client.post('/api/v1/spaces/'+sid+'/conversations/'+cid+'/messages',json={'role':'user','content':'待恢复的原始消息'})
            jid=new_id('job')
            app.state.store.put('job',{'id':jid,'space_id':sid,'status':'running','trigger':'manual','created_at':iso(),'request':{},'event_ids':[]},sid)
            with TestClient(create_app(directory,seed=False)) as client:
                for _ in range(100):
                    result=client.get('/api/v1/spaces/'+sid+'/jobs/'+jid).json()
                    if result['status']=='completed':
                        break
                    time.sleep(.01)
                self.assertEqual(result['status'],'completed',result)
                self.assertEqual(result['message_count'],1)

    def test_transient_model_error_retries_and_reports_attempts(self):
        from memory_slice.monitoring import Monitoring
        with tempfile.TemporaryDirectory() as directory:
            metrics=Monitoring(Store(directory))
            attempts=[]
            actual_client=httpx.AsyncClient
            def handler(request):
                attempts.append(1)
                if len(attempts)==1:
                    return httpx.Response(503)
                return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
            def factory(*args,**kwargs):
                return actual_client(*args,transport=httpx.MockTransport(handler),**kwargs)
            with patch('memory_slice.model.httpx.AsyncClient',factory):
                result=asyncio.run(complete({'model':'test','base_url':'http://model.test','max_retries':1},'',[],purpose='chat',metrics=metrics))
            self.assertEqual(result,'ok')
            self.assertEqual(len(attempts),2)
            text=metrics.prometheus().decode()
            self.assertIn('memory_model_attempts_total{outcome="http_error",purpose="chat"} 1.0',text)
            self.assertIn('memory_model_usage_missing_total{purpose="chat"} 1.0',text)


if __name__=='__main__':
    unittest.main()
