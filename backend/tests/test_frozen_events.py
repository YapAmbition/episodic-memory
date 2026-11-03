import unittest
import test_memory as fixtures
import test_upgrade as upgrade
from memory_slice.errors import MemoryError
from memory_slice.store import Store
from memory_slice.maintenance_context import material, PROTOCOL


class FrozenEventTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    create_event = fixtures.APITests.create_event
    post_message = fixtures.APITests.post_message
    await_job = fixtures.APITests.await_job
    model_mode = upgrade.UpgradeTests.model_mode
    run_plan = upgrade.UpgradeTests.run_plan

    def freeze(self, event, value=True):
        r = self.client.put(self.prefix+'/events/'+event['id']+'/freeze',json={'frozen':value,'expected_version':event['version']})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def test_freeze_edit_unfreeze_scope_and_restart(self):
        e = self.freeze(self.create_event())
        url = self.prefix+'/events/'+e['id']
        self.assertTrue(Store(self.temp.name).get('event',e['id'],self.space)['frozen'])
        self.assertEqual(self.client.put(url,json={'title':'改写','body':'不允许'}).status_code,409)
        self.assertEqual(self.client.put(url+'/freeze',json={'frozen':False,'expected_version':e['version']-1}).status_code,409)
        other=self.client.post('/api/v1/spaces',json={'name':'other'}).json()['id']
        self.assertEqual(self.client.put(f'/api/v1/spaces/{other}/events/{e["id"]}/freeze',json={'frozen':False}).status_code,404)
        self.freeze(e,False)
        self.assertEqual(self.client.put(url,json={'title':'改写','body':'解冻后允许'}).status_code,200)

    def test_model_cannot_mutate_frozen_or_consume_messages(self):
        e=self.freeze(self.create_event())
        m=self.post_message('第一次烤面包后续又失败了').json()
        job=self.run_plan({'events':[{'id':e['id'],'title':e['title'],'body':'追加内容','source_ids':[m['id']]}]})
        self.assertNotEqual(job['status'],'completed')
        self.assertEqual(self.app.state.store.get('event',e['id'],self.space)['body'],e['body'])
        self.assertFalse(self.app.state.store.get('message',m['id'],self.space)['processed'])

    def test_successor_can_reference_frozen_event(self):
        e=self.freeze(self.create_event())
        m=self.post_message('第一次烤面包的后续，这次成功了').json()
        job=self.run_plan({'events':[{'title':'面包后续','body':'这次成功了','source_ids':[m['id']],'links':[{'target_id':e['id'],'relation':'后续'}]}]})
        self.assertEqual(job['status'],'completed',job)
        events=self.client.get(self.prefix+'/events').json()
        successor=next(x for x in events if x['id']!=e['id'])
        self.assertEqual(successor['links'][0]['target_id'],e['id'])
        self.assertEqual(self.app.state.store.get('event',e['id'],self.space)['version'],e['version'])
        self.assertTrue(material(e,{},'',100)['frozen'])
        self.assertIn('frozen=true',PROTOCOL)

    def test_merge_split_and_indirect_rewrite_rollback(self):
        a=self.create_event(body='甲')
        b=self.create_event(body='乙')
        frozen=self.freeze(self.create_event(body='引用 [['+b['id']+']]',links=[{'target_id':b['id']}]))
        # Redirecting a frozen event's references must not silently change its body.
        r=self.client.post(self.prefix+'/events/merge',json={'event_ids':[a['id'],b['id']],'target_id':a['id'],'reason':'合并'})
        self.assertEqual(r.status_code,409,r.text)
        self.assertEqual(self.app.state.store.get('event',a['id'],self.space)['body'],'甲')
        self.assertEqual(self.app.state.store.get('event',b['id'],self.space)['status'],'closed')
        a=self.freeze(a)
        self.assertEqual(self.client.post(self.prefix+'/events/merge',json={'event_ids':[a['id'],b['id']],'target_id':b['id'],'reason':'合并'}).status_code,409)
        self.assertEqual(self.client.post(self.prefix+'/events/'+a['id']+'/split',json={'parts':[{'title':'甲','body':'甲'},{'title':'乙','body':'乙'}],'reason':'拆分'}).status_code,409)
        with self.assertRaises(MemoryError):
            self.app.state.store.put('event',dict(frozen,body='绕过服务写入'),self.space)
        with self.assertRaises(MemoryError):
            self.app.state.store.put('event',dict(frozen,frozen=False),self.space)

    def test_local_archive_creates_linked_successor(self):
        self.post_message('第一次烤面包')
        job=self.await_job(self.client.post(self.prefix+'/maintenance',json={}).json()['id'])
        self.assertEqual(job['status'],'completed',job)
        e=self.freeze(self.client.get(self.prefix+'/events').json()[0])
        self.post_message('继续聊第二次烤面包')
        job=self.await_job(self.client.post(self.prefix+'/maintenance',json={}).json()['id'])
        self.assertEqual(job['status'],'completed',job)
        rows=self.client.get(self.prefix+'/events').json()
        self.assertEqual(len(rows),2)
        new=next(x for x in rows if x['id']!=e['id'])
        self.assertEqual(new['links'][0]['target_id'],e['id'])
