import asyncio
import signal
import socket
import tempfile
import unittest
from pathlib import Path

import httpx
from test_astrbot_plugin import Runtime, Host, Event, PACKAGE
from astrbot_plugin_memory_slice.memory_slice.web import create_app
from astrbot_plugin_memory_slice.memory_slice.schemas import EventInput
from astrbot_plugin_memory_slice.web_server import WebServer

TOKEN = "test-dashboard-token-12345"
FRONTEND = Path(PACKAGE.name) / "astrbot_plugin_memory_slice" / "frontend"


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Static-serving tests do not require building a distributable plugin ZIP.
        (FRONTEND/"assets").mkdir(parents=True, exist_ok=True)
        if not (FRONTEND/'index.html').exists():
            (FRONTEND/'index.html').write_text('<!doctype html><title>Memory Slice test</title>')
        self.runtime = Runtime(Host(), {"auto_maintain": False}, self.tmp.name)
        await self.runtime.start()
        self.record = await self.runtime.ingest(Event())
        self.sid = self.record["space_id"]
        self.base = "/api/v1/spaces/" + self.sid
        self.app = create_app(
            service=self.runtime.core,
            host_mode="astrbot",
            access_token=TOKEN,
            frontend_dir=FRONTEND,
            scope_info=self.runtime.scope_info,
            pinned_memories=self.runtime.pins,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + TOKEN},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.runtime.close()
        self.tmp.cleanup()

    async def test_console_context_includes_old_platform_person(self):
        from astrbot_plugin_memory_slice.memory_slice.schemas import PersonInput
        core = self.runtime.core
        old = core.add_person(self.sid, PersonInput(name="旧人物", platform="snowluma", external_uid="42"))
        new = core.add_person(self.sid, PersonInput(name="新人物", platform="napcat", external_uid="42"))
        cfg = core.config_for(self.sid)
        core.store.put("config", dict(cfg, id=self.sid, focus_person_id=new["id"]), self.sid)
        event = core.create_event(self.sid, EventInput(title="陨石一号", body="陨石一号烤面包失败了",
            person_ids=[old["id"]], salience=1, anchors={"details": ["陨石一号"]}))
        payload = {"query": "陨石一号", "retrieval_mode": "local", "trace": True}
        previous = await self.client.post(self.base+"/context", json=payload)
        self.assertEqual(previous.status_code, 200, previous.text)
        self.assertNotIn(event["id"], previous.json()["context"])
        spaces = (await self.client.get("/api/v1/spaces")).json()
        policy = next(s for s in spaces if s["id"] == self.sid)["host"]["injection_policy"]
        payload["filter_by_focus"] = policy["filter_by_focus"]
        response = await self.client.post(self.base+"/context", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(event["id"], response.json()["context"])
        self.assertEqual(core.store.get("event", event["id"], self.sid), event)
        self.runtime.config["recall_filter_by_focus"] = True
        payload["filter_by_focus"] = self.runtime.scope_info(self.sid)["injection_policy"]["filter_by_focus"]
        restricted = await self.client.post(self.base+"/context", json=payload)
        self.assertNotIn(event["id"], restricted.json()["context"])

    async def test_recovery_records_are_authenticated_readonly_and_portable(self):
        core=self.runtime.core
        message=core.store.list('message',self.sid)[0]
        core.store.put('message',dict(message,role='unknown',content='需要保留的原始正文'),self.sid)
        destination=(await self.runtime.bind(Event(platform='recovery-target')))['space_id']
        body={'target_space_id':destination}
        plan=await self.client.post(self.base+'/copy/preview',json=body)
        self.assertGreater(plan.json()['compensation']['pending'],0)
        body['preview_token']=plan.json()['preview_token']
        copied=await self.client.post(self.base+'/copy',json=body)
        self.assertEqual(copied.status_code,200,copied.text)
        url='/api/v1/spaces/'+destination+'/recovery'
        denied=await self.client.get(url,headers={'Authorization':'Bearer wrong'})
        self.assertEqual(denied.status_code,401)
        response=await self.client.get(url+'?limit=1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['items']),1)
        items=(await self.client.get(url)).json()['items']
        self.assertTrue(any(x['status']=='pending' and x['payload'].get('content')=='需要保留的原始正文' for x in items))
        self.assertEqual((await self.client.get(url+'?limit=1000')).status_code,422)
        self.assertEqual((await self.client.delete(url)).status_code,405)

    async def test_transfer_authenticated_roundtrip(self):
        self.runtime.core.create_event(self.sid, EventInput(title='迁移约定',body='下周一起散步'))
        package=await self.client.get(self.base+'/transfer')
        self.assertEqual(package.status_code,200,package.text)
        new=await self.runtime.bind(Event(platform='new-transfer-platform'))
        operation=await self.client.post('/api/v1/transfers/operation-id',json={})
        self.assertEqual(operation.status_code,200)
        body=dict(package=package.json(),target_space_id=new['space_id'],operation_id=operation.json()['operation_id'])
        denied=await self.client.post('/api/v1/transfers/preview',json=body,headers={'Authorization':'Bearer bad'})
        self.assertEqual(denied.status_code,401)
        preview=await self.client.post('/api/v1/transfers/preview',json=body)
        self.assertEqual(preview.status_code,200,preview.text)
        body['preview_token']=preview.json()['preview_token']
        result=await self.client.post('/api/v1/transfers/import',json=body)
        self.assertEqual(result.status_code,200,result.text)
        repeated=await self.client.post('/api/v1/transfers/import',json=body)
        self.assertEqual(repeated.json(),result.json())
        status=await self.client.get('/api/v1/transfers/operations/'+body['operation_id'])
        self.assertEqual(status.json(),result.json())
        self.assertEqual((await self.runtime.bind(Event()))['space_id'],self.sid)
        self.assertEqual((await self.runtime.bind(Event(platform='new-transfer-platform')))['space_id'],new['space_id'])
        self.assertEqual(len(self.runtime.core.store.list('event',new['space_id'])),1)
        bad=await self.client.post('/api/v1/transfers/preview',content=b'{broken')
        self.assertEqual(bad.status_code,422)

    async def test_space_copy_api_authenticated(self):
        new=await self.runtime.bind(Event(platform='new-platform'))
        body={'target_space_id':new['space_id']}
        preview=await self.client.post(self.base+'/copy/preview',json=body)
        self.assertEqual(preview.status_code,200,preview.text)
        body['preview_token']=preview.json()['preview_token']
        denied=await self.client.post(self.base+'/copy',json=body,headers={'Authorization':'Bearer wrong'})
        self.assertEqual(denied.status_code,401)
        response=await self.client.post(self.base+'/copy',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['space_id'],new['space_id'])
        self.assertEqual((await self.client.get('/api/v1/bindings')).status_code,404)

    async def test_pinned_and_frozen_are_independent(self):
        e = self.runtime.core.create_event(self.sid, EventInput(title="约定", body="每周三浇花"))
        url = self.base + '/events/' + e['id']
        response = await self.client.put(url+'/freeze', json={'frozen': True, 'expected_version': e['version']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.runtime.pins.ids(self.sid), [])
        self.assertEqual((await self.client.put(self.base+'/pinned-memories', json=[e['id']])).status_code,200)
        wire = await self.runtime.recall(Event('聊点别的', mid='2'))
        self.assertIn('每周三浇花',wire)
        self.assertEqual((await self.client.put(url,json={'title':'改写','body':'不允许'})).status_code,409)
        self.assertEqual((await self.client.put(self.base+'/pinned-memories', json=[])).status_code,200)
        self.assertTrue((await self.client.get(url)).json()['frozen'])
        self.assertEqual((await self.client.put(url+'/freeze',json={'frozen':False})).status_code,200)
        self.assertEqual((await self.client.put(url,json={'title':'改写','body':'允许'})).status_code,200)

    async def test_pinned_selection_api(self):
        url = self.base + '/pinned-memories'
        self.assertEqual((await self.client.get(url)).json()['event_ids'], [])
        e = self.runtime.core.create_event(self.sid, EventInput(title="约定", body="完整正文"))
        response = await self.client.put(url, json=[e['id']])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['event_ids'], [e['id']])
        for invalid in [[e['id']] * 4, ['missing']]:
            self.assertEqual((await self.client.put(url, json=invalid)).status_code, 422)
            self.assertEqual((await self.client.get(url)).json()['event_ids'], [e['id']])
        self.assertEqual((await self.client.put(url, json=[], headers={'Authorization': 'Bearer invalid'})).status_code, 401)
        self.assertEqual((await self.client.put(url, json=[])).json()['event_ids'], [])

    async def test_web_uses_exact_same_instance_without_owning_lifecycle(self):
        scheduler = self.runtime.core.scheduler_task
        async with self.app.router.lifespan_context(self.app):
            self.assertIs(self.app.state.memory, self.runtime.core)
            self.assertIs(self.runtime.core.scheduler_task, scheduler)
        self.assertFalse(scheduler.done())
        self.assertFalse(self.runtime.core._closed)
        await self.runtime.ingest(Event(mid="2", text="第二条实时消息"))
        messages = (
            await self.client.get(
                self.base
                + "/conversations/"
                + self.record["conversation_id"]
                + "/messages"
            )
        ).json()
        self.assertEqual(len(messages), 2)

    async def test_api_metrics_protected_but_static_login_page_available(self):
        for path in [
            "/api/v1/spaces",
            "/api/v1/runtime",
            "/metrics",
            self.base + "/events",
        ]:
            self.assertEqual(
                (
                    await self.client.get(
                        path, headers={"Authorization": "Bearer invalid"}
                    )
                ).status_code,
                401,
            )
        self.assertEqual((await self.client.get("/")).status_code, 200)
        self.assertEqual((await self.client.get("/metrics")).status_code, 200)
        self.assertEqual(
            (await self.client.get("/api/v1/runtime")).json()["mode"], "astrbot"
        )

    async def test_embedding_status_and_monitoring_are_readable(self):
        response = await self.client.get(self.base + "/embedding")
        self.assertEqual(response.status_code,200)
        self.assertNotIn("api_key",response.json())
        snapshot = (await self.client.get("/api/v1/monitoring")).json()
        self.assertEqual(snapshot["embedding"][0]["space_id"],self.sid)
        self.assertIn("memory_embedding_open_circuits",(await self.client.get("/metrics")).text)

    async def test_managed_config_and_fake_messages_cannot_be_changed(self):
        for method, path, body in [
            ("post", "/api/v1/spaces", {"name": "fake"}),
            ("put", self.base + "/config", {}),
            ("put", self.base + "/model", {}),
            ("put", self.base + "/embedding", {}),
            ("post", self.base + "/embedding/test", {}),
            ("post", self.base + "/persons", {"name": "fake"}),
            ("post", self.base + "/conversations", {}),
            (
                "post",
                self.base
                + "/conversations/"
                + self.record["conversation_id"]
                + "/messages",
                {"role": "user", "content": "fake"},
            ),
            (
                "post",
                self.base
                + "/conversations/"
                + self.record["conversation_id"]
                + "/chat",
                {"content": "fake"},
            ),
        ]:
            response = await getattr(self.client, method)(path, json=body)
            self.assertEqual(response.status_code, 403, (path, response.text))
        self.assertEqual(len(self.runtime.core.spaces()), 1)

    async def test_event_edits_and_manual_maintenance_visible_to_plugin(self):
        event = self.runtime.core.create_event(
            self.sid, EventInput(title="烤面包", body="第一次烤面包失败")
        )
        response = await self.client.put(
            self.base + "/events/" + event["id"],
            json={
                "title": "陨石一号",
                "body": "第一次面包，叫陨石一号",
                "expected_version": event["version"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            self.runtime.core.get_event(self.sid, event["id"])["title"], "陨石一号"
        )
        response = await self.client.post(self.base + "/maintenance", json={})
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()
        for _ in range(100):
            row = self.runtime.core.get_job(self.sid, job["id"])
            if row["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(row["status"], "completed", row)

    async def test_host_identity_and_inactive_space_cannot_schedule(self):
        spaces = (await self.client.get("/api/v1/spaces")).json()
        self.assertTrue(spaces[0]["host"]["active"])
        self.assertEqual(spaces[0]["host"]["kind"], "private")
        self.runtime.config["enabled"] = False
        response = await self.client.post(self.base + "/maintenance", json={})
        self.assertEqual(response.status_code, 409)

    async def test_real_server_stop_and_restart_releases_port_preserves_core_signals(
        self,
    ):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        before = signal.getsignal(signal.SIGINT)
        web = WebServer(self.runtime, {"web_token": TOKEN, "web_port": port}, FRONTEND)
        try:
            await web.start()
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"http://127.0.0.1:{port}/api/v1/runtime",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                self.assertEqual(response.status_code, 200)
            self.assertIs(signal.getsignal(signal.SIGINT), before)
            await web.close()
            self.assertFalse(self.runtime.core._closed)
            await web.start()
        finally:
            await web.close()
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    async def test_port_conflict_and_bad_token_do_not_exit_process(self):
        occupied = socket.socket()
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        web = WebServer(
            self.runtime,
            {"web_token": TOKEN, "web_port": occupied.getsockname()[1]},
            FRONTEND,
        )
        try:
            with self.assertRaises(OSError):
                await web.start()
            self.assertFalse(self.runtime.core._closed)
        finally:
            await web.close()
            occupied.close()
        with self.assertRaises(ValueError):
            await WebServer(self.runtime, {"web_token": ""}, FRONTEND).start()


if __name__ == "__main__":
    unittest.main()
