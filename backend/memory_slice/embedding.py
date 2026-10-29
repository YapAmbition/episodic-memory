"""Optional remote embeddings. Never a dependency of message/event persistence."""

import asyncio
import hashlib
import json
import math
import logging
import os
import time

import httpx

from .schemas import EmbeddingInput


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Unavailable(Exception):
    pass


class Embeddings:
    def __init__(self, store, metrics):
        self.store, self.metrics = store, metrics
        self.keys, self.states = {}, {}
        self.slots = None
        self.worker = None
        self.transport = None  # injectable HTTP transport for contract tests

    def settings(self, sid):
        rows = self.store.list("embedding_config", sid)
        return EmbeddingInput.model_validate(
            {
                k: v
                for k, v in (rows[0] if rows else {}).items()
                if k not in ("id", "version")
            }
        ).model_dump(exclude={"api_key"})

    def identity(self, cfg):
        return digest(
            json.dumps(
                [cfg["base_url"], cfg["model"], cfg["dimensions"]], sort_keys=True
            )
        )

    def state(self, sid):
        return self.states.setdefault(
            sid,
            {
                "failures": 0,
                "until": 0,
                "probing": False,
                "last_error": None,
                "dimension": None,
                "generation": 0,
            },
        )

    def configure(self, sid, body):
        previous = self.settings(sid)
        cfg = body.model_dump(exclude={"api_key"})
        changed = previous != cfg or (
            body.api_key is not None and body.api_key != self.keys.get(sid, "")
        )
        if body.api_key is not None:
            if body.api_key:
                self.keys[sid] = body.api_key
            else:
                self.keys.pop(sid, None)
        if changed:
            st = self.state(sid)
            self.states[sid] = dict(
                failures=0,
                until=0,
                probing=False,
                last_error=None,
                dimension=None,
                generation=st["generation"] + 1,
            )
            self.store.put("embedding_config", dict(cfg, id=sid), sid)
        return self.status(sid)

    def chunks(self, events):
        for event in events:
            if event["status"] == "archived":
                continue
            body = event["body"]
            anchors = json.dumps(
                event.get("anchors", {}), ensure_ascii=False, sort_keys=True
            )[:1200]
            for start in range(0, max(1, len(body)), 1000):
                text = (
                    event["title"] + "\n" + anchors + "\n" + body[start : start + 1200]
                )
                yield {
                    "id": digest(event["id"] + ":" + str(start)),
                    "event_id": event["id"],
                    "hash": digest(text),
                    "text": text,
                }

    def rows(self, sid, events, cfg):
        chunks = list(self.chunks(events))
        current = {c["id"]: c for c in chunks}
        signature = self.identity(cfg)
        valid = [
            r
            for r in self.store.list("embedding_vector", sid)
            if r["id"] in current
            and r.get("hash") == current[r["id"]]["hash"]
            and r.get("signature") == signature
        ]
        return chunks, valid

    def status(self, sid):
        cfg = self.settings(sid)
        st = self.state(sid)
        chunks, rows = self.rows(sid, self.store.list("event", sid), cfg)
        return dict(
            cfg,
            has_key=bool(self.keys.get(sid) or os.environ.get("EMBEDDING_API_KEY")),
            indexed_chunks=len(rows),
            pending_chunks=len(chunks) - len(rows),
            last_error=st["last_error"],
            circuit_open=st["until"] > time.monotonic(),
            retry_after_seconds=max(0, round(st["until"] - time.monotonic(), 1)),
        )

    async def request(self, sid, texts, purpose):
        cfg = self.settings(sid)
        st = self.state(sid)
        generation = st["generation"]
        if not cfg["enabled"] or not cfg["base_url"] or not cfg["model"]:
            raise Unavailable("not_configured")
        if st["until"] > time.monotonic():
            raise Unavailable("circuit_open")
        probe = st["failures"] >= cfg["failure_threshold"]
        if probe and st["probing"]:
            raise Unavailable("circuit_open")
        if probe:
            st["probing"] = True
        outcome = "error"
        started = time.monotonic()
        if self.slots is None:
            self.slots = asyncio.Semaphore(2)

        async def call():
            async with self.slots:
                key = self.keys.get(sid) or os.environ.get("EMBEDDING_API_KEY", "")
                headers = {"Authorization": "Bearer " + key} if key else {}
                payload = {
                    "model": cfg["model"],
                    "input": texts,
                    "encoding_format": "float",
                }
                if cfg["dimensions"]:
                    payload["dimensions"] = cfg["dimensions"]
                async with httpx.AsyncClient(
                    timeout=cfg["timeout_seconds"],
                    transport=self.transport,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        cfg["base_url"].rstrip("/") + "/embeddings",
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            raise Unavailable(
                                "auth"
                                if response.status_code in (401, 403)
                                else "rate_limited"
                                if response.status_code == 429
                                else "http_error"
                            )
                        raw = bytearray()
                        async for part in response.aiter_bytes():
                            raw.extend(part)
                            if len(raw) > 8_000_000:
                                raise Unavailable("invalid_response")
                        data = json.loads(raw)
                items = data["data"]
                if len(items) != len(texts) or sorted(
                    x["index"] for x in items
                ) != list(range(len(texts))):
                    raise Unavailable("invalid_response")
                vectors = [
                    x["embedding"] for x in sorted(items, key=lambda x: x["index"])
                ]
                dim = len(vectors[0])
                if not 1 <= dim <= 16384:
                    raise Unavailable("invalid_response")
                for vector in vectors:
                    if (
                        len(vector) != dim
                        or any(
                            isinstance(v, bool)
                            or not isinstance(v, (int, float))
                            or not math.isfinite(v)
                            for v in vector
                        )
                        or not math.isfinite(sum(v * v for v in vector))
                        or sum(v * v for v in vector) <= 0
                    ):
                        raise Unavailable("invalid_response")
                if self.state(sid)["generation"] != generation:
                    raise Unavailable("configuration_changed")
                if cfg["dimensions"] and cfg["dimensions"] != dim:
                    raise Unavailable("dimension_mismatch")
                existing_dim = st["dimension"]
                if existing_dim is None:
                    rows = self.store.list("embedding_vector", sid)
                    existing_dim = next(
                        (
                            len(r["vector"])
                            for r in rows
                            if r.get("signature") == self.identity(cfg)
                        ),
                        None,
                    )
                if existing_dim and existing_dim != dim:
                    for row in self.store.list("embedding_vector", sid):
                        self.store.delete("embedding_vector", row["id"], sid)
                    st["dimension"] = dim
                    raise Unavailable("dimension_changed")
                st["dimension"] = dim
                return vectors

        try:
            vectors = await asyncio.wait_for(call(), cfg["timeout_seconds"])
            outcome = "success"
            st.update(failures=0, until=0, last_error=None)
            return vectors
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            code = (
                str(exc)
                if isinstance(exc, Unavailable)
                else "timeout"
                if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException))
                else "network"
                if isinstance(exc, httpx.RequestError)
                else "invalid_response"
            )
            outcome = code
            if self.state(sid)["generation"] == generation:
                st["failures"] += 1
                if (
                    st["last_error"] != code
                    or st["failures"] == cfg["failure_threshold"]
                ):
                    logging.getLogger("memory_slice").warning(
                        "embedding_failed purpose=%s reason=%s failures=%s",
                        purpose,
                        code,
                        st["failures"],
                    )
                st["last_error"] = code
                if st["failures"] >= cfg["failure_threshold"]:
                    st["until"] = time.monotonic() + cfg["cooldown_seconds"]
            raise Unavailable(code) from None
        finally:
            st["probing"] = False
            self.metrics.embedding_calls.labels(purpose, outcome).inc()
            self.metrics.embedding_seconds.labels(purpose).observe(
                time.monotonic() - started
            )

    async def query(self, sid, query, events):
        started = time.monotonic()
        try:
            cfg = self.settings(sid)
            if not cfg["enabled"]:
                raise Unavailable("not_configured")
            chunks, rows = self.rows(sid, events, cfg)
            if not rows:
                st = self.state(sid)
                raise Unavailable(
                    "circuit_open"
                    if st["until"] > time.monotonic()
                    else st["last_error"] or "index_pending"
                )
            vector = (await self.request(sid, [query[-4000:] or "相关经历"], "query"))[
                0
            ]
            # Re-check after awaiting: edits/configuration changes must not use stale vectors.
            fresh = {
                r["id"]: r
                for r in self.rows(
                    sid, self.store.list("event", sid), self.settings(sid)
                )[1]
            }
            scores = {}
            for row in rows:
                if (
                    row["id"] not in fresh
                    or fresh[row["id"]]["hash"] != row["hash"]
                    or len(row["vector"]) != len(vector)
                ):
                    continue
                v = row["vector"]
                norm_v = math.sqrt(sum(a * a for a in v))
                norm_q = math.sqrt(sum(b * b for b in vector))
                score = max(
                    -1.0,
                    min(
                        1.0, sum((a / norm_v) * (b / norm_q) for a, b in zip(v, vector))
                    ),
                )
                if score >= cfg["min_similarity"]:
                    scores[row["event_id"]] = max(scores.get(row["event_id"], 0), score)
            return scores, {
                "status": "partial" if len(rows) < len(chunks) else "ok",
                "candidate_count": len(scores),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = str(exc) if isinstance(exc, Unavailable) else "internal_error"
            return {}, {
                "status": code,
                "candidate_count": 0,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            }

    async def index_once(self, sid):
        cfg = self.settings(sid)
        if not cfg["enabled"]:
            return
        generation = self.state(sid)["generation"]
        events = self.store.list("event", sid)
        chunks, rows = self.rows(sid, events, cfg)
        current = {c["id"] for c in chunks}
        ready = {r["id"] for r in rows}
        for row in self.store.list("embedding_vector", sid):
            if row["id"] not in current:
                self.store.delete("embedding_vector", row["id"], sid)
        pending = [c for c in chunks if c["id"] not in ready][:8]
        if not pending:
            return
        vectors = await self.request(sid, [c["text"] for c in pending], "index")
        if generation != self.state(sid)["generation"]:
            return
        fresh = {c["id"]: c["hash"] for c in self.chunks(self.store.list("event", sid))}
        for chunk, vector in zip(pending, vectors):
            if fresh.get(chunk["id"]) == chunk["hash"]:
                self.store.put(
                    "embedding_vector",
                    dict(
                        id=chunk["id"],
                        event_id=chunk["event_id"],
                        hash=chunk["hash"],
                        signature=self.identity(cfg),
                        vector=vector,
                    ),
                    sid,
                )

    async def loop(self):
        while True:
            for space in self.store.list("space"):
                try:
                    await self.index_once(space["id"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass  # request records sanitized errors; never interrupts host
            await asyncio.sleep(5)

    def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self.loop())

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        self.keys.clear()
        self.slots = None
