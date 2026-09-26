from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.autonomous_orchestrator import AutonomousTaskOrchestrator
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

    def test_pre_input_rejection_keeps_workflow_retryable_without_replaying_completed_steps(self):
        from core.desktop_input import InputNotDispatchedError
        reject = Mock(side_effect=InputNotDispatchedError("editor not writable"))
        self.register("ui_type_native", Risk.MEDIUM, reject)
        program = [step("launch"), step("type", "ui_type_native")]
        first = self.execute(program)
        self.assertTrue(first.startswith("ERROR:"), first)
        self.assertEqual(self.agent.orchestrator.current.workflows[0]["steps"][1]["status"], "blocked")
        self.assertFalse(self.agent.orchestrator.needs_action_review())
        reject.side_effect = None
        reject.return_value = "VERIFIED: exact text readback"
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.action.assert_called_once()
        self.assertEqual(reject.call_count, 2)

    def test_fast_paths_do_not_record_rejected_typing_as_delivered_mutation(self):
        from core.desktop_input import InputNotDispatchedError
        from core.fast_mission import execute_fast_mission
        from core.whatsapp_fast_mission import execute_whatsapp_ordinal_mission
        self.register("launch_installed_app", Risk.MEDIUM, self.action, {"type": "object"})
        self.register("whatsapp_select_chat_native", Risk.MEDIUM, self.action, {"type": "object"})
        reject = Mock(side_effect=InputNotDispatchedError("editor not writable"))
        self.register("ui_type_native", Risk.MEDIUM, reject, {"type": "object"})
        for run, mission in ((execute_fast_mission, "Open Notepad and write CAT"),
                             (execute_whatsapp_ordinal_mission, "Open WhatsApp and select the first chat and write CAT")):
            with self.subTest(mission=mission), patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
                result = run(self.agent, mission)
            self.assertIn("InputNotDispatchedError", result)
            self.assertFalse(self.agent.orchestrator.needs_action_review())
            self.assertIsNone(self.agent.orchestrator.current.in_flight)

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

    def test_universal_wait_checkpoint_completes_delivered_action_in_same_workflow_call(self):
        self.action.return_value = "DELIVERED: semantic click"
        waiter = Mock(return_value="VERIFIED: target visible")
        self.register("interaction_wait", Risk.LOW, waiter)
        program = [
            step(
                checkpoint={"tool": "interaction_wait", "arguments": {"surface": "desktop", "target": "Composer"}}
            )
        ]
        result = self.execute(program)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.action.assert_called_once()
        waiter.assert_called_once()
        self.agent.client.chat.completions.create.assert_not_called()

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

    def test_transient_checkpoint_timeout_recovers_without_model_or_action_replay(self):
        self.action.return_value = "DELIVERED: navigation requested"
        waiter = Mock(side_effect=["ERROR: TimeoutError: page still loading", "VERIFIED: destination loaded"])
        self.register("ui_wait_state", Risk.LOW, waiter)
        program = [step(checkpoint={"tool": "ui_wait_state", "arguments": {}})]

        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.action.assert_called_once()
        self.assertEqual(waiter.call_count, 2)
        current = self.agent.orchestrator.current
        self.assertEqual(current.metrics["checkpoint_read_retries"], 1)
        self.assertEqual(current.workflows[0]["steps"][0]["checkpoint_attempts"], 2)
        self.agent.client.chat.completions.create.assert_not_called()

    def test_persistent_timeout_is_bounded_and_resume_only_reads_checkpoint(self):
        self.action.side_effect = ["DELIVERED: navigation requested", "VERIFIED: next action"]
        waiter = Mock(side_effect=["ERROR: timed out waiting for destination"] * 2 + ["VERIFIED: destination loaded"])
        self.register("ui_wait_state", Risk.LOW, waiter)
        program = [step(checkpoint={"tool": "ui_wait_state", "arguments": {}}), step("two")]

        self.assertTrue(self.execute(program).startswith("ERROR: Checkpoint pending"))
        self.assertEqual(waiter.call_count, 2)
        self.action.assert_called_once()
        self.assertEqual([item["status"] for item in self.agent.orchestrator.current.workflows[0]["steps"]],
                         ["checkpoint_pending", "pending"])
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.assertEqual(waiter.call_count, 3)
        self.assertEqual(self.action.call_count, 2)

    def test_non_timeout_checkpoints_never_retry_automatically(self):
        self.action.return_value = "DELIVERED: requested action"
        for result in ("ERROR: ambiguous destination", "BROWSER_ACTION_BLOCKED: challenge",
                       "CANCELLED: Emergency stop is active"):
            with self.subTest(result=result):
                self.agent.orchestrator.begin("checkpoint fixture")
                waiter = Mock(return_value=result)
                self.register("ui_wait_state", Risk.LOW, waiter)
                before = self.action.call_count
                self.assertFalse(self.execute([
                    step(checkpoint={"tool": "ui_wait_state", "arguments": {}}), step("two")
                ]).startswith("VERIFIED:"))
                waiter.assert_called_once()
                self.assertEqual(self.action.call_count, before + 1)

    def test_emergency_stop_between_timeout_reads_prevents_retry(self):
        self.action.return_value = "DELIVERED: navigation requested"
        def stop_during_read():
            self.agent._stop.set()
            return "ERROR: TimeoutError: loading"
        waiter = Mock(side_effect=stop_during_read)
        self.register("ui_wait_state", Risk.LOW, waiter)
        result = self.execute([step(checkpoint={"tool": "ui_wait_state", "arguments": {}})])

        self.assertTrue(result.startswith("CANCELLED:"), result)
        self.action.assert_called_once()
        waiter.assert_called_once()
        self.assertEqual(self.agent.orchestrator.current.metrics.get("checkpoint_read_retries", 0), 0)

    def test_completed_workflow_resume_does_not_reauthorize_completed_effects(self):
        program = [step()]
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.agent.tools.permissions.deny_tools.add("verified_action")
        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.action.assert_called_once()

    def test_partial_resume_validates_only_remaining_actions(self):
        def stop_after_first():
            self.agent._stop.set()
            return "VERIFIED: first action complete"
        self.action.side_effect = stop_after_first
        later = Mock(return_value="VERIFIED: remaining action complete")
        self.register("remaining_action", Risk.MEDIUM, later)
        program = [step(), step("two", "remaining_action")]
        self.assertTrue(self.execute(program).startswith("CANCELLED:"))
        self.agent._stop.clear()
        self.agent.tools.permissions.deny_tools.add("verified_action")

        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.action.assert_called_once()
        later.assert_called_once()

    def test_checkpoint_resume_does_not_reauthorize_delivered_mutation(self):
        self.action.return_value = "DELIVERED: requested action"
        waiter = Mock(side_effect=["ERROR: still loading", "VERIFIED: completed"])
        self.register("ui_wait_state", Risk.LOW, waiter)
        program = [step(checkpoint={"tool": "ui_wait_state", "arguments": {}})]
        self.assertTrue(self.execute(program).startswith("ERROR: Checkpoint"))
        self.agent.tools.permissions.deny_tools.add("verified_action")

        self.assertTrue(self.execute(program).startswith("VERIFIED:"))
        self.action.assert_called_once()
        self.assertEqual(waiter.call_count, 2)

    def test_malformed_resume_journal_cannot_skip_or_reorder_steps(self):
        program = [step(), step("two")]
        self.action.return_value = "ERROR: uncertain"
        self.execute(program)
        original = copy.deepcopy(self.agent.orchestrator.current.workflows[0])
        journals = [[], [{"id": "one", "status": "completed"}],
                    [{"id": "two", "status": "pending"}, {"id": "one", "status": "pending"}],
                    [{"id": "one", "status": "pending"}, {"id": "one", "status": "pending"}],
                    [{"id": "one", "status": "pending"}, {"id": "two", "status": "completed"}],
                    [{"id": "one", "status": "checkpoint_pending"}, {"id": "two", "status": "pending"}],
                    [{"id": "one", "status": "skipped"}, {"id": "two", "status": "pending"}]]
        for records in journals:
            with self.subTest(records=records):
                workflow = copy.deepcopy(original)
                workflow["steps"] = records
                self.agent.orchestrator.current.workflows = [workflow]
                result = self.execute(program)
                self.assertTrue(result.startswith("ERROR"), result)
                self.assertIn("Workflow journal", result)
                review = self.agent._execute_tool("workflow_review", {
                    "workflow_id": "mission", "step_id": "one", "claim": "fixture claim"
                }, True)
                self.assertIn("Workflow journal", review)
        self.action.assert_called_once()

    def test_resume_cannot_change_bound_program(self):
        self.assertTrue(self.execute([step()]).startswith("VERIFIED:"))
        result = self.execute([step(), step("two")])
        self.assertIn("immutable ordered program", result)
        self.action.assert_called_once()

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

    def test_focused_native_input_burst_uses_one_post_burst_observation(self):
        native_type = Mock(return_value="RUST_EXECUTED: typed")
        native_press = Mock(return_value="RUST_EXECUTED: pressed")
        observe = Mock(return_value='VERIFIED: {"foreground_hwnd": 123, "scene_bound": false, "stable": true}')
        self.register(
            "desktop_type",
            Risk.MEDIUM,
            native_type,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )
        self.register(
            "desktop_press",
            Risk.MEDIUM,
            native_press,
            {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
        )
        self.register(
            "screen_observe",
            Risk.LOW,
            observe,
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.agent.client.chat.completions.create.side_effect = [
            self.response([
                ("desktop_type", {"text": "hello"}),
                ("desktop_press", {"key": "enter"}),
            ]),
            self.response([
                ("task_verify", {
                    "claim": "focused keyboard burst applied",
                    "verified": True,
                    "evidence": "fresh post-burst screen observation",
                }),
            ]),
            self.response(content="Done"),
        ]
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = FullAccessJarvisAgent.run(self.agent, "type hello and press enter")

        self.assertEqual(result, "Done")
        native_type.assert_called_once_with(text="hello")
        native_press.assert_called_once_with(key="enter")
        observe.assert_called_once()
        traces = [trace.name for trace in self.agent.orchestrator.current.traces]
        self.assertEqual(traces.count("screen_observe"), 1)
        self.assertEqual(self.agent.orchestrator.current.metrics["native_input_burst_actions"], 2)
        self.assertEqual(self.agent.orchestrator.current.metrics["native_input_bursts"], 1)
        self.assertEqual(self.agent.orchestrator.current.metrics["model_calls"], 3)
        self.assertFalse(self.agent.orchestrator.needs_action_review())

    def test_autonomous_orchestrator_executes_native_burst_without_contract_error(self):
        autonomous = AutonomousTaskOrchestrator(self.directory.name)
        autonomous.strict_order = True
        autonomous.begin("type hello and press enter")
        self.agent.orchestrator = autonomous
        register_task_tools(self.agent.tools, autonomous)

        native_type = Mock(return_value="RUST_EXECUTED: typed")
        native_press = Mock(return_value="RUST_EXECUTED: pressed")
        observe = Mock(return_value='VERIFIED: {"foreground_hwnd": 123, "scene_bound": false, "stable": true}')
        self.register(
            "desktop_type",
            Risk.MEDIUM,
            native_type,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )
        self.register(
            "desktop_press",
            Risk.MEDIUM,
            native_press,
            {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
        )
        self.register(
            "screen_observe",
            Risk.LOW,
            observe,
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.agent.client.chat.completions.create.side_effect = [
            self.response([
                ("desktop_type", {"text": "hello"}),
                ("desktop_press", {"key": "enter"}),
            ]),
            self.response([
                ("task_verify", {
                    "claim": "native burst visible",
                    "verified": True,
                    "evidence": "fresh post-burst observation",
                }),
            ]),
            self.response(content="Done"),
        ]

        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = FullAccessJarvisAgent.run(self.agent, "type hello and press enter", resume_current=True)

        self.assertEqual(result, "Done")
        native_type.assert_called_once_with(text="hello")
        native_press.assert_called_once_with(key="enter")
        observe.assert_called_once()
        summary = autonomous.summary()
        # These fixture handlers do not dispatch a native adapter. Tool names and
        # result prose must never masquerade as actual Rust execution evidence.
        self.assertEqual(summary["engine_visibility"][0]["execution_backend"], "unreported")
        self.assertEqual(summary["engine_visibility"][1]["execution_backend"], "unreported")
        self.assertFalse(autonomous.needs_action_review())

    def test_failed_parallel_tool_response_defers_later_mutation(self):
        self.action.return_value = "ERROR: target moved"
        self.agent.max_turns = 1
        self.agent.client.chat.completions.create.return_value = self.response([("verified_action", {}), ("verified_action", {})])
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            FullAccessJarvisAgent.run(self.agent, "ordered fixture")
        self.assertEqual(self.action.call_count, 1)
        self.assertTrue(any("Not executed" in message.get("content", "") for message in self.agent.messages))

    def test_live_context_keeps_one_image_without_extra_model_round_trips(self):
        from PIL import Image
        from core.live_desktop import LiveDesktopMonitor
        from core.vision_tools import is_internal_vision_message
        self.agent.live_monitor = LiveDesktopMonitor(lambda: False,
            capture=lambda: (Image.new("RGB", (30, 30)), 12, (0, 0)))
        self.agent.live_monitor.poll()
        self.agent.client.chat.completions.create.side_effect = [
            self.response([("workflow_execute", {"workflow_id": "mission", "steps": [step()]})]), self.response()]
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            self.assertEqual(self.agent.run("perform this complex fixture"), "Done")
        self.assertEqual(self.agent.client.chat.completions.create.call_count, 2)
        for call in self.agent.client.chat.completions.create.call_args_list:
            self.assertEqual(sum(is_internal_vision_message(item) for item in call.kwargs["messages"]), 1)
        self.action.assert_called_once()

    def test_requested_coordinate_observation_reaches_model_before_live_preview(self):
        from core.vision_tools import VISION_MARKER
        exact = {"role": "user", "content": [{"type": "text", "text": VISION_MARKER + " exact coordinates"}]}
        live = {"role": "user", "content": [{"type": "text", "text": VISION_MARKER + " Live preview observed"}]}
        self.agent.live_monitor = Mock()
        self.agent.live_monitor.model_message.return_value = live
        self.agent._replace_internal_vision(exact)
        self.agent._refresh_live_vision()
        self.assertEqual(self.agent.messages, [exact])
        self.agent.live_monitor.model_message.assert_not_called()
        self.agent._refresh_live_vision()
        self.assertEqual(self.agent.messages, [live])

    def test_stale_live_context_is_removed_without_removing_user_mission(self):
        from core.vision_tools import VISION_MARKER
        mission = {"role": "user", "content": "original mission"}
        self.agent.messages = [mission]
        self.agent._replace_internal_vision({"role": "user", "content": [
            {"type": "text", "text": VISION_MARKER + " Live preview observed"}]}, live=True)
        self.agent.live_monitor = Mock()
        self.agent.live_monitor.model_message.return_value = None
        self.agent._refresh_live_vision()
        self.assertEqual(self.agent.messages, [mission])

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

    def test_arabic_simple_mission_verifies_steps_without_model_call(self):
        launch = Mock(return_value="VERIFIED: requested application visible")
        self.register("launch_installed_app", Risk.MEDIUM, launch, {"type": "object", "properties": {
            "query": {"type": "string"}, "timeout_seconds": {"type": "number"}}, "required": ["query"]})
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            self.agent.run("افتح ديسكورد ثم افتح الحاسبة ثم افتح المفكرة")
        self.assertEqual([call.kwargs["query"] for call in launch.call_args_list], ["Discord", "Calculator", "Notepad"])
        self.agent.client.chat.completions.create.assert_not_called()
        self.assertEqual(self.agent.orchestrator.current.status, "completed")
        self.assertEqual([item.status for item in self.agent.orchestrator.current.plan], ["completed"] * 3)


if __name__ == "__main__":
    unittest.main()
