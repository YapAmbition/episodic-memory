"""Program and model recall regression tests."""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

import httpx

import test_memory as fixtures

NOW = fixtures.NOW


class RetrievalTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    create_event = fixtures.APITests.create_event

    def context(self, **extra):
        return self.client.post(self.prefix + '/context', json=dict(person_id=self.person, query='电脑折腾半天，电源居然没开', at=NOW.isoformat(), **extra))


    def test_unconfigured_default_and_read_endpoints_never_call_models(self):
        self.create_event()
        with patch('httpx.AsyncClient', side_effect=AssertionError('unexpected remote call')):
            response = self.context(trace=True)
            self.assertEqual(response.status_code, 200, response.text)
            trace = response.json()
            self.assertEqual(trace['requested_mode'], 'local')
            self.assertEqual(trace['effective_mode'], 'local')
            self.assertFalse(trace['degraded'])
            self.assertIn('trace_persistence', trace['timings_ms'])
            for path in ('/capabilities', '/recalls/' + trace['id']):
                self.assertEqual(self.client.get(self.prefix + path).status_code, 200)
            self.assertEqual(self.client.get('/api/v1/monitoring').status_code, 200)

    def test_chat_model_does_not_turn_on_model_recall(self):
        self.create_event()
        self.client.put(self.prefix + '/model', json={'mode': 'llm', 'model': 'chat-model'})
        purposes = []
        async def complete(*args, **kwargs):
            purposes.append(kwargs['purpose'])
            return '这是聊天回复。'
        with patch('memory_slice.model.complete', complete), patch('memory_slice.service.expand_query', side_effect=AssertionError('expansion')), patch('memory_slice.service.association_matches', side_effect=AssertionError('association')):
            result = self.client.post(self.prefix + '/conversations/' + self.cid + '/chat', json={'content': '陨石一号', 'at': NOW.isoformat()})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(purposes, ['chat'])
        self.assertEqual(result.json()['recall']['effective_mode'], 'local')

    def test_legacy_modes_and_conflict_are_explicit(self):
        self.assertEqual(self.context(use_model=False).json()['requested_mode'], 'local')
        self.assertEqual(self.context(use_model=True).status_code, 422)
        self.assertEqual(self.context(use_model=False, retrieval_mode='auto').status_code, 422)
        self.assertEqual(self.context(retrieval_mode='hybrid').status_code, 422)

    def test_route_switches_and_default_mode_are_applied(self):
        self.create_event()
        config = self.client.get(self.prefix + '/config').json()
        config.update(default_retrieval_mode='local', retrieval_routes={'lexical': False, 'person_salience': False, 'temporal': False})
        self.client.put(self.prefix + '/config', json=config)
        result = self.context().json()
        self.assertEqual(result['requested_mode'], 'local')
        self.assertEqual(result['selected'], [])
        self.assertEqual(result['route_status']['lexical']['status'], 'disabled')


    def test_cancelling_disconnected_recall_releases_pending_operation(self):
        from app.main import cancellable_recall
        from memory_slice.errors import MemoryError as HTTPException
        finished = []
        class Request:
            async def is_disconnected(self):
                return True
        async def operation():
            try:
                await asyncio.sleep(30)
            finally:
                finished.append(True)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(cancellable_recall(Request(), operation()))
        self.assertEqual(caught.exception.status_code, 499)
        self.assertEqual(finished, [True])


if __name__ == '__main__':
    unittest.main()
