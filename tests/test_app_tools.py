from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from core.app_tools import _decode_start_apps, _score, register_app_tools
from core.orchestrator import TaskOrchestrator
from core.task_tools import register_task_tools
from core.tools import ToolRegistry


class AppToolsTests(unittest.TestCase):
    def test_decode_start_apps_accepts_single_and_multiple_rows(self) -> None:
        single = _decode_start_apps('{"Name":"WhatsApp","AppID":"WhatsApp_123!App"}')
        self.assertEqual(single, [{"name": "WhatsApp", "app_id": "WhatsApp_123!App"}])
        multiple = _decode_start_apps('[{"Name":"WhatsApp","AppID":"a"},{"Name":"WhatsApp Beta","AppID":"b"}]')
        self.assertEqual(len(multiple), 2)

    def test_app_name_scoring_prefers_exact_match(self) -> None:
        exact = _score("WhatsApp app", "WhatsApp")
        beta = _score("WhatsApp app", "WhatsApp Beta")
        unrelated = _score("WhatsApp app", "Calculator")
        self.assertGreater(exact, beta)
        self.assertGreater(beta, unrelated)

    def test_app_tools_are_registered_without_arbitrary_shell_tooling(self) -> None:
        registry = ToolRegistry()
        register_app_tools(registry)
        self.assertIn("find_installed_app", registry._tools)
        self.assertIn("launch_installed_app", registry._tools)
        self.assertEqual(registry._tools["find_installed_app"].risk.name, "LOW")
        self.assertEqual(registry._tools["launch_installed_app"].risk.name, "MEDIUM")


class TaskProgressTests(unittest.TestCase):
    def test_in_progress_alias_is_normalized_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                registry = ToolRegistry()
                orchestrator = TaskOrchestrator()
                orchestrator.begin("test")
                orchestrator.set_plan(["inspect"])
                register_task_tools(registry, orchestrator)
                result = registry.execute("task_update_step", {"step_id": "step-1", "status": "in_progress"}, approved=True)
                self.assertFalse(result.startswith("ERROR"))
                self.assertEqual(orchestrator.current.plan[0].status, "running")

    def test_completion_requires_successful_non_task_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                registry = ToolRegistry()
                orchestrator = TaskOrchestrator()
                orchestrator.begin("test")
                orchestrator.set_plan(["open app"])
                register_task_tools(registry, orchestrator)
                blocked = registry.execute("task_update_step", {"step_id": "step-1", "status": "completed"}, approved=True)
                self.assertTrue(blocked.startswith("ERROR"))
                orchestrator.record_tool("launch_installed_app", {"query": "WhatsApp"}, 'VERIFIED: {"window_title":"WhatsApp"}', 1.0, 1)
                allowed = registry.execute("task_update_step", {"step_id": "step-1", "status": "completed", "result": "Verified WhatsApp window"}, approved=True)
                self.assertFalse(allowed.startswith("ERROR"))
                self.assertEqual(orchestrator.current.plan[0].status, "completed")
                self.assertEqual(len(orchestrator.current.verifications), 1)
                self.assertTrue(orchestrator.current.verifications[0].verified)


if __name__ == "__main__":
    unittest.main()
