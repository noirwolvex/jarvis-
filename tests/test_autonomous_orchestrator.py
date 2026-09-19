from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.autonomous_orchestrator import (
    AutonomousTaskOrchestrator,
    ExecutionRouter,
)
from core.execution_telemetry import ToolResult


class ExecutionRouterTests(unittest.TestCase):
    def test_deterministic_engine_classification(self) -> None:
        self.assertEqual(ExecutionRouter.classify("read_file").execution_backend, "DIRECT")
        self.assertEqual(ExecutionRouter.classify("chrome_use_tab").execution_backend, "CDP_DOM")
        self.assertEqual(ExecutionRouter.classify("ui_activate").execution_backend, "UIA")
        self.assertEqual(ExecutionRouter.classify("ui_type_native").execution_backend, "RUST_NATIVE")
        self.assertEqual(ExecutionRouter.classify("screen_observe").execution_backend, "VISION")
        desktop = ExecutionRouter.classify("desktop_click", {"x": 10, "y": 20})
        self.assertEqual(desktop.execution_backend, "RUST_NATIVE")
        self.assertEqual(desktop.resolution_backend, "SCREEN")
        self.assertEqual(
            ExecutionRouter.classify("desktop_type", {"text": "hello"}).execution_backend,
            "RUST_NATIVE",
        )

    def test_native_semantic_action_reports_both_engines(self) -> None:
        route = ExecutionRouter.classify("whatsapp_select_chat_native", {"position": 2})
        self.assertEqual(route.execution_backend, "RUST_NATIVE")
        self.assertEqual(route.resolution_backend, "UIA")
        self.assertEqual(route.fallback_chain, ("VISION", "COORDINATE"))


class AutonomousTaskOrchestratorTests(unittest.TestCase):
    def test_override_contract_accepts_every_base_record_tool_parameter(self) -> None:
        from core.orchestrator import TaskOrchestrator

        base = inspect.signature(TaskOrchestrator.record_tool)
        rich = inspect.signature(AutonomousTaskOrchestrator.record_tool)
        self.assertTrue(
            set(base.parameters).issubset(set(rich.parameters)),
            f"Autonomous override drifted from base contract: base={base}, autonomous={rich}",
        )

    def test_deferred_review_flag_survives_autonomous_trace_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = AutonomousTaskOrchestrator(tmp)
            orchestrator.begin("type then press enter")
            orchestrator.record_tool(
                "desktop_type",
                {"text": "hello"},
                ToolResult("RUST_EXECUTED: typed", {"backend": "rust_native", "operations": []}),
                4.0,
                1,
                mutation=True,
                review_required=False,
            )
            self.assertFalse(orchestrator.needs_action_review())
            summary = orchestrator.summary()
            self.assertEqual(summary["engine_visibility"][-1]["execution_backend"], "rust_native")
            self.assertEqual(summary["engine_visibility"][-1]["resolution_backend"], "")

            orchestrator.require_action_review()
            self.assertTrue(orchestrator.needs_action_review())
            orchestrator.verify("typed text is visible", True, "fresh observation")
            self.assertFalse(orchestrator.needs_action_review())

    def test_rich_task_graph_and_engine_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                orchestrator = AutonomousTaskOrchestrator()
                orchestrator.begin("open WhatsApp and select the second chat")
                orchestrator.set_plan([
                    {
                        "id": "open-app",
                        "description": "Open WhatsApp",
                        "action": "launch_application",
                        "execution_method": "DIRECT",
                        "expected_result": "WhatsApp visible",
                        "verification_method": "PROCESS_STATE",
                    },
                    {
                        "id": "select-chat",
                        "description": "Select second visible chat",
                        "action": "select_chat",
                        "depends_on": ["open-app"],
                        "required_state": ["WhatsApp foreground", "chat list visible"],
                        "execution_method": "RUST_NATIVE",
                        "expected_result": "Second chat selected",
                        "verification_method": "UI_TREE_DIFF",
                        "fallback_strategy": ["VISION", "COORDINATE"],
                        "retry_policy": {"max_attempts": 2, "retry_only_if_safe": True},
                    },
                ])

                orchestrator.update_step("open-app", "running")
                orchestrator.record_tool(
                    "launch_installed_app",
                    {"query": "WhatsApp"},
                    "VERIFIED: WhatsApp process and foreground window are visible",
                    18.5,
                    1,
                    mutation=True,
                )
                graph = orchestrator.live_task_graph()
                self.assertEqual(graph[0]["status"], "DELIVERED")
                self.assertEqual(graph[0]["execution_backend"], "unreported")
                self.assertEqual(graph[1]["retry_policy"]["max_attempts"], 2)

                orchestrator.verify(
                    "open-app: application launch",
                    True,
                    "VERIFIED: WhatsApp foreground",
                )
                self.assertEqual(orchestrator.live_task_graph()[0]["status"], "VERIFIED")
                orchestrator.update_step("open-app", "completed")
                self.assertEqual(orchestrator.live_task_graph()[0]["status"], "COMPLETED")

                summary = orchestrator.summary()
                self.assertEqual(summary["engine_visibility"][-1]["execution_backend"], "unreported")
                self.assertIn("task_graph", summary)
                self.assertEqual(
                    summary["execution_priority"],
                    ["DIRECT", "CDP_DOM", "UIA", "RUST_NATIVE", "VISION", "COORDINATE"],
                )

    def test_recovery_history_is_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"JARVIS_WORKSPACE": tmp}, clear=False):
                orchestrator = AutonomousTaskOrchestrator()
                task = orchestrator.begin("click a semantic control")
                orchestrator.set_plan([{
                    "id": "click-target",
                    "description": "Activate target control",
                    "execution_method": "UIA",
                }])
                orchestrator.update_step("click-target", "running")
                hint = orchestrator.recovery_hint("ERROR: foreground changed", "ui_activate")
                self.assertIn("Recovery guidance", hint)
                summary = orchestrator.summary()
                self.assertEqual(summary["task_graph"][0]["status"], "RECOVERING")
                self.assertEqual(len(summary["recovery_history"]), 1)

                restored = AutonomousTaskOrchestrator(str(Path(tmp) / ".jarvis" / "traces"))
                restored.restore(task.task_id)
                self.assertEqual(len(restored.summary()["recovery_history"]), 1)
                self.assertEqual(restored.live_task_graph()[0]["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
