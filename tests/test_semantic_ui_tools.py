from __future__ import annotations

import unittest
import json
from unittest.mock import Mock, patch

from core.semantic_ui_tools import _find_control, _score, register_semantic_ui_tools
from core.tools import ToolRegistry
from core import semantic_ui_tools as ui
from core.desktop_input import InputDeliveryError


class _Info:
    def __init__(self, name: str, control_type: str, automation_id: str = "") -> None:
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(self, name: str, control_type: str, *, automation_id: str = "", top: int = 0, bottom: int = 100) -> None:
        self.element_info = _Info(name, control_type, automation_id)
        self._rect = _Rect(0, top, 500, bottom)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def rectangle(self):
        return self._rect

    def window_text(self):
        return self.element_info.name


class _Window:
    def __init__(self, controls):
        self._controls = controls
        self.handle = 123
        self.reads = 0

    def descendants(self):
        self.reads += 1
        return list(self._controls)

    def window_text(self):
        return "Demo"


class _Editor(_Control):
    def __init__(self, value=""):
        super().__init__("Message", "Edit")
        self.value = value
        self.iface_value = Mock()
        self.iface_value.SetValue.side_effect = self._set_value

    def _set_value(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def has_keyboard_focus(self):
        return True


class SemanticUiToolsTests(unittest.TestCase):
    def setUp(self):
        ui._SNAPSHOTS.invalidate()
        foreground = patch.object(ui, "_foreground_hwnd", return_value=123)
        foreground.start()
        self.addCleanup(foreground.stop)
    def test_exact_semantic_name_beats_partial_match(self) -> None:
        exact = _Control("Space Zone", "Button")
        partial = _Control("Space Zone Games", "Button")
        win = _Window([partial, exact])
        self.assertIs(_find_control(win, "Space Zone"), exact)
        self.assertGreater(_score(exact, "Space Zone"), _score(partial, "Space Zone"))

    def test_auto_edit_prefers_named_message_composer(self) -> None:
        search = _Control("Search", "Edit", top=50, bottom=90)
        composer = _Control("Message #general", "Edit", top=800, bottom=850)
        win = _Window([search, composer])
        self.assertIs(_find_control(win, editable=True), composer)

    def test_tools_register_with_compound_batch(self) -> None:
        registry = ToolRegistry()
        register_semantic_ui_tools(registry)
        for name in ("ui_inspect", "ui_focus", "ui_activate", "ui_type", "ui_hotkey", "ui_batch"):
            self.assertIn(name, registry._tools)
        self.assertEqual(registry._tools["ui_batch"].risk.name, "MEDIUM")
        self.assertEqual(registry._tools["ui_wait_state"].risk.name, "LOW")
        self.assertEqual(registry._tools["ui_focus"].risk.name, "MEDIUM")

    def test_fuzzy_and_duplicate_exact_targets_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "exact visible"):
            _find_control(_Window([_Control("Send now", "Button")]), "Send")
        with self.assertRaisesRegex(RuntimeError, "2 exact"):
            _find_control(_Window([_Control("Send", "Button"), _Control("Send", "Button")]), "Send")

    def test_auto_composer_does_not_choose_by_screen_position(self):
        with self.assertRaisesRegex(RuntimeError, "2 exact"):
            _find_control(_Window([_Control("Message #one", "Edit"), _Control("Message #two", "Edit", top=900)]), editable=True)

    def test_inspection_reuses_detached_cache_without_focusing(self):
        win = _Window([_Control("Send", "Button")])
        with patch.object(ui, "_window", return_value=win), patch.object(ui, "_focus_window") as focus:
            first = json.loads(ui.ui_inspect())
            second = json.loads(ui.ui_inspect())
            ui.ui_inspect(force_refresh=True)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(win.reads, 2)
        focus.assert_not_called()

    def test_live_actions_never_resolve_control_from_cached_snapshot(self):
        old = _Control("Send", "Button")
        win = _Window([old])
        with patch.object(ui, "_window", return_value=win):
            ui.ui_inspect()
            replacement = _Control("Send", "Button")
            win._controls = [replacement]
            self.assertIs(ui._find_control(win, "Send"), replacement)

    def test_activation_is_delivery_only_and_does_not_physically_click(self):
        button = _Control("Open", "Button")
        button.iface_invoke = Mock()
        button.click_input = Mock()
        win = _Window([button])
        with patch.object(ui, "_window", return_value=win), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_foreground_hwnd", return_value=123), patch.object(ui, "_guard_foreground"):
            result = ui.ui_activate("Open")
        self.assertTrue(result.startswith("DELIVERED:"))
        button.iface_invoke.Invoke.assert_called_once()
        button.click_input.assert_not_called()

    def test_uia_control_without_invoke_uses_rust_center_click_in_strict_mode(self):
        button = _Control("Open", "Button")
        win = _Window([button])
        client = Mock()
        client.click.return_value = {"executed": True, "simulation": False}
        with patch.object(ui, "_window", return_value=win), \
             patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), \
             patch("core.rust_engine._preflight", return_value=(client, {"native_input": True})), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"):
            result = ui.ui_activate("Open")
        self.assertTrue(result.startswith("DELIVERED:"))
        data = json.loads(result.split(": ", 1)[1])
        self.assertEqual(data["method"], "rust_uia_center_click")
        client.click.assert_called_once_with(250, 50, {"native_input": True})

    def test_uncertain_invoke_never_retries_select_or_physical_click(self):
        button = _Control("Open", "Button")
        button.iface_invoke = Mock()
        button.iface_invoke.Invoke.side_effect = RuntimeError("COM failed after dispatch")
        button.iface_selection_item = Mock()
        with self.assertRaises(InputDeliveryError):
            ui._invoke(button)
        button.iface_selection_item.Select.assert_not_called()

    def test_unsupported_patterns_request_observed_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "fresh screen observation"):
            ui._invoke(_Control("Canvas", "Button"))

    def test_state_assertion_reads_without_focusing(self):
        win = _Window([_Control("Ready", "Button")])
        with patch.object(ui, "_window", return_value=win), patch.object(ui, "_focus_window") as focus:
            self.assertTrue(ui.ui_wait_state("Ready", timeout_ms=0).startswith("VERIFIED:"))
        focus.assert_not_called()

    def test_batch_preflights_every_step_before_first_action(self):
        for invalid in [{"op": "type", "target": "Message"}, {"op": "hotkey", "keys": []},
                        {"op": "type", "target": "Message", "text": "x", "submit": "false"},
                        {"op": "activate", "target": ""}]:
            with self.subTest(invalid=invalid), patch.object(ui, "ui_activate") as activate:
                with self.assertRaises(Exception):
                    ui.ui_batch([{"op": "activate", "target": "Open"}, invalid, {"op": "assert_visible", "target": "Ready"}])
                activate.assert_not_called()

    def test_batch_requires_result_checkpoint_before_any_dispatch(self):
        with patch.object(ui, "ui_activate") as activate:
            with self.assertRaisesRegex(ValueError, "checkpoint|assert_visible"):
                ui.ui_batch([{"op": "activate", "target": "Open"}, {"op": "wait", "seconds": 0}])
        activate.assert_not_called()

    def test_batch_continues_known_navigation_with_semantic_checkpoints(self):
        with patch.object(ui, "ui_activate", return_value="DELIVERED: opened") as activate, \
             patch.object(ui, "ui_hotkey", return_value="DELIVERED: tab") as hotkey, \
             patch.object(ui, "ui_wait_state", return_value="VERIFIED: ready") as verify:
            result = ui.ui_batch([{"op": "activate", "target": "Open"}, {"op": "assert_visible", "target": "Dialog"},
                                  {"op": "hotkey", "keys": ["tab"]},
                                  {"op": "assert_visible", "target": "Ready"}])
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertEqual(json.loads(result.split(": ", 1)[1])["completed_count"], 4)
        self.assertEqual((activate.call_count, hotkey.call_count, verify.call_count), (1, 1, 2))

    def test_wait_only_batch_has_no_completion_evidence(self):
        with patch.object(ui, "cancellable_delay") as delay:
            with self.assertRaisesRegex(ValueError, "completion evidence"):
                ui.ui_batch([{"op": "wait", "seconds": 1}])
        delay.assert_not_called()

    def test_text_cannot_replace_result_assertion_after_submit_hotkey(self):
        with patch.object(ui, "ui_hotkey") as hotkey:
            with self.assertRaisesRegex(ValueError, "before another mutation"):
                ui.ui_batch([{"op": "hotkey", "keys": ["enter"]}, {"op": "type", "target": "Message", "text": "hello"}])
        hotkey.assert_not_called()

    def test_registered_batch_cannot_bypass_nested_tool_deny(self):
        registry = ToolRegistry()
        ui.register_semantic_ui_tools(registry)
        registry.permissions.deny_tools.add("ui_type")
        with patch.object(ui, "ui_type") as type_text:
            result = registry.execute("ui_batch", {"actions": [{"op": "type", "target": "Message", "text": "hello"}]}, approved=True)
        self.assertIn("explicitly denied", result)
        type_text.assert_not_called()

    def test_registered_batch_rechecks_changed_policy_before_dispatch(self):
        registry = ToolRegistry()
        ui.register_semantic_ui_tools(registry)
        def first_action(**kwargs):
            registry.permissions.deny_tools.add("ui_type")
            return "VERIFIED: ready"
        with patch.object(ui, "ui_wait_state", side_effect=first_action), patch.object(ui, "ui_type") as type_text:
            result = registry.execute("ui_batch", {"actions": [{"op": "assert_visible", "target": "Ready"},
                                       {"op": "type", "target": "Message", "text": "hello"}]}, approved=True)
        self.assertTrue(result.startswith("PERMISSION_DENIED:"))
        self.assertIn('"completed_count": 1', result)
        type_text.assert_not_called()

    def test_batch_failure_keeps_completed_and_unrun_indexes_no_replay(self):
        with patch.object(ui, "ui_activate", return_value="DELIVERED: opened") as activate, \
             patch.object(ui, "ui_wait_state", side_effect=TimeoutError("missing result")) as verify, \
             patch.object(ui, "ui_type") as type_text:
            result = ui.ui_batch([{"op": "activate", "target": "Open"}, {"op": "assert_visible", "target": "Ready"},
                                  {"op": "type", "target": "Message", "text": "hello"}])
        data = json.loads(result.split(": ", 1)[1])
        self.assertTrue(result.startswith("ERROR:"))
        self.assertEqual(data["failed_index"], 1)
        self.assertEqual(data["not_run"], [2])
        self.assertEqual(data["completed_count"], 1)
        activate.assert_called_once()
        verify.assert_called_once()
        type_text.assert_not_called()

    def test_batch_cancellation_does_not_execute_later_actions(self):
        with patch.object(ui, "check_cancelled", side_effect=[None, RuntimeError("Emergency stop is active")]), \
             patch.object(ui, "ui_activate", return_value="DELIVERED: opened"), patch.object(ui, "ui_wait_state") as verify:
            result = ui.ui_batch([{"op": "activate", "target": "Open"}, {"op": "assert_visible", "target": "Ready"}])
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIn("Emergency stop", result)
        verify.assert_not_called()

    def test_value_reader_does_not_mistake_editor_name_for_value(self):
        self.assertIsNone(ui._control_value(_Control("hello", "Edit")))

    def test_type_verifies_full_replacement_not_substring(self):
        editor = _Editor()
        editor.iface_value.SetValue.side_effect = lambda value: setattr(editor, "value", value + "wrong")
        real_wait = ui.wait_until
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch.object(ui, "wait_until", side_effect=lambda probe, **kw: real_wait(probe, timeout=0)), \
             patch.object(ui, "paste_text") as paste:
            with self.assertRaisesRegex(InputDeliveryError, "exactly"):
                ui.ui_type("hello", target="Message", replace=True)
        editor.iface_value.SetValue.assert_called_once_with("hello")
        paste.assert_not_called()

    def test_type_does_not_retry_uncertain_setvalue(self):
        editor = _Editor()
        editor.iface_value.SetValue.side_effect = RuntimeError("after write")
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch.object(ui, "paste_text") as paste:
            with self.assertRaisesRegex(InputDeliveryError, "uncertain"):
                ui.ui_type("hello", target="Message")
        editor.iface_value.SetValue.assert_called_once()
        paste.assert_not_called()

    def test_existing_message_echo_is_not_new_submit_evidence(self):
        editor = _Editor()
        existing = _Control("hello", "Text")
        real_wait = ui.wait_until
        with patch.object(ui, "_window", return_value=_Window([editor, existing])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch.object(ui, "wait_until", side_effect=lambda probe, **kw: real_wait(probe, timeout=0)), \
             patch("core.tools._desktop_press") as press:
            with self.assertRaisesRegex(InputDeliveryError, "do not resend"):
                ui.ui_type("hello", target="Message", submit=True)
        press.assert_called_once_with("enter")

    def test_verified_value_then_composer_clear_is_new_submit_evidence(self):
        editor = _Editor()
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch("core.tools._desktop_press", side_effect=lambda key: setattr(editor, "value", "")):
            result = ui.ui_type("hello", target="Message", submit=True)
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertTrue(json.loads(result.split(": ", 1)[1])["composer_cleared"])

    def test_existing_draft_is_not_silently_submitted(self):
        editor = _Editor("draft")
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch("core.tools._desktop_press") as press:
            with self.assertRaisesRegex(InputDeliveryError, "already contains"):
                ui.ui_type("hello", target="Message", submit=True)
        editor.iface_value.SetValue.assert_not_called()
        press.assert_not_called()

    def test_external_state_guard_runs_before_any_focus(self):
        with patch.object(ui, "_window") as window:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                ui.ui_type("hello", state_guard=Mock(side_effect=RuntimeError("Context changed")))
        window.assert_not_called()

    def test_post_submit_context_failure_is_uncertain_not_retryable(self):
        editor = _Editor()
        submitted = False
        def press(key):
            nonlocal submitted
            submitted = True
        def state_guard(*args):
            if submitted:
                raise RuntimeError("selected channel changed")
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground", side_effect=state_guard), patch("core.tools._desktop_press", side_effect=press):
            with self.assertRaisesRegex(InputDeliveryError, "do not resend"):
                ui.ui_type("hello", target="Message", submit=True)

    def test_multiline_unicode_fallback_never_submits_partial_lines(self):
        editor = _Editor()
        del editor.iface_value
        with patch.object(ui, "_window", return_value=_Window([editor])), patch.object(ui, "_focus_window", return_value=123), \
             patch.object(ui, "_guard_foreground"), patch.object(ui, "paste_text") as paste, patch("core.tools._desktop_press") as press:
            with self.assertRaisesRegex(InputDeliveryError, "Multiline"):
                ui.ui_type("hello\nworld", target="Message", submit=True)
        paste.assert_not_called()
        press.assert_not_called()

    def test_inspection_includes_container_hierarchy_and_focus(self):
        win = _Window([])
        panel = _Control("Channel conversation", "Pane")
        editor = _Editor()
        editor.parent = lambda: panel
        panel.parent = lambda: win
        win._controls = [editor]
        with patch.object(ui, "_window", return_value=win):
            data = json.loads(ui.ui_inspect())
        self.assertEqual(data["focused_controls"], [0])
        self.assertTrue(data["is_foreground"])
        self.assertEqual(data["controls"][0]["ancestors"], ["container-0", "window"])
        self.assertEqual(data["controls"][0]["depth"], 2)
        self.assertEqual(data["containers"][0]["name"], "Channel conversation")

    def test_focus_reuses_foreground_without_fixed_wait(self):
        win = Mock(handle=123)
        with patch.object(ui, "wait_until") as wait:
            self.assertEqual(ui._focus_window(win), 123)
        win.set_focus.assert_not_called()
        wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
