from __future__ import annotations

import io
import json
import sys
import unittest
from unittest.mock import patch

from core import full_access_worker as worker


class WorkerEncodingTests(unittest.TestCase):
    def test_write_is_safe_on_legacy_windows_pipe_encoding(self) -> None:
        raw = io.BytesIO()
        legacy_stdout = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
        payload = {"type": "ready", "message": "مرحبا — JARVIS"}

        with patch.object(sys, "stdout", legacy_stdout):
            worker._write(payload)
            legacy_stdout.flush()

        encoded = raw.getvalue().decode("ascii")
        decoded = json.loads(encoded)
        self.assertEqual(decoded, payload)

    def test_runtime_env_forces_utf8_python_protocol(self) -> None:
        from pathlib import Path
        from scripts import jarvis_runtime

        with patch.dict(jarvis_runtime.os.environ, {}, clear=True):
            env = jarvis_runtime._runtime_env(
                Path("session"),
                {"0": "observe-0"},
                {"0": "input-0"},
            )
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")


if __name__ == "__main__":
    unittest.main()
