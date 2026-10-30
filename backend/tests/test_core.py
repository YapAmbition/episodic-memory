"""Exercise the shared core without FastAPI, sockets, or a provider service."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, ConversationInput, MessageInput, ModelInput, MaintenanceInput, ContextInput
from memory_slice.errors import MemoryError


class RecordingModel:
    def __init__(self):
        self.calls = []

    async def complete(self, settings, api_key, messages, **options):
        self.calls.append((messages, options))
        if options['purpose'] == 'query_expansion':
            return json.dumps({'details': ['陨石一号']})
        if options['purpose'] == 'association':
            data = json.loads(messages[1]['content'])
            item = data['catalog'][0]
            return json.dumps({'matches': [{'event_id': item['id'], 'score': .95,
                                           'reason': '明确原话', 'evidence': '陨石一号'}]})
        data = json.loads(messages[1]['content'])
        source = data['new_messages'][0]
        return json.dumps({'events': [{'title': '陨石一号面包', 'body': source['content'],
            'person_ids': [source['person_id']], 'source_ids': [source['id']],
            'anchors': {'details': ['陨石一号']}}]})


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = RecordingModel()
        self.service = MemoryService(self.temp.name, model=self.adapter)
        await self.service.start()
        self.sid = self.service.add_space(SpaceInput(name='独立核心测试'))['id']
        self.pid = self.service.persons(self.sid)[0]['id']
        self.cid = self.service.add_conversation(self.sid, ConversationInput(person_id=self.pid))['id']

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def finished(self, job):
        for _ in range(100):
            value = self.service.get_job(self.sid, job['id'])
            if value['status'] not in ('queued', 'running'):
                return value
            await asyncio.sleep(.01)
        self.fail('job did not finish')

    async def learn(self):
        self.service.put_model(self.sid, ModelInput(mode='llm', model='host-provided'))
        self.service.add_message(self.sid, self.cid, MessageInput(role='user', content='昨天烤面包失败，叫它陨石一号。'))
        job = await self.finished(self.service.enqueue(self.sid, MaintenanceInput()))
        self.assertEqual(job['status'], 'completed', job)
        return job

    async def test_embedded_maintenance_and_local_recall_use_no_http(self):
        await self.learn()
        result = await self.service.recall(self.sid, ContextInput(query='陨石一号'))
        self.assertEqual(result['effective_mode'], 'local')
        self.assertIn('陨石一号', result['context'])
        self.assertEqual([call[1]['purpose'] for call in self.adapter.calls], ['maintenance'])

    async def test_hybrid_without_embeddings_does_not_call_chat_model(self):
        await self.learn()
        result = await self.service.recall(self.sid, ContextInput(query='陨石一号', retrieval_mode='hybrid'))
        self.assertEqual(result['effective_mode'], 'local')
        self.assertEqual([call[1]['purpose'] for call in self.adapter.calls], ['maintenance'])

    async def test_start_is_idempotent_and_close_cancels_jobs(self):
        first = self.service.scheduler_task
        await self.service.start()
        self.assertIs(first, self.service.scheduler_task)
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.sleep(100)
        self.adapter.complete = blocked
        self.service.put_model(self.sid, ModelInput(mode='llm', model='host-provided'))
        self.service.add_message(self.sid, self.cid, MessageInput(role='user', content='等待整理'))
        job = self.service.enqueue(self.sid, MaintenanceInput())
        await asyncio.wait_for(entered.wait(), 2)
        await self.service.close()
        self.assertTrue(first.cancelled())
        self.assertFalse(self.service.background_tasks)
        self.assertEqual(self.service.get_job(self.sid, job['id'])['status'], 'deferred')
        with self.assertRaises(RuntimeError):
            self.service.enqueue(self.sid, MaintenanceInput())
        self.adapter.complete = RecordingModel().complete
        await self.service.start()
        recovered = await self.finished(job)
        self.assertEqual(recovered['status'], 'deferred', recovered)
        self.assertTrue(self.service.store.get('integration','maintenance-safety',self.sid)['blocked'])

    async def test_instances_are_isolated_and_errors_are_domain_exceptions(self):
        with tempfile.TemporaryDirectory() as other:
            service = MemoryService(other)
            self.assertEqual(service.spaces(), [])
            with self.assertRaises(MemoryError) as caught:
                service.get_config(self.sid)
            self.assertEqual(caught.exception.status_code, 404)
            await service.close()

    async def test_legacy_vector_data_is_preserved_but_unused(self):
        await self.service.close()
        store = self.service.store
        cfg = store.get('config', self.sid)
        cfg['default_retrieval_mode'] = 'llm'
        store.put('config', cfg, self.sid)
        store.put('embedding', {'id': self.sid, 'model': 'obsolete', 'enabled': True}, self.sid)
        with store.connect() as con:
            con.execute('CREATE TABLE embedding_vectors (payload TEXT)')
            con.execute("INSERT INTO embedding_vectors VALUES ('legacy')")
        before = store.list('message', self.sid), store.list('event', self.sid)
        self.service = MemoryService(self.temp.name)
        self.assertEqual(self.service.config_for(self.sid)['default_retrieval_mode'], 'local')
        self.assertEqual(before, (store.list('message', self.sid), store.list('event', self.sid)))
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT payload FROM embedding_vectors').fetchone()[0], 'legacy')
        self.assertNotIn('embedding', self.service.capabilities(self.sid))
        self.assertIn('memory_embedding_', self.service.metrics.prometheus().decode())
        self.assertEqual((await self.service.recall(self.sid, ContextInput(query='旧事', retrieval_mode='auto')))['effective_mode'], 'local')

    async def test_old_index_job_is_not_resumed(self):
        await self.service.close()
        self.service.store.put('job', {'id':'legacy_job','space_id':self.sid,'status':'queued','type':'embedding_index'}, self.sid)
        await self.service.start()
        self.assertEqual(self.service.get_job(self.sid,'legacy_job')['status'],'failed')
        with self.assertRaises(MemoryError) as caught:
            await self.service.retry_job(self.sid,'legacy_job')
        self.assertEqual(caught.exception.status_code,410)


class ImportBoundaryTests(unittest.TestCase):
    def test_core_import_and_run_without_web_frameworks(self):
        script = '''
import importlib.abc, sys, asyncio, tempfile
class BlockWeb(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi','starlette','uvicorn'}:
            raise AssertionError('Core imported web framework: '+fullname)
sys.meta_path.insert(0,BlockWeb())
from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, ContextInput
async def run():
    with tempfile.TemporaryDirectory() as folder:
        service=MemoryService(folder)
        await service.start()
        sid=service.add_space(SpaceInput(name='No HTTP'))['id']
        assert (await service.recall(sid,ContextInput(query='hello')))['effective_mode']=='local'
        await service.close()
asyncio.run(run())
'''
        result = subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_http_adapter_exposes_embedding_endpoints(self):
        from app.main import create_app
        with tempfile.TemporaryDirectory() as folder:
            paths = create_app(folder,seed=False).openapi()['paths']
            self.assertIn('/api/v1/spaces/{sid}/embedding', paths)
            self.assertIn('/api/v1/spaces/{sid}/context',paths)
