from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.native_ui_input import _append_caret, ui_type_native
from core.desktop_input import InputDeliveryError, InputNotDispatchedError
from test_semantic_ui_tools import _Editor, _Window


class TextRange:
    def __init__(self, pattern, start=0, end=None):
        self.pattern = pattern
        self.start = start
        self.end = len(pattern.control.value) if end is None else end

    def Clone(self):
        return TextRange(self.pattern, self.start, self.end)

    def GetText(self, limit):
        return self.pattern.control.value[self.start:self.end][:limit]

    def MoveEndpointByRange(self, endpoint, other, other_endpoint):
        position = other.end if other_endpoint else other.start
        if endpoint:
            self.end = position
            self.start = min(self.start, self.end)
        else:
            self.start = position
            self.end = max(self.start, self.end)

    def MoveEndpointByUnit(self, endpoint, unit, count):
        assert unit == 0
        old = self.end if endpoint else self.start
        position = max(0, min(len(self.pattern.control.value), old + count))
        if endpoint:
            self.end = position
        else:
            self.start = position
        return position - old

    def CompareEndpoints(self, endpoint, other, other_endpoint):
        return (self.end if endpoint else self.start) - (other.end if other_endpoint else other.start)

    def Select(self):
        self.pattern.selection = self.Clone()


class TextPattern:
    def __init__(self, control):
        self.control = control
        self.selection = TextRange(self, 0, 0)

    @property
    def DocumentRange(self):
        return TextRange(self)

    def GetSelection(self):
        return SimpleNamespace(Length=1, GetElement=lambda _: self.selection)


