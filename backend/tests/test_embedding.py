"""Remote embedding contracts and safe degradation; no external network."""

import asyncio
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, EventInput, ContextInput, EmbeddingInput
from memory_slice.embedding import Unavailable


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.core = MemoryService(self.temp.name)
        self.sid = self.core.add_space(SpaceInput(name="Embedding test"))["id"]
        self.manager = self.core.embeddings
        self.calls = []
        self.manager.transport = httpx.MockTransport(self.ok)
        self.cfg = dict(
            enabled=True,
            base_url="https://embedding.invalid/v1",
            model="test",
            api_key="secret-key",
            timeout_seconds=0.1,
            failure_threshold=2,
            cooldown_seconds=1,
        )
        self.manager.configure(self.sid, EmbeddingInput(**self.cfg))
        self.event = self.core.create_event(
            self.sid,
            EventInput(
                title="眼镜在领口", body="翻遍全屋，眼镜一直挂在领口。", salience=0.9
            ),
        )

    async def asyncTearDown(self):
        await self.core.close()
        self.temp.cleanup()

    def ok(self, request):
        self.calls.append(request)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, 0.0]}
                    for i in reversed(range(len(payload["input"])))
                ]
            },
        )

    async def recall(self, mode="hybrid"):
        return await self.core.recall(
            self.sid, ContextInput(query="眼镜在领口", retrieval_mode=mode)
        )

    async def test_semantics_can_supply_entry_without_shared_keywords(self):
        await self.manager.index_once(self.sid)
        result = await self.core.recall(
            self.sid,
            ContextInput(query="电脑排查半天才发现没开电源", retrieval_mode="hybrid"),
        )
        self.assertEqual(result["effective_mode"], "hybrid")
        self.assertIn(self.event["id"], result["seeds"])
        self.assertIn("embedding", result["retrieval"]["routes"][self.event["id"]])
        self.assertIn("眼镜一直挂在领口", result["context"])
        self.assertTrue(all(r.url.path == "/v1/embeddings" for r in self.calls))

    async def test_failure_matrix_preserves_local_result_and_hides_provider_secrets(
        self,
    ):
        await self.manager.index_once(self.sid)
        local = await self.recall("local")
        cases = [
            (401, "auth"),
            (403, "auth"),
            (429, "rate_limited"),
            (500, "http_error"),
            (302, "http_error"),
        ]
        for status, reason in cases:
            with self.subTest(status=status):
                self.manager.state(self.sid).update(failures=0, until=0)
                self.manager.transport = httpx.MockTransport(
                    lambda r: httpx.Response(status, text="secret-key provider detail")
                )
                result = await self.recall()
                self.assertEqual(result["selected"], local["selected"])
                self.assertEqual(result["effective_mode"], "local")
                self.assertTrue(result["degraded"])
                self.assertEqual(result["route_status"]["embedding"]["status"], reason)
                self.assertNotIn("secret-key", json.dumps(result))
        self.assertNotIn("secret-key", json.dumps(self.manager.status(self.sid)))
        self.assertNotIn(
            "secret-key", json.dumps(self.core.store.list("embedding_config", self.sid))
        )
        self.assertNotIn("secret-key", json.dumps(self.core.metrics.snapshot()))

    async def test_timeout_and_network_errors_are_bounded(self):
        await self.manager.index_once(self.sid)

        async def slow(request):
            await asyncio.sleep(10)

        def broken(request):
            raise httpx.ConnectError("secret-key", request=request)

        for handler, reason in ((slow, "timeout"), (broken, "network")):
            self.manager.transport = httpx.MockTransport(handler)
            start = time.monotonic()
            result = await self.recall()
            self.assertLess(time.monotonic() - start, 0.8)
            self.assertEqual(result["route_status"]["embedding"]["status"], reason)
            self.assertTrue(result["selected"])

    async def test_malformed_vectors_fall_back(self):
        await self.manager.index_once(self.sid)
        payloads = [
            None,
            {},
            {"data": []},
            {"data": [{"index": 1, "embedding": [1, 0]}]},
            {"data": [{"index": 0, "embedding": [0, 0]}]},
            {"data": [{"index": 0, "embedding": [True, 0]}]},
            {"data": [{"index": 0, "embedding": ["x", 0]}]},
            {"data": [{"index": 0, "embedding": []}]},
            {"data": [{"index": 0, "embedding": [1e308, 1e308]}]},
        ]
        for payload in payloads:
            self.manager.state(self.sid).update(failures=0, until=0)
            self.manager.transport = httpx.MockTransport(
                lambda r: httpx.Response(200, json=payload)
            )
            result = await self.recall()
            self.assertEqual(
                result["route_status"]["embedding"]["status"],
                "invalid_response",
                payload,
            )
            self.assertTrue(result["selected"])
        for raw in (b"not json", b'{"data":[{"index":0,"embedding":[NaN,1]}]}'):
            self.manager.state(self.sid).update(failures=0, until=0)
            self.manager.transport = httpx.MockTransport(
                lambda r: httpx.Response(200, content=raw)
            )
            self.assertEqual((await self.recall())["effective_mode"], "local")

    async def test_circuit_skips_requests_and_recovers_after_one_probe(self):
        await self.manager.index_once(self.sid)
        calls = []

        def fail(r):
            calls.append(r)
            return httpx.Response(503)

        self.manager.transport = httpx.MockTransport(fail)
        await self.recall()
        await self.recall()
        self.assertEqual(
            (await self.recall())["route_status"]["embedding"]["status"], "circuit_open"
        )
        self.assertEqual(len(calls), 2)
        self.manager.state(self.sid)["until"] = time.monotonic() - 1
        entered, release = asyncio.Event(), asyncio.Event()

        async def probe(r):
            entered.set()
            await release.wait()
            return self.ok(r)

        self.manager.transport = httpx.MockTransport(probe)
        first = asyncio.create_task(self.recall())
        await entered.wait()
        self.assertEqual(
            (await self.recall())["route_status"]["embedding"]["status"], "circuit_open"
        )
        release.set()
        self.assertEqual((await first)["effective_mode"], "hybrid")
        self.assertFalse(self.manager.status(self.sid)["circuit_open"])
        self.assertIsNone(self.manager.status(self.sid)["last_error"])
        self.assertEqual((await self.recall())["effective_mode"], "hybrid")

    async def test_pending_edit_archive_and_model_switch_never_use_stale_vectors(self):
        self.assertEqual(
            (await self.recall())["route_status"]["embedding"]["status"],
            "index_pending",
        )
        await self.manager.index_once(self.sid)
        self.core.update_event(
            self.sid, self.event["id"], EventInput(title="新版本", body="修改后的正文")
        )
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 0)
        await self.manager.index_once(self.sid)
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 1)
        self.manager.configure(
            self.sid, EmbeddingInput(**dict(self.cfg, model="another-model"))
        )
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 0)
        await self.manager.index_once(self.sid)
        self.core.delete_event(self.sid, self.event["id"])
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 0)
        await self.manager.index_once(self.sid)
        self.assertEqual(self.core.store.list("embedding_vector", self.sid), [])

    async def test_dimension_change_invalidates_then_rebuilds(self):
        await self.manager.index_once(self.sid)
        self.manager.transport = httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [1, 0, 0]}
                        for i, _ in enumerate(json.loads(r.content)["input"])
                    ]
                },
            )
        )
        self.assertEqual(
            (await self.recall())["route_status"]["embedding"]["status"],
            "dimension_changed",
        )
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 0)
        await self.manager.index_once(self.sid)
        self.assertEqual((await self.recall())["effective_mode"], "hybrid")

    async def test_partial_index_and_tail_chunks(self):
        self.core.create_event(
            self.sid, EventInput(title="长记忆", body="正文" * 6000 + "末尾重要细节")
        )
        await self.manager.index_once(self.sid)
        self.assertGreater(self.manager.status(self.sid)["pending_chunks"], 0)
        self.assertEqual(
            (await self.recall())["route_status"]["embedding"]["status"], "partial"
        )
        await self.manager.index_once(self.sid)
        texts = [text for r in self.calls for text in json.loads(r.content)["input"]]
        self.assertTrue(any("末尾重要细节" in t for t in texts))

    async def test_config_change_during_request_discards_result(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(r):
            entered.set()
            await release.wait()
            return self.ok(r)

        self.manager.transport = httpx.MockTransport(delayed)
        task = asyncio.create_task(self.manager.index_once(self.sid))
        await entered.wait()
        self.manager.configure(
            self.sid, EmbeddingInput(**dict(self.cfg, model="changed"))
        )
        release.set()
        with self.assertRaises(Unavailable):
            await task
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 0)
        self.assertIsNone(self.manager.status(self.sid)["last_error"])

    async def test_identical_configuration_does_not_reset_breaker(self):
        self.manager.state(self.sid).update(failures=2, until=time.monotonic() + 30)
        self.manager.configure(self.sid, EmbeddingInput(**self.cfg))
        self.assertTrue(self.manager.status(self.sid)["circuit_open"])

    async def test_cancel_releases_slot_without_marking_provider_failed(self):
        entered = asyncio.Event()

        async def slow(r):
            entered.set()
            await asyncio.sleep(10)

        self.manager.transport = httpx.MockTransport(slow)
        task = asyncio.create_task(self.manager.index_once(self.sid))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.manager.state(self.sid)["failures"], 0)
        self.manager.transport = httpx.MockTransport(self.ok)
        await self.manager.index_once(self.sid)
        self.assertEqual(self.manager.status(self.sid)["indexed_chunks"], 1)

    async def test_local_never_contacts_embedding_provider(self):
        with patch("httpx.AsyncClient", side_effect=AssertionError("remote")):
            self.assertEqual((await self.recall("local"))["effective_mode"], "local")


