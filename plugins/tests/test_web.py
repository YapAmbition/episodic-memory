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

    async def test_managed_config_and_fake_messages_cannot_be_changed(self):
        for method, path, body in [
            ("post", "/api/v1/spaces", {"name": "fake"}),
            ("put", self.base + "/config", {}),
            ("put", self.base + "/model", {}),
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
