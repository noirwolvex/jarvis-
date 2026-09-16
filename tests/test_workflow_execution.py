from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.desktop_observation import DesktopObservationGate
from core.fast_execution_agent import FastExecutionFullAccessAgent
from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.task_tools import register_task_tools
from core.tools import ToolRegistry, ToolSpec
from core.workflow_tools import register_workflow_tools


def step(identifier="one", tool="verified_action", arguments=None, checkpoint=None):
    value = {"id": identifier, "description": identifier, "tool": tool, "arguments": arguments or {}}
    if checkpoint:
        value["checkpoint"] = checkpoint
    return value


class WorkflowExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.agent = FastExecutionFullAccessAgent.__new__(FastExecutionFullAccessAgent)
        agent = self.agent
        agent.orchestrator = TaskOrchestrator(self.directory.name)
        agent.orchestrator.strict_order = True
        agent.orchestrator.begin("fixture ordered mission")
        agent.tools = ToolRegistry()
        agent.tools.permissions.set_access_mode("full")
        agent.approval = lambda *_: True
        agent._stop = threading.Event()
        agent._desktop_observation = DesktopObservationGate()
        agent._tool_executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(agent._tool_executor.shutdown, wait=True)
        agent._browser_action_guard = lambda *_: None
        agent._system_prompt = lambda *_: "fixture"
        agent.workspace_context = Mock()
        agent.memory = Mock()
        agent.messages = []
        agent.model = "fixture"
        agent.max_turns = 8
        agent.client = Mock()
        self.action = Mock(return_value="VERIFIED: expected outcome")
        self.register("verified_action", Risk.MEDIUM, self.action)
        self.register("ui_wait_state", Risk.LOW, Mock(return_value="VERIFIED: target visible"))
        register_task_tools(agent.tools, agent.orchestrator)
        register_workflow_tools(agent)

    def register(self, name, risk, handler, schema=None):
        self.agent.tools.register(ToolSpec(name, "fixture", risk,
            schema or {"type": "object", "properties": {}, "additionalProperties": False}, handler))

    def execute(self, steps, workflow_id="mission"):
        return self.agent._execute_tool("workflow_execute", {"workflow_id": workflow_id, "steps": steps}, approved=True)

    def test_many_steps_execute_once_without_model_calls_or_screenshots(self):
        program = [step(f"s{i}") for i in range(16)]
        result = self.execute(program)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual(self.action.call_count, 16)
        self.execute(program)
        self.assertEqual(self.action.call_count, 16, "Completed steps must never be replayed")
        self.agent.client.chat.completions.create.assert_not_called()
        self.assertNotIn("screen_observe", [trace.name for trace in self.agent.orchestrator.current.traces])
        self.assertEqual(self.agent.orchestrator.summary()["workflows"][0]["remaining"], [])

    def test_browser_guard_nested_read_does_not_deadlock_single_executor(self):
        self.agent._browser_action_guard = FullAccessJarvisAgent._browser_action_guard.__get__(self.agent)
        self.register("browser_check_challenge", Risk.LOW, Mock(return_value=json.dumps({"challenge_detected": False})))
        self.register("browser_click", Risk.MEDIUM, self.action)
        result = self.execute([step(tool="browser_click")])
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.action.assert_called_once()

    def test_invalid_or_denied_later_step_prevents_all_side_effects(self):
        for last in (step("bad", arguments={"unknown": 1}), step("bad", tool="desktop_click", arguments={"x": 1, "y": 2})):
            self.assertTrue(self.execute([step(), last]).startswith("ERROR"))
        self.agent.tools.permissions.deny_tools.add("ui_wait_state")
        self.assertTrue(self.execute([step(), step("read", "ui_wait_state")]).startswith("ERROR"))
        self.action.assert_not_called()

    def test_checkpoint_retry_does_not_repeat_delivered_action(self):
        self.action.return_value = "ACTION_EXECUTED: navigation requested"
        waiter = Mock(side_effect=["ERROR: still loading", "VERIFIED: destination loaded"])
        self.register("ui_wait_state", Risk.LOW, waiter)
        program = [step(checkpoint={"tool": "ui_wait_state", "arguments": {}})]
        self.assertTrue(self.execute(program).startswith("ERROR: Checkpoint"))
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.assertEqual(self.action.call_count, 1)
        self.assertEqual(waiter.call_count, 2)
        self.assertTrue(self.agent.orchestrator.summary()["verified"])

    def test_uncertain_send_requires_live_review_and_is_never_replayed(self):
        self.action.side_effect = ["ERROR: delivery uncertain", "VERIFIED: later action"]
        program = [step(), step("two")]
        self.assertTrue(self.execute(program).startswith("ERROR"))
        self.assertTrue(self.execute(program).startswith("ERROR: Uncertain"))
        self.assertEqual(self.action.call_count, 1)
        args = {"workflow_id": "mission", "step_id": "one", "claim": "message exists"}
        self.assertTrue(self.agent._execute_tool("workflow_review", args, True).startswith("ERROR"))
        current = self.agent.orchestrator
        current.record_tool("ui_inspect", {}, "Message exists once in correct conversation", 1, 1)
        self.agent._execute_tool("task_verify", {"claim": "message exists", "verified": True, "evidence": "read current conversation"}, True)
        self.assertFalse(self.agent._execute_tool("workflow_review", args, True).startswith("ERROR"))
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.assertEqual(self.action.call_count, 2)

    def test_cancel_and_browser_checkpoint_stop_later_steps(self):
        def cancel():
            self.agent._stop.set()
            return "VERIFIED: first complete"
        self.action.side_effect = cancel
        self.assertTrue(self.execute([step(), step("two")]).startswith("CANCELLED"))
        self.assertEqual(self.action.call_count, 1)
        self.agent._stop.clear()
        self.agent.orchestrator.begin("challenge fixture")
        self.action.side_effect = None
        self.action.return_value = "BROWSER_ACTION_BLOCKED: human verification"
        self.assertTrue(self.execute([step(), step("two")]).startswith("BROWSER_ACTION_BLOCKED"))
        self.assertEqual(self.action.call_count, 2)

    def test_worker_crash_journal_never_replays_running_step(self):
        self.action.return_value = "ERROR: unknown outcome"
        self.execute([step()])
        current = self.agent.orchestrator.current
        current.workflows[0]["steps"][0]["status"] = "running"
        self.agent.orchestrator._persist(current)
        restored = TaskOrchestrator(self.directory.name)
        restored.restore(current.task_id)
        self.agent.orchestrator = restored
        register_workflow_tools(self.agent)
        self.assertTrue(self.execute([step()]).startswith("ERROR: Uncertain"))
        self.assertEqual(self.action.call_count, 1)

    def test_workflow_metadata_is_not_fresh_observation_evidence(self):
        self.action.return_value = "ERROR: uncertain"
        self.execute([step()])
        self.agent.orchestrator.record_tool("workflow_status", {}, "cached workflow data", 1, 1)
        result = self.agent._execute_tool("task_verify", {"claim": "sent", "verified": True, "evidence": "workflow metadata"}, True)
        self.assertTrue(result.startswith("ERROR"), result)

    def test_plan_cannot_skip_remove_or_reorder_required_steps(self):
        orchestrator = self.agent.orchestrator
        orchestrator.set_plan(["first", "second"])
        for operation in (lambda: orchestrator.update_step("step-1", "skipped"),
                          lambda: orchestrator.update_step("step-2", "running"),
                          lambda: orchestrator.set_plan(["only one"]),
                          lambda: orchestrator.set_plan([{"id": "step-2", "description": "second"}, {"id": "step-1", "description": "first"}])):
            with self.assertRaises(ValueError):
                operation()

    def response(self, calls=None, content="Done"):
        calls = calls or []
        items = [SimpleNamespace(id=f"call-{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args))) for i, (name, args) in enumerate(calls)]
        wire = {"role": "assistant", "content": content}
        if calls:
            wire["tool_calls"] = [{"id": item.id, "type": "function", "function": {"name": item.function.name, "arguments": item.function.arguments}} for item in items]
        message = SimpleNamespace(content=content, tool_calls=items, model_dump=lambda **_: wire)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_full_access_executes_compiled_program_in_one_model_tool_cycle(self):
        program = [step(f"s{i}") for i in range(8)]
        self.agent.client.chat.completions.create.side_effect = [
            self.response([("workflow_execute", {"workflow_id": "mission", "steps": program})]), self.response()]
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = self.agent.run("perform this complex fixture")
        self.assertEqual(result, "Done")
        self.assertEqual(self.action.call_count, 8)
        self.assertEqual(self.agent.orchestrator.current.metrics["model_calls"], 2)
        self.assertEqual(self.agent.orchestrator.current.status, "completed")

    def test_failed_parallel_tool_response_defers_later_mutation(self):
        self.action.return_value = "ERROR: target moved"
        self.agent.max_turns = 1
        self.agent.client.chat.completions.create.return_value = self.response([("verified_action", {}), ("verified_action", {})])
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            FullAccessJarvisAgent.run(self.agent, "ordered fixture")
        self.assertEqual(self.action.call_count, 1)
        self.assertTrue(any("Not executed" in message.get("content", "") for message in self.agent.messages))

    def test_fast_failure_recovers_same_task_without_restarting_completed_app(self):
        launch = Mock(side_effect=["VERIFIED: first application open", "ERROR: second still loading"])
        self.register("launch_installed_app", Risk.MEDIUM, launch, {"type": "object", "properties": {"query": {"type": "string"}, "timeout_seconds": {"type": "number"}}, "required": ["query"]})
        self.register("ui_inspect", Risk.LOW, Mock(return_value="VERIFIED: second application is now open"))
        self.agent.client.chat.completions.create.side_effect = [
            self.response([("ui_inspect", {})]),
            self.response([("task_verify", {"claim": "second app open", "verified": True, "evidence": "current UI tree shows second app"}),
                           ("task_update_step", {"step_id": "fast-2", "status": "completed"})]), self.response()]
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = self.agent.run("open Discord app then open Calculator app")
        self.assertEqual(result, "Done")
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(self.agent.orchestrator.current.status, "completed")
        self.assertEqual([item.status for item in self.agent.orchestrator.current.plan], ["completed", "completed"])


if __name__ == "__main__":
    unittest.main()