import test_memory as fixtures


class EmbeddingAPITests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    create_event = fixtures.APITests.create_event

    def test_configuration_errors_never_echo_keys(self):
        response = self.client.put(
            self.prefix + "/embedding", json={"enabled": True, "api_key": "do-not-echo"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("do-not-echo", response.text)

    def test_embedding_outage_does_not_break_chat_or_monitoring(self):
        self.create_event()
        manager = self.app.state.memory.embeddings
        manager.transport = httpx.MockTransport(
            lambda r: httpx.Response(401, text="do-not-echo")
        )
        saved = self.client.put(
            self.prefix + "/embedding",
            json={
                "enabled": True,
                "base_url": "https://embedding.invalid/v1",
                "model": "mock",
                "api_key": "do-not-echo",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn("api_key", saved.json())
        self.assertEqual(
            self.client.post(self.prefix + "/embedding/test").json(),
            {"ok": False, "reason": "auth"},
        )
        result = self.client.post(
            self.prefix + "/conversations/" + self.cid + "/chat",
            json={
                "content": "陨石一号",
                "at": fixtures.NOW.isoformat(),
                "retrieval_mode": "hybrid",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["recall"]["effective_mode"], "local")
        self.assertTrue(result.json()["recall"]["selected"])
        self.assertEqual(
            result.json()["recall"]["route_status"]["embedding"]["status"], "auth"
        )
        snapshot = self.client.get("/api/v1/monitoring").json()
        self.assertEqual(snapshot["embedding"][0]["last_error"], "auth")
        self.assertNotIn("do-not-echo", json.dumps(snapshot))
        self.assertIn(
            'memory_embedding_requests_total{outcome="auth",purpose="test"} 1.0',
            self.client.get("/metrics").text,
        )
