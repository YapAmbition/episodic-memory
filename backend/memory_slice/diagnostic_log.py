"""Instance-owned, bounded diagnostic log. Never serialize prompts or exceptions."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import traceback
import uuid
from datetime import datetime, timezone


class QuietHandler(RotatingFileHandler):
    def handleError(self, record):
        # logging's default handler prints the record (and traceback) to stderr.
        self.failed = True


class DiagnosticLog:
    def __init__(self, directory, level="INFO", max_bytes=5 * 1024 * 1024, backups=4):
        self.directory = Path(directory) / "logs"
        self.level = str(level).upper() if str(level).upper() in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO"
        self.handler = None
        self.lock = threading.RLock()
        self.closed = False
        self.failed = False
        self.backups = backups
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.handler = QuietHandler(self.directory / "memory-slice.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
            self.handler.failed = False
            self.handler.setFormatter(logging.Formatter("%(message)s"))
        except Exception:
            self.failed = True

    def emit(self, event, level="INFO", exc=None, **fields):
        diagnostic_id = uuid.uuid4().hex[:12]
        try:
            if logging._nameToLevel.get(level, 20) < logging._nameToLevel[self.level]:
                return diagnostic_id
            row = {"time": datetime.now(timezone.utc).isoformat(), "level": level,
                   "event": event, "diagnostic_id": diagnostic_id}
            # Explicit metadata only; arbitrary payloads, headers and exception text are forbidden.
            allowed = {"space_id", "job_id", "stage", "code", "status", "purpose", "calls", "messages",
                       "candidates", "estimated_input_tokens", "input_limit", "elapsed_ms", "failures", "selected"}
            row.update({k: v for k, v in fields.items() if k in allowed and isinstance(v, (str, int, float, bool))})
            if exc is not None:
                chain, seen = [], set()
                while exc is not None and id(exc) not in seen and len(chain) < 8:
                    seen.add(id(exc))
                    chain.append({"type": type(exc).__name__, "frames": [
                        {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
                        for f in traceback.extract_tb(exc.__traceback__)]})
                    exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
                row["exceptions"] = chain
            with self.lock:
                if self.handler and not self.closed:
                    record = logging.LogRecord("memory_slice", logging._nameToLevel.get(level, 20), "", 0,
                                               json.dumps(row, ensure_ascii=False), (), None)
                    self.handler.handle(record)
        except Exception:
            self.failed = True
        return diagnostic_id

    def snapshot(self, download=False):
        # Fixed filenames only. Bounded reads also tolerate rotation and unavailable disks.
        with self.lock:
            parts = []
            try:
                if self.handler and not self.closed:
                    self.handler.flush()
                names = ([f"memory-slice.log.{i}" for i in range(self.backups, 0, -1)] if download else []) + ["memory-slice.log"]
                for name in names:
                    path = self.directory / name
                    if not path.exists():
                        continue
                    with path.open("rb") as stream:
                        limit = 5 * 1024 * 1024 if download else 256 * 1024
                        stream.seek(0, 2)
                        size = stream.tell()
                        stream.seek(max(0, size - limit))
                        data = stream.read(limit)
                        if size > limit:
                            data = data.partition(b"\n")[2]
                        parts.append(data.decode("utf-8", errors="replace"))
            except Exception:
                self.failed = True
            text = "".join(parts)
            return {"available": True, "level": self.level, "write_failed": self.failed or bool(self.handler and self.handler.failed),
                    "text": text if download else "\n".join(text.splitlines()[-200:])}

    def close(self):
        with self.lock:
            self.closed = True
            if self.handler:
                try:
                    self.handler.close()
                except Exception:
                    self.failed = True