class NativeEditorCaretTests(unittest.TestCase):
    def setUp(self):
        self.editor = _Editor()
        self.editor.iface_text = TextPattern(self.editor)
        self.window = _Window([self.editor])
        self.client = Mock()
        self.status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "Fixture"}}
        for item in (patch.object(ui, "_window", return_value=self.window),
                     patch.object(ui, "_foreground_hwnd", return_value=123),
                     patch("core.semantic_ui_guard._browser_block", return_value=None),
                     patch("core.rust_engine._preflight", return_value=(self.client, self.status)),
                     patch("core.rust_engine.native_engine_mode", return_value="rust")):
            item.start()
            self.addCleanup(item.stop)

    def test_empty_webview_paragraph_uses_rust_and_exact_readback_with_or_without_marker(self):
        for typing in (ui_type_native, ui.ui_type):
            for before in ("\n", "\r\n"):
                for suffix in ("", before):
                    with self.subTest(typing=typing.__name__, before=before, suffix=suffix):
                        self.editor.value = before
                        self.client.reset_mock()
                        def deliver(text, status, *, before_dispatch):
                            before_dispatch()
                            self.editor.value = text + suffix
                            return {"executed": True, "simulation": False}
                        self.client.type_text.side_effect = deliver
                        result = typing("CAT")
                        self.assertTrue(result.startswith("VERIFIED:"), result)
                        self.assertIn("rust_native_input", result)
                        self.editor.iface_value.SetValue.assert_not_called()
                        self.client.type_text.assert_called_once()
                        self.client.hotkey.assert_not_called()

    def test_standard_typing_preserves_context_guard_after_native_capture(self):
        self.editor.value = "\n"
        active_context = [True]
        def guard():
            if not active_context[0]:
                raise InputDeliveryError("Mission context changed")
        dispatched = []
        def deliver(text, status, *, before_dispatch):
            active_context[0] = False
            before_dispatch()
            dispatched.append(text)
        self.client.type_text.side_effect = deliver
        with self.assertRaises(InputNotDispatchedError):
            ui.ui_type("CAT", state_guard=guard)
        self.assertEqual(dispatched, [])
        self.editor.iface_value.SetValue.assert_not_called()

    def test_changed_or_partial_text_after_native_input_never_verifies_or_repeats(self):
        for typing in (ui_type_native, ui.ui_type):
            for after in ("\n", "CA", "CAT extra", "CAT\n\n"):
                with self.subTest(typing=typing.__name__, after=after):
                    self.editor.value = "\n"
                    self.client.reset_mock()
                    def deliver(text, status, *, before_dispatch):
                        before_dispatch()
                        self.editor.value = after
                        return {"executed": True, "simulation": False}
                    self.client.type_text.side_effect = deliver
                    def poll_once(probe, **kwargs):
                        if not probe():
                            raise TimeoutError("no exact readback")
                    with patch("core.ui_state.wait_until", side_effect=poll_once), \
                         patch.object(ui, "wait_until", side_effect=poll_once):
                        with self.assertRaises(InputDeliveryError):
                            typing("CAT")
                    self.client.type_text.assert_called_once()
                    self.editor.iface_value.SetValue.assert_not_called()

    def test_existing_text_is_preserved_and_caret_moves_before_terminal_marker(self):
        for before, after in (("draft", "draftCAT"), ("draft\n", "draftCAT\n")):
            with self.subTest(before=before):
                self.editor.value = before
                def deliver(text, status, *, before_dispatch):
                    before_dispatch()
                    position = self.editor.iface_text.selection.start
                    self.editor.value = before[:position] + text + before[position:]
                    return {"executed": True, "simulation": False}
                self.client.type_text.side_effect = deliver
                self.assertTrue(ui_type_native("CAT").startswith("VERIFIED:"))
                self.assertEqual(self.editor.value, after)

    def test_caret_change_during_capture_prevents_native_dispatch(self):
        self.editor.value = "draft"
        delivered = []
        def prepare(text, status, *, before_dispatch):
            self.editor.iface_text.selection = TextRange(self.editor.iface_text, 0, 0)
            before_dispatch()
            delivered.append(text)
        self.client.type_text.side_effect = prepare
        with self.assertRaises(InputNotDispatchedError):
            ui_type_native("CAT")
        self.assertEqual(delivered, [])
        self.assertEqual(self.editor.value, "draft")

    def test_webview_caret_beyond_document_end_appends_without_reselecting(self):
        self.editor.value = "draft"
        pattern = self.editor.iface_text
        pattern.selection = TextRange(pattern, 6, 6)
        def deliver(text, status, *, before_dispatch):
            before_dispatch()
            self.editor.value += text
            return {"executed": True, "simulation": False}
        self.client.type_text.side_effect = deliver
        with patch.object(TextRange, "Select", side_effect=AssertionError("Already at logical end")):
            self.assertTrue(ui_type_native("CAT").startswith("VERIFIED:"))
        self.assertEqual(self.editor.value, "draftCAT")
        self.client.type_text.assert_called_once()
        self.editor.iface_value.SetValue.assert_not_called()

    def test_ignored_select_cannot_type_in_middle_or_over_selection(self):
        self.editor.value = "draft"
        for start, end in ((0, 0), (2, 2), (4, 5), (5, 6)):
            with self.subTest(start=start, end=end):
                pattern = self.editor.iface_text
                pattern.selection = TextRange(pattern, start, end)
                with patch.object(TextRange, "Select", return_value=None):
                    with self.assertRaises(InputNotDispatchedError):
                        ui_type_native("CAT")
                self.client.type_text.assert_not_called()
                self.assertEqual(self.editor.value, "draft")

    def test_append_guard_rereads_document_and_rejects_provider_errors(self):
        self.editor.value = "draft"
        _, _, guard = _append_caret(self.editor, "draft")
        self.editor.value = "changed"
        with self.assertRaises(InputNotDispatchedError):
            guard()
        self.editor.value = "draft"
        with patch.object(self.editor.iface_text, "GetSelection", side_effect=RuntimeError("Provider disconnected")):
            with self.assertRaises(InputNotDispatchedError):
                guard()

    def test_disagreeing_text_and_value_providers_reject_before_typing(self):
        self.editor.value = "draft"
        self.editor.iface_text = Mock()
        self.editor.iface_text.DocumentRange.GetText.return_value = "different"
        with self.assertRaises(InputNotDispatchedError):
            ui_type_native("CAT")
        self.client.type_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
