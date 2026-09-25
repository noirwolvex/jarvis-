from __future__ import annotations

import tempfile
import unittest

from core.autonomous_orchestrator import AutonomousTaskOrchestrator, ExecutionRouter
from core.permissions import PermissionEngine, Risk
from core.task_tools import register_task_tools
from core.tools import ToolRegistry
from core.wincom_tools import register_wincom_tools


class AdaptiveExecutionRouterTests(unittest.TestCase):
    def test_wincom_uses_application_api_before_gui_fallbacks(self) -> None:
        route = ExecutionRouter.classify("wincom_excel_set_cell", {"cell": "B7", "value": "ok"})
        self.assertEqual(route.execution_backend, "APP_API")
        self.assertEqual(route.resolution_backend, "WINCOM")
        self.assertEqual(route.fallback_chain, ("UIA", "RUST_NATIVE", "VISION", "COORDINATE"))


class DynamicDagRewriteTests(unittest.TestCase):
    def test_failed_step_is_preserved_and_downstream_is_rewired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = AutonomousTaskOrchestrator(tmp)
            task = orchestrator.begin("Recover a failed semantic desktop action")
            orchestrator.set_plan([
                {"id": "open-app", "description": "Open application"},
                {"id": "select-target", "description": "Select target", "depends_on": ["open-app"]},
                {"id": "type-text", "description": "Type text", "depends_on": ["select-target"]},
            ])
            orchestrator.update_step("open-app", "running")
            orchestrator.update_step("open-app", "completed", "Application visible")
            orchestrator.update_step("select-target", "running")
            orchestrator.update_step("select-target", "failed", "UIA target ambiguous")

            inserted = orchestrator.rewrite_failed_step(
                "select-target",
                [
                    {"description": "Inspect fresh semantic state", "execution_method": "UIA"},
                    {"description": "Activate alternate verified target", "execution_method": "RUST_NATIVE"},
                ],
                "Original UIA target became ambiguous",
            )

            self.assertEqual(
                [step.id for step in inserted],
                ["recovery-select-target-1", "recovery-select-target-2"],
            )
            ids = [step.id for step in orchestrator.current.plan]
            self.assertEqual(
                ids,
                [
                    "open-app",
                    "select-target",
                    "recovery-select-target-1",
                    "recovery-select-target-2",
                    "type-text",
                ],
            )
            original = next(step for step in orchestrator.current.plan if step.id == "select-target")
            downstream = next(step for step in orchestrator.current.plan if step.id == "type-text")
            self.assertEqual(original.status, "failed")
            self.assertEqual(original.phase, "RECOVERING")
            self.assertEqual(inserted[0].depends_on, ["open-app"])
            self.assertEqual(inserted[1].depends_on, ["recovery-select-target-1"])
            self.assertEqual(downstream.depends_on, ["recovery-select-target-2"])
            self.assertFalse(orchestrator.step_is_resolved(original))

            orchestrator.update_step("recovery-select-target-1", "running")
            orchestrator.update_step("recovery-select-target-1", "completed", "Fresh target resolved")
            orchestrator.update_step("recovery-select-target-2", "running")
            orchestrator.update_step("recovery-select-target-2", "completed", "Alternate target selected")
            self.assertTrue(orchestrator.step_is_resolved(original))
            self.assertEqual([step.id for step in orchestrator.ready_steps()], ["type-text"])
            recovered_node = next(node for node in orchestrator.live_task_graph() if node["id"] == "select-target")
            self.assertEqual(recovered_node["status"], "COMPLETED")
            self.assertTrue(recovered_node["recovered"])
            self.assertEqual(recovered_node["original_status"], "RECOVERING")
            self.assertEqual(recovered_node["verification_result"], "VERIFIED")

            summary = orchestrator.summary()
            self.assertEqual(summary["metrics"]["graph_rewrites"], 1)
            self.assertEqual(summary["graph_rewrites"][0]["failed_step_id"], "select-target")
            self.assertEqual(summary["graph_rewrites"][0]["rewired_step_ids"], ["type-text"])

            restored = AutonomousTaskOrchestrator(tmp)
            restored.restore(task.task_id)
            restored_original = next(step for step in restored.current.plan if step.id == "select-target")
            self.assertTrue(restored.step_is_resolved(restored_original))
            self.assertEqual(restored.summary()["graph_rewrites"][0]["reason"], "Original UIA target became ambiguous")

    def test_rewrite_refuses_completed_node_and_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = AutonomousTaskOrchestrator(tmp)
            orchestrator.begin("Guard graph integrity")
            orchestrator.set_plan([
                {"id": "done", "description": "Done"},
                {"id": "failed", "description": "Failed", "depends_on": ["done"]},
            ])
            orchestrator.update_step("done", "running")
            orchestrator.update_step("done", "completed")
            with self.assertRaises(ValueError):
                orchestrator.rewrite_failed_step("done", ["should not run"])

            orchestrator.update_step("failed", "running")
            orchestrator.update_step("failed", "failed")
            with self.assertRaises(ValueError):
                orchestrator.rewrite_failed_step(
                    "failed",
                    [{"id": "done", "description": "Duplicate"}],
                )


class AdaptiveToolRegistrationTests(unittest.TestCase):
    def test_recovery_and_wincom_tools_are_registered_with_policy_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry()
            orchestrator = AutonomousTaskOrchestrator(tmp)
            register_task_tools(registry, orchestrator)
            register_wincom_tools(registry)

            self.assertIn("task_rewrite_recovery", registry._tools)
            self.assertIn("wincom_inspect", registry._tools)
            self.assertIn("wincom_word_append", registry._tools)

            task_plan = registry._tools["task_plan"].input_schema
            execution_enum = task_plan["properties"]["steps"]["items"]["oneOf"][1]["properties"]["execution_method"]["enum"]
            self.assertIn("APP_API", execution_enum)

            policy = PermissionEngine()
            policy.set_access_mode("restricted")
            allowed, _ = policy.check("wincom_inspect", Risk.SAFE, approved=True)
            blocked, reason = policy.check("wincom_word_append", Risk.MEDIUM, approved=True)
            self.assertTrue(allowed)
            self.assertFalse(blocked)
            self.assertIn("disabled in restricted mode", reason)


if __name__ == "__main__":
    unittest.main()
