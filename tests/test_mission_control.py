"""Operator gating with isolated handlers: no desktop, browser, or model calls."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from core.action_confirmation import ConfirmationRequirement
from core.desktop_observation import DesktopObservationGate
from core.full_access_agent import FullAccessJarvisAgent
from core.mission_control import MissionControl
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


REQUIREMENT = ConfirmationRequirement("send", "Send this fixture message once", "External effect")


class ControlFixture(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.pending = threading.Event()
        self.paused = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.control = MissionControl(self.emit, confirmation_timeout=2)
        self.addCleanup(self.pool.shutdown, wait=True)
        self.addCleanup(self.control.cancel)

    def emit(self, snapshot):
        self.events.append(snapshot)
        if snapshot.get("pendingConfirmation"):
            self.pending.set()
        if snapshot.get("paused"):
            self.paused.set()

    def confirm_async(self, arguments=None):
        return self.pool.submit(self.control.confirm, "discord_send_message",
                                arguments if arguments is not None else {"text": "fixture"},
                                REQUIREMENT, "fixture-mission", lambda: False)

    def pending_id(self):
        self.assertTrue(self.pending.wait(2), "Confirmation did not reach the operator")
        return self.control.snapshot()["pendingConfirmation"]["id"]


class MissionControlTests(ControlFixture):
    def test_running_boundary_is_immediate_and_pause_acknowledges_only_at_boundary(self):
        self.assertFalse(self.control.boundary(lambda: False))
        self.control.command("pause")
        self.assertEqual(self.control.snapshot(), {"paused": False, "pauseRequested": True})
        future = self.pool.submit(self.control.boundary, lambda: False)
        self.assertTrue(self.paused.wait(2))
        self.assertFalse(future.done())
        self.control.command("resume")
        self.assertTrue(future.result(2))
        self.assertEqual(self.control.snapshot(), {"paused": False, "pauseRequested": False})

    def test_cancel_wakes_paused_boundary_without_resuming(self):
        self.control.command("pause")
        future = self.pool.submit(self.control.boundary, lambda: False)
        self.assertTrue(self.paused.wait(2))
        self.control.cancel()
        with self.assertRaisesRegex(RuntimeError, "stop|cancel"):
            future.result(2)
        self.assertTrue(self.control.cancelled)

    def test_external_stop_wakes_confirmation_without_operator_reply(self):
        stopped = threading.Event()
        future = self.pool.submit(self.control.confirm, "discord_send_message", {"text": "fixture"},
                                  REQUIREMENT, "fixture-mission", stopped.is_set)
        self.pending_id()
        stopped.set()
        with self.assertRaisesRegex(RuntimeError, "stop|cancel"):
            future.result(2)
        self.assertNotIn("pendingConfirmation", self.control.snapshot())

    def test_wrong_id_and_reused_id_cannot_authorize_action(self):
        future = self.confirm_async()
        identifier = self.pending_id()
        with self.assertRaisesRegex(ValueError, "another action|expired"):
            self.control.command("confirm", "0" * 32)
        self.assertFalse(future.done())
        self.control.command("confirm", identifier)
        future.result(2)
        with self.assertRaisesRegex(ValueError, "answered|expired"):
            self.control.command("confirm", identifier)
        self.pending.clear()
        second = self.confirm_async()
        second_id = self.pending_id()
        self.assertNotEqual(identifier, second_id)
        with self.assertRaises(ValueError):
            self.control.command("confirm", identifier)
        self.assertFalse(second.done())
        self.control.command("confirm", second_id)
        second.result(2)

    def test_rejection_cancels_and_drops_pending_action(self):
        future = self.confirm_async()
        self.control.command("reject", self.pending_id())
        with self.assertRaisesRegex(RuntimeError, "stop|cancel"):
            future.result(2)
        self.assertTrue(self.control.cancelled)
        self.assertNotIn("pendingConfirmation", self.control.snapshot())
        with self.assertRaisesRegex(ValueError, "cancelled"):
            self.control.command("resume")

    def test_expired_confirmation_never_grants_and_cancels(self):
        self.control._timeout = 0.08
        future = self.confirm_async()
        identifier = self.pending_id()
        with self.assertRaisesRegex(RuntimeError, "expired"):
            future.result(2)
        self.assertTrue(self.control.cancelled)
        self.assertNotIn("pendingConfirmation", self.control.snapshot())
        with self.assertRaises(ValueError):
            self.control.command("confirm", identifier)

    def test_changed_nested_arguments_invalidate_exact_confirmation(self):
        arguments = {"text": "fixture", "target": {"chat": "self"}}
        future = self.confirm_async(arguments)
        identifier = self.pending_id()
        arguments["target"]["chat"] = "other"
        self.control.command("confirm", identifier)
        with self.assertRaisesRegex(RuntimeError, "arguments changed"):
            future.result(2)
        self.assertTrue(self.control.cancelled)

    def test_parallel_confirmation_cannot_overwrite_pending_invocation(self):
        future = self.confirm_async()
        identifier = self.pending_id()
        with self.assertRaisesRegex(RuntimeError, "already pending"):
            self.control.confirm("discord_send_message", {"text": "second"}, REQUIREMENT,
                                 "fixture-mission", lambda: False)
        self.assertEqual(self.control.snapshot()["pendingConfirmation"]["id"], identifier)
        self.control.command("confirm", identifier)
        future.result(2)

    def test_broken_status_sink_cannot_leave_stale_approval(self):
        self.control._emit = Mock(side_effect=RuntimeError("fixture status sink failed"))
        with self.assertRaisesRegex(RuntimeError, "status sink failed"):
            self.control.confirm("discord_send_message", {"text": "fixture"}, REQUIREMENT,
                                 "fixture-mission", lambda: False)
        self.assertNotIn("pendingConfirmation", self.control.snapshot())

    def test_already_cancelled_control_never_publishes_confirmation(self):
        self.control.cancel()
        with self.assertRaisesRegex(RuntimeError, "stop|cancel"):
            self.control.confirm("discord_send_message", {"text": "fixture"}, REQUIREMENT,
                                 "fixture-mission", lambda: False)
        self.assertFalse(any(item.get("pendingConfirmation") for item in self.events))

    def test_review_details_preserve_content_and_hide_secrets_without_mutating_action(self):
        arguments = {"text": "English / \u0645\u0631\u062d\u0628\u0627", "destination": "self-chat",
                     "credentials": {"password": "fixture-password"},
                     "headers": [{"authorization": "Bearer fixture-auth"}],
                     "command": "echo api_key=fixture-key"}
        original = json.loads(json.dumps(arguments))
        future = self.confirm_async(arguments)
        identifier = self.pending_id()
        request = self.control.snapshot()["pendingConfirmation"]
        details = json.loads(request["details"])
        self.assertEqual(details["text"], arguments["text"])
        self.assertEqual(details["destination"], "self-chat")
        self.assertEqual(details["credentials"]["password"], "[REDACTED]")
        self.assertEqual(details["headers"][0]["authorization"], "[REDACTED]")
        self.assertNotIn("fixture-key", details["command"])
        self.assertEqual(request["reason"], REQUIREMENT.reason)
        self.assertEqual(arguments, original)
        self.control.command("confirm", identifier)
        future.result(2)
        history = self.control.history()
        self.assertEqual([entry["action"] for entry in history],
                         ["confirmation_requested", "confirm", "approval_consumed"])
        self.assertEqual({entry["fingerprint"] for entry in history}, {request["fingerprint"]})
        self.assertNotIn("fixture-password", json.dumps(history))
        self.assertNotIn("details", json.dumps(history))

    def test_oversized_review_is_refused_instead_of_truncated(self):
        with self.assertRaisesRegex(ValueError, "too large to review"):
            self.control.confirm("discord_send_message", {"text": "x" * 16001}, REQUIREMENT,
                                 "fixture-mission", lambda: False)
        self.assertFalse(self.pending.is_set())
        self.assertNotIn("pendingConfirmation", self.control.snapshot())

    def test_emoji_review_uses_dashboard_utf16_limit(self):
        with self.assertRaisesRegex(ValueError, "too large to review"):
            self.control.confirm("discord_send_message", {"text": "\U0001f600" * 9000}, REQUIREMENT,
                                 "fixture-mission", lambda: False)
        self.assertFalse(self.pending.is_set())

    def test_operator_cancel_wakes_approval_without_granting_permission(self):
        future = self.confirm_async()
        self.pending_id()
        self.control.command("cancel")
        with self.assertRaisesRegex(RuntimeError, "stop|cancel"):
            future.result(2)
        self.assertTrue(self.control.cancelled)
        self.assertEqual([entry["action"] for entry in self.control.history()],
                         ["confirmation_requested", "cancel"])

    def test_resume_does_not_implicitly_approve_pending_action(self):
        future = self.confirm_async()
        identifier = self.pending_id()
        self.control.command("pause")
        self.control.command("resume")
        self.assertFalse(future.done())
        self.assertEqual(self.control.snapshot()["pendingConfirmation"]["id"], identifier)
        self.control.command("confirm", identifier)
        future.result(2)


class AgentControlIntegrationTests(ControlFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.agent = agent = object.__new__(FullAccessJarvisAgent)
        agent._stop = threading.Event()
        agent._desktop_observation = DesktopObservationGate()
        agent.orchestrator = TaskOrchestrator(temporary.name)
        agent.orchestrator.begin("send a fixture message")
        agent.tools = ToolRegistry()
        agent.tools.permissions.set_access_mode("full")
        agent.mission_control = self.control
        agent._browser_action_guard = Mock(return_value=None)
        agent._tool_executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(agent._tool_executor.shutdown, wait=True)
        # Cancel both executors before joining them even if an assertion fails.
        self.addCleanup(self.control.cancel)
        self.handler = Mock(return_value="VERIFIED: fixture outcome")
        schema = {"type": "object", "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}
        agent.tools.register(ToolSpec("discord_send_message", "fixture", Risk.MEDIUM, schema, self.handler))
        agent.tools.register(ToolSpec("ui_type", "fixture", Risk.MEDIUM, schema, self.handler))

    def dispatch(self, tool="discord_send_message", arguments=None):
        return self.pool.submit(self.agent._execute_tool, tool,
                                arguments if arguments is not None else {"text": "fixture"}, True)

    def test_mutation_is_not_journaled_or_dispatched_before_exact_confirmation(self):
        future = self.dispatch()
        self.control.command("confirm", self.pending_id())
        self.assertTrue(future.result(2).startswith("VERIFIED:"))
        self.handler.assert_called_once_with(text="fixture")
        self.assertEqual(self.agent.orchestrator.current.in_flight["name"], "discord_send_message")

    def test_pending_request_and_reject_leave_handler_and_journal_untouched(self):
        future = self.dispatch()
        identifier = self.pending_id()
        self.handler.assert_not_called()
        self.assertIsNone(self.agent.orchestrator.current.in_flight)
        self.control.command("reject", identifier)
        self.assertTrue(future.result(2).startswith(("CANCELLED:", "PERMISSION_DENIED:")))
        self.handler.assert_not_called()
        self.assertIsNone(self.agent.orchestrator.current.in_flight)

    def test_schema_and_policy_rejection_happen_before_operator_prompt(self):
        from core.execution_telemetry import input_not_dispatched
        invalid = self.agent._execute_tool("discord_send_message", {"text": 42}, True)
        self.assertTrue(invalid.startswith("ERROR executing discord_send_message: ValidationError:"), invalid)
        self.assertTrue(input_not_dispatched(invalid))
        self.agent.tools.permissions.deny_tools.add("discord_send_message")
        self.assertTrue(self.agent._execute_tool("discord_send_message", {"text": "fixture"}, True)
                        .startswith("PERMISSION_DENIED:"))
        self.assertFalse(self.pending.is_set())
        self.handler.assert_not_called()
        self.assertIsNone(self.agent.orchestrator.current.in_flight)

    def test_pause_blocks_next_action_and_resume_preserves_completed_plan(self):
        orchestrator = self.agent.orchestrator
        orchestrator.set_plan(["already complete", "remaining typing"])
        orchestrator.update_step("step-1", "completed", "VERIFIED: fixture")
        identifier = orchestrator.current.task_id
        self.control.command("pause")
        future = self.dispatch("ui_type")
        self.assertTrue(self.paused.wait(2))
        self.handler.assert_not_called()
        self.control.command("resume")
        self.assertTrue(future.result(2).startswith("VERIFIED:"))
        self.handler.assert_called_once_with(text="fixture")
        self.assertEqual(orchestrator.current.task_id, identifier)
        self.assertEqual(orchestrator.current.plan[0].status, "completed")

    def test_pause_after_approval_invalidates_coordinate_authority(self):
        self.agent._desktop_observation = Mock()
        future = self.dispatch()
        identifier = self.pending_id()
        self.control.command("pause")
        self.control.command("confirm", identifier)
        self.assertTrue(self.paused.wait(2))
        self.handler.assert_not_called()
        self.control.command("resume")
        self.assertTrue(future.result(2).startswith("VERIFIED:"))
        self.agent._desktop_observation.invalidate.assert_called_once()

    def test_safe_typing_requires_no_confirmation_round_trip(self):
        result = self.agent._execute_tool("ui_type", {"text": "fixture"}, True)
        self.assertTrue(result.startswith("VERIFIED:"))
        self.handler.assert_called_once_with(text="fixture")
        self.assertFalse(self.pending.is_set())

    def test_missing_operator_channel_does_not_bypass_confirmation(self):
        self.agent.mission_control = None
        self.agent.require_action_confirmation = True
        result = self.agent._execute_tool("discord_send_message", {"text": "fixture"}, True)
        self.assertIn("one-time operator confirmation", result)
        self.handler.assert_not_called()

    def test_new_mission_cannot_clear_cancellation_while_old_handler_is_running(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow_handler(**_arguments):
            entered.set()
            if not release.wait(2):
                raise AssertionError("Fixture release was not signalled")
            return "DELIVERED: fixture only"
        self.handler.side_effect = slow_handler
        future = self.dispatch("ui_type")
        self.assertTrue(entered.wait(2))
        self.control.command("cancel")
        self.assertTrue(future.result(2).startswith("CANCELLED:"))
        self.assertTrue(self.agent._stop.is_set())
        with self.assertRaisesRegex(RuntimeError, "Previous action is still stopping"):
            self.agent.reset()
        release.set()
        self.agent._interrupted_tool.result(2)

    def test_raw_key_cannot_follow_operator_focus_into_dashboard(self):
        self.agent.tools.register(ToolSpec("desktop_press", "fixture", Risk.MEDIUM,
            {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}, self.handler))
        with patch("core.desktop_observation.foreground_identity", side_effect=[42, 99]):
            future = self.dispatch("desktop_press", {"key": "Enter"})
            self.control.command("confirm", self.pending_id())
            self.assertIn("Foreground changed during confirmation", future.result(2))
        self.handler.assert_not_called()
        self.assertIsNone(self.agent.orchestrator.current.in_flight)

    def test_raw_typing_after_pause_requires_fresh_target_resolution(self):
        self.agent.tools.register(ToolSpec("desktop_type", "fixture", Risk.MEDIUM,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, self.handler))
        self.control.command("pause")
        future = self.dispatch("desktop_type")
        self.assertTrue(self.paused.wait(2))
        self.control.command("resume")
        self.assertIn("Fresh screen_observe required after pause", future.result(2))
        self.handler.assert_not_called()

    def test_cancel_at_model_boundary_preserves_cancelled_status(self):
        agent = self.agent
        agent.workspace_context = Mock()
        agent.memory = Mock()
        agent.messages = []
        agent.max_turns = 1
        agent._chat_completion = Mock(side_effect=AssertionError("No model call should run"))
        self.control.command("pause")
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            future = self.pool.submit(agent.run, "fixture mission")
            self.assertTrue(self.paused.wait(2))
            self.control.cancel()
            self.assertTrue(future.result(2).startswith("CANCELLED:"))
        self.assertEqual(agent.orchestrator.current.status, "cancelled")
        agent._chat_completion.assert_not_called()

    def test_fast_path_cancellation_is_not_reported_as_incomplete_failure(self):
        from core.fast_execution_agent import FastExecutionFullAccessAgent
        agent = object.__new__(FastExecutionFullAccessAgent)
        agent.orchestrator = self.agent.orchestrator
        agent._is_stopped = lambda: True
        with patch("core.whatsapp_fast_mission.execute_whatsapp_ordinal_mission", return_value=None), \
             patch("core.fast_execution_agent.execute_fast_mission", return_value="CANCELLED: fixture"):
            self.assertTrue(agent.run("fixture mission").startswith("CANCELLED:"))
        self.assertEqual(agent.orchestrator.current.status, "cancelled")


class WorkerControlRoutingTests(unittest.TestCase):
    def setUp(self):
        from core import full_access_worker as worker
        self.worker = worker
        self.control = Mock()
        self.writer = Mock()
        for name, value in (("_ACTIVE_CONTROL", self.control), ("_ACTIVE_REQUEST", "fixture-request"),
                            ("_write", self.writer)):
            context = patch.object(worker, name, value)
            context.start()
            self.addCleanup(context.stop)

    def test_active_request_routes_pause_and_exact_confirmation(self):
        for action, identifier in (("pause", ""), ("resume", ""), ("cancel", ""), ("confirm", "c" * 32),
                                   ("reject", "d" * 32)):
            with self.subTest(action=action):
                self.assertTrue(self.worker._control_message({"protocol": 1, "id": "fixture-request",
                                                              "action": action, "confirmation_id": identifier}))
                self.control.command.assert_called_with(action, identifier)
        self.writer.assert_not_called()

    def test_stale_request_or_injected_properties_cannot_change_control(self):
        for extra in ({"id": "other-mission"}, {"approved": True}, {"command": "arbitrary tool"}):
            with self.subTest(extra=extra):
                self.assertTrue(self.worker._control_message({"protocol": 1, "id": "fixture-request",
                                                              "action": "pause", **extra}))
                self.assertEqual(self.writer.call_args.args[0]["kind"], "control_error")
        self.control.command.assert_not_called()

    def test_unrelated_messages_are_not_treated_as_operator_commands(self):
        for message in ({"protocol": 2, "action": "pause"}, {"protocol": 1, "action": "run"},
                        {"protocol": 1, "action": "task_verify", "approved": True}):
            self.assertFalse(self.worker._control_message(message))
        self.control.command.assert_not_called()
        self.writer.assert_not_called()

    def test_wrong_confirmation_error_keeps_worker_control_channel_alive(self):
        self.control.command.side_effect = ValueError("Confirmation is expired")
        self.assertTrue(self.worker._control_message({"protocol": 1, "id": "fixture-request",
                                                      "action": "confirm", "confirmation_id": "bad"}))
        self.assertEqual(self.writer.call_args.args[0]["kind"], "control_error")
        self.assertIn("expired", self.writer.call_args.args[0]["message"])


if __name__ == "__main__":
    unittest.main()
