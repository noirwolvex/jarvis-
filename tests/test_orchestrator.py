from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.orchestrator import TaskOrchestrator


class OrchestratorTests(unittest.TestCase):
    def test_workflow_readback_failure_never_suggests_replaying_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = TaskOrchestrator(tmp)
            orchestrator.begin("Open and save the draft")
            for result in ("ERROR: Checkpoint pending. save draft: timeout",
                           "ERROR: Delivered action has no verified outcome; open the file"):
                with self.subTest(result=result):
                    hint = orchestrator.recovery_hint(result, "workflow_execute")
                    self.assertIn("retries only the declared read-only checkpoint", hint)
                    self.assertIn("Do not repeat an already delivered", hint)
                    self.assertNotIn("before repeating the action", hint)

    def test_bookkeeping_rejection_does_not_suggest_replaying_ui_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = TaskOrchestrator(tmp)
            orchestrator.begin("Open and save the draft")
            hint = orchestrator.recovery_hint(
                "ERROR: Cannot complete a mutating plan step from observation-only evidence; execute click/select/open/type/send first",
                "task_update_step")
        self.assertIn("plan/evidence bookkeeping rejection", hint)
        self.assertIn("Do not repeat a delivered", hint)
        self.assertNotIn("Inspect the foreground dialog", hint)

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
