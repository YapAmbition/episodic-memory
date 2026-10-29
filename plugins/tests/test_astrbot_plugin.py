import asyncio
import importlib
import json
import sys
import tempfile
import time
import types
import unittest
import zipfile
import httpx
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_astrbot_plugin import build

PACKAGE = tempfile.TemporaryDirectory()
archive = build(Path(PACKAGE.name) / "plugin.zip", build_frontend=False)
with zipfile.ZipFile(archive) as z:
    z.extractall(PACKAGE.name)
sys.path.insert(0, PACKAGE.name)
from astrbot_plugin_memory_slice.runtime import Runtime
from astrbot_plugin_memory_slice.adapter import AstrBotModelAdapter
from astrbot_plugin_memory_slice.memory_slice.schemas import EventInput
from astrbot_plugin_memory_slice.memory_slice.errors import MemoryError
from astrbot_plugin_memory_slice.memory_slice.model import OutputLimitError


class Event:
    def __init__(
        self,
        text="昨天烤面包失败，叫它陨石一号。",
        uid="42",
        group="",
        platform="qq-main",
        mid="1",
    ):
        self.message_str, self.uid, self.group, self.platform = (
            text,
            uid,
            group,
            platform,
        )
        self.unified_msg_origin = platform + (
            ":GroupMessage:" + group if group else ":FriendMessage:" + uid
        )
        self.message_obj = types.SimpleNamespace(message_id=mid, timestamp=time.time())
        self.parts, self.extras = [], {}

    def get_platform_id(self):
        return self.platform

    def get_self_id(self):
        return "bot"

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.uid

    def get_sender_name(self):
        return "小明" if self.uid == "42" else "小红"

    def get_messages(self):
        return self.parts

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return text


class Host:
    def __init__(self):
        self.calls = []
        self.provider_queries = []
        self.failure = None

    async def get_current_chat_provider_id(self, umo):
        self.provider_queries.append(umo)
        return "configured-host-model"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        data = json.loads(kwargs["prompt"])
        source = data["new_messages"][0]
        result = {
            "events": [
                {
                    "title": "面包陨石一号",
                    "body": source["content"],
                    "person_ids": [source["person_id"]],
                    "source_ids": [source["id"]],
                    "anchors": {"details": ["陨石一号"]},
                    "focus_evidence": {
                        "message_id": source["id"],
                        "quote": source["content"],
                    },
                }
            ]
        }
        return types.SimpleNamespace(
            role="assistant",
            completion_text=json.dumps(result),
            usage=None,
            raw_completion=None,
        )


class PluginRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = Host()
        self.config = {"auto_maintain": False}
        self.runtime = Runtime(self.host, self.config, self.tmp.name)
        await self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.tmp.cleanup()

    async def finished(self, job, sid):
        for _ in range(200):
            row = self.runtime.core.get_job(sid, job["id"])
            if row["status"] not in ("queued", "running"):
                return row
            await asyncio.sleep(0.01)
        self.fail("job timeout")

    async def reset(self, **config):
        await self.runtime.close()
        self.config.update(config)
        self.runtime = Runtime(self.host, self.config, self.tmp.name)
        await self.runtime.start()

    async def test_private_maintenance_and_recall_use_host_model(self):
        event = Event()
        record = await self.runtime.ingest(event)
        job = await self.finished(
            await self.runtime.maintain(event), record["space_id"]
        )
        self.assertEqual(job["status"], "completed", job)
        text = await self.runtime.recall(Event("陨石一号怎么样了", mid="2"))
        self.assertIn("陨石一号", text)
        self.assertEqual(len(self.host.calls), 1)  # local recall makes no model call
        self.assertEqual(
            self.host.calls[0]["chat_provider_id"], "configured-host-model"
        )
        self.assertIsNone(
            self.runtime.core.config_for(record["space_id"])["focus_person_id"]
        )

    async def test_hybrid_provider_failure_still_injects_local_memory(self):
        await self.reset(retrieval_mode="hybrid", embedding_enabled=True,
                         embedding_base_url="https://embedding.invalid/v1",
                         embedding_model="test", embedding_api_key="private-key",
                         embedding_timeout_seconds=.1)
        manager = self.runtime.core.embeddings
        manager.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"data":[{"index":i,"embedding":[1,0]} for i,_ in enumerate(json.loads(r.content)["input"])]}))
        event = Event()
        record = await self.runtime.ingest(event)
        job = await self.finished(await self.runtime.maintain(event), record["space_id"])
        self.assertEqual(job["status"], "completed")
        await manager.index_once(record["space_id"])
        manager.transport = httpx.MockTransport(lambda r: httpx.Response(401, text="private-key"))
        text = await self.runtime.recall(Event("陨石一号怎么样了", mid="2"))
        self.assertIn("陨石一号", text)
        self.assertEqual(len(self.host.calls), 1)
        self.assertNotIn("private-key", await self.runtime.status(Event(mid="3")))
        await self.runtime.response(event,"下次再试试")
        rows = self.runtime.core.messages(record["space_id"],record["conversation_id"])
        self.assertTrue(any(r["role"] == "assistant" for r in rows))

    async def test_legacy_llm_option_becomes_local(self):
        await self.reset(retrieval_mode="llm")
        record = await self.runtime.ingest(Event())
        self.assertEqual(self.runtime.config["retrieval_mode"], "local")
        self.assertEqual(self.runtime.core.config_for(record["space_id"])["default_retrieval_mode"], "local")

    async def test_duplicate_message_and_sent_reply_are_idempotent(self):
        first = Event()
        a = await self.runtime.ingest(first)
        b = await self.runtime.ingest(Event())
        self.assertEqual(a["message"]["id"], b["message"]["id"])
        await self.runtime.response(first, "下次降温试试。")
        await self.runtime.response(first, "下次降温试试。")
        rows = self.runtime.core.messages(a["space_id"], a["conversation_id"])
        self.assertEqual([m["role"] for m in rows], ["user", "assistant"])
        self.assertEqual(rows[1]["in_reply_to"], rows[0]["id"])

    async def test_group_requires_focus_and_isolates_group_platform_private(self):
        self.assertIsNone(await self.runtime.ingest(Event(group="100")))
        await self.reset(focus_uid="42")
        records = [
            await self.runtime.ingest(e)
            for e in [
                Event(group="100"),
                Event(uid="43", group="100", mid="2"),
                Event(group="200"),
                Event(group="100", platform="other"),
                Event(),
            ]
        ]
        self.assertEqual(records[0]["space_id"], records[1]["space_id"])
        self.assertEqual(len({r["space_id"] for r in records}), 4)
        cfg = self.runtime.core.config_for(records[0]["space_id"])
        self.assertEqual(cfg["focus_person_id"], records[0]["message"]["person_id"])
        self.assertEqual(records[0]["message"]["retention"], "buffer")

    async def test_mentions_and_replies_map_real_people_and_messages(self):
        await self.reset(focus_uid="42")
        first = await self.runtime.ingest(Event(group="100"))
        second = Event(uid="43", group="100", mid="2")
        At = type("At", (), {})
        at = At()
        at.qq = "42"
        Reply = type("Reply", (), {})
        reply = Reply()
        reply.id = "1"
        reply.sender_id = "42"
        second.parts = [at, reply]
        record = await self.runtime.ingest(second)
        self.assertEqual(record["message"]["in_reply_to"], first["message"]["id"])
        self.assertEqual(
            record["message"]["mention_ids"], [first["message"]["person_id"]]
        )

    async def test_restart_preserves_scope_and_new_target_gets_new_scope(self):
        await self.reset(focus_uid="42")
        before = await self.runtime.ingest(Event(group="100"))
        await self.reset()
        same = await self.runtime.ingest(Event(group="100", mid="2"))
        self.assertEqual(before["space_id"], same["space_id"])
        await self.reset(focus_uid="99")
        after = await self.runtime.ingest(Event(group="100", mid="3"))
        self.assertNotEqual(before["space_id"], after["space_id"])
        self.assertFalse(
            self.runtime.core.config_for(before["space_id"])["schedule_enabled"]
        )

    async def test_failure_preserves_pending_message_and_safe_error(self):
        self.host.failure = RuntimeError("secret-key should not escape")
        event = Event()
        record = await self.runtime.ingest(event)
        job = await self.finished(
            await self.runtime.maintain(event), record["space_id"]
        )
        self.assertEqual(job["status"], "failed", job)
        self.assertNotIn("secret-key", json.dumps(job))
        self.assertFalse(
            self.runtime.core.messages(record["space_id"], record["conversation_id"])[
                0
            ]["processed"]
        )

    async def test_allowlist_commands_and_disabled_are_not_captured(self):
        self.assertIsNone(await self.runtime.ingest(Event("/记忆整理")))
        await self.reset(focus_uid="42", group_ids=["100"], focus_platform="qq-main")
        self.assertIsNone(await self.runtime.ingest(Event(group="200")))
        self.assertIsNone(
            await self.runtime.ingest(Event(group="100", platform="other"))
        )
        await self.reset(enabled=False)
        self.assertIsNone(await self.runtime.ingest(Event()))

    async def test_invalid_config_fails_before_hooks(self):
        with self.assertRaises(ValueError):
            Runtime(
                self.host, {"memory_options": '{"focus_person_id":"x"}'}, self.tmp.name
            )
        with self.assertRaises(ValueError):
            Runtime(self.host, {"retrieval_mode": "unsupported"}, self.tmp.name)

    async def test_unrelated_group_chat_is_not_archived(self):
        await self.reset(focus_uid="42")
        event = Event("今天买了牛奶", uid="43", group="100")
        record = await self.runtime.ingest(event)
        await self.finished(await self.runtime.maintain(event), record["space_id"])
        self.assertEqual(self.runtime.core.events(record["space_id"]), [])
        self.assertEqual(self.host.calls, [])
        self.assertEqual(record["message"]["retention"], "buffer")

    async def test_group_focus_maintenance(self):
        await self.reset(focus_uid="42", focus_name="小明")
        event = Event(group="100")
        record = await self.runtime.ingest(event)
        job = await self.finished(
            await self.runtime.maintain(event), record["space_id"]
        )
        self.assertEqual(job["status"], "completed", job)
        events = self.runtime.core.events(record["space_id"])
        self.assertEqual(len(events), 1)
        self.assertIn(record["message"]["person_id"], events[0]["person_ids"])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_provider_and_truncation(self):
        host = Host()

        async def truncated(**kwargs):
            return types.SimpleNamespace(
                raw_completion={"choices": [{"finish_reason": "length"}]}
            )

        host.llm_generate = truncated
        with self.assertRaises(OutputLimitError):
            await AstrBotModelAdapter(host, "fixed").complete(
                {"astrbot_umo": "umo"}, None, [{"role": "user", "content": "x"}]
            )
        self.assertFalse(host.provider_queries)

    async def test_timeout_and_cancellation(self):
        host = Host()

        async def hang(**kwargs):
            await asyncio.sleep(60)

        host.llm_generate = hang
        adapter = AstrBotModelAdapter(host)
        with self.assertRaises(MemoryError) as caught:
            await adapter.complete(
                {"astrbot_umo": "u", "timeout_seconds": 0.01},
                None,
                [{"role": "user", "content": "x"}],
            )
        self.assertEqual(caught.exception.status_code, 504)
        task = asyncio.create_task(
            adapter.complete(
                {"astrbot_umo": "u"}, None, [{"role": "user", "content": "x"}]
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


# Exercise the shipped entry point through the documented AstrBot hook contract.
# These host doubles do not claim to be a live QQ/platform integration.
from unittest.mock import patch


def host_modules(data_dir):
    modules = {}
    for name in [
        "astrbot",
        "astrbot.api",
        "astrbot.api.event",
        "astrbot.api.provider",
        "astrbot.api.star",
        "astrbot.core",
        "astrbot.core.agent",
        "astrbot.core.agent.message",
        "astrbot.core.utils",
        "astrbot.core.utils.astrbot_path",
    ]:
        modules[name] = types.ModuleType(name)

    def decorator(*a, **kw):
        return lambda f: f

    class Star:
        def __init__(self, context):
            self.context = context

    class TextPart:
        def __init__(self, text):
            self.text = text
            self.temporary = False

        def mark_as_temp(self):
            self.temporary = True
            return self

    api = modules["astrbot.api"]
    api.AstrBotConfig = dict
    api.logger = types.SimpleNamespace(error=lambda *args: None)
    modules["astrbot.api.event"].AstrMessageEvent = Event
    modules["astrbot.api.event"].filter = types.SimpleNamespace(
        event_message_type=decorator,
        on_llm_request=decorator,
        on_llm_response=decorator,
        after_message_sent=decorator,
        permission_type=decorator,
        command=lambda name: lambda f: (setattr(f, "registered_command", name) or f),
        EventMessageType=types.SimpleNamespace(ALL="all"),
        PermissionType=types.SimpleNamespace(ADMIN="admin"),
    )
    modules["astrbot.api.provider"].ProviderRequest = types.SimpleNamespace
    modules["astrbot.api.provider"].LLMResponse = types.SimpleNamespace
    modules["astrbot.api.star"].Context = Host
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].register = decorator
    modules["astrbot.core.agent.message"].TextPart = TextPart
    modules["astrbot.core.utils.astrbot_path"].get_astrbot_data_path = lambda: data_dir
    return modules


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch.dict(sys.modules, host_modules(self.tmp.name))
        self.patcher.start()
        sys.modules.pop("astrbot_plugin_memory_slice.main", None)
        self.module = importlib.import_module("astrbot_plugin_memory_slice.main")
        self.plugin = self.module.MemorySlicePlugin(Host(), {"auto_maintain": False})
        await self.plugin.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.patcher.stop()
        self.tmp.cleanup()

    async def test_injection_is_temporary_deduplicated_and_does_not_mutate_history(
        self,
    ):
        event = Event()
        await self.plugin.capture(event)
        binding = event.get_extra("memory_slice_record")
        self.plugin.runtime.core.create_event(
            binding["space_id"],
            EventInput(
                title="陨石一号",
                body="第一次烤面包失败叫陨石一号",
                salience=1,
                anchors={"details": ["陨石一号"]},
            ),
        )
        req = types.SimpleNamespace(
            extra_user_content_parts=[],
            prompt="原始输入",
            system_prompt="原有人设",
            contexts=[],
        )
        await self.plugin.inject(event, req)
        await self.plugin.inject(event, req)
        self.assertEqual(len(req.extra_user_content_parts), 1)
        self.assertTrue(req.extra_user_content_parts[0].temporary)
        self.assertEqual(
            (req.prompt, req.system_prompt, req.contexts), ("原始输入", "原有人设", [])
        )
        rows = self.plugin.runtime.core.messages(
            binding["space_id"], binding["conversation_id"]
        )
        self.assertEqual(rows[0]["content"], event.message_str)

    async def test_context_forwarding_and_core_dedup_across_long_history(self):
        event = Event()
        await self.plugin.capture(event)
        binding = event.get_extra("memory_slice_record")
        self.plugin.runtime.core.create_event(binding["space_id"], EventInput(
            title="陨石一号", body="陨石一号面包失败", salience=1,
            anchors={"details": ["陨石一号"]}))
        req = types.SimpleNamespace(extra_user_content_parts=[], prompt=event.message_str,
                                    system_prompt="人设", contexts=[])
        await self.plugin.inject(event, req)
        memory = req.extra_user_content_parts[0].text
        req.contexts = [{"role": "user", "content": memory}] + [
            {"role": "assistant", "content": "继续聊陨石一号"} for _ in range(35)]
        req.extra_user_content_parts = []
        await self.plugin.inject(event, req)
        self.assertEqual(req.extra_user_content_parts, [])
        self.assertEqual(len(req.contexts), 36)

    async def test_command_and_recall_choices(self):
        self.assertEqual(self.plugin.status.registered_command, "记忆切片")
        schema = json.loads((ROOT / "plugins/astrbot_plugin_memory_slice/_conf_schema.json").read_text())
        self.assertEqual(schema["retrieval_mode"]["options"], ["local", "hybrid"])
        self.assertIn("延迟", schema["retrieval_mode"]["description"])

    async def test_reply_saved_only_after_send(self):
        event = Event()
        await self.plugin.capture(event)
        response = types.SimpleNamespace(
            role="assistant",
            is_chunk=False,
            tools_call_name=[],
            completion_text="下次再试试",
        )
        await self.plugin.generated(event, response)
        binding = event.get_extra("memory_slice_record")
        rows = lambda: self.plugin.runtime.core.messages(
            binding["space_id"], binding["conversation_id"]
        )
        self.assertEqual(len(rows()), 1)
        await self.plugin.sent(event)
        await self.plugin.sent(event)
        self.assertEqual(len(rows()), 2)

    async def test_tool_and_error_results_are_not_memories(self):
        event = Event()
        await self.plugin.capture(event)
        for role, chunk, tools in [
            ("err", False, []),
            ("assistant", True, []),
            ("assistant", False, ["search"]),
        ]:
            await self.plugin.generated(
                event,
                types.SimpleNamespace(
                    role=role,
                    is_chunk=chunk,
                    tools_call_name=tools,
                    completion_text="not a reply",
                ),
            )
        await self.plugin.sent(event)
        binding = event.get_extra("memory_slice_record")
        self.assertEqual(
            len(
                self.plugin.runtime.core.messages(
                    binding["space_id"], binding["conversation_id"]
                )
            ),
            1,
        )

    async def test_recall_failure_does_not_break_chat(self):
        async def failure(event):
            raise RuntimeError("model unavailable")

        self.plugin.runtime.recall = failure
        req = types.SimpleNamespace(extra_user_content_parts=[])
        await self.plugin.inject(Event(), req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_second_instance_cannot_share_active_database_and_reload_works(self):
        from filelock import Timeout

        another = self.module.MemorySlicePlugin(Host(), {"auto_maintain": False})
        with self.assertRaises(Timeout):
            await another.initialize()
        await self.plugin.terminate()
        await another.initialize()
        await another.terminate()


class PackagingTests(unittest.TestCase):
    def test_core_is_exact_and_archive_excludes_service_data_and_dependencies(self):
        import hashlib

        with zipfile.ZipFile(archive) as z:
            prefix = "astrbot_plugin_memory_slice/"
            manifest = json.loads(z.read(prefix + "core-manifest.json"))
            for name, sha in manifest.items():
                self.assertEqual(hashlib.sha256(z.read(prefix + name)).hexdigest(), sha)
                self.assertEqual(
                    z.read(prefix + name), ((ROOT / "evals/recall" / name.removeprefix("memory_slice/benchmark_data/")) if name.startswith("memory_slice/benchmark_data/") else (ROOT / "backend" / name)).read_bytes()
                )
            self.assertIn(prefix + "main.py", z.namelist())
            self.assertFalse(
                any(
                    x.endswith((".sqlite3", ".pyc"))
                    or "/app/" in x
                    or "/node_modules/" in x
                    for x in z.namelist()
                )
            )
            deps = z.read(prefix + "requirements.txt").decode().lower()
            self.assertIn("fastapi", deps)
            self.assertIn("uvicorn", deps)


if __name__ == "__main__":
    unittest.main()
