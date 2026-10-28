import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_memory as fixtures
from memory_slice.conversation import ordered_messages
from memory_slice.store import iso

NOW=fixtures.NOW


def msg(mid, role, at, parent=None, cid='conversation'):
    result={'id':mid,'conversation_id':cid,'role':role,'at':at,'content':mid}
    if parent:result['in_reply_to']=parent
    return result


class OrderTests(unittest.TestCase):
    def test_legacy_fixed_clock_replies_are_restored_without_rewriting_data(self):
        rows=[]
        for index in range(3):
            rows += [msg('u'+str(index),'user',iso(NOW)),msg('a'+str(index),'assistant',iso(NOW+timedelta(milliseconds=1)),'u'+str(index))]
        before=copy.deepcopy(rows)
        self.assertEqual([m['id'] for m in ordered_messages(rows)],['u0','a0','u1','a1','u2','a2'])
        self.assertEqual(rows,before)

    def test_tied_unlinked_messages_keep_recording_order_without_forcing_alternation(self):
        rows=[msg('u1','user',iso(NOW)),msg('u2','user',iso(NOW)),msg('a1','assistant',iso(NOW))]
        self.assertEqual([m['id'] for m in ordered_messages(rows)],['u1','u2','a1'])

    def test_roots_use_real_time_including_timezone_offsets(self):
        rows=[msg('later','user','2026-10-26T13:00:00+00:00'),msg('earlier','user','2026-10-26T20:00:00+08:00')]
        self.assertEqual([m['id'] for m in ordered_messages(rows)],['earlier','later'])

    def test_orphan_and_cross_conversation_reply_links_do_not_move_messages(self):
        rows=[msg('u','user',iso(NOW)),msg('raw','user',iso(NOW)),msg('other','assistant',iso(NOW+timedelta(milliseconds=1)),'u',cid='elsewhere'),msg('orphan','assistant',iso(NOW+timedelta(seconds=1)),'missing')]
        self.assertEqual([m['id'] for m in ordered_messages(rows)],['u','raw','other','orphan'])


class ConversationTests(unittest.TestCase):
    setUp=fixtures.APITests.setUp
    tearDown=fixtures.APITests.tearDown

    @property
    def path(self):return self.prefix+'/conversations/'+self.cid

    def test_three_fixed_clock_rounds_preserve_both_display_order_and_model_history(self):
        self.client.put(self.prefix+'/model',json={'mode':'llm','model':'fixture','max_retries':0})
        calls=[]
        async def reply(settings,key,messages,**kw):
            calls.append(messages)
            return '回复'+str(len(calls))
        with patch('memory_slice.model.complete',reply):
            for index in range(3):
                response=self.client.post(self.path+'/chat',json={'content':'问题'+str(index+1),'at':iso(NOW),'use_memory':False})
                self.assertEqual(response.status_code,200,response.text)
                pair=response.json()
                self.assertEqual(pair['user']['at'],pair['assistant']['at'])
        rows=self.client.get(self.path+'/messages').json()
        self.assertEqual([m['content'] for m in rows],['问题1','回复1','问题2','回复2','问题3','回复3'])
        self.assertEqual([m['role'] for m in calls[2]],[ 'system','user','assistant','user','assistant','user'])
        self.assertEqual([m['content'] for m in calls[2][1:]],['问题1','回复1','问题2','回复2','问题3'])

    def test_existing_millisecond_replies_are_included_in_next_model_request(self):
        store=self.app.state.store
        for index in range(2):
            user=store.put('message',dict(msg('old_u'+str(index),'user',iso(NOW),cid=self.cid),space_id=self.space,person_id=self.person,processed=False,fixture=False),self.space)
            store.put('message',dict(msg('old_a'+str(index),'assistant',iso(NOW+timedelta(milliseconds=1)),parent=user['id'],cid=self.cid),space_id=self.space,person_id=self.person,processed=False,fixture=False),self.space)
        self.client.put(self.prefix+'/model',json={'mode':'llm','model':'fixture','max_retries':0})
        calls=[]
        async def reply(settings,key,messages,**kw):calls.append(messages);return 'new reply'
        with patch('memory_slice.model.complete',reply):
            result=self.client.post(self.path+'/chat',json={'content':'继续刚才的对话','at':iso(NOW),'use_memory':False})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual([m['content'] for m in calls[0][1:-1]],['old_u0','old_a0','old_u1','old_a1'])
        self.assertEqual([m['id'] for m in store.pending_messages(self.space,self.cid)][:4],['old_u0','old_a0','old_u1','old_a1'])

    def test_maintenance_limit_is_applied_after_reply_pair_ordering(self):
        store=self.app.state.store
        with store.connect() as con:
            for index in range(110):
                uid='u_'+str(index);aid='a_'+str(index)
                store.put('message',dict(msg(uid,'user',iso(NOW),cid=self.cid),space_id=self.space,person_id=self.person,processed=False,fixture=False),self.space,con=con)
                store.put('message',dict(msg(aid,'assistant',iso(NOW+timedelta(milliseconds=1)),parent=uid,cid=self.cid),space_id=self.space,person_id=self.person,processed=False,fixture=False),self.space,con=con)
        rows=store.pending_messages(self.space,self.cid)
        self.assertEqual(len(rows),200)
        self.assertEqual([m['id'] for m in rows[:4]],['u_0','a_0','u_1','a_1'])
        self.assertEqual([m['id'] for m in rows[-2:]],['u_99','a_99'])

    def test_processed_parent_does_not_push_pending_reply_after_newer_turns(self):
        store=self.app.state.store
        for mid,role,at,parent,processed in [('u1','user',iso(NOW),None,True),('a1','assistant',iso(NOW+timedelta(milliseconds=1)),'u1',False),('u2','user',iso(NOW),None,False)]:
            store.put('message',dict(msg(mid,role,at,parent,self.cid),space_id=self.space,person_id=self.person,processed=processed,fixture=False),self.space)
        self.assertEqual([m['id'] for m in store.pending_messages(self.space,self.cid)],['a1','u2'])
