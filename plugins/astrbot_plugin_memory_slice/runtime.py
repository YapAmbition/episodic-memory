"""Platform identity, durable scope mapping and host lifecycle orchestration."""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from .adapter import AstrBotModelAdapter
from .memory_slice.service import MemoryService
from .memory_slice.maintenance_context import budget_for
from .memory_slice.schemas import (
    SpaceInput,
    PersonInput,
    ConversationInput,
    MessageInput,
    ContextInput,
    MemoryConfig,
    ModelInput,
    MaintenanceInput,
)


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


class Runtime:
    def __init__(self, context, config, directory):
        self.config = dict(config)
        self.focus = str(config.get("focus_uid", "")).strip()
        self.platform = str(config.get("focus_platform", "")).strip()
        self.provider = AstrBotModelAdapter(
            context, str(config.get("provider_id", "")).strip()
        )
        self.core = MemoryService(directory, model=self.provider)
        self.lock = asyncio.Lock()
        self.closed = False
        options = config.get("memory_options", "{}")
        self.options = (
            json.loads(options) if isinstance(options, str) else dict(options)
        )
        if not isinstance(self.options, dict):
            raise ValueError("高级记忆配置必须是 JSON 对象")
        forbidden = {
            k
            for k in self.options
            if k.startswith("schedule_") or k == "focus_person_id"
        }
        if forbidden:
            raise ValueError("关注和定时选项请使用插件专用配置")
        MemoryConfig.model_validate(self.options)
        self.model_settings = ModelInput(
            mode="llm",
            model="astrbot-host",
            timeout_seconds=config.get("model_timeout_seconds", 60),
            maintenance_output_tokens=config.get("output_tokens", 4096),
            context_window_tokens=config.get("context_window_tokens") or None,
            max_retries=0,
        ).model_dump(exclude={"api_key"})
        if config.get("retrieval_mode", "local") not in ("local", "llm"):
            raise ValueError("召回方式必须是 local 或 llm")
        initial = MemoryConfig.model_validate(
            dict(
                self.options,
                schedule_interval_minutes=config.get("interval_minutes", 60),
            )
        )
        budget_for(self.model_settings, initial.model_dump())
        self.signature = digest(self.focus, self.platform)

    def allowed(self, scope):
        if not self.config.get("enabled", True):
            return False
        if scope["group"]:
            groups = self.config.get("group_ids", [])
            return (
                self.config.get("group_enabled", True)
                and bool(self.focus)
                and (not self.platform or self.platform == scope["platform"])
                and (not groups or scope["group"] in [str(x) for x in groups])
            )
        return self.config.get("private_enabled", True)

    async def start(self):
        # Old focus targets remain on disk but never run under a new identity/config.
        for binding in self.core.store.list("astrbot_scope"):
            active = binding["signature"] == self.signature and self.allowed(binding)
            self.configure(binding, active)
            if not active:
                for job in self.core.jobs(binding["space_id"]):
                    if job["status"] in ("queued", "running"):
                        job.update(status="deferred", error="此会话或关注配置已停用")
                        self.core.store.put("job", job, binding["space_id"])
        await self.core.start()

    async def close(self):
        self.closed = True
        await self.core.close()

    def scope(self, event):
        return {
            "platform": str(event.get_platform_id()),
            "bot": str(event.get_self_id()),
            "group": str(event.get_group_id() or ""),
            "sender": str(event.get_sender_id()),
        }

    def person(self, sid, platform, uid, name=""):
        for p in self.core.persons(sid):
            if p.get("platform") == platform and p.get("external_uid") == uid:
                return p["id"]
        return self.core.add_person(
            sid,
            PersonInput(
                name=(name or uid)[:60], platform=platform[:80], external_uid=uid[:200]
            ),
        )["id"]

    def scope_info(self, sid):
        binding = next(
            (b for b in self.core.store.list("astrbot_scope") if b["space_id"] == sid),
            None,
        )
        if not binding:
            return {"active": False, "label": "历史空间"}
        return {
            "active": binding["signature"] == self.signature and self.allowed(binding),
            "kind": "group" if binding["group"] else "private",
            "platform": binding["platform"],
            "external_id": binding["group"] or binding["sender"],
            "label": (
                "群聊 " + binding["group"]
                if binding["group"]
                else "私聊 " + binding["sender"]
            )
            + " · "
            + binding["platform"],
        }

    def configure(self, binding, active=True):
        sid = binding["space_id"]
        cfg = self.core.config_for(sid)
        if not active:
            cfg["schedule_enabled"] = False
            self.core.store.put("config", dict(cfg, id=sid), sid)
            return
        cfg.update(self.options)
        cfg.update(
            default_retrieval_mode=self.config.get("retrieval_mode", "local"),
            schedule_enabled=bool(active and self.config.get("auto_maintain", True)),
            schedule_interval_minutes=self.config.get("interval_minutes", 60),
        )
        # A configured UID applies on its platform; unconfigured private chat has no person bonus.
        focused = self.focus and (
            not self.platform or self.platform == binding["platform"]
        )
        cfg["focus_person_id"] = (
            self.person(
                sid, binding["platform"], self.focus, self.config.get("focus_name", "")
            )
            if focused
            else None
        )
        cfg = MemoryConfig.model_validate(cfg).model_dump()
        budget_for(self.model_settings, cfg)
        self.core.store.put("config", dict(cfg, id=sid), sid)
        self.core.store.put(
            "model", dict(self.model_settings, id=sid, astrbot_umo=binding["umo"]), sid
        )

    async def bind(self, event):
        if self.closed:
            return None
        scope = self.scope(event)
        if not scope["sender"] or not self.allowed(scope):
            return None
        key = digest(
            scope["platform"],
            scope["bot"],
            "group" if scope["group"] else "private",
            scope["group"] or scope["sender"],
            self.signature,
        )
        async with self.lock:
            bindings = {b["id"]: b for b in self.core.store.list("astrbot_scope")}
            binding = bindings.get(key)
            if not binding:
                sid = self.core.add_space(
                    SpaceInput(
                        name="AstrBot "
                        + ("群聊 " if scope["group"] else "私聊 ")
                        + key[:12]
                    )
                )["id"]
                binding = dict(
                    scope,
                    id=key,
                    space_id=sid,
                    signature=self.signature,
                    umo=event.unified_msg_origin,
                )
                pid = self.person(
                    sid, scope["platform"], scope["sender"], event.get_sender_name()
                )
                cid = self.core.add_conversation(
                    sid,
                    ConversationInput(
                        kind="group" if scope["group"] else "direct",
                        person_id=pid if not scope["group"] else None,
                        title="AstrBot 对话",
                    ),
                )["id"]
                binding["conversation_id"] = cid
                self.core.store.put("astrbot_scope", binding, sid)
                self.configure(binding)
            # In groups, AstrBot may use per-member UMOs. Recall/maintenance use the latest caller's provider.
            if binding["umo"] != event.unified_msg_origin:
                binding["umo"] = event.unified_msg_origin
                self.core.store.put("astrbot_scope", binding, binding["space_id"])
                self.configure(binding)
            return binding

    def message_key(self, event):
        key = event.get_extra("memory_slice_key")
        if key:
            return key
        mid = getattr(event.message_obj, "message_id", None)
        key = digest(str(mid)) if mid not in (None, "") else uuid.uuid4().hex
        event.set_extra("memory_slice_key", key)
        return key

    def ignored(self, event):
        text = event.message_str.strip()
        return (
            not text
            or str(event.get_sender_id()) == str(event.get_self_id())
            or text.lstrip("/").split()[0] in {"记忆状态", "记忆整理", "记忆身份"}
            or any(
                p and text.startswith(p)
                for p in self.config.get("ignore_prefixes", ["/"])
            )
        )

    async def ingest(self, event):
        existing = event.get_extra("memory_slice_record")
        if existing:
            return existing
        if self.ignored(event):
            return None
        binding = await self.bind(event)
        if not binding:
            return None
        sid, cid = binding["space_id"], binding["conversation_id"]
        pid = self.person(
            sid,
            binding["platform"],
            str(event.get_sender_id()),
            event.get_sender_name(),
        )
        mentions, reply = [], None
        messages = self.core.messages(sid, cid)
        for part in event.get_messages():
            kind = type(part).__name__
            if kind == "At" and str(part.qq) not in ("all", str(event.get_self_id())):
                mentions.append(self.person(sid, binding["platform"], str(part.qq)))
            elif kind == "Reply":
                parent_key = digest(str(part.id))
                reply = next(
                    (
                        m["id"]
                        for m in messages
                        if m.get("client_id") == "u:" + parent_key
                    ),
                    None,
                )
                # If the original wasn't observed, preserve its author as an interaction cue, not a fabricated message.
                if not reply and getattr(part, "sender_id", None) not in (
                    None,
                    0,
                    "0",
                    "",
                    event.get_self_id(),
                ):
                    mentions.append(
                        self.person(sid, binding["platform"], str(part.sender_id))
                    )
        timestamp = getattr(event.message_obj, "timestamp", None)
        at = (
            datetime.fromtimestamp(float(timestamp), timezone.utc)
            if timestamp
            else None
        )
        text = event.message_str.strip()
        if len(text) > 20000:
            raise ValueError("消息超过 20000 字符，未截断或写入")
        row = self.core.add_message(
            sid,
            cid,
            MessageInput(
                role="user",
                content=text,
                sender_id=pid,
                in_reply_to=reply,
                mention_ids=list(dict.fromkeys(mentions))[:30],
                at=at,
                client_id="u:" + self.message_key(event),
            ),
        )
        record = dict(binding, message=row)
        event.set_extra("memory_slice_record", record)
        return record

    async def recall(self, event):
        record = await self.ingest(event)
        if not record:
            return ""
        row = record["message"]
        recent = self.core.messages(record["space_id"], record["conversation_id"])
        # Only prior messages, not future concurrent arrivals or injected memory.
        index = next(i for i, m in enumerate(recent) if m["id"] == row["id"])
        parent = next((m for m in recent if m["id"] == row.get("in_reply_to")), {})
        result = await self.core.recall(
            record["space_id"],
            ContextInput(
                reply_to_person_id=parent.get("person_id"),
                person_id=row["person_id"],
                query=row["content"],
                mention_ids=row["mention_ids"],
                recent_messages=[
                    {"role": m["role"], "content": m["content"]}
                    for m in recent[max(0, index - 6) : index]
                ],
                retrieval_mode=self.config.get("retrieval_mode", "local"),
            ),
        )
        return result["context"]

    async def response(self, event, text):
        record = event.get_extra("memory_slice_record")
        if (
            not record
            or event.get_extra("memory_slice_response_saved")
            or not text.strip()
            or self.closed
        ):
            return
        if len(text) > 20000:
            raise ValueError("回复超过 20000 字符，未截断或写入")
        self.core.add_message(
            record["space_id"],
            record["conversation_id"],
            MessageInput(
                role="assistant",
                content=text,
                sender_id=record["message"]["person_id"],
                in_reply_to=record["message"]["id"],
                client_id="a:" + self.message_key(event),
            ),
        )
        event.set_extra("memory_slice_response_saved", True)

    async def maintain(self, event):
        binding = await self.bind(event)
        if not binding:
            return None
        return self.core.enqueue(binding["space_id"], MaintenanceInput())

    async def status(self, event):
        binding = await self.bind(event)
        if not binding:
            return "此会话未启用记忆；群聊需要配置关注 UID。"
        sid = binding["space_id"]
        messages, events = self.core.store.list("message", sid), self.core.events(sid)
        jobs = self.core.jobs(sid)
        last = max(jobs, key=lambda j: j["created_at"]) if jobs else None
        status = f"记忆切片：{len(events)} 件事，{sum(not m['processed'] for m in messages)} 条待整理消息。"
        if last:
            status += f"\n最近整理：{last['status']}（{last['id']}）"
            if last.get("error"):
                status += "\n" + str(last["error"])[:300]
        return status
