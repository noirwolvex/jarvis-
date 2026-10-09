from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from core.desktop_observation import DesktopObservationGate
from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


def response(*calls):
    tool_calls = [
        SimpleNamespace(id=f"call-{index}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
        for index, (name, arguments) in enumerate(calls)
    ]
    message = SimpleNamespace(
        content="Done", tool_calls=tool_calls,
        model_dump=lambda **_: {"role": "assistant", "content": "Done"},
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class DesktopRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.agent = agent = object.__new__(FullAccessJarvisAgent)
        agent._stop = threading.Event()
        agent._device_action_active = threading.Event()
        agent._desktop_observation = DesktopObservationGate()
        agent.orchestrator = TaskOrchestrator(temporary.name)
        agent.tools = ToolRegistry()
        agent.tools.permissions.set_access_mode("full")
        self.click = Mock(return_value="DELIVERED: click")
        self.type_text = Mock(return_value="VERIFIED: exact text readback")
        self.frame = {
            "foreground_hwnd": 7, "stable": True,
            "source_width": 100, "source_height": 100,
            "virtual_origin_x": 0, "virtual_origin_y": 0,
        }
        self.observe = Mock(return_value="VERIFIED: " + json.dumps(self.frame))
        for name, risk, handler in (
            ("desktop_click_button", Risk.MEDIUM, self.click),
            ("desktop_type", Risk.MEDIUM, self.type_text),
            ("ui_type", Risk.MEDIUM, self.type_text),
            ("screen_observe", Risk.LOW, self.observe),
        ):
            agent.tools.register(ToolSpec(name, name, risk, {"type": "object"}, handler))
        agent.approval = lambda *_: True
        agent._browser_action_guard = lambda *_: None
        agent._system_prompt = lambda *_: "fixture"
        agent._chat_completion = Mock()
        agent.pop_provider_failover_notice = lambda: None
        agent.workspace_context = Mock()
        agent.memory = Mock()
        agent.messages = []
        agent.max_turns = 8
        agent._tool_executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(agent.close)
        self.vision = {"role": "user", "content": [{"type": "text", "text": "fixture observation"}]}
        for target, value in (
            ("core.full_access_agent._chrome_tab_rows", []),
            ("core.desktop_observation.foreground_identity", 7),
            ("core.full_access_agent.vision_followup_message", self.vision),
        ):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_explicit_multi_step_goal_cannot_finish_with_a_missing_action(self):
        self.agent._chat_completion.side_effect = [
            response(("ui_type", {"text": "hello"})),
            response(),
            response(),
        ]
        result = self.agent.run("type hello and then press enter")
        self.assertTrue(result.startswith("INCOMPLETE:"), result)
        self.assertEqual(self.agent.orchestrator.current.status, "incomplete")
        self.type_text.assert_called_once_with(text="hello")
        self.assertEqual(self.agent._chat_completion.call_count, 3)
        metrics = self.agent.orchestrator.current.metrics
        self.assertEqual(metrics["ordered_actions_required"], 2)
        self.assertEqual(metrics["ordered_actions_evidenced"], 1)
        self.assertEqual(metrics["ordered_action_repair_requests"], 1)
        self.assertTrue(any("unexecuted original clauses" in str(item) for item in self.agent.messages))

    def test_two_typing_tools_cannot_substitute_for_click_then_type(self):
        self.agent._chat_completion.side_effect = [
            response(("ui_type", {"text": "first"}), ("ui_type", {"text": "second"})),
            response(),
            response(),
        ]
        result = self.agent.run("click editor then type hello")
        self.assertIn("INCOMPLETE:", result)
        self.assertEqual(self.agent.orchestrator.current.status, "incomplete")
        self.assertEqual(self.type_text.call_count, 2)
        self.assertEqual(self.agent.orchestrator.current.metrics["ordered_actions_required"], 2)
        self.assertEqual(self.agent.orchestrator.current.metrics["ordered_actions_evidenced"], 0)
        self.assertEqual(self.agent._chat_completion.call_count, 3)

    def test_explicit_multi_step_goal_completes_after_both_verified_actions(self):
        self.agent._chat_completion.side_effect = [
            response(("ui_type", {"text": "hello"}), ("ui_type", {"text": "world"})),
            response(),
        ]
        result = self.agent.run("type hello and then write world")
        self.assertEqual(result, "Done")
        self.assertEqual(self.agent.orchestrator.current.status, "completed")
        self.assertEqual(self.type_text.call_count, 2)
        self.assertEqual(self.agent.orchestrator.current.metrics["ordered_actions_evidenced"], 2)
        self.assertEqual(self.agent._chat_completion.call_count, 2)

    def test_provider_rate_limit_pauses_with_checkpoint_instead_of_crashing(self):
        self.agent._chat_completion.side_effect = RuntimeError(
            "AI_PROVIDER_RATE_LIMITED: quota exhausted"
        )
        result = self.agent.run("inspect Discord")
        self.assertIn("AI_PROVIDER_RATE_LIMITED", result)
        self.assertIn("checkpoint", result.casefold())
        self.assertEqual(self.agent.orchestrator.current.status, "waiting_user")
        self.assertEqual(self.agent._chat_completion.call_count, 1)

    def test_provider_outage_keeps_verified_work_on_same_checkpoint(self):
        goal = 'type text then inspect another interface'
        self.agent.orchestrator.begin(goal)
        self.agent.orchestrator.record_tool('ui_type', {'text': 'h'}, 'VERIFIED: exact readback', 1, 1, mutation=True)
        task_id = self.agent.orchestrator.current.task_id
        for reason in ('AI_PROVIDER_UNAVAILABLE', 'AI_PROVIDER_FAILOVER_FAILED', 'AI_PROVIDER_ACCESS_DENIED'):
            with self.subTest(reason=reason):
                self.agent._chat_completion.side_effect = RuntimeError(reason + ': fixture outage')
                result = self.agent.run(goal, resume_current=True)
                self.assertIn('checkpoint', result)
                self.assertEqual(self.agent.orchestrator.current.status, 'waiting_user')
                self.assertEqual(self.agent.orchestrator.current.task_id, task_id)
                self.assertEqual(len(self.agent.orchestrator.current.traces), 1)
                self.type_text.assert_not_called()

    def test_cancel_does_not_wait_for_model_or_dispatch_late_response(self):
        started, release = threading.Event(), threading.Event()
        def slow_model(**_):
            started.set()
            release.wait(2)
            return response(('ui_type', {'text': 'must not type'}))
        self.agent._chat_completion.side_effect = slow_model
        result = []
        runner = threading.Thread(target=lambda: result.append(self.agent.run('inspect interface')))
        runner.start()
        try:
            self.assertTrue(started.wait(1))
            self.agent.request_stop()
            runner.join(0.5)
            self.assertFalse(runner.is_alive(), 'Cancel was blocked by provider latency')
            self.assertIn('CANCELLED', result[0])
            self.assertEqual(self.agent.orchestrator.current.status, 'cancelled')
            with self.assertRaisesRegex(RuntimeError, 'Previous AI request'):
                self.agent.reset()
            release.set()
            self.agent._pending_model.result(timeout=1)
            self.agent.reset()
            self.type_text.assert_not_called()
            self.assertEqual(self.agent.messages, [])
        finally:
            release.set()
            runner.join(2)

    def test_model_wall_deadline_preserves_checkpoint_and_discards_late_action(self):
        release = threading.Event()
        self.agent.ai_timeout_seconds = 0.08
        self.agent._chat_completion.side_effect = lambda **_: (
            release.wait(2), response(('ui_type', {'text': 'must not type'})))[1]
        try:
            result = self.agent.run('inspect interface')
            self.assertIn('exceeded', result)
            self.assertEqual(self.agent.orchestrator.current.status, 'waiting_user')
            self.assertEqual(self.agent.orchestrator.current.metrics['model_calls'], 1)
            release.set()
            self.agent._pending_model.result(timeout=1)
            self.type_text.assert_not_called()
        finally:
            release.set()

    def test_model_completion_racing_poll_timeout_returns_actual_response(self):
        reply = response()
        future = Mock()
        future.result.side_effect = [TimeoutError(), reply]
        future.done.return_value = True
        self.agent._model_executor = Mock()
        self.agent._model_executor.submit.return_value = future
        self.assertIs(self.agent._wait_for_model(messages=[]), reply)
        self.assertIsNone(self.agent._pending_model)

    def test_model_internal_timeout_is_not_mistaken_for_polling(self):
        self.agent._chat_completion.side_effect = TimeoutError('fixture provider timeout')
        with self.assertRaisesRegex(TimeoutError, 'fixture provider timeout'):
            self.agent._wait_for_model(messages=[])
        self.assertEqual(self.agent._chat_completion.call_count, 1)

    def test_pause_while_model_waits_holds_returned_action_until_resume(self):
        from core.mission_control import MissionControl
        started, release, paused = threading.Event(), threading.Event(), threading.Event()
        self.agent.mission_control = control = MissionControl(lambda state: paused.set() if state['paused'] else None)
        def slow_model(**_):
            started.set()
            release.wait(2)
            return response(('ui_type', {'text': 'h'}))
        replies = iter([slow_model, response()])
        self.agent._chat_completion.side_effect = lambda **kwargs: (
            reply(**kwargs) if callable(reply := next(replies)) else reply)
        runner = threading.Thread(target=lambda: self.agent.run('type h'))
        with patch('core.desktop_control_tools.release_held_inputs'):
            runner.start()
            try:
                self.assertTrue(started.wait(1))
                control.command('pause')
                release.set()
                self.assertTrue(paused.wait(1))
                self.type_text.assert_not_called()
                control.command('resume')
                runner.join(2)
                self.assertFalse(runner.is_alive())
                self.type_text.assert_called_once_with(text='h')
            finally:
                self.agent.request_stop()
                release.set()
                runner.join(2)

    def test_desktop_only_mission_does_not_enumerate_chrome_tabs(self):
        self.agent._chat_completion.side_effect = [response(("ui_type", {"text": "h"})), response()]
        with patch("core.full_access_agent._chrome_tab_rows") as tabs:
            self.assertEqual(self.agent.run("type h in the selected chat"), "Done")
        tabs.assert_not_called()

    def test_compiled_desktop_launch_does_not_enumerate_chrome_or_call_model(self):
        from core.fast_mission import execute_fast_mission
        launch = Mock(return_value="VERIFIED: target app is foreground")
        self.agent.tools.register(ToolSpec("launch_installed_app", "launch", Risk.MEDIUM, {"type": "object"}, launch))
        with patch("core.full_access_agent._chrome_tab_rows") as tabs:
            result = execute_fast_mission(self.agent, "open Notepad")
        self.assertTrue(result.startswith("Completed and verified:"), result)
        tabs.assert_not_called()
        self.agent._chat_completion.assert_not_called()
        launch.assert_called_once()

    def test_literal_fast_typing_and_navigation_use_no_model_and_require_verification(self):
        from core.fast_mission import execute_fast_mission
        for goal, name, arguments in (
            ('type "hello" in "Notepad"', "interaction_type", {"text": "hello", "title": "Notepad", "submit": False}),
            ('open https://example.test', "browser_navigate", {"url": "https://example.test"}),
        ):
            for result in ("VERIFIED: independent outcome", "DELIVERED: pending readback"):
                with self.subTest(goal=goal, result=result):
                    action = Mock(return_value=result)
                    self.agent.tools.register(ToolSpec(name, name, Risk.MEDIUM, {"type": "object"}, action))
                    execute_fast_mission(self.agent, goal)
                    action.assert_called_once_with(**arguments)
                    self.agent._chat_completion.assert_not_called()
                    self.assertEqual(self.agent.orchestrator.current.status,
                                     "completed" if result.startswith("VERIFIED:") else "incomplete")

    def test_resumed_tab_mission_uses_saved_baseline_and_records_provider_delay_on_failure(self):
        goal = "open a new tab in Google"
        self.agent.orchestrator.begin(goal)
        self.agent._mission_initial_tab_count = 3
        self.agent._chat_completion.side_effect = RuntimeError("AI_PROVIDER_RATE_LIMITED: quota exhausted")
        with patch("core.full_access_agent._chrome_tab_rows") as tabs, \
             patch("core.full_access_agent.time.perf_counter", side_effect=[10.0, 10.25]):
            self.agent.run(goal, resume_current=True)
        tabs.assert_not_called()
        metrics = self.agent.orchestrator.current.metrics
        self.assertEqual(metrics["model_wait_ms"], 250)
        self.assertEqual(metrics["model_calls"], 1)
        self.assertGreater(metrics["model_tool_schema_chars"], 0)
        self.assertGreater(metrics["model_tool_count"], 0)

    def test_empty_semantic_tree_does_not_suppress_live_visual_context(self):
        self.agent.orchestrator.begin("inspect interface")
        self.agent.orchestrator.record_tool("interaction_inspect", {}, '{"controls": []}', 1, 1)
        self.agent.live_monitor = monitor = Mock()
        monitor.model_message.return_value = self.vision
        self.agent._refresh_live_vision()
        monitor.model_message.assert_called_once()
        monitor.stop.assert_not_called()
        self.assertIn(self.vision, self.agent.messages)

    def test_requested_vision_keeps_precedence_over_semantic_optimization(self):
        self.agent.orchestrator.begin("inspect interface")
        self.agent.orchestrator.record_tool("interaction_inspect", {}, '{"controls": []}', 1, 1)
        self.agent.live_monitor = monitor = Mock()
        self.agent._replace_internal_vision(self.vision)
        self.agent._refresh_live_vision()
        self.assertIn(self.vision, self.agent.messages)
        monitor.model_message.assert_not_called()
        self.assertNotIn("semantic_preview_skips", self.agent.orchestrator.current.metrics)

    def test_successful_wait_supplies_review_guidance_without_automatic_verification(self):
        from core.task_tools import register_task_tools
        register_task_tools(self.agent.tools, self.agent.orchestrator)
        self.agent.tools.register(ToolSpec("interaction_click", "click", Risk.MEDIUM, {"type": "object"}, self.click))
        wait = Mock(return_value='VERIFIED: {"state": "visible", "control": {"name": "Downloads", "selected": true}}')
        self.agent.tools.register(ToolSpec("interaction_wait", "wait", Risk.LOW, {"type": "object"}, wait))
        def verify_next(**kwargs):
            self.assertTrue(self.agent.orchestrator.needs_action_review())
            self.assertEqual(self.agent.orchestrator.current.verifications, [])
            return response(("task_verify", {"claim": "Downloads selected", "verified": True,
                                             "evidence": "Fresh control metadata reports selected=true"}),
                            ("ui_type", {"text": "h"}))
        replies = iter([response(("interaction_click", {"target": "Downloads"}),
                                 ("interaction_wait", {"target": "Downloads"})), verify_next, response()])
        self.agent._chat_completion.side_effect = lambda **kwargs: (
            reply(**kwargs) if callable(reply := next(replies)) else reply)
        self.agent.run("select Downloads then type h")
        self.click.assert_called_once()
        self.type_text.assert_called_once()
        self.observe.assert_not_called()
        wait_result = next(message["content"] for message in self.agent.messages
                           if message.get("role") == "tool" and "Fresh observation is available" in message["content"])
        self.assertIn("A visible or focused control alone does not prove navigation", wait_result)
        self.assertEqual(len(self.agent.orchestrator.current.verifications), 2)

    def test_google_native_completion_does_not_repeat_search_when_cdp_has_no_tabs(self):
        payload = {"session_type": "existing-window", "verified": True,
                   "window": {"hwnd": 12}, "initial_tab_count": 3, "tab_count": 3,
                   "action": "google_search", "query": "cats", "url": "https://www.google.com/search?q=cats"}
        search = Mock(return_value="VERIFIED: " + json.dumps(payload))
        self.agent.tools.register(ToolSpec("google_search", "fixture", Risk.MEDIUM, {"type": "object"}, search))
        self.agent._chat_completion.side_effect = [response(("google_search", {"query": "cats"})), response()]
        with patch("core.chrome_existing_window.read_existing_chrome", return_value={"tab_count": 3}):
            self.assertEqual(self.agent.run("Search Google for cats"), "Done")
        search.assert_called_once_with(query="cats")
        self.assertEqual(self.agent.orchestrator.current.status, "completed")

    def test_reported_mixed_mission_executes_without_model_and_preserves_completed_draft_on_failure(self):
        from core.fast_mission import execute_fast_mission
        goal = "open WhatsApp and press the first chat and write h after open new tab in google and search for a cat"
        for browser_result in ("VERIFIED: loaded requested search", "ERROR: browser inspection unavailable"):
            with self.subTest(browser_result=browser_result):
                calls = []
                def handler(tool, result):
                    def run(**arguments):
                        calls.append((tool, arguments))
                        return result
                    return run
                for name in ("launch_installed_app", "whatsapp_select_chat_native", "interaction_type", "google_search"):
                    self.agent.tools.register(ToolSpec(name, name, Risk.MEDIUM, {"type": "object"},
                        handler(name, browser_result if name == "google_search" else "VERIFIED: exact postcondition")))
                execute_fast_mission(self.agent, goal)
                self.agent._chat_completion.assert_not_called()
                self.assertEqual([name for name, _ in calls], ["launch_installed_app", "whatsapp_select_chat_native", "interaction_type", "google_search"])
                self.assertEqual(calls[2][1], {"text": "h", "title": "WhatsApp", "submit": False})
                self.assertEqual(calls[3][1], {"query": "a cat", "new_tab": True})
                expected_last = "completed" if browser_result.startswith("VERIFIED:") else "failed"
                self.assertEqual([step.status for step in self.agent.orchestrator.current.plan],
                                 ["completed", "completed", "completed", expected_last])

    def test_missing_scene_is_refreshed_without_replaying_click_or_following_typing(self):
        self.agent._chat_completion.side_effect = [
            response(("desktop_click_button", {"x": 10, "y": 20}), ("desktop_type", {"text": "HI"})),
            response(("ui_type", {"text": "HI", "target": "composer"})),
            response(),
        ]
        self.assertEqual(self.agent.run("type HI"), "Done")
        self.click.assert_not_called()
        self.type_text.assert_called_once_with(text="HI", target="composer")
        self.observe.assert_called_once_with()
        self.assertEqual([trace.name for trace in self.agent.orchestrator.current.traces],
                         ["desktop_click_button", "screen_observe", "ui_type"])
        self.assertEqual(self.agent.orchestrator.current.metrics["desktop_recovery_observations"], 1)
        self.assertTrue(any("earlier ordered action failed" in str(item) for item in self.agent.messages))
        self.assertIn(self.vision, self.agent.messages)

    def test_unstable_scene_rejection_does_not_create_action_review(self):
        self.agent._desktop_observation.observe({**self.frame, "stable": False})
        self.agent._chat_completion.side_effect = [response(("desktop_click_button", {"x": 10, "y": 20})), response()]
        self.agent.run("inspect before clicking")
        self.click.assert_not_called()
        self.observe.assert_called_once_with()
        self.assertFalse(self.agent.orchestrator.needs_action_review())
        self.assertEqual(self.agent.orchestrator.current.last_mutation_index, -1)
        self.assertIsNone(self.agent.orchestrator.current.in_flight)

    def test_failed_refresh_still_stops_rejected_click_loop_with_underlying_error(self):
        self.observe.return_value = "ERROR: Capture unavailable"
        self.agent._chat_completion.side_effect = [
            response(("desktop_click_button", {"x": 10, "y": 20})) for _ in range(3)
        ]
        result = self.agent.run("type HI")
        self.assertIn("Stopped after three failed desktop_click_button attempts", result)
        self.assertIn("Last error: ERROR: Fresh screen_observe required", result)
        self.click.assert_not_called()
        self.assertEqual(self.observe.call_count, 3)
        self.assertFalse(self.agent.orchestrator.needs_action_review())

    def test_permission_rejection_does_not_trigger_screen_capture(self):
        self.agent.tools.permissions.set_access_mode("restricted")
        self.agent._desktop_observation.observe(self.frame)
        self.agent._chat_completion.side_effect = [response(("desktop_click_button", {"x": 10, "y": 20})), response()]
        self.agent.run("click requested target")
        self.click.assert_not_called()
        self.observe.assert_not_called()

    def test_reworded_invalid_verification_is_bounded_and_completes_tool_responses(self):
        error = "ERROR executing task_verify: ValueError: Verification requires successful observation after the action and nonempty evidence"
        verify = Mock(return_value=error)
        self.agent.tools.register(ToolSpec("task_verify", "verify", Risk.LOW, {"type": "object"}, verify))
        self.agent._chat_completion.side_effect = [
            response(("task_verify", {"claim": f"changed claim {index}", "evidence": f"changed evidence {index}"}),
                     ("ui_type", {"text": "must not execute"})) for index in range(3)
        ]
        result = self.agent.run("type HI")
        self.assertIn("Stopped after three failed task_verify", result)
        self.assertEqual(self.agent._chat_completion.call_count, 3)
        self.assertEqual(self.agent.orchestrator.current.status, "incomplete")
        self.type_text.assert_not_called()
        responses = [message for message in self.agent.messages if message["role"] == "tool"]
        self.assertEqual(len(responses), 6)
        self.assertTrue(responses[-1]["content"].startswith("CANCELLED:"))

    def test_uncertain_action_gets_one_observation_without_replay_or_automatic_verification(self):
        from core.desktop_input import InputDeliveryError
        from core.task_tools import register_task_tools
        register_task_tools(self.agent.tools, self.agent.orchestrator)
        self.type_text.side_effect = InputDeliveryError("Existing Chrome navigation outcome is uncertain")
        self.agent._chat_completion.side_effect = [
            response(("ui_type", {"text": "h"})),
            response(("task_verify", {"claim": "input outcome", "verified": False, "evidence": "Fresh screen shows no requested text"})),
            response(),
        ]
        result = self.agent.run("type h")
        self.assertIn("Verification failed", result)
        self.assertEqual(self.agent.orchestrator.current.status, "incomplete")
        self.assertEqual(self.agent._chat_completion.call_count, 3)
        self.observe.assert_called_once()
        self.type_text.assert_called_once_with(text="h")
        traces = self.agent.orchestrator.current.traces
        self.assertEqual([trace.name for trace in traces], ["ui_type", "screen_observe", "task_verify"])
        self.assertTrue(traces[-1].success)
        self.assertFalse(self.agent.orchestrator.current.verifications[-1].verified)

    def test_failed_recovery_observation_is_bounded_and_never_fabricated(self):
        from core.desktop_input import InputDeliveryError
        from core.task_tools import register_task_tools
        register_task_tools(self.agent.tools, self.agent.orchestrator)
        self.type_text.side_effect = InputDeliveryError("uncertain input")
        self.observe.return_value = "ERROR: screen unavailable"
        self.agent._chat_completion.side_effect = [response(("ui_type", {"text": "h"})), *[
            response(("task_verify", {"claim": "input outcome", "verified": True, "evidence": "invented"})) for _ in range(3)]]
        result = self.agent.run("type h")
        self.assertIn("Stopped after three failed task_verify", result)
        self.observe.assert_called_once()
        self.type_text.assert_called_once()
        self.assertEqual(self.agent.orchestrator.current.verifications, [])

    def test_pre_input_rejection_allows_safe_semantic_recovery_without_fake_review(self):
        from core.desktop_input import InputNotDispatchedError
        reject = Mock(side_effect=InputNotDispatchedError("composer has no writable Value pattern"))
        self.agent.tools.register(ToolSpec("ui_type_native", "type", Risk.MEDIUM, {"type": "object"}, reject))
        self.agent._chat_completion.side_effect = [
            response(("ui_type_native", {"text": "HI"})),
            response(("ui_type", {"text": "HI"})),
            response(),
        ]
        self.assertEqual(self.agent.run("type HI"), "Done")
        self.type_text.assert_called_once_with(text="HI")
        self.observe.assert_not_called()
        self.assertEqual([t.name for t in self.agent.orchestrator.current.traces], ["ui_type_native", "ui_type"])

    def test_browser_target_rejection_clears_intent_and_corrected_click_runs_once(self):
        from core.browser_semantic import run_browser_operation, register_browser_semantic_tools
        from core.execution_telemetry import input_not_dispatched
        register_browser_semantic_tools(self.agent.tools)
        page = MagicMock()
        page.url = 'http://fixture.invalid/'
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        target = MagicMock()
        target.is_visible.return_value = target.is_enabled.return_value = True
        page.get_by_role.return_value.element_handles.return_value = [target]
        o = self.agent.orchestrator
        o.begin('Open a local test record')
        def execute(operation, **args):
            return run_browser_operation(page, operation, args, lambda: None)
        with patch('core.chrome_cdp.chrome_page_operation', side_effect=execute):
            args = {"action": "click", "target": {"node_id": "n1"}}
            rejected = self.agent._execute_tool('browser_semantic_action', args, approved=True)
            self.assertTrue(input_not_dispatched(rejected))
            o.record_tool('browser_semantic_action', args, rejected, 1, 1,
                          mutation=not input_not_dispatched(rejected))
            self.assertIsNone(o.current.in_flight)
            self.assertFalse(o.needs_action_review())
            corrected = self.agent._execute_tool('browser_semantic_action', {
                "action": "click", "target": {"role": "link", "name": "Open record"}}, approved=True)
        self.assertNotIn('ERROR', corrected)
        target.click.assert_called_once()

    def test_uncertain_browser_recovery_observes_browser_without_desktop_capture(self):
        inspect = Mock(return_value='VERIFIED: current page metadata')
        self.agent.tools.register(ToolSpec('interaction_inspect', 'inspect', Risk.LOW,
                                         {"type": "object"}, inspect))
        o = self.agent.orchestrator
        o.begin('Open a local test record')
        o.record_tool('interaction_click', {"surface": "browser"}, 'DELIVERED: click', 1, 1, mutation=True)
        self.assertEqual(self.agent._recovery_observation(), ('interaction_inspect', {'surface': 'browser'}))
        o.record_tool('desktop_click_button', {}, 'DELIVERED: click', 1, 2, mutation=True)
        self.assertEqual(self.agent._recovery_observation(), ('screen_observe', {}))

    def test_invalid_browser_shortcut_is_validation_error_not_permission_denial(self):
        from core.execution_telemetry import input_not_dispatched
        from core.universal_interaction import register_universal_interaction_tools
        register_universal_interaction_tools(self.agent.tools)
        self.agent.require_action_confirmation = True
        self.agent.orchestrator.begin('Select all in the test draft')
        with patch('core.browser_semantic.browser_semantic_action') as action:
            result = self.agent._execute_tool('interaction_hotkey', {
                'surface': 'browser', 'browser_target': {'role': 'textbox'},
                'keys': ['Control', 'a'],
            }, approved=True)
        self.assertTrue(result.startswith('ERROR executing interaction_hotkey: ValidationError:'), result)
        self.assertTrue(input_not_dispatched(result))
        self.assertLess(len(result), 250)
        self.assertIsNone(self.agent.orchestrator.current.in_flight)
        action.assert_not_called()

    def test_recovery_cannot_add_submit_to_the_reported_whatsapp_write_mission(self):
        self.agent.orchestrator.begin("OPEN WHATSAPP AND PRESS THE FIRST CHAT AFTER WRITE HI")
        result = self.agent._execute_tool("ui_type", {"text": "HI", "submit": True}, approved=True)
        self.assertTrue(result.startswith("PERMISSION_DENIED:"))
        self.type_text.assert_not_called()
        self.assertIsNone(self.agent.orchestrator.current.in_flight)
        result = self.agent._execute_tool("ui_type", {"text": "HI", "submit": False}, approved=True)
        self.assertTrue(result.startswith("VERIFIED:"))
        self.type_text.assert_called_once_with(text="HI", submit=False)

    def test_discord_compiled_typing_retains_unsent_exact_write_obligation(self):
        from core.whatsapp_fast_mission import typing_completion_error
        o = self.agent.orchestrator
        task = o.begin("OPEN DISCORD AND PRESS THE FIRST CHAT AFTER WRITE H")
        o.record_tool("discord_select_chat", {"position": 1}, "VERIFIED: exact conversation route", 0, 1, mutation=True)
        self.assertIsNotNone(typing_completion_error(task, "fast-3"))
        denied = self.agent._execute_tool("ui_type", {"text": "H", "title": "Discord", "submit": True}, approved=True)
        self.assertTrue(denied.startswith("PERMISSION_DENIED:"))
        self.type_text.assert_not_called()
        universal = Mock(return_value="VERIFIED: exact text readback")
        self.agent.tools.register(ToolSpec("interaction_type", "type", Risk.MEDIUM, {"type": "object"}, universal))
        denied_universal = self.agent._execute_tool(
            "interaction_type",
            {"text": "H", "title": "Discord", "surface": "desktop", "submit": True},
            approved=True,
        )
        self.assertTrue(denied_universal.startswith("PERMISSION_DENIED:"))
        universal.assert_not_called()
        o.record_tool("interaction_type", {"text": "H", "title": "Discord", "surface": "desktop"},
                      "VERIFIED: exact text readback", 0, 1, mutation=True)
        self.assertIsNone(typing_completion_error(task, "fast-3"))

    def test_native_stop_reports_required_restart_without_continuing_tool_batch(self):
        self.type_text.return_value = "ERROR executing ui_type: RustEngineUnavailable: Rust daemon emergency stop is latched; restart the daemon before native execution"
        self.agent._chat_completion.return_value = response(
            ("ui_type", {"text": "HI"}), ("desktop_click_button", {"x": 10, "y": 20}))
        result = self.agent.run("type HI")
        self.assertIn("Restart JARVIS", result)
        self.assertEqual(self.agent.orchestrator.current.status, "waiting_user")
        self.agent._chat_completion.assert_called_once()
        self.type_text.assert_called_once()
        self.click.assert_not_called()
        self.observe.assert_not_called()
        self.assertEqual([message["tool_call_id"] for message in self.agent.messages if message["role"] == "tool"],
                         ["call-0", "call-1"])

    def test_existing_draft_and_model_claim_cannot_complete_failed_compiled_typing(self):
        from core.whatsapp_fast_mission import typing_completion_error
        o = self.agent.orchestrator
        task = o.begin("OPEN WHATSAPP AND PRESS THE FIRST CHAT AND WRITE H")
        o.record_tool("whatsapp_select_chat_native", {"position": 1}, "VERIFIED: chat selected", 0, 1, mutation=True)
        o.record_tool("ui_type_native", {"text": "H", "title": "WhatsApp"}, "ERROR: no input delivered", 0, 1)
        o.record_tool("ui_inspect", {}, 'composer value: HI', 0, 2)
        o.verify("composer is ready", True, "existing draft HI")
        update = Mock(return_value="completed")
        self.agent.tools.register(ToolSpec("task_update_step", "update", Risk.LOW, {"type": "object"}, update))
        result = self.agent._execute_tool("task_update_step", {"step_id": "fast-3", "status": "completed",
                                          "result": "VERIFIED: the existing HI is ready"}, approved=True)
        self.assertTrue(result.startswith("ERROR:"))
        update.assert_not_called()
        for arguments in ({"text": "HI", "title": "WhatsApp"}, {"text": "H", "title": "Other"},
                          {"text": "H", "title": "WhatsApp", "replace": True}):
            o.record_tool("ui_type", arguments, "VERIFIED: editor readback", 0, 3, mutation=True)
            self.assertIsNotNone(typing_completion_error(task, "fast-3"))
        o.record_tool("ui_type_native", {"text": "H", "title": "WhatsApp"}, "VERIFIED: exact append readback", 0, 4, mutation=True)
        o.verify("write H", True, "native readback")
        self.assertEqual(self.agent._execute_tool("task_update_step", {"step_id": "fast-3", "status": "completed"}, approved=True), "completed")
        update.assert_called_once()
        o.record_tool("ui_hotkey", {"keys": ["backspace"]}, "DELIVERED: key", 0, 5, mutation=True)
        self.assertIsNotNone(typing_completion_error(task, "fast-3"))


class RecoveryGuidanceTests(unittest.TestCase):
    def test_guidance_names_required_safe_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = TaskOrchestrator(temporary)
            orchestrator.begin("type HI")
            for error, expected in (
                ("ERROR: Fresh stable screen_observe required: the UI is still changing", "Call screen_observe"),
                ("ERROR: Observe the last action and call task_verify", "verified=false"),
                ("ERROR: Verification requires successful observation after the action and nonempty evidence", "do not repeat task_verify"),
                ("ERROR executing ui_type_native: InputNotDispatchedError: Editor caret changed before input; no input delivered", "Do not call task_verify"),
                ("ERROR executing discord_send_message: InputNotDispatchedError: no message was sent", "Do not call task_verify"),
                ("ERROR: Target '' has 8 exact visible enabled matches", "unique selector or control identity"),
            ):
                with self.subTest(error=error):
                    self.assertIn(expected, orchestrator.recovery_hint(error, "desktop_click_button"))

    def test_new_observation_resets_verification_retry_budget_but_bookkeeping_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = TaskOrchestrator(temporary)
            orchestrator.begin("type HI")
            error = "ERROR: Verification requires successful observation"
            for index in range(2):
                orchestrator.record_tool("task_verify", {"evidence": str(index)}, error, 0, index)
            orchestrator.record_tool("task_status", {}, "current task status", 0, 2)
            orchestrator.record_tool("task_verify", {"evidence": "different words"}, error, 0, 3)
            self.assertTrue(orchestrator.repeated_failure("task_verify", {"evidence": "different words"}))
            orchestrator.record_tool("ui_inspect", {}, '{"controls": []}', 0, 4)
            orchestrator.record_tool("task_verify", {"evidence": "fresh observation"}, error, 0, 5)
            self.assertFalse(orchestrator.repeated_failure("task_verify", {"evidence": "fresh observation"}))


if __name__ == "__main__":
    unittest.main()
