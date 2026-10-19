from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.tools import _write_file


class FileWriteVerificationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict("os.environ", {"JARVIS_WORKSPACE": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_unicode_and_line_endings_are_verified_without_translation(self):
        for content in ("JARVIS — مرحبا\nnext\n", "Windows\r\nlines\r\n", "", "emoji 🐈"):
            with self.subTest(content=content):
                result = _write_file("output/report.md", content)
                self.assertTrue(result.startswith("VERIFIED:"))
                evidence = json.loads(result.split(":", 1)[1])
                self.assertEqual(evidence["verification"], "exact_utf8_bytes")
                self.assertEqual(evidence["bytes"], len(content.encode("utf-8")))
                self.assertEqual((self.root / "output/report.md").read_bytes(), content.encode("utf-8"))

    def test_mismatched_or_unreadable_write_is_never_replayed(self):
        original = Path.write_text
        for readback in (b"truncated", OSError("readback unavailable")):
            with self.subTest(readback=readback), \
                    patch.object(Path, "write_text", autospec=True, side_effect=original) as write, \
                    patch.object(Path, "read_bytes", side_effect=readback if isinstance(readback, Exception) else None,
                                 return_value=readback):
                with self.assertRaises((RuntimeError, OSError)):
                    _write_file("report.md", "complete report")
                self.assertEqual(write.call_count, 1)

    def test_workspace_boundary_is_checked_before_writing(self):
        with patch.object(Path, "write_text") as write:
            with self.assertRaises(PermissionError):
                _write_file("../outside.md", "test")
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
