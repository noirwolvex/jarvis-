from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import core.whatsapp_native as whatsapp


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(
        self,
        name: str,
        control_type: str,
        rect: tuple[int, int, int, int],
        parent=None,
        visible: bool = True,
        enabled: bool = True,
    ):
        self.name = name
        self.control_type = control_type
        self.rect = list(rect)
        self._parent = parent
        self._visible = visible
        self._enabled = enabled

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def parent(self):
        return self._parent


class _Window:
    def __init__(self, controls):
        self.controls = controls

    def rectangle(self):
        return _Rect(0, 0, 1200, 800)


def _name(control):
    return control.name


def _type(control):
    return control.control_type


def _rect(control):
    return list(control.rect)


class WhatsAppNativeRowDiscoveryTests(unittest.TestCase):
    def test_modern_custom_rows_are_resolved_from_named_text_descendants(self) -> None:
        first_row = _Control("", "Custom", (70, 130, 545, 200))
        first_name = _Control("Alice", "Text", (118, 145, 260, 168), parent=first_row)
        first_preview = _Control("hello", "Text", (118, 171, 340, 190), parent=first_row)

        second_row = _Control("", "Group", (70, 205, 545, 275))
        second_name = _Control("Bob", "Text", (118, 220, 250, 244), parent=second_row)
        second_preview = _Control("meeting at 4", "Text", (118, 246, 355, 266), parent=second_row)

        nav_row = _Control("", "Custom", (70, 70, 545, 120))
        nav_label = _Control("Chats", "Text", (110, 82, 190, 105), parent=nav_row)

        win = _Window([
            nav_label,
            nav_row,
            first_name,
            first_preview,
            first_row,
            second_name,
            second_preview,
            second_row,
        ])

        with patch("core.semantic_ui_tools._descendants", return_value=win.controls), \
             patch("core.semantic_ui_tools._control_name", side_effect=_name), \
             patch("core.semantic_ui_tools._control_type", side_effect=_type), \
             patch("core.semantic_ui_tools._rect", side_effect=_rect):
            candidates = whatsapp._chat_candidates(win, 2)

        self.assertEqual(candidates, [first_row, second_row])

    def test_duplicate_text_children_do_not_create_duplicate_chat_positions(self) -> None:
        row = _Control("", "Custom", (70, 130, 545, 200))
        title = _Control("Alice", "Text", (118, 145, 260, 168), parent=row)
        preview = _Control("two child controls in the same visual row", "Text", (118, 171, 430, 190), parent=row)
        win = _Window([title, preview, row])

        with patch("core.semantic_ui_tools._descendants", return_value=win.controls), \
             patch("core.semantic_ui_tools._control_name", side_effect=_name), \
             patch("core.semantic_ui_tools._control_type", side_effect=_type), \
             patch("core.semantic_ui_tools._rect", side_effect=_rect):
            candidates = whatsapp._chat_candidates(win, 1)

        self.assertEqual(candidates, [row])


