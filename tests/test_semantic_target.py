import json
import unittest
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.desktop_input import InputDeliveryError
from core.semantic_target import select
from core.tools import ToolRegistry
from test_semantic_ui_tools import _Control, _Window, _Editor


def row(name, y, *, kind="Button", parent="panel", selected=False):
    return {"identity": name, "name": name, "automation_id": "", "type": kind,
            "visible": True, "enabled": True, "selected": selected,
            "rect": [0, y, 50, y + 20], "parent_identity": parent, "ancestors": [parent]}


class SemanticTargetTests(unittest.TestCase):
    def test_ordinals_use_visible_geometry_and_exclude_hidden_or_disabled(self):
        rows = [row("third", 60), row("first", 0), row("second", 30),
                {**row("hidden", 10), "visible": False}, {**row("disabled", 20), "enabled": False}]
        self.assertEqual(select(rows, "", "Button", {"ordinal": 2})["name"], "second")
        self.assertEqual(select(rows, "", "Button", {"ordinal": "last"})["name"], "third")
        with self.assertRaisesRegex(RuntimeError, "exceeds"):
            select(rows, "", "Button", {"ordinal": 4})

    def test_ordinals_reject_uncertain_containers_and_overlapping_positions(self):
        with self.assertRaisesRegex(RuntimeError, "containers"):
            select([row("one", 0), row("two", 30, parent="other")], "", "Button", {"ordinal": 1})
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            select([row("one", 0), row("two", 0)], "", "Button", {"ordinal": 1})
        with self.assertRaisesRegex(ValueError, "control_type"):
            select([row("one", 0)], "", "", {"ordinal": 1})

    def test_parent_and_selected_filters_are_exact(self):
        rows = [row("panel", 0, kind="List", parent="window"), row("one", 30, selected=True),
                row("two", 60), row("other", 90, parent="sidebar", selected=True)]
        self.assertEqual(select(rows, "", "Button", {"parent": {"target": "panel"}, "selected": True})["name"], "one")
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            select(rows, "", "Button", {"parent": {"target": "pan"}})

    def test_adjacency_is_nearest_in_same_container_and_rejects_ties(self):
        search = row("Search", 0, kind="Edit")
        near = {**row("Near", 0), "rect": [60, 0, 80, 20]}
        far = {**row("Far", 0), "rect": [90, 0, 110, 20]}
        foreign = {**row("Foreign", 0, parent="other"), "rect": [51, 0, 58, 20]}
        selector = {"adjacent": {"target": "Search", "direction": "right"}}
        self.assertEqual(select([search, far, near, foreign], "", "Button", selector)["name"], "Near")
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            select([search, near, {**near, "identity": "copy", "name": "Copy"}], "", "Button", selector)

    def test_live_selector_and_registry_support_without_focus_for_read(self):
        first, second = _Control("First", "Button", top=0, bottom=20), _Control("Second", "Button", top=30, bottom=50)
        win = _Window([second, first])
        for control in [first, second]:
            control.parent = lambda: win
        registry = ToolRegistry()
        registry.permissions.check = Mock(return_value=(True, "fixture"))
        ui.register_semantic_ui_tools(registry)
        with patch.object(ui, "_window", return_value=win), patch.object(ui, "_foreground_hwnd", return_value=123), patch.object(ui, "_focus_window") as focus:
            result = registry.execute("ui_resolve", {"control_type": "Button", "selector": {"ordinal": 2}})
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual(json.loads(result.split(": ", 1)[1])["control"]["name"], "Second")
        self.assertEqual(result.execution["backend"], "windows_uia")
        focus.assert_not_called()

    def test_truncated_selector_tree_is_not_usable(self):
        with self.assertRaisesRegex(RuntimeError, "exceeds 700"):
            ui._find_control(_Window([_Control("Button", "Button") for _ in range(701)]),
                             control_type="Button", selector={"ordinal": 1})

    def test_target_change_prevents_activation(self):
        control = _Control("Send", "Button")
        win = _Window([control])
        binding = ui._target_binding(win, control)
        control._rect.left = 20
        with patch.object(ui, "_guard_foreground"), self.assertRaises(InputDeliveryError):
            ui._validate_target(win, control, binding)

    def test_selector_batch_forwards_targets_and_does_not_skip_verification(self):
        selector = {"ordinal": 2}
        with patch.object(ui, "ui_activate", return_value="DELIVERED: select") as activate, \
             patch.object(ui, "ui_wait_state", return_value="VERIFIED: selected") as verify:
            result = ui.ui_batch([
                {"op": "activate", "control_type": "ListItem", "selector": selector},
                {"op": "assert_visible", "control_type": "ListItem", "selector": {"selected": True}},
            ])
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual(activate.call_args.kwargs["selector"], selector)
        verify.assert_called_once()
        with self.assertRaises(ValueError):
            ui.ui_batch([{"op": "activate", "control_type": "ListItem", "selector": selector}])

    def test_explicit_python_mode_retains_semantic_keyboard_compatibility(self):
        with patch("core.rust_engine._preflight", return_value=(None, {})), \
             patch("core.rust_engine.native_engine_mode", return_value="python"), patch.object(ui, "paste_text") as paste:
            self.assertEqual(ui._rust_type_or_python("fixture"), "windows_unicode_input")
        paste.assert_called_once_with("fixture")

    def test_concurrent_draft_change_is_not_overwritten(self):
        editor = _Editor()
        with patch.object(ui, "_window", return_value=_Window([editor])), \
             patch.object(ui, "_focus_window", return_value=123), patch.object(ui, "_guard_foreground"), \
             patch.object(ui, "_control_value", side_effect=["", "new human draft"]):
            with self.assertRaisesRegex(InputDeliveryError, "Editor changed"):
                ui.ui_type("fixture")
        editor.iface_value.SetValue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
