import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import MemoryError as HTTPException


def utcnow():
    return datetime.now(timezone.utc)


def iso(value=None):
    value = value or utcnow()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def dt(value):
    return datetime.fromisoformat(iso(value))


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:12]


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "memory.sqlite3"
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, space_id TEXT NOT NULL,
                    data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS records_scope ON records(kind,space_id);
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, event_id TEXT NOT NULL, space_id TEXT NOT NULL,
                    data TEXT NOT NULL, saved_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_event ON versions(space_id,event_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(
                    event_id UNINDEXED, space_id UNINDEXED, version UNINDEXED,
                    title, body, anchors, structure, tokenize='unicode61');
            """)
            # Backfill existing databases without rewriting canonical event records.
            for row in con.execute("SELECT r.data,r.version FROM records r LEFT JOIN event_search s ON s.event_id=r.id WHERE r.kind='event' AND (s.event_id IS NULL OR CAST(s.version AS INTEGER)!=r.version)").fetchall():
                self._index_event(con, json.loads(row["data"]), row["version"])

    @staticmethod
    def _terms(text):
        terms = set()
        for word in re.findall(r"\w+", str(text).lower()):
            if len(word) <= 60:
                terms.add(word)
            terms.update(word[i:i + 2] for i in range(len(word) - 1))
        return " ".join(sorted(terms))

    def _index_event(self, con, item, version):
        con.execute("DELETE FROM event_search WHERE event_id=?", (item["id"],))
        anchors = item.get("anchors", {})
        con.execute("INSERT INTO event_search(event_id,space_id,version,title,body,anchors,structure) VALUES(?,?,?,?,?,?,?)", (
            item["id"], item["space_id"], version, self._terms(item["title"]), self._terms(item["body"]),
            self._terms(" ".join(v for values in anchors.values() for v in values)),
            self._terms(" ".join(anchors.get("structure", []))),
        ))

    @contextmanager
    def connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def get(self, kind, item_id, space_id=None, con=None):
        if con is None:
            with self.connect() as own:
                return self.get(kind, item_id, space_id, own)
        row = con.execute("SELECT data,version,space_id FROM records WHERE kind=? AND id=?", (kind, item_id)).fetchone()
        if not row or (space_id is not None and row["space_id"] != space_id):
            raise HTTPException(404, "记录不存在或不属于此记忆空间")
        result = json.loads(row["data"])
        result["version"] = row["version"]
        return result

    def list(self, kind, space_id=None, con=None):
        if con is None:
            with self.connect() as own:
                return self.list(kind, space_id, own)
        if space_id is None:
            rows = con.execute("SELECT data,version FROM records WHERE kind=? ORDER BY rowid", (kind,))
        else:
            rows = con.execute("SELECT data,version FROM records WHERE kind=? AND space_id=? ORDER BY rowid", (kind, space_id))
        return [dict(json.loads(row["data"]), version=row["version"]) for row in rows]

    def put(self, kind, item, space_id=None, expected=None, con=None):
        if con is None:
            with self.connect() as own:
                return self.put(kind, item, space_id, expected, own)
        item = dict(item)
        item.pop("version", None)
        space_id = space_id or item.get("space_id") or item["id"]
        old = con.execute("SELECT data,version FROM records WHERE kind=? AND id=?", (kind, item["id"])).fetchone()
        if expected is not None and (not old or old["version"] != expected):
            raise HTTPException(409, "此记录已被更新，请刷新后重试")
        if kind == "event" and old:
            con.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (new_id("rev"), item["id"], space_id, old["data"], iso()))
        version = old["version"] + 1 if old else 1
        con.execute("INSERT INTO records(kind,id,space_id,data,version) VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,version=excluded.version", (kind, item["id"], space_id, json.dumps(item, ensure_ascii=False), version))
        if kind == "event":
            self._index_event(con, item, version)
        return dict(item, version=version)

    def delete(self, kind, item_id, space_id, con=None):
        if con is None:
            with self.connect() as own:
                return self.delete(kind, item_id, space_id, own)
        self.get(kind, item_id, space_id, con)
        con.execute("DELETE FROM records WHERE kind=? AND id=? AND space_id=?", (kind, item_id, space_id))
        if kind == "event":
            con.execute("DELETE FROM event_search WHERE event_id=? AND space_id=?", (item_id, space_id))

    def event_catalog(self, space_id):
        """Lightweight projections; full narratives are fetched only for candidates."""
        fields = ("id", "space_id", "title", "person_ids", "anchors", "links", "salience", "learned_at", "occurred_at", "status", "reinforcements", "updated_at", "source_ids")
        with self.connect() as con:
            projections = ",".join(f"json_extract(data,'$.{field}') AS {field}" for field in fields)
            rows = con.execute(f"SELECT {projections},version FROM records WHERE kind='event' AND space_id=?", (space_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for field in ("person_ids", "anchors", "links", "reinforcements", "source_ids"):
                item[field] = json.loads(item[field]) if item[field] else ([] if field != "anchors" else {})
            result.append(item)
        return result

    def pending_messages(self, space_id, conversation_id=None, char_budget=32000):
        where = "m.kind='message' AND m.space_id=? AND NOT coalesce(json_extract(m.data,'$.processed'),0) AND NOT coalesce(json_extract(m.data,'$.fixture'),0)"
        params = [space_id]
        if conversation_id:
            where += " AND json_extract(m.data,'$.conversation_id')=?"
            params.append(conversation_id)
        with self.connect() as con:
            rows = con.execute("""SELECT m.data,m.version FROM records m
                LEFT JOIN records p ON p.kind='message' AND p.id=json_extract(m.data,'$.in_reply_to')
                  AND p.space_id=m.space_id
                  AND json_extract(p.data,'$.conversation_id')=json_extract(m.data,'$.conversation_id')
                  AND json_extract(m.data,'$.role')='assistant' AND json_extract(p.data,'$.role')='user'
                  AND NOT coalesce(json_extract(p.data,'$.fixture'),0)
                WHERE """ + where + """
                ORDER BY coalesce(json_extract(p.data,'$.at'),json_extract(m.data,'$.at')),
                         coalesce(p.rowid,m.rowid),m.rowid LIMIT 200""", params).fetchall()
        result, used = [], 0
        for row in rows:
            item = dict(json.loads(row["data"]), version=row["version"])
            remaining = len(item["content"]) - item.get("maintenance_offset", 0)
            if result and used + remaining > char_budget:
                break
            result.append(item)
            used += remaining
        return result

    def search_events(self, space_id, query, limit=30, channel=None):
        # Terms are generated internally and quoted; user text is never FTS syntax.
        terms = self._terms(query).split()[:160]
        if not terms:
            return []
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        if channel in ("structure", "anchors"):
            expression = channel + " : (" + expression + ")"
        with self.connect() as con:
            rows = con.execute("SELECT event_id FROM event_search WHERE event_search MATCH ? AND space_id=? ORDER BY bm25(event_search,0,0,0,5,1,3,4) LIMIT ?", (expression, space_id, limit)).fetchall()
        return [row["event_id"] for row in rows]

    def get_events(self, space_id, ids):
        if not ids:
            return []
        with self.connect() as con:
            rows = con.execute("SELECT data,version FROM records WHERE kind='event' AND space_id=? AND id IN (" + ",".join("?" for _ in ids) + ")", (space_id, *ids)).fetchall()
        return [dict(json.loads(row["data"]), version=row["version"]) for row in rows]

    def operational_snapshot(self):
        with self.connect() as con:
            counts = {r[0]: r[1] for r in con.execute("SELECT kind,COUNT(*) FROM records GROUP BY kind")}
            jobs = {r[0]: r[1] for r in con.execute("SELECT json_extract(data,'$.status'),COUNT(*) FROM records WHERE kind='job' GROUP BY json_extract(data,'$.status')")}
            pending = con.execute("SELECT COUNT(*) FROM records WHERE kind='message' AND NOT coalesce(json_extract(data,'$.processed'),0) AND NOT coalesce(json_extract(data,'$.fixture'),0)").fetchone()[0]
        size = 0
        for path in (self.path, Path(str(self.path) + "-wal")):
            try:
                size += path.stat().st_size
            except FileNotFoundError:
                pass
        return {"records": counts, "jobs": jobs, "pending_messages": pending, "database_bytes": size}

    def revisions(self, event_id, space_id):
        self.get("event", event_id, space_id)
        with self.connect() as con:
            rows = con.execute("SELECT id,data,saved_at FROM versions WHERE event_id=? AND space_id=? ORDER BY saved_at DESC", (event_id, space_id)).fetchall()
        return [{"id": row["id"], "event": json.loads(row["data"]), "saved_at": row["saved_at"]} for row in rows]
