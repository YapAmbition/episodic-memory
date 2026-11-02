import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_memory as fixtures
from memory_slice.store import Store, utcnow, iso
from memory_slice.schemas import MemoryConfig
from memory_slice.message_retention import cleanup_messages, STATE_ID


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.cfg = MemoryConfig().model_dump()
        self.now = utcnow()

    def message(self, mid, **extra):
        return self.store.put('message', dict(id=mid, space_id='s', conversation_id='c', role='user',
            content='需要清理的原话', at=iso(self.now-timedelta(days=90)), processed=True,
            processed_at=iso(self.now-timedelta(days=31)), **extra), 's')

    def test_expiry_retains_identity_and_independent_event(self):
        self.message('m', client_id='external')
        event = dict(fixtures.event('event'), source_ids=['m'], space_id='s')
        self.store.put('event', event, 's')
        before = self.store.get('event', 'event', 's')
        result = cleanup_messages(self.store,'s',self.cfg,self.now)
        msg = self.store.get('message','m','s')
        self.assertEqual(result['last_cleaned'],1)
        self.assertEqual(msg['content'],'')
        self.assertTrue(msg['processed'])
        self.assertEqual(msg['client_id'],'external')
        self.assertEqual(self.store.get('event','event','s'),before)
        self.assertIsNone(cleanup_messages(self.store,'s',self.cfg,self.now))
        self.assertEqual(cleanup_messages(self.store,'s',self.cfg,self.now+timedelta(days=1))['last_cleaned'],0)

    def test_old_data_gets_full_grace_period_and_uses_completion_not_message_time(self):
        for mid in ('legacy','recent'):
            msg=self.message(mid)
            if mid=='legacy': msg.pop('processed_at')
            else: msg['processed_at']=iso(self.now)
            self.store.put('message',msg,'s')
        self.assertEqual(cleanup_messages(self.store,'s',self.cfg,self.now)['last_cleaned'],0)
        self.assertEqual(cleanup_messages(self.store,'s',self.cfg,self.now+timedelta(days=29))['last_cleaned'],0)
        self.assertEqual(cleanup_messages(self.store,'s',self.cfg,self.now+timedelta(days=30))['last_cleaned'],2)

    def test_waiting_partial_and_required_context_are_protected(self):
        for mid in ('old','previous1','previous2','pending'):
            self.message(mid)
        pending=self.store.get('message','pending','s')
        pending.update(processed=False, maintenance_offset=2, in_reply_to='old')
        self.store.put('message',pending,'s')
        self.assertEqual(cleanup_messages(self.store,'s',self.cfg,self.now)['last_cleaned'],0)
        self.assertTrue(all(m['content'] for m in self.store.list('message','s')))

    def test_disabled_and_running_jobs_do_not_clear(self):
        self.message('m')
        self.assertIsNone(cleanup_messages(self.store,'s',dict(self.cfg,message_cleanup_enabled=False),self.now))
        self.store.put('job',{'id':'j','status':'running'},'s')
        self.assertIsNone(cleanup_messages(self.store,'s',self.cfg,self.now))
        self.assertTrue(self.store.get('message','m','s')['content'])

    def test_batches_and_restart_keep_daily_progress(self):
        for i in range(3): self.message(str(i))
        result=cleanup_messages(self.store,'s',self.cfg,self.now,batch_size=2)
        self.assertEqual(result['pending_cleanup'],1)
        restarted=Store(self.temp.name)
        self.assertIsNone(cleanup_messages(restarted,'s',self.cfg,self.now+timedelta(seconds=10)))
        self.assertEqual(cleanup_messages(restarted,'s',self.cfg,self.now+timedelta(seconds=16))['total_cleaned'],3)

    def test_spaces_keep_separate_cleanup_state(self):
        cleanup_messages(self.store,'s',self.cfg,self.now)
        cleanup_messages(self.store,'other',self.cfg,self.now+timedelta(days=1))
        self.assertEqual(self.store.list('integration','s')[0]['enabled_at'],iso(self.now))
        self.assertEqual(self.store.list('integration','other')[0]['enabled_at'],iso(self.now+timedelta(days=1)))

    def test_failure_rolls_back_body_clearing_and_state(self):
        self.message('m')
        put=self.store.put
        def fail(kind,*args,**kw):
            if kind=='integration': raise RuntimeError('simulated commit failure')
            return put(kind,*args,**kw)
        with patch.object(self.store,'put',side_effect=fail):
            with self.assertRaises(RuntimeError): cleanup_messages(self.store,'s',self.cfg,self.now)
        self.assertTrue(self.store.get('message','m','s')['content'])
        self.assertEqual(self.store.list('integration','s'),[])


class RetentionAPITests(unittest.TestCase):
    setUp=fixtures.APITests.setUp
    tearDown=fixtures.APITests.tearDown
    post_message=fixtures.APITests.post_message

    def test_resync_is_idempotent_and_conflicts_still_rejected(self):
        msg=self.post_message('已经处理的原文',client_id='stable').json()
        msg.update(processed=True,processed_at=iso(utcnow()-timedelta(days=31)))
        store=self.app.state.store
        store.put('message',msg,self.space)
        cleanup_messages(store,self.space,MemoryConfig().model_dump())
        retry=self.post_message('已经处理的原文',client_id='stable')
        self.assertEqual(retry.status_code,201,retry.text)
        self.assertEqual(retry.json()['id'],msg['id'])
        self.assertEqual(retry.json()['content'],'')
        self.assertEqual(self.post_message('不同的原文',client_id='stable').status_code,409)
        self.assertEqual(len(store.list('message',self.space)),1)
        stats=self.client.get(self.prefix+'/stats').json()
        self.assertEqual(stats['message_cleanup']['total_cleaned'],1)
        self.assertEqual(stats['pending_messages'],0)
