import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from memory_slice.diagnostic_log import DiagnosticLog
from memory_slice.service import MemoryService


class DiagnosticLogTests(unittest.TestCase):
    def test_private_payloads_and_exception_chain(self):
        with tempfile.TemporaryDirectory() as root:
            sink = DiagnosticLog(root)
            try:
                try:
                    raise ValueError("secret-key private-chat model-output")
                except ValueError as exc:
                    raise RuntimeError("Bearer secret-key") from exc
            except RuntimeError as exc:
                diagnostic = sink.emit("failed", "ERROR", exc=exc, job_id="job_test", prompt="private-chat", api_key="secret-key")
            data = sink.snapshot()["text"]
            self.assertNotIn("secret-key", data)
            self.assertNotIn("private-chat", data)
            self.assertNotIn("model-output", data)
            row = json.loads(data)
            self.assertEqual(row["diagnostic_id"], diagnostic)
            self.assertEqual([x["type"] for x in row["exceptions"]], ["RuntimeError", "ValueError"])
            self.assertTrue(row["exceptions"][0]["frames"])
            sink.close()

    def test_rotation_level_and_bounded_tail(self):
        with tempfile.TemporaryDirectory() as root:
            sink = DiagnosticLog(root, max_bytes=700, backups=4)
            sink.emit("hidden", "DEBUG")
            self.assertEqual(sink.snapshot()["text"], "")
            for i in range(100):
                sink.emit("example", calls=i)
            files = list((Path(root) / "logs").glob("memory-slice.log*"))
            self.assertEqual(len(files), 5)
            self.assertTrue(all(p.stat().st_size < 1000 for p in files))
            rows = [json.loads(x) for x in sink.snapshot(download=True)["text"].splitlines()]
            self.assertEqual(rows[-1]["calls"], 99)
            self.assertLess(len(rows), 100)
            sink.close()

    def test_disk_failure_and_closed_handler_do_not_raise(self):
        with tempfile.TemporaryDirectory() as root:
            sink = DiagnosticLog(root)
            with patch.object(sink.handler, "shouldRollover", side_effect=OSError("private-disk-path")):
                sink.emit("failed_write")
            self.assertTrue(sink.snapshot()["write_failed"])
            sink.close()
            sink.emit("after_close")
            self.assertNotIn("after_close", sink.snapshot()["text"])
            blocked = Path(root) / "blocked"
            blocked.write_text("file")
            broken = DiagnosticLog(blocked)
            broken.emit("failure")
            self.assertTrue(broken.snapshot()["write_failed"])
            broken.close()

    def test_instance_isolation_and_reload(self):
        with tempfile.TemporaryDirectory() as root:
            a = DiagnosticLog(Path(root) / "a")
            b = DiagnosticLog(Path(root) / "b")
            a.emit("only_a")
            self.assertEqual(b.snapshot()["text"], "")
            a.close()
            a = DiagnosticLog(Path(root) / "a")
            a.emit("new_instance")
            self.assertEqual(len(a.snapshot()["text"].splitlines()), 2)
            a.close(); b.close()

    def test_reporting_failure_never_interrupts_core(self):
        with tempfile.TemporaryDirectory() as root:
            sink = DiagnosticLog(root)
            core = MemoryService(root, diagnostics=sink)
            with patch.object(sink, "emit", side_effect=OSError("disk")):
                self.assertIsNone(core.report("failure"))
            sink.close()
