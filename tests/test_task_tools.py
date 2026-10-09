from __future__ import annotations

import tempfile
import unittest

from core.autonomous_orchestrator import AutonomousTaskOrchestrator
from core.task_tools import register_task_tools
from core.tools import ToolRegistry


class TaskCompletionEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.orchestrator = AutonomousTaskOrchestrator(directory.name)
        self.orchestrator.begin("Evidence recovery fixture")
        self.registry = ToolRegistry()
        register_task_tools(self.registry, self.orchestrator)

    def call(self, name, **arguments):
        result = self.registry.execute(name, arguments, approved=True)
        self.orchestrator.record_tool(name, arguments, result, 0, 1)
        return result

    def read(self, result="Approved revision is 4"):
        self.orchestrator.record_tool("browser_read_page", {}, result, 0, 1)

    def test_failed_completion_preserves_read_for_verification_and_retry(self):
        self.orchestrator.set_plan(["Identify highest approved revision"])
        self.read()
        failed = self.call("task_update_step", step_id="step-1", status="completed")
        self.assertIn("observe the outcome and verify it first", failed)
        verified = self.call("task_verify", claim="Revision 4 is approved", verified=True,
                             evidence="Page lists revision 5 as draft and revision 4 as approved")
        self.assertFalse(verified.startswith("ERROR"), verified)
        completed = self.call("task_update_step", step_id="step-1", status="completed")
        self.assertFalse(completed.startswith("ERROR"), completed)
        self.assertEqual(self.orchestrator.current.plan[0].status, "completed")
        self.assertEqual(sum(trace.name == "browser_read_page" for trace in self.orchestrator.current.traces), 1)

    def test_running_update_does_not_consume_completed_action_evidence(self):
        self.orchestrator.set_plan(["Open approved record"])
        self.orchestrator.record_tool("interaction_click", {}, "ACTION_EXECUTED: navigation", 0, 1, mutation=True)
        self.read("VERIFIED: approved record editor visible")
        self.assertFalse(self.call("task_update_step", step_id="step-1", status="running").startswith("ERROR"))
        completed = self.call("task_update_step", step_id="step-1", status="completed")
        self.assertFalse(completed.startswith("ERROR"), completed)
        self.assertTrue(self.orchestrator.current.plan[0].mutation_delivered)

    def test_completion_consumes_evidence_so_next_step_requires_new_read(self):
        self.orchestrator.set_plan(["Inspect library", "Inspect record"])
        self.read("VERIFIED: library ready")
        self.assertFalse(self.call("task_update_step", step_id="step-1", status="completed").startswith("ERROR"))
        self.call("task_update_step", step_id="step-2", status="running")
        rejected = self.call("task_update_step", step_id="step-2", status="completed")
        self.assertIn("successful non-task tool result", rejected)
        self.assertEqual(self.orchestrator.current.plan[1].status, "running")
        self.read("VERIFIED: record ready")
        completed = self.call("task_update_step", step_id="step-2", status="completed")
        self.assertFalse(completed.startswith("ERROR"), completed)

    def test_failed_bookkeeping_cannot_replace_missing_mutation(self):
        self.orchestrator.set_plan(["Open approved record"])
        self.read("VERIFIED: record link exists")
        for _ in range(2):
            rejected = self.call("task_update_step", step_id="step-1", status="completed")
            self.assertIn("observation-only evidence", rejected)
        self.assertEqual(self.orchestrator.current.plan[0].status, "pending")


if __name__ == "__main__":
    unittest.main()