class WhatsAppNativeExecutionTests(unittest.TestCase):
    def setUp(self):
        from core import semantic_ui_tools as ui
        from test_semantic_ui_tools import _Control as Control, _Window as Window, _Info
        self.ui = ui
        self.row = Control("Fixture chat", "ListItem", top=130, bottom=200)
        self.row.is_selected = Mock(return_value=False)
        self.row.has_keyboard_focus = Mock(return_value=False)
        self.row.iface_selection_item = Mock()
        self.composer = Control("Type a message", "Edit", top=700, bottom=740)
        self.composer._rect.left, self.composer._rect.right = 650, 1100
        self.win = Window([self.row, self.composer])
        self.win.element_info = _Info("WhatsApp", "Window")
        self.win.rectangle = lambda: _Rect(0, 0, 1200, 800)
        for index, control in enumerate([self.win, self.row, self.composer]):
            control.element_info.runtime_id = [42, index]
            control.element_info.process_id = 42
        self.client = Mock()
        self.status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "WhatsApp"}}
        self.dispatches = []
        query_patch = patch.object(ui, "_descendants", wraps=ui._descendants)
        self.query = query_patch.start()
        self.addCleanup(query_patch.stop)

        def click(x, y, status, *, before_dispatch):
            before_dispatch()
            self.dispatches.append((x, y))
            self.row.is_selected.return_value = True
            return {"executed": True, "simulation": False}
        self.client.click.side_effect = click
        ui._SNAPSHOTS.invalidate()
        self.addCleanup(ui._SNAPSHOTS.invalidate)
        for mocked in (
            patch.object(ui, "_window", return_value=self.win),
            patch.object(ui, "_foreground_hwnd", return_value=123),
            patch("core.semantic_ui_guard._browser_block", return_value=None),
            patch("core.rust_engine._preflight", return_value=(self.client, self.status)),
            patch("core.rust_engine.native_engine_mode", return_value="rust"),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_selected_row_verifies_with_one_tree_scan_and_one_guarded_click(self):
        result = whatsapp.whatsapp_select_chat_native(1)
        data = json.loads(result.split(": ", 1)[1])
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertEqual(self.dispatches, [(250, 165)])
        self.assertEqual(self.query.call_count, 1)
        self.assertTrue(data["evidence"]["selected"])
        self.assertFalse(data["evidence"]["view_changed"])

    def test_already_selected_row_skips_click_and_conversation_scan(self):
        self.row.is_selected.return_value = True
        with patch.object(whatsapp, "_right_pane_signature") as signature:
            result = whatsapp.whatsapp_select_chat_native(1)
        self.assertIn("already_selected", result)
        self.assertEqual(self.query.call_count, 1)
        self.client.click.assert_not_called()
        signature.assert_not_called()

    def test_focus_alone_cannot_verify_conversation_selection_or_replay_click(self):
        from core.desktop_input import InputDeliveryError
        def click(x, y, status, *, before_dispatch):
            before_dispatch()
            self.row.has_keyboard_focus.return_value = True
            return {"executed": True, "simulation": False}
        self.client.click.side_effect = click
        def bounded_wait(probe, **kwargs):
            if not probe():
                raise TimeoutError("fixture state did not change")
        with patch("core.ui_state.wait_until", side_effect=bounded_wait):
            with self.assertRaisesRegex(InputDeliveryError, "no independent"):
                whatsapp.whatsapp_select_chat_native(1)
        self.client.click.assert_called_once()
        self.assertEqual(self.query.call_count, 2)

    def test_missing_selection_pattern_uses_independent_conversation_change(self):
        def click(x, y, status, *, before_dispatch):
            before_dispatch()
            self.composer.element_info.name = "Type a message to Fixture chat"
            return {"executed": True, "simulation": False}
        self.client.click.side_effect = click
        result = whatsapp.whatsapp_select_chat_native(1)
        data = json.loads(result.split(": ", 1)[1])
        self.assertTrue(data["evidence"]["view_changed"])
        self.assertFalse(data["evidence"]["selected"])
        self.assertEqual(self.query.call_count, 2)
        self.assertEqual([call.kwargs["control_types"] for call in self.query.call_args_list],
                         [whatsapp._FAST_CHAT_TYPES, whatsapp._FAST_CHAT_TYPES])
        self.client.click.assert_called_once()

    def test_modern_webview_discovery_uses_targeted_query_before_full_tree(self):
        self.client.click.side_effect = lambda x, y, status, *, before_dispatch: (
            before_dispatch(),
            self.row.is_selected.return_value is not True and self.row.is_selected.return_value,
            self.row.is_selected.configure_mock(return_value=True),
            {"executed": True, "simulation": False},
        )[-1]
        with patch.object(self.ui, "_descendants", return_value=[self.row, self.composer]) as descendants, \
             patch.object(whatsapp, "_chat_candidates", side_effect=[[], [self.row]]) as candidates:
            result = whatsapp.whatsapp_select_chat_native(1)
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertEqual(descendants.call_count, 2)
        self.assertEqual(descendants.call_args_list[0].kwargs["control_types"], whatsapp._FAST_CHAT_TYPES)
        self.assertEqual(descendants.call_args_list[1].kwargs["control_types"], whatsapp._WEBVIEW_CHAT_TYPES)
        self.assertTrue(all(call.kwargs.get("control_types") for call in descendants.call_args_list))
        self.assertEqual(candidates.call_count, 2)

    def test_chat_row_moving_during_preflight_or_capture_never_receives_input(self):
        from core.desktop_input import InputDeliveryError
        for phase in ("preflight", "capture"):
            with self.subTest(phase=phase):
                self.client.reset_mock()
                def move():
                    self.row._rect.left += 10
                def preflight():
                    if phase == "preflight":
                        move()
                    return self.client, self.status
                def click(x, y, status, *, before_dispatch):
                    move()
                    before_dispatch()
                    self.dispatches.append((x, y))
                self.client.click.side_effect = click
                with patch("core.rust_engine._preflight", side_effect=preflight):
                    with self.assertRaisesRegex(InputDeliveryError, "target changed"):
                        whatsapp.whatsapp_select_chat_native(1)
                self.assertEqual(self.dispatches, [])
                if phase == "preflight":
                    self.client.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
