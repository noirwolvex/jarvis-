from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.orchestrator import TaskOrchestrator


class OrchestratorTests(unittest.TestCase):
    def test_tracks_failures_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                orchestrator = TaskOrchestrator()
                task = orchestrator.begin("save a file")
                orchestrator.start_turn(1)
                orchestrator.record_tool("desktop_hotkey", {"keys": ["ctrl", "shift", "s"]}, "ERROR: dialog timeout", 12.4, 1)
                hint = orchestrator.recovery_hint("ERROR: dialog timeout", "desktop_hotkey")
                self.assertIn("Recovery guidance", hint)
                orchestrator.finish("incomplete", "not verified")
                trace = Path(tmp) / ".jarvis" / "traces" / f"{task.task_id}.json"
                self.assertTrue(trace.exists())
                payload = json.loads(trace.read_text(encoding="utf-8"))
                self.assertEqual(payload["failures"], 1)
                self.assertEqual(payload["recoveries"], 1)
                self.assertEqual(payload["status"], "incomplete")
                self.assertEqual(payload["traces"][0]["name"], "desktop_hotkey")

    def test_summary_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                orchestrator = TaskOrchestrator()
                orchestrator.begin("check system")
                orchestrator.start_turn(2)
                summary = orchestrator.summary()
                self.assertEqual(summary["status"], "running")
                self.assertEqual(summary["turn"], 2)
                self.assertIn("task_id", summary)


if __name__ == "__main__":
    unittest.main()
