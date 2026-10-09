from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, PropertyMock, patch

from core import semantic_ui_tools as ui
if __package__:
    from .test_semantic_ui_tools import _Control, _Editor, _Window
else:
    from test_semantic_ui_tools import _Control, _Editor, _Window


class SemanticWaitStateTests(unittest.TestCase):
    def setUp(self):
        self.editor = _Editor("draft مرحبا")
        self.button = _Control("Continue", "Button")
        self.window = _Window([self.editor, self.button])
        for item in (patch.object(ui, "_window", return_value=self.window),
                     patch.object(ui, "_foreground_hwnd", return_value=123)):
            item.start()
            self.addCleanup(item.stop)

    def wait(self, target, state, **kwargs):
        return json.loads(ui.ui_wait_state(target, state=state, timeout_ms=0, **kwargs).split(": ", 1)[1])

    def test_exact_text_reads_editor_value_without_focus_or_keyboard_input(self):
        with patch.object(ui, "_focus_window") as focus_window, patch.object(ui, "_focus_control") as focus_control:
            evidence = self.wait("Message", "text", text="draft مرحبا")
        self.assertEqual(evidence["text"], "draft مرحبا")
        focus_window.assert_not_called()
        focus_control.assert_not_called()
        self.editor.iface_value.SetValue.assert_not_called()

    def test_text_mismatch_and_unreadable_editor_label_never_verify(self):
        for text in ("draft", "draft مرحبا ", "Message"):
            with self.subTest(text=text), self.assertRaises(TimeoutError):
                self.wait("Message", "text", text=text)
        self.window._controls = [_Control("Message", "Edit")]
        with self.assertRaises(TimeoutError):
            self.wait("Message", "text", text="Message")

    def test_empty_value_and_exact_noneditor_display_text_are_supported(self):
        self.editor.value = ""
        self.assertEqual(self.wait("Message", "text", text="")["text"], "")
        self.button.element_info.name = "  Ready\nnow  "
        self.button.element_info.automation_id = "status"
        self.assertEqual(self.wait("status", "text", text="  Ready\nnow  ")["text"], "  Ready\nnow  ")
        with self.assertRaises(TimeoutError):
            self.wait("status", "text", text="Ready now")

    def test_enabled_wait_rejects_disabled_controls(self):
        self.button.is_enabled = Mock(return_value=False)
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "enabled")
        self.button.is_enabled.return_value = True
        self.assertEqual(self.wait("Continue", "enabled")["control"]["enabled"], True)

    def test_selected_wait_reads_selection_without_focus_or_activation(self):
        self.button.is_selected = Mock(return_value=True)
        self.button.is_enabled = Mock(return_value=False)
        with patch.object(ui, "_focus_window") as focus, patch.object(ui, "_activate_control") as activate:
            evidence = self.wait("Continue", "selected", control_type="Button")
        self.assertTrue(evidence["selected"])
        self.assertTrue(evidence["control"]["selected"])
        self.assertFalse(evidence["control"]["enabled"])
        focus.assert_not_called()
        activate.assert_not_called()

    def test_selected_wait_rejects_missing_pattern_false_unknown_and_ambiguous_results(self):
        for selection in (False, "true", None, RuntimeError("stale provider")):
            with self.subTest(selection=selection):
                self.button.is_selected = Mock(side_effect=selection) if isinstance(selection, Exception) else Mock(return_value=selection)
                with self.assertRaises(TimeoutError):
                    self.wait("Continue", "selected")
        self.button.is_selected = Mock(return_value=True)
        duplicate = _Control("Continue", "Button")
        duplicate.is_enabled = Mock(return_value=False)
        self.window._controls.append(duplicate)
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "selected")

    def test_disabled_visible_controls_are_observable_but_not_actionable(self):
        self.editor.is_enabled = Mock(return_value=False)
        self.assertFalse(self.wait("Message", "visible")["control"]["enabled"])
        self.assertEqual(self.wait("Message", "text", text=self.editor.value)["text"], self.editor.value)
        with self.assertRaises(TimeoutError):
            self.wait("Message", "enabled")
        with self.assertRaises(RuntimeError):
            ui._find_control(self.window, "Message")

    def test_duplicate_disabled_control_cannot_hide_readback_ambiguity(self):
        duplicate = _Editor(self.editor.value)
        duplicate.is_enabled = Mock(return_value=False)
        self.window._controls.append(duplicate)
        for state in ("visible", "text"):
            with self.subTest(state=state), self.assertRaises(TimeoutError):
                self.wait("Message", state, text=self.editor.value)

    def test_hidden_requires_absence_or_observed_hidden_status(self):
        self.button.is_enabled = Mock(return_value=False)
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "hidden")
        self.button.is_visible = Mock(return_value=False)
        hidden = self.wait("Continue", "hidden")
        self.assertFalse(hidden["absent"])
        self.assertFalse(hidden["control"]["visible"])
        self.window._controls = [self.editor]
        absent = self.wait("Continue", "hidden")
        self.assertTrue(absent["absent"])
        self.assertTrue(absent["complete_tree"])

    def test_hidden_does_not_accept_provider_errors_ambiguity_or_unknown_visibility(self):
        with patch.object(ui, "_descendants", side_effect=RuntimeError("UIA disconnected")):
            with self.assertRaises(TimeoutError):
                self.wait("Continue", "hidden")
        self.button.is_visible = Mock(side_effect=RuntimeError("Stale control"))
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "hidden")
        self.button.is_visible = Mock(return_value=False)
        duplicate = _Control("Continue", "Button")
        duplicate.is_visible = Mock(return_value=False)
        self.window._controls.append(duplicate)
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "hidden")

    def test_hidden_requires_complete_tree_and_stable_named_reference(self):
        with patch.object(ui, "_descendants", wraps=ui._descendants) as descendants:
            self.wait("Missing", "hidden", control_type="Button")
        descendants.assert_called_once_with(self.window, require_complete=True, control_types=("Button",))
        with self.assertRaises(ValueError):
            self.wait("Continue", "hidden", selector={"ordinal": 1}, control_type="Button")
        self.window._controls = [_Control(f"Button {index}", "Button") for index in range(701)]
        with self.assertRaises(TimeoutError):
            self.wait("Missing", "hidden", control_type="Button")

    def test_hidden_does_not_treat_unreadable_control_identity_as_absence(self):
        self.button.element_info = Mock()
        type(self.button.element_info).name = PropertyMock(side_effect=RuntimeError("Provider disconnected"))
        with self.assertRaises(TimeoutError):
            self.wait("Continue", "hidden")

    def test_inspection_exposes_rows_sliders_and_spinners(self):
        self.window._controls = [_Control(kind, kind) for kind in ("DataItem", "Slider", "Spinner")]
        observed = json.loads(ui.ui_inspect(force_refresh=True))
        self.assertEqual([row["type"] for row in observed["controls"]], ["DataItem", "Slider", "Spinner"])


if __name__ == "__main__":
    unittest.main()
