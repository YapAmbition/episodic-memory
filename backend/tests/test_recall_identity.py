import tempfile
import unittest
from unittest.mock import patch

from memory_slice.focus import recall_person_keys, matches_focus, person_score
from memory_slice.schemas import SpaceInput, PersonInput, EventInput, RecallInput, ContextInput, SearchInput
from memory_slice.service import MemoryService


class RecallIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = MemoryService(self.tmp.name)
        self.sid = self.core.add_space(SpaceInput(name='目标空间'))['id']
        self.old = self.person('snowluma', '42')
        self.new = self.person('napcat', '42')
        self.other = self.person('napcat', '43')
        self.blank = self.person('', '')
        self.blank2 = self.person('', '')
        cfg = self.core.config_for(self.sid)
        self.core.store.put('config', dict(cfg, id=self.sid, focus_person_id=self.new, auto_reinforcement_enabled=False), self.sid)

    def person(self, platform, uid):
        return self.core.add_person(self.sid, PersonInput(name='人物', platform=platform, external_uid=uid))['id']

    def event(self, people, sid=None):
        return self.core.create_event(sid or self.sid, EventInput(title='陨石一号', body='陨石一号烤面包失败',
            person_ids=people, salience=1, anchors={'details': ['陨石一号']}))

    async def asyncTearDown(self):
        await self.core.close()
        self.tmp.cleanup()

    async def test_same_uid_filter_both_facades_local_and_fallback_preserves_data(self):
        yes = self.event([self.old]); no = self.event([self.other]); blank = self.event([self.blank])
        sid2 = self.core.add_space(SpaceInput(name='其他空间'))['id']
        foreign = self.core.add_person(sid2, PersonInput(name='外部', platform='napcat', external_uid='42'))['id']
        outside = self.event([foreign], sid2)
        before = {k: self.core.store.list(k, self.sid) for k in ('event', 'person', 'config')}
        for mode in ('local', 'hybrid'):
            for method, schema, key in ((self.core.retrieve, RecallInput, 'items'), (self.core.recall, ContextInput, 'selected')):
                r = await method(self.sid, schema(query='陨石一号', retrieval_mode=mode, trace=True))
                ids = {x['event_id'] for x in r[key]}
                self.assertIn(yes['id'], ids)
                self.assertTrue(ids.isdisjoint({no['id'], blank['id'], outside['id']}))
                self.assertEqual(r['effective_mode'], 'local')
                self.assertIn(yes['id'], r['retrieval']['lane_candidates']['person_salience'])
        self.assertEqual(before, {k: self.core.store.list(k, self.sid) for k in before})

    async def test_embedding_receives_uid_filtered_candidates(self):
        yes = self.event([self.old]); no = self.event([self.other])
        async def query(sid, dialogue, events):
            self.assertEqual(sid, self.sid)
            self.assertEqual({e['id'] for e in events}, {yes['id']})
            return {yes['id']: .99}, {'status':'ok', 'candidate_count':1, 'elapsed_ms':0}
        with patch.object(self.core.embeddings, 'query', side_effect=query):
            result = await self.core.retrieve(self.sid, RecallInput(query='一次厨房里的挫折', retrieval_mode='hybrid'))
        self.assertIn(yes['id'], [x['event_id'] for x in result['items']])
        self.assertNotIn(no['id'], [x['event_id'] for x in result['items']])

    async def test_empty_uid_uses_internal_id_and_no_focus_disables_person_score(self):
        yes = self.event([self.blank]); no = self.event([self.blank2])
        cfg = self.core.config_for(self.sid)
        self.core.store.put('config', dict(cfg, id=self.sid, focus_person_id=self.blank), self.sid)
        r = await self.core.retrieve(self.sid, RecallInput(query='陨石一号'))
        self.assertEqual({x['event_id'] for x in r['items']}, {yes['id']})
        keys = recall_person_keys(self.core.store.list('person', self.sid))
        self.assertFalse(matches_focus(no, dict(cfg, focus_person_id=self.blank, _recall_person_keys=keys)))
        self.assertEqual(person_score(yes, dict(cfg, focus_person_id=None, _recall_person_keys=keys)), 0)

    async def test_uid_weighting_deduplicates_mentions_reply_and_speaker(self):
        other_old = self.person('snowluma', '43')
        cfg = dict(self.core.config_for(self.sid), _recall_person_keys=recall_person_keys(self.core.store.list('person', self.sid)),
                   _interaction_ids=[self.new, self.old, self.other, other_old])
        event = {'person_ids':[self.old, other_old]}
        self.assertEqual(person_score(event, cfg, self.new), 1)
        self.assertEqual(person_score(event, dict(cfg, _interaction_ids=[]), self.old), .25)
        # Maintenance callers lack the request-local map, retaining strict identity.
        self.assertEqual(person_score(event, self.core.config_for(self.sid), self.new), 0)
        self.assertNotIn('_recall_person_keys', self.core.config_for(self.sid))

    async def test_identity_changes_during_embedding_are_rechecked(self):
        for change in ('uid', 'participants', 'focus'):
            for facade in ('retrieve', 'context', 'search'):
                with self.subTest(change=change, facade=facade):
                    self.core.update_person(self.sid, self.old, PersonInput(name='旧人物', platform='snowluma', external_uid='42'))
                    cfg = self.core.config_for(self.sid)
                    self.core.store.put('config', dict(cfg, id=self.sid, focus_person_id=self.new), self.sid)
                    event = self.event([self.old])
                    async def query(sid, dialogue, events):
                        if change == 'uid':
                            self.core.update_person(sid, self.old, PersonInput(name='旧人物', platform='snowluma', external_uid='99'))
                        elif change == 'participants':
                            self.core.update_event(sid, event['id'], EventInput(title='陨石一号', body='陨石一号烤面包失败', person_ids=[self.other], salience=1))
                        else:
                            self.core.store.put('config', dict(cfg, id=sid, focus_person_id=self.other), sid)
                        return {event['id']: .99}, {'status':'ok', 'candidate_count':1, 'elapsed_ms':0}
                    with patch.object(self.core.embeddings, 'query', side_effect=query):
                        args = dict(query='陨石一号', retrieval_mode='hybrid')
                        if facade == 'retrieve':
                            result = await self.core.retrieve(self.sid, RecallInput(**args))
                        else:
                            result = await self.core.recall(self.sid, (SearchInput if facade == 'search' else ContextInput)(**args), search_only=facade == 'search')
                    self.assertNotIn(event['id'], [x['event_id'] for x in result['items' if facade == 'search' else 'candidates']])
