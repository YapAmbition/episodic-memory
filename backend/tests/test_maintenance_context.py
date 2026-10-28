"""Behavioral tests for bounded incremental maintenance; no real model quality claims."""
import asyncio
import copy
import json
import time
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx

import test_memory as fixtures
from memory_slice.maintenance_context import budget_for, estimated_tokens, prepare, rank_events, segments
from memory_slice.model import ContextLimitError, OutputLimitError, complete, is_context_limit
from memory_slice.schemas import MemoryConfig, ModelInput

NOW = fixtures.NOW


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.config = MemoryConfig().model_dump()

    def test_unknown_capacity_is_explicit_and_not_inferred_from_name(self):
        result = budget_for({'model': 'some-128k-name'}, self.config)
        self.assertEqual(result['source'], 'fallback')
        self.assertIsNone(result['context_window_tokens'])
        self.assertTrue(result['estimated'])

    def test_known_capacity_reserves_output_and_safety_together(self):
        result = budget_for({'context_window_tokens': 7000, 'maintenance_output_tokens': 2000}, self.config)
        self.assertEqual(result['input_limit'], 4488)
        self.assertEqual(result['source'], 'configured')

    def test_invalid_reserves_are_rejected(self):
        with self.assertRaises(Exception):
            budget_for({'context_window_tokens': 4096, 'maintenance_output_tokens': 4000}, self.config)
        with self.assertRaises(ValueError):
            ModelInput(context_window_tokens=4096, maintenance_output_tokens=4096)

    def test_token_estimate_accounts_for_serialization_and_multibyte_text(self):
        english = estimated_tokens([{'role': 'user', 'content': 'abc'}])
        chinese = estimated_tokens([{'role': 'user', 'content': '中文🙂'}])
        self.assertGreater(chinese, english)
        self.assertGreater(english, 256)

    def test_segment_positions_reconstruct_exact_source_characters(self):
        body = '一二三🙂甲乙丙\n' * 700
        parts = segments(body, '🙂甲乙', 1200)
        self.assertLessEqual(sum(len(p['text']) for p in parts), 1200)
        for part in parts:
            self.assertEqual(part['text'], body[part['start']:part['end']])

    def test_only_explicit_context_errors_trigger_adaptation(self):
        for code in (400, 413, 422):
            self.assertTrue(is_context_limit(httpx.Response(code, json={'error': {'code': 'context_length_exceeded'}})))
        for code, data in ((401, {'error': {'code':'context_length_exceeded'}}), (400, {'error': {'message':'invalid max_tokens parameter'}}), (413, {'error': {'message': 'request entity too large'}})):
            self.assertFalse(is_context_limit(httpx.Response(code, json=data)))

    def test_output_truncation_is_rejected_even_when_json_is_valid(self):
        actual = httpx.AsyncClient
        def response(request):
            return httpx.Response(200, json={'choices': [{'finish_reason':'length', 'message': {'content':'{"events":[]}'}}]})
        with patch('memory_slice.model.httpx.AsyncClient', lambda *a, **kw: actual(*a, transport=httpx.MockTransport(response), **kw)):
            with self.assertRaises(OutputLimitError):
                asyncio.run(complete({'model':'test', 'base_url':'http://provider/v1', 'max_retries':0}, '', [], max_tokens=256))


class MaintenanceTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    create_event = fixtures.APITests.create_event
    post_message = fixtures.APITests.post_message
    await_job = fixtures.APITests.await_job

    def config(self, **extra):
        value = self.client.get(self.prefix + '/config').json()
        value.update(extra)
        result = self.client.put(self.prefix + '/config', json=value)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def model(self, **extra):
        body = dict(mode='llm', model='fixture', base_url='http://fixture/v1', max_retries=0)
        body.update(extra)
        result = self.client.put(self.prefix + '/model', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def run_mock(self, reply):
        self.model()
        with patch('memory_slice.model.complete', reply):
            result = self.client.post(self.prefix + '/maintenance', json={}).json()
            return self.await_job(result['id'])

    def test_validation_failure_is_visible_and_logged_without_input(self):
        raw = self.post_message('private-dialogue-marker').json()
        async def invalid(*args, **kwargs):
            return json.dumps({'events': [{'title': 'private-output-marker', 'body': 123}]})
        with self.assertLogs('memory_slice', level='ERROR') as logs:
            job = self.run_mock(invalid)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error_detail']['stage'], 'validate')
        self.assertTrue(any(i['field'] == 'events.0.body' for i in job['error_detail']['issues']))
        rendered = json.dumps(job['error_detail']) + str(logs.output)
        self.assertIn(job['id'], str(logs.output))
        self.assertNotIn('private-dialogue-marker', rendered)
        self.assertNotIn('private-output-marker', rendered)
        self.assertFalse(self.app.state.store.get('message', raw['id'], self.space)['processed'])

    def test_invalid_json_reports_parse_stage(self):
        self.post_message()
        async def invalid(*args, **kwargs):
            return 'not-json-private-marker'
        with self.assertLogs('memory_slice', level='ERROR') as logs:
            job = self.run_mock(invalid)
        self.assertEqual(job['error_detail']['stage'], 'parse')
        self.assertNotIn('not-json-private-marker', str(logs.output))

    def wait_drained(self):
        for _ in range(700):
            jobs = self.client.get(self.prefix + '/jobs').json()
            pending = self.client.get(self.prefix + '/stats').json()['pending_messages']
            if not pending and not any(j['status'] in ('queued','running') for j in jobs):
                return jobs
            time.sleep(.01)
        self.fail('continuation chain did not drain')

    def plan_for(self, messages, body=None, eid=None):
        return json.dumps({'events':[dict(id=eid, title='长对话的同一事情', body=body or ''.join(m['content'] for m in messages), source_ids=[m['id'] for m in messages], person_ids=[self.person])]}, ensure_ascii=False)

    def test_budget_includes_prompt_persons_messages_and_old_event_material(self):
        self.create_event(body='很多细节。' * 500)
        raw = self.post_message('陨石一号的后续。' * 1500).json()
        cfg = self.config(maintenance_input_token_budget=5000)
        result = prepare(self.app.state.store, self.space, [raw], self.model(), cfg, NOW, '详细事件规则。')
        self.assertLessEqual(estimated_tokens(result['request']), 5000)
        self.assertGreater(result['messages'][0]['fragment']['end'], 0)
        self.assertLess(result['messages'][0]['fragment']['end'], len(raw['content']))
        self.assertGreater(result['budget']['included_count'], 0)
        original = self.app.state.store.get('message', raw['id'], self.space)
        self.assertEqual(original['content'], raw['content'])
        self.assertFalse(original['processed'])

    def test_long_message_fragments_commit_contiguously_without_overwriting_source(self):
        text = ('开头🙂与结尾不能丢失。\n' * 700) + '唯一尾巴'
        raw = self.post_message(text).json()
        self.config(maintenance_input_token_budget=4000)
        self.model()
        ranges, output_limits = [], []
        async def reply(settings, key, request, **kw):
            data = json.loads(request[1]['content'])
            messages = data['new_messages']
            ranges.extend(m['fragment'] for m in messages)
            output_limits.append(kw['max_tokens'])
            eid = data['existing_events'][0]['id'] if data['existing_events'] else None
            return self.plan_for(messages, eid=eid)
        with patch('memory_slice.model.complete', reply):
            first = self.client.post(self.prefix + '/maintenance', json={}).json()
            self.await_job(first['id'])
            jobs = self.wait_drained()
        self.assertGreater(len(ranges), 1)
        self.assertEqual(ranges[0]['start'], 0)
        self.assertEqual(ranges[-1]['end'], len(text))
        self.assertTrue(all(a['end'] == b['start'] for a,b in zip(ranges, ranges[1:])))
        original = self.app.state.store.get('message', raw['id'], self.space)
        self.assertEqual(original['content'], text)
        self.assertTrue(original['processed'])
        events = self.client.get(self.prefix + '/events').json()
        self.assertEqual(len(events), 1)
        fragments = self.client.get(self.prefix+'/events/'+events[0]['id']+'/fragments').json()
        restored = ''.join(original['content'][f['source_ranges'][0]['start']:f['source_ranges'][0]['end']] for f in fragments)
        self.assertTrue(restored == text, 'source ranges must reconstruct the complete original message')
        for f in fragments:
            r = f['source_ranges'][0]
            self.assertTrue(f['content'] == text[r['start']:r['end']].strip(), 'derived narrative may trim boundary whitespace; sources remain exact')
        self.assertEqual([f['source_ranges'][0]['start'] for f in fragments], [r['start'] for r in ranges])
        self.assertEqual(sum(j['message_count'] for j in jobs), 1)
        self.assertTrue(all(limit == 4096 for limit in output_limits))

    def test_failed_second_fragment_preserves_committed_progress_and_remaining_text(self):
        raw = self.post_message('有据可查的长消息🙂。' * 900).json()
        self.config(maintenance_input_token_budget=4000)
        self.model()
        calls = []
        async def reply(settings, key, request, **kw):
            data = json.loads(request[1]['content']); calls.append(data)
            if len(calls) > 1:
                raise RuntimeError('provider unavailable')
            return self.plan_for(data['new_messages'])
        with patch('memory_slice.model.complete', reply):
            first = self.client.post(self.prefix + '/maintenance', json={}).json()
            self.await_job(first['id'])
            for _ in range(100):
                jobs = self.client.get(self.prefix+'/jobs').json()
                if any(j['status']=='failed' for j in jobs): break
                time.sleep(.01)
        original = self.app.state.store.get('message',raw['id'],self.space)
        self.assertEqual(original['content'],raw['content'])
        self.assertEqual(original['maintenance_offset'],calls[0]['new_messages'][0]['fragment']['end'])
        self.assertFalse(original['processed'])
        self.assertTrue(any(j['status']=='failed' for j in jobs))

    def test_explicit_context_overflow_shrinks_and_continuations_inherit_limit(self):
        self.post_message('这段话需要拆成几个小批次。' * 700)
        self.config(maintenance_input_token_budget=7000, maintenance_max_adaptations=3)
        self.model()
        sizes = []
        async def reply(settings, key, request, **kw):
            data = json.loads(request[1]['content']); sizes.append(estimated_tokens(request))
            if len(sizes)==1: raise ContextLimitError()
            eid = data['existing_events'][0]['id'] if data['existing_events'] else None
            return self.plan_for(data['new_messages'],eid=eid)
        with patch('memory_slice.model.complete',reply):
            jid=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
            first=self.await_job(jid)
            jobs=self.wait_drained()
        self.assertEqual(first['adaptation_count'],1,first)
        self.assertLess(sizes[1],sizes[0])
        inherited = next(j['adaptive_input_limit'] for j in jobs if j.get('adaptive_input_limit'))
        self.assertTrue(all(n <= inherited for n in sizes[1:]))
        self.assertLess(inherited,sizes[0])
        text=self.client.get('/metrics').text
        self.assertIn('memory_maintenance_adaptations_total{reason="context_limit"} 1.0',text)
        self.assertIn('memory_maintenance_estimated_input_tokens_count{source="fallback"}',text)

    def test_adaptation_is_bounded_and_never_commits_rejected_material(self):
        raw=self.post_message('后端应该保留这条完整原文。' * 200).json()
        self.config(maintenance_max_adaptations=1,maintenance_input_token_budget=16000)
        calls=[]
        async def reply(*args,**kw):
            calls.append(1); raise ContextLimitError()
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'failed',job)
        self.assertEqual(len(calls),2)
        self.assertFalse(self.app.state.store.get('message',raw['id'],self.space)['processed'])
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])

    def test_authentication_error_is_not_retried_as_context_overflow(self):
        self.post_message()
        calls=[]
        from memory_slice.errors import MemoryError as HTTPException
        async def reply(*args,**kw):
            calls.append(1);raise HTTPException(502,'模型服务返回 HTTP 401')
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(len(calls),1)
        self.assertEqual(job['adaptation_count'],0)

    def test_oversize_prompt_fails_before_network_and_keeps_messages(self):
        raw=self.post_message().json()
        self.config(maintenance_input_token_budget=1024,maintenance_prompt='特别长的规则。'*1500)
        async def forbidden(*args,**kw): self.fail('must not send an over-budget request')
        job=self.run_mock(forbidden)
        self.assertEqual(job['status'],'failed',job)
        self.assertFalse(self.app.state.store.get('message',raw['id'],self.space)['processed'])

    def test_fading_changes_maintenance_order_and_strong_details_revive_old_event(self):
        older=self.create_event(title='周末一次散步',body='很久以前去散步',salience=.2,learned_at=(NOW-timedelta(days=1500)).isoformat(),anchors={'details':['紫色长椅上的纸风车']})
        fresh=self.create_event(title='周末另一次散步',salience=.2,learned_at=NOW.isoformat(),anchors={})
        cfg=self.config()
        raw=self.post_message('这个周末想去散步').json()
        meta=self.app.state.store.event_catalog(self.space)
        ranked,_=rank_events(self.app.state.store,self.space,meta,[raw],NOW,cfg)
        scores=dict(ranked)
        self.assertGreater(scores[fresh['id']]['score'],scores.get(older['id'],{}).get('score',0))
        raw['content']='还记得紫色长椅上的纸风车吗？'
        ranked,_=rank_events(self.app.state.store,self.space,meta,[raw],NOW,cfg)
        self.assertEqual(ranked[0][0],older['id'])
        self.assertTrue(ranked[0][1]['revived'])
        self.assertEqual(self.client.get(self.prefix+'/events/'+older['id']).json()['reinforcements'],[])

    def test_weak_unrelated_old_events_are_omitted_and_reads_do_not_reinforce(self):
        old=self.create_event(salience=.1,learned_at=(NOW-timedelta(days=2000)).isoformat(),anchors={})
        raw=self.post_message('完全不同的新事情').json()
        cfg=self.config()
        settings=self.model()
        before=self.app.state.store.get('event',old['id'],self.space)
        result=prepare(self.app.state.store,self.space,[raw],settings,cfg,NOW,'规则')
        self.assertGreaterEqual(result['budget']['unrelated_count'],1)
        self.assertNotIn(old['id'],[e['id'] for e in result['existing']])
        self.assertEqual(before,self.app.state.store.get('event',old['id'],self.space))

    def test_real_remention_strength_and_salience_affect_maintenance_priority(self):
        old=self.create_event(salience=.1,learned_at=(NOW-timedelta(days=600)).isoformat(),anchors={})
        meta=self.app.state.store.event_catalog(self.space)
        raw=self.post_message('用户说').json();cfg=self.config()
        before,_=rank_events(self.app.state.store,self.space,meta,[raw],NOW,cfg)
        meta[0]['reinforcements']=[{'message_id':'real-user-reference','at':NOW.isoformat()}]
        after,_=rank_events(self.app.state.store,self.space,meta,[raw],NOW,cfg)
        self.assertNotIn(old['id'],dict(before))
        self.assertIn(old['id'],dict(after))
        meta[0]['salience']=1
        more,_=rank_events(self.app.state.store,self.space,meta,[raw],NOW,cfg)
        self.assertGreater(dict(more)[old['id']]['score'],dict(after)[old['id']]['score'])

    def test_model_can_defer_instead_of_inventing_an_event(self):
        raw=self.post_message().json()
        async def reply(*args,**kw):
            return json.dumps({'deferred_messages':[{'message_id':raw['id'],'reason':'无法确定指的是哪次尝试'}]})
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'deferred',job)
        self.assertFalse(self.app.state.store.get('message',raw['id'],self.space)['processed'])
        self.assertEqual(job['fragment_count'],0)
        self.assertIn('memory_maintenance_deferred_messages_total 1.0',self.client.get('/metrics').text)

    def test_deferred_and_modified_same_message_rolls_back(self):
        raw=self.post_message().json()
        async def reply(*args,**kw):
            value=json.loads(self.plan_for([raw]));value['deferred_messages']=[{'message_id':raw['id'],'reason':'不确定'}]
            return json.dumps(value)
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'failed',job)
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])

    def test_requested_old_body_is_read_by_exact_range_before_update(self):
        old=self.create_event(body='原始细节不丢失。'*100)
        raw=self.post_message('陨石一号的后续').json()
        calls=[]
        async def reply(settings,key,request,**kw):
            data=json.loads(request[1]['content']);calls.append(data)
            if len(calls)==1:
                return json.dumps({'read_requests':[{'event_id':old['id'],'start':0,'end':len(old['body'])}]})
            self.assertTrue(data['existing_events'][0]['body_complete'])
            self.assertEqual(data['existing_events'][0]['segments'][0]['text'],old['body'])
            return self.plan_for(data['new_messages'],body='新增后续',eid=old['id'])
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['model_call_count'],2)
        self.assertEqual(job['read_rounds'],1)
        self.assertTrue(self.client.get(self.prefix+'/events/'+old['id']).json()['body'].startswith(old['body']))

    def test_unaffordable_body_read_is_deferred_without_progress(self):
        old=self.create_event(body='不能丢失的巨长正文。'*2000)
        raw=self.post_message('陨石一号').json()
        async def reply(*args,**kw):
            return json.dumps({'read_requests':[{'event_id':old['id'],'start':0,'end':len(old['body'])}]})
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'deferred',job)
        self.assertEqual(job['model_call_count'],1)
        self.assertFalse(self.app.state.store.get('message',raw['id'],self.space)['processed'])
        self.assertEqual(self.client.get(self.prefix+'/events/'+old['id']).json()['body'],old['body'])

    def test_read_rounds_are_bounded(self):
        old=self.create_event()
        self.post_message('陨石一号')
        self.config(maintenance_max_read_rounds=1)
        async def reply(*args,**kw):
            return json.dumps({'read_requests':[{'event_id':old['id'],'start':0,'end':5}]})
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'deferred',job)
        self.assertEqual(job['model_call_count'],2)

    def test_metrics_keep_fixed_labels_and_separate_estimates_from_provider_tokens(self):
        raw=self.post_message('私密内容绝不进入指标标签').json()
        async def reply(*args,**kw):return self.plan_for([raw],body='有据的事件')
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'completed',job)
        text=self.client.get('/metrics').text
        self.assertIn('memory_maintenance_stage_duration_seconds_count{stage="model"}',text)
        self.assertNotIn(raw['id'],text)
        self.assertNotIn(raw['content'],text)
        snapshot=self.client.get('/api/v1/monitoring').json()
        self.assertTrue(any(m['name']=='memory_maintenance_estimated_input_tokens' for m in snapshot['metrics']))

    def test_capacity_persists_and_does_not_expose_key(self):
        result=self.model(context_window_tokens=16000,maintenance_output_tokens=2000,api_key='private-fixture-key')
        self.assertEqual(result['context_window_tokens'],16000)
        self.assertEqual(result['maintenance_output_tokens'],2000)
        self.assertNotIn('api_key',result)
        self.assertNotIn('private-fixture-key',self.client.get(self.prefix+'/export').text)

    def test_zero_forgetting_weight_disables_time_penalty_for_equal_cues(self):
        old=self.create_event(title='同样的散步',salience=.3,learned_at=(NOW-timedelta(days=1500)).isoformat(),anchors={})
        fresh=self.create_event(title='同样的散步',salience=.3,learned_at=NOW.isoformat(),anchors={})
        raw=self.post_message('同样的散步').json()
        cfg=self.config(maintenance_forgetting_weight=0)
        ranked,_=rank_events(self.app.state.store,self.space,self.app.state.store.event_catalog(self.space),[raw],NOW,cfg)
        self.assertEqual(dict(ranked)[old['id']]['score'],dict(ranked)[fresh['id']]['score'])

    def test_invalid_cross_space_read_does_not_commit(self):
        self.post_message()
        async def reply(*args,**kw):return json.dumps({'read_requests':[{'event_id':'event_other_space','start':0,'end':10}]})
        job=self.run_mock(reply)
        self.assertEqual(job['status'],'failed',job)
        self.assertEqual(self.client.get(self.prefix+'/events').json(),[])

    def test_deferred_job_can_retry_after_configuration_changes(self):
        raw=self.post_message().json()
        async def deferred(*args,**kw):return json.dumps({'deferred_messages':[{'message_id':raw['id'],'reason':'需要更多材料'}]})
        job=self.run_mock(deferred)
        async def resolved(*args,**kw):return self.plan_for([raw])
        with patch('memory_slice.model.complete',resolved):
            response=self.client.post(self.prefix+'/jobs/'+job['id']+'/retry')
            self.assertEqual(response.status_code,202,response.text)
            retried=self.await_job(response.json()['id'])
        self.assertEqual(retried['status'],'completed',retried)
        self.assertEqual(retried['retry_of'],job['id'])

    def test_output_limit_adapts_without_committing_partial_model_output(self):
        raw=self.post_message('详细经过。'*500).json()
        self.config(maintenance_input_token_budget=12000)
        calls=[]
        async def reply(settings,key,request,**kw):
            calls.append(estimated_tokens(request))
            if len(calls)==1:raise OutputLimitError()
            data=json.loads(request[1]['content'])
            eid=data['existing_events'][0]['id'] if data['existing_events'] else None
            return self.plan_for(data['new_messages'],eid=eid)
        self.model()
        with patch('memory_slice.model.complete',reply):
            jid=self.client.post(self.prefix+'/maintenance',json={}).json()['id']
            job=self.await_job(jid)
            self.wait_drained()
        self.assertEqual(job['adaptation_count'],1,job)
        self.assertEqual(job['attempts'][0]['outcome'],'output_limit')
        self.assertTrue(self.app.state.store.get('message',raw['id'],self.space)['processed'])
        self.assertIn('memory_maintenance_adaptations_total{reason="output_limit"} 1.0',self.client.get('/metrics').text)

    def test_small_over_limit_request_changes_material_instead_of_repeating_same_payload(self):
        self.create_event(title='无关但鲜明的旧事',anchors={})
        old=self.create_event(title='纸风车',anchors={'details':['紫色长椅上的纸风车']},learned_at=(NOW-timedelta(days=1500)).isoformat(),salience=.1)
        self.post_message('紫色长椅上的纸风车那天还下过雨。')
        self.model()
        seen=[]
        async def reply(settings,key,request,**kw):
            seen.append(request)
            if len(seen)==1:raise ContextLimitError()
            data=json.loads(request[1]['content'])
            return self.plan_for(data['new_messages'],body='当天还下过雨',eid=old['id'])
        with patch('memory_slice.model.complete',reply):
            job=self.await_job(self.client.post(self.prefix+'/maintenance',json={}).json()['id'])
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(len(seen),2)
        self.assertLess(estimated_tokens(seen[1]),estimated_tokens(seen[0]))
        self.assertNotEqual(seen[0],seen[1])
