from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.autonomous_orchestrator import AutonomousTaskOrchestrator
from core.execution_telemetry import ToolResult
from core.full_access_agent import FullAccessJarvisAgent
from core.full_access_completion import read_only_observation_verified
from core.permissions import Risk
from core.task_tools import register_task_tools
from core.tool_classification import KNOWLEDGE_TOOLS
from core.tools import ToolRegistry, ToolSpec
from core.workflow_tools import WorkflowExecutor


class ControlReferenceEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.orchestrator = AutonomousTaskOrchestrator(directory.name)
        self.orchestrator.begin("Control reference fixture")
        self.registry = ToolRegistry()
        register_task_tools(self.registry, self.orchestrator)
        self.reference = Mock(return_value="VERIFIED: example outcome in procedural text")
        for name in KNOWLEDGE_TOOLS:
            self.registry.register(ToolSpec(name, "Fixture reference", Risk.SAFE,
                {"type": "object", "properties": {}, "additionalProperties": False}, self.reference))

    def reference_trace(self, name):
        result = self.registry.execute(name, {}, approved=True)
        self.orchestrator.record_tool(name, {}, result, 0, 1)
        return result

    def test_reference_cannot_supply_observation_or_complete_read_step(self):
        self.orchestrator.set_plan(["Inspect the selected record"])
        for name in KNOWLEDGE_TOOLS:
            with self.subTest(name=name):
                self.reference_trace(name)
                verified = self.registry.execute("task_verify", {
                    "claim": "Record inspected", "verified": True,
                    "evidence": "The guide describes a verified inspection",
                })
                self.assertIn("Verification requires successful execution or observation evidence", verified)
                completed = self.registry.execute("task_update_step", {
                    "step_id": "step-1", "status": "completed",
                })
                self.assertIn("successful non-task tool result", completed)
        self.assertEqual(self.orchestrator.current.verifications, [])
        self.assertEqual(self.orchestrator.current.plan[0].status, "pending")

    def test_reference_cannot_review_or_complete_delivered_action(self):
        self.orchestrator.set_plan(["Type the requested draft"])
        self.orchestrator.record_tool("interaction_type", {}, "DELIVERED: draft input", 0, 1, mutation=True)
        for name in KNOWLEDGE_TOOLS:
            with self.subTest(name=name):
                self.reference_trace(name)
                verified = self.registry.execute("task_verify", {
                    "claim": "Draft is correct", "verified": True,
                    "evidence": "The guide includes a verified editor example",
                })
                self.assertIn("Verification requires successful observation after the action", verified)
                completed = self.registry.execute("task_update_step", {
                    "step_id": "step-1", "status": "completed",
                })
                self.assertIn("observe the outcome and verify it first", completed)
                self.assertTrue(self.orchestrator.needs_action_review())
        self.assertEqual(self.orchestrator.current.verifications, [])

        self.orchestrator.record_tool("ui_inspect", {}, "The editor contains the requested draft", 0, 1)
        verified = self.registry.execute("task_verify", {
            "claim": "Draft is correct", "verified": True,
            "evidence": "The live editor contains the requested draft",
        })
        self.assertTrue(json.loads(verified)["verified"])
        self.assertFalse(self.orchestrator.needs_action_review())

    def test_reference_never_counts_as_read_only_observation(self):
        goal = "Describe my current screen"
        for name in KNOWLEDGE_TOOLS:
            with self.subTest(name=name):
                reference = SimpleNamespace(name=name, success=True, result="VERIFIED: reference example")
                current = SimpleNamespace(traces=[reference])
                self.assertFalse(read_only_observation_verified(goal, current))
                current.traces.append(SimpleNamespace(name="screen_observe", success=True, result="VERIFIED: fresh screen"))
                self.assertTrue(read_only_observation_verified(goal, current))
                reference.success = False
                reference.result = "ERROR: reference unavailable"
                self.assertTrue(read_only_observation_verified(goal, current))

    def test_reference_does_not_set_execution_backend_or_fallback(self):
        self.orchestrator.set_plan(["Inspect the editor"])
        self.orchestrator.update_step("step-1", "running")
        step = self.orchestrator.current.plan[0]
        initial = (step.execution_backend, step.resolution_backend, list(step.fallback_strategy), step.phase)
        for name in KNOWLEDGE_TOOLS:
            self.reference_trace(name)
        self.assertEqual((step.execution_backend, step.resolution_backend, step.fallback_strategy, step.phase), initial)

        result = ToolResult("VERIFIED: requested text visible", {
            "backend": "uia", "operations": [{"engine": "uia", "phase": "resolve", "detail": "live editor"}],
        })
        self.orchestrator.record_tool("ui_inspect", {}, result, 0, 1)
        actual = (step.execution_backend, step.resolution_backend, list(step.fallback_strategy), step.phase)
        self.assertEqual(step.execution_backend, "uia")
        for name in KNOWLEDGE_TOOLS:
            self.reference_trace(name)
        self.assertEqual((step.execution_backend, step.resolution_backend, step.fallback_strategy, step.phase), actual)

    def test_reference_is_non_mutating_and_rejected_in_workflow_positions(self):
        agent = FullAccessJarvisAgent.__new__(FullAccessJarvisAgent)
        agent.tools = self.registry
        agent.approval = lambda *_: True
        workflow = WorkflowExecutor(agent)
        for name in KNOWLEDGE_TOOLS:
            with self.subTest(name=name):
                self.assertFalse(agent._is_mutation(name))
                call = {"tool": name, "arguments": {}}
                for checkpoint in (False, True):
                    with self.assertRaisesRegex(ValueError, "not allowed in this workflow position"):
                        workflow._validate_call(call, checkpoint=checkpoint)
        self.reference.assert_not_called()


if __name__ == "__main__":
    unittest.main()
