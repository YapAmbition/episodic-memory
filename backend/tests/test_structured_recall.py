import copy
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import create_app
from memory_slice.engine import retrieve_events
from memory_slice.context_assembly import assemble_context
from memory_slice.schemas import MemoryConfig
from test_memory import NOW, event


class StructuredRecallTests(unittest.TestCase):
    def test_limits_are_separate_from_injection_budget(self):
        cfg = MemoryConfig(max_events=1, context_char_budget=1024).model_dump()
        events = [event(str(i), age=0) for i in range(15)]
        result = retrieve_events(events, None, '陨石一号', NOW, cfg)
        self.assertEqual(result['returned_count'], 8)
        self.assertTrue(result['truncated'])
        self.assertNotIn('context', result)
        self.assertEqual(len(retrieve_events(events, None, '陨石一号', NOW, cfg, limit=20)['items']), 15)
        cfg['recall_default_limit'] = 4
        self.assertEqual(len(retrieve_events(events, None, '陨石一号', NOW, cfg)['items']), 4)
        for limit in (0, 121, True):
            with self.assertRaises(ValueError):
                retrieve_events(events, None, '', NOW, cfg, limit=limit)

    def test_no_padding_and_lossless_fragments(self):
        cfg = MemoryConfig().model_dump()
        result = retrieve_events([event(age=0)], None, 'xyz987', NOW, cfg)
        self.assertEqual(result['items'], [])
        e = event(age=0, body='陨石一号。' * 4000)
        result = retrieve_events([e], None, '陨石一号', NOW, cfg)
        parts = result['items'][0]['fragments']
        self.assertEqual(''.join(p['text'] for p in parts), e['body'])
        self.assertTrue(all('score' in p and 'dependencies' in p for p in parts))

    def test_host_dedup_does_not_change_retrieval(self):
        cfg = MemoryConfig().model_dump()
        raw = retrieve_events([event(age=0)], None, '陨石一号', NOW, cfg)
        original = copy.deepcopy(raw)
        first = assemble_context(raw, cfg)
        self.assertTrue(first['selected'])
        second = assemble_context(raw, cfg, [first['context']])
        self.assertEqual(second['context'], '')
        self.assertEqual(raw, original)

    def test_http_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory, seed=False)) as client:
                sid = client.post('/api/v1/spaces', json={'name': 'raw recall'}).json()['id']
                url = f'/api/v1/spaces/{sid}/recall'
                result = client.post(url, json={'query': '生日'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['items'], [])
                self.assertEqual(result.json()['limit'], 8)
                for body in ({'current_context': []}, {'limit': 0}, {'limit': 121}):
                    self.assertEqual(client.post(url, json=body).status_code, 422)

    def test_http_nonempty_and_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory, seed=False)) as client:
                sid = client.post('/api/v1/spaces', json={'name': 'recall feedback'}).json()['id']
                base = f'/api/v1/spaces/{sid}'
                created = client.post(base + '/events', json={'title': '陨石一号', 'body': '生日烤面包陨石一号', 'salience': 1})
                self.assertEqual(created.status_code, 201, created.text)
                result = client.post(base + '/recall', json={'query': '生日烤面包陨石一号'}).json()
                self.assertEqual(len(result['items']), 1)
                response = client.put(base + '/recalls/' + result['id'] + '/feedback', json={'useful_ids': [created.json()['id']]})
                self.assertEqual(response.status_code, 200, response.text)

    def test_delete_event_after_structured_recall_removes_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory, seed=False)) as client:
                sid = client.post('/api/v1/spaces', json={'name': 'delete recall'}).json()['id']
                base = f'/api/v1/spaces/{sid}'
                eid = client.post(base + '/events', json={'title': '陨石一号', 'body': '陨石一号烤面包', 'salience': 1}).json()['id']
                result = client.post(base + '/recall', json={'query': '陨石一号'}).json()
                self.assertTrue(result['items'])
                self.assertEqual(client.delete(base + '/events/' + eid).status_code, 200)
                self.assertEqual(client.get(base + '/recalls/' + result['id']).status_code, 404)
