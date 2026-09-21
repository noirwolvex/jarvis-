"""Diagnostic protocol tests use isolated Python pipes; never start JARVIS or input."""
import subprocess
import sys
import time
import unittest

from scripts.jarvis_runtime import _WorkerProtocolReader


class WorkerProtocolReaderTests(unittest.TestCase):
    def reader(self, code):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        reader = _WorkerProtocolReader(process)
        self.addCleanup(reader.close)
        return reader

    def assert_closed(self, reader):
        reader.close()
        reader.close()  # Cleanup is idempotent.
        self.assertIsNotNone(reader.process.poll())
        self.assertTrue(all(not thread.is_alive() for thread in reader._threads))
        self.assertTrue(all(stream.closed for stream in
                            (reader.process.stdin, reader.process.stdout, reader.process.stderr)))

    def test_incomplete_line_does_not_block_deadline_and_cleanup_releases_readers(self):
        reader = self.reader(
            "import sys,time; print('{\"type\":\"ready\"}'); "
            "sys.stdout.write('{'); sys.stdout.flush(); time.sleep(30)"
        )
        self.assertEqual(reader.read_message(timeout_s=3)["type"], "ready")
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            reader.read_message("pending", timeout_s=0.1)
        self.assertLess(time.monotonic() - started, 1)
        self.assert_closed(reader)

    def test_large_stderr_is_drained_while_waiting_for_matching_result(self):
        reader = self.reader(
            "import sys; print('{\"type\":\"ready\"}'); "
            "sys.stderr.write('e'*200000); sys.stderr.flush(); "
            "print('{\"type\":\"progress\"}'); "
            "print('{\"type\":\"result\",\"id\":\"fixture\",\"ok\":true}')"
        )
        reader.read_message(timeout_s=3)
        self.assertTrue(reader.read_message("fixture", timeout_s=3)["ok"])
        self.assert_closed(reader)
        self.assertLessEqual(len(reader._stderr), 8192)

    def test_closed_protocol_returns_bounded_stderr(self):
        reader = self.reader("import sys; sys.stderr.write('fixture failure'); sys.stderr.flush()")
        with self.assertRaisesRegex(RuntimeError, "protocol closed.*fixture failure"):
            reader.read_message(timeout_s=3)
        self.assert_closed(reader)

    def test_invalid_protocol_messages_fail_instead_of_hanging(self):
        for text in ("not json", "[]"):
            with self.subTest(text=text):
                reader = self.reader(f"print({text!r})")
                with self.assertRaisesRegex(RuntimeError, "protocol"):
                    reader.read_message(timeout_s=3)
                self.assert_closed(reader)
