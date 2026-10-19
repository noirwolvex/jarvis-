"""Typed engine, scheduler, recovery and permission tests; no desktop input."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from core.control_engines import Action, CancellationToken, Check, ControlSession, RetryPolicy, Status
from core.control_engines.integration import register_control_engines
from core.control_engines.scheduler import action_context, input_lane, ActionTimedOut
from core.desktop_observation import DesktopObservationGate
from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


class FixtureProvider:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.cleanup = Mock()

    def validate(self, tool, arguments):
        if "invalid" in arguments:
            raise ValueError("Invalid fixture arguments")

    def dispatch(self, tool, arguments):
        self.calls.append((tool, arguments))
        result = self.responses.get(tool, "VERIFIED: fixture readback")
        return result(tool, arguments) if callable(result) else result


class ControlSessionTests(unittest.TestCase):
    def setUp(self):
        self.provider = FixtureProvider()
        self.session = ControlSession(self.provider)
        self.addCleanup(self.session.close)

    def test_five_engines_share_one_validated_interface(self):
        for operation in ("click", "type_text", "inspect", "observe", "verify"):
            result = self.session.execute(Action(operation, operation))
            self.assertEqual(result.status, Status.SUCCEEDED)
            self.assertTrue(result.verification_result)
            self.assertGreaterEqual(result.duration_ms, 0)
            self.assertTrue(result.evidence)
        self.assertEqual(len(self.provider.calls), 5)
        self.assertEqual(self.provider.cleanup.call_count, 5)

    def test_mutation_delivery_alone_does_not_complete(self):
        self.provider.responses["interaction_click"] = "DELIVERED: input sent"
        outcome = self.session.execute(Action("click", "click"))
        self.assertEqual(outcome.status, Status.DELIVERED)
        self.assertFalse(outcome.verification_result)

    def test_verification_requires_explicit_positive_readback(self):
        self.provider.responses["interaction_wait"] = "UNVERIFIED: field unreadable"
        outcome = self.session.execute(Action("check", "verify"))
        self.assertEqual(outcome.status, Status.VERIFICATION_FAILED)
        self.assertFalse(outcome.verification_result)

    def test_empty_read_is_not_success(self):
        self.provider.responses["interaction_inspect"] = ""
        outcome = self.session.execute(Action("empty", "inspect"))
        self.assertEqual(outcome.status, Status.FAILED)
        self.assertEqual(outcome.error_code, "EMPTY_BACKEND_RESULT")

    def test_failed_mutation_is_never_retried(self):
        self.provider.responses["interaction_type"] = "ERROR: delivery uncertain"
        action = Action("type", "type_text", expected_outcome=Check("interaction_wait", {"state": "text"}),
                        retry_policy=RetryPolicy(5, 0))
        result = self.session.execute(action)
        self.assertEqual(result.status, Status.FAILED)
        self.assertEqual([name for name, _ in self.provider.calls], ["interaction_type"])

    def test_transient_postcondition_recovers_without_retyping(self):
        self.provider.responses["interaction_type"] = "DELIVERED: text"
        reads = iter(["ERROR: TimeoutError", "VERIFIED: exact readback"])
        self.provider.responses["interaction_wait"] = lambda *_: next(reads)
        action = Action("type", "type_text", parameters={"text": "Hello مرحبا\nSecond line"},
                        expected_outcome=Check("interaction_wait", {"state": "text", "text": "Hello مرحبا\nSecond line"}),
                        retry_policy=RetryPolicy(2, 0))
        result = self.session.execute(action)
        self.assertEqual(result.status, Status.SUCCEEDED)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual([name for name, _ in self.provider.calls].count("interaction_type"), 1)

    def test_missing_precondition_prevents_input_and_reports_remaining(self):
        self.provider.responses["interaction_wait"] = "ERROR: target missing"
        result = self.session.run([
            Action("inspect", "inspect"),
            Action("type", "type_text", preconditions=(Check("interaction_wait", {"state": "visible"}),)),
            Action("next", "click"),
        ])
        self.assertFalse(result["completed"])
        self.assertEqual(result["remaining"], ["next"])
        self.assertEqual(result["actions"][1]["error_code"], "PRECONDITION_FAILED")
        self.assertNotIn("interaction_type", [name for name, _ in self.provider.calls])

    def test_entire_task_is_preflighted_before_input(self):
        with self.assertRaises(ValueError):
            self.session.run([Action("one", "click"), Action("two", "type_text", {"invalid": True})])
        self.assertEqual(self.provider.calls, [])

    def test_ids_are_immutable_no_replay_and_results_detached(self):
        action = Action("one", "click")
        first = self.session.execute(action)
        first.evidence.clear()
        self.assertTrue(self.session.execute(action).evidence)
        self.assertEqual(len(self.provider.calls), 1)
        with self.assertRaises(ValueError):
            self.session.execute(Action("one", "type_text"))

    def test_task_preflight_detects_changed_later_id_before_earlier_input(self):
        self.session.execute(Action("saved", "click"))
        self.provider.calls.clear()
        with self.assertRaisesRegex(ValueError, "bound to different"):
            self.session.run([Action("new", "click"), Action("saved", "type_text")])
        self.assertEqual(self.provider.calls, [])

    def test_dependencies_require_verified_predecessors(self):
        with self.assertRaises(ValueError):
            self.session.execute(Action("two", "click", dependencies=("missing",)))
        self.provider.responses["interaction_click"] = "DELIVERED: click"
        self.session.execute(Action("one", "click"))
        with self.assertRaises(ValueError):
            self.session.execute(Action("two", "click", dependencies=("one",)))

    def test_cancelled_action_dispatches_nothing_and_cleans_up(self):
        token = CancellationToken()
        token.cancel()
        result = self.session.execute(Action("one", "click"), token)
        self.session._pending.result(timeout=1)
        self.assertEqual(result.status, Status.CANCELLED)
        self.assertEqual(self.provider.calls, [])
        self.provider.cleanup.assert_called_once()

    def test_timeout_returns_promptly_and_quarantines_slow_provider(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*_):
            entered.set()
            release.wait(2)
            return "VERIFIED: late response"
        self.provider.responses["interaction_click"] = slow
        try:
            started = time.monotonic()
            outcome = self.session.execute(Action("one", "click", timeout=0.05))
            self.assertTrue(entered.is_set())
            self.assertEqual(outcome.status, Status.TIMED_OUT)
            self.assertLess(time.monotonic() - started, 0.5)
            with self.assertRaisesRegex(RuntimeError, "busy"):
                self.session.execute(Action("two", "click"))
        finally:
            release.set()
            self.session._pending.result(timeout=1)
        self.assertEqual(self.session.execute(Action("one", "click", timeout=0.05)).status, Status.TIMED_OUT)
        self.provider.cleanup.assert_called_once()

    def test_model_cannot_supply_code_or_mutating_verification(self):
        with self.assertRaises(ValueError):
            self.session.execute(Action("one", "run_powershell"))
        with self.assertRaises(ValueError):
            Check("desktop_type", {"text": "not proof"})
        for value in (float("inf"), float("nan"), -1, True):
            with self.subTest(timeout=value), self.assertRaises(ValueError):
                Action("one", "click", timeout=value)
        with self.assertRaises(ValueError):
            Action("one", "click", {"title": "Wrong"}, {"title": "Intended"})

    def test_nested_session_inherits_parent_deadline(self):
        from core.process_control import check_cancelled
        def cancellable(*_):
            while True:
                check_cancelled()
                time.sleep(0.005)
        self.provider.responses["interaction_inspect"] = cancellable
        began = time.monotonic()
        with action_context(began + 0.05, lambda: False):
            outcome = self.session.execute(Action("nested", "inspect", timeout=10))
        self.assertEqual(outcome.status, Status.TIMED_OUT)
        self.assertLess(time.monotonic() - began, 0.5)
        self.session._pending.result(timeout=1)
        self.provider.cleanup.assert_called_once()

    def test_logging_sink_cannot_fail_execution(self):
        self.session.on_event = Mock(side_effect=ValueError("broken subscriber"))
        self.assertEqual(self.session.execute(Action("one", "inspect")).status, Status.SUCCEEDED)


class SchedulerTests(unittest.TestCase):
    def test_cleanup_releases_only_inputs_owned_by_dispatch_even_on_failure(self):
        from core import desktop_control_tools as desktop
        gui = Mock(FAILSAFE=True, PAUSE=0.1)
        keys, buttons = {"ctrl"}, {"right"}
        with patch.dict("sys.modules", {"pyautogui": gui}), patch.object(desktop.os, "name", "nt"), \
                patch.object(desktop, "_HELD_KEYS", keys), patch.object(desktop, "_HELD_MOUSE_BUTTONS", buttons):
            with self.assertRaises(RuntimeError), desktop.scoped_held_inputs():
                keys.add("shift")
                buttons.add("left")
                raise RuntimeError("Injected dispatch failure")
            self.assertEqual(keys, {"ctrl"})
            self.assertEqual(buttons, {"right"})
            gui.keyUp.assert_called_once_with("shift")
            gui.mouseUp.assert_called_once_with(button="left")
            self.assertTrue(gui.FAILSAFE)

    def test_two_registries_cannot_inject_input_concurrently(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        schema = {"type": "object", "additionalProperties": False}
        one, two = ToolRegistry(), ToolRegistry()
        one.register(ToolSpec("desktop_fixture", "fixture", Risk.LOW, schema,
                              lambda: (entered.set(), release.wait(2), "done")[-1]))
        two.register(ToolSpec("desktop_fixture", "fixture", Risk.LOW, schema,
                              lambda: (second.set(), "done")[-1]))
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(one.execute, "desktop_fixture", {})
            self.assertTrue(entered.wait(1))
            next_call = pool.submit(two.execute, "desktop_fixture", {})
            self.assertFalse(second.wait(0.05))
            release.set()
            first.result(timeout=1)
            next_call.result(timeout=1)
            self.assertTrue(second.is_set())

    def test_context_deadline_reaches_existing_cancellation_checks(self):
        from core.process_control import check_cancelled
        with self.assertRaises(ActionTimedOut), action_context(time.monotonic() - 1, lambda: False):
            check_cancelled()


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        agent = self.agent = FullAccessJarvisAgent.__new__(FullAccessJarvisAgent)
        agent.orchestrator = TaskOrchestrator(directory.name)
        agent.orchestrator.begin("owned fixture mission")
        agent.tools = ToolRegistry()
        agent.tools.permissions.set_access_mode("full")
        agent.approval = lambda *_: True
        agent._stop = threading.Event()
        agent._desktop_observation = DesktopObservationGate()
        agent._tool_executor = ThreadPoolExecutor(1)
        self.addCleanup(agent._tool_executor.shutdown, wait=True)
        self.addCleanup(lambda: getattr(agent, "control_session", None) and agent.control_session.close())
        agent._browser_action_guard = lambda *_: None
        self.write = Mock(return_value="DELIVERED: fixture input")
        agent.tools.register(ToolSpec("interaction_type", "fixture", Risk.MEDIUM, {"type": "object"}, self.write))
        agent.tools.register(ToolSpec("interaction_wait", "fixture", Risk.LOW, {"type": "object"},
                                     lambda **_: "VERIFIED: exact fixture text"))
        register_control_engines(agent)

    def test_typed_action_journals_real_children_and_clears_review_after_readback(self):
        result = self.agent._execute_tool("workflow_control_action", {"action": {
            "action_id": "type", "action_type": "type_text", "parameters": {"text": "fixture"},
            "expected_outcome": {"tool": "interaction_wait", "arguments": {"text": "fixture", "state": "text"}},
        }}, approved=True)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual([t.name for t in self.agent.orchestrator.current.traces], ["interaction_type", "interaction_wait"])
        self.assertFalse(self.agent.orchestrator.needs_action_review())
        self.write.assert_called_once()

    def test_child_policy_cannot_be_bypassed_by_container(self):
        self.agent.tools.permissions.deny_tools.add("interaction_type")
        result = self.agent._execute_tool("workflow_control_action", {"action": {
            "action_id": "type", "action_type": "type_text", "parameters": {"text": "fixture"},
        }}, approved=True)
        self.assertTrue(result.startswith("ERROR:"), result)
        self.write.assert_not_called()

    def test_failed_requested_postcondition_invalidates_backend_success(self):
        self.write.return_value = "VERIFIED: backend text"
        self.agent.tools.register(ToolSpec("interaction_wait", "fixture", Risk.LOW, {"type": "object"},
                                         lambda **_: "ERROR: requested outcome missing"))
        result = self.agent._execute_tool("workflow_control_action", {"action": {
            "action_id": "type", "action_type": "type_text", "parameters": {"text": "fixture"},
            "expected_outcome": {"tool": "interaction_wait", "arguments": {"state": "text", "text": "fixture"}},
        }}, approved=True)
        self.assertIn("VERIFICATION_FAILED", result)
        self.assertTrue(self.agent.orchestrator.needs_action_review())
        self.assertFalse(self.agent.orchestrator.current.verifications[-1].verified)
        self.write.assert_called_once()

    def test_model_action_cannot_disable_one_time_confirmation(self):
        self.agent.require_action_confirmation = True
        self.agent.tools.register(ToolSpec("interaction_click", "fixture", Risk.MEDIUM, {"type": "object"}, self.write))
        result = self.agent._execute_tool("workflow_control_action", {"action": {
            "action_id": "send", "action_type": "click", "target": {"target": "Send"},
        }}, approved=True)
        self.assertIn("NEEDS_CONFIRMATION", result)
        self.write.assert_not_called()

    def test_engine_operation_aliases(self):
        from core.control_engines.engines import MouseEngine, KeyboardEngine
        self.assertEqual(MouseEngine.operations["click_button"], "desktop_click_button")
        self.assertEqual(MouseEngine.operations["desktop_click"], "desktop_click_button")
        self.assertEqual(KeyboardEngine.operations["press"], "desktop_press")
        self.assertEqual(KeyboardEngine.operations["desktop_press"], "desktop_press")
        self.assertEqual(KeyboardEngine.operations["type"], "interaction_type")
        self.assertEqual(KeyboardEngine.operations["desktop_type"], "interaction_type")
        self.assertEqual(KeyboardEngine.operations["desktop_hotkey"], "interaction_hotkey")

    def test_compact_tool_result_supports_vision_and_desktop_tools(self):
        from core.model_context import compact_tool_result
        sample = json.dumps({"text": "sample", "boxes": [1, 2, 3]}, indent=2)
        for tool_name in ("vision_ocr", "inspect_window", "browser_links", "screen_observe"):
            compact = compact_tool_result(tool_name, sample)
            self.assertNotIn("\n", compact)
            self.assertEqual(json.loads(compact), {"text": "sample", "boxes": [1, 2, 3]})



if __name__ == "__main__":
    unittest.main()
