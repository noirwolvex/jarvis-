from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        self.addCleanup(agent._tool_executor.shutdown, wait=True)
        self.vision = {"role": "user", "content": [{"type": "text", "text": "fixture observation"}]}
        for target, value in (
            ("core.full_access_agent._chrome_tab_rows", []),
            ("core.desktop_observation.foreground_identity", 7),
            ("core.full_access_agent.vision_followup_message", self.vision),
        ):
            mock = patch(target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

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
