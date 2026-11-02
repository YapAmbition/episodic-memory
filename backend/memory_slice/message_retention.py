"""Expire consumed message bodies, retaining identity and independent event records."""
import hashlib
from datetime import timedelta
from .conversation import ordered_messages
from .store import dt, iso, utcnow

STATE_ID = "message-retention"


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def cleanup_messages(store, sid, config, now=None, batch_size=500):
    now = dt(now) if now else utcnow()
    if not config["message_cleanup_enabled"]:
        return None
    with store.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if any(j["status"] in ("queued", "running") for j in store.list("job", sid, con)):
            return None
        state = next((s for s in store.list("integration", sid, con) if s["id"] == STATE_ID + ":" + sid), None)
        if state is None:
            state = {"id": STATE_ID + ":" + sid, "enabled_at": iso(now), "total_cleaned": 0, "total_chars": 0}
        if state.get("next_run_at") and dt(state["next_run_at"]) > now:
            return None
        messages = store.list("message", sid, con)
        protected = set()
        by_conversation = {}
        for message in messages:
            by_conversation.setdefault(message["conversation_id"], []).append(message)
            if not message.get("processed") and message.get("in_reply_to"):
                protected.add(message["in_reply_to"])
        # Retain the small preceding context needed by the oldest unfinished segment.
        for group in by_conversation.values():
            ordered = ordered_messages(group)
            for index, message in enumerate(ordered):
                if not message.get("processed") and not message.get("fixture"):
                    preceding = [m for m in ordered[:index] if m.get("processed") and not m.get("content_cleared_at")]
                    protected.update(m["id"] for m in preceding[-2:])
                    break
        cutoff = now - timedelta(days=config["processed_message_retention_days"])
        candidates = []
        for message in messages:
            if not message.get("processed") or message.get("fixture") or message.get("content_cleared_at") or message["id"] in protected:
                continue
            # Old installations have no completion timestamp: start their grace period now.
            completed = dt(message.get("processed_at") or state["enabled_at"])
            if completed <= cutoff:
                candidates.append(message)
        removed_chars = 0
        for message in candidates[:batch_size]:
            content = message.get("content", "")
            removed_chars += len(content)
            message.update(content="", content_sha256=content_hash(content), content_chars=len(content),
                           content_cleared_at=iso(now), retention="permanent", expires_at=None)
            store.put("message", message, sid, con=con)
        count = min(batch_size, len(candidates))
        state.update(last_run_at=iso(now), last_cleaned=count,
                     total_cleaned=state["total_cleaned"] + count,
                     total_chars=state["total_chars"] + removed_chars,
                     pending_cleanup=max(0, len(candidates)-count),
                     next_run_at=iso(now + (timedelta(seconds=15) if len(candidates)>batch_size else timedelta(days=1))))
        store.put("integration", state, sid, con=con)
        return state
