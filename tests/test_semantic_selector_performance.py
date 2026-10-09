"""Bound cross-process selector work without weakening live target resolution."""
import unittest
import json
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from test_semantic_ui_tools import _Control, _Editor, _Window


class SemanticSelectorPerformanceTests(unittest.TestCase):
    def setUp(self):
        ui._SNAPSHOTS.invalidate()

    def test_inspection_filters_provider_before_reading_irrelevant_metadata(self):
        win, first, second, noise = self._buttons_with_noise(900)
        with patch.object(ui, "_window", return_value=win), \
             patch.object(ui, "_foreground_hwnd", return_value=win.handle), \
             patch.object(ui, "_meta", wraps=ui._meta) as metadata:
            result = json.loads(ui.ui_inspect(control_type="bUtToN", query="Second", force_refresh=True))
        self.assertEqual([row["name"] for row in result["controls"]], ["Second"])
        self.assertNotIn(first, [call.args[0] for call in metadata.call_args_list])
        self.assertFalse(any(item in [call.args[0] for call in metadata.call_args_list] for item in noise))
        self.assertEqual(win.reads, 1)

    def test_inspection_type_filters_have_separate_caches(self):
        editor, button = _Editor(), _Control("Save", "Button")
        win = _Window([editor, button])
        with patch.object(ui, "_window", return_value=win), patch.object(ui, "_foreground_hwnd", return_value=win.handle):
            editors = json.loads(ui.ui_inspect(control_type="Edit"))
            buttons = json.loads(ui.ui_inspect(control_type="Button"))
        self.assertEqual([row["type"] for row in editors["controls"]], ["Edit"])
        self.assertEqual([row["type"] for row in buttons["controls"]], ["Button"])
        self.assertFalse(buttons["cached"])

    def test_filtered_inspection_reports_provider_truncation_when_target_was_omitted(self):
        win = _Window([_Control(f"Noise {index}", "Button") for index in range(700)]
                      + [_Control("Target", "Button")])
        with patch.object(ui, "_window", return_value=win), \
             patch.object(ui, "_foreground_hwnd", return_value=win.handle), \
             patch.object(ui, "_meta", wraps=ui._meta) as metadata:
            result = json.loads(ui.ui_inspect(query="Target", control_type="Button", force_refresh=True))
            cached = json.loads(ui.ui_inspect(query="Target", control_type="Button"))
        self.assertEqual(result["controls"], [])
        self.assertTrue(result["truncated"])
        self.assertTrue(result["scan_truncated"])
        self.assertTrue(cached["truncated"])
        self.assertTrue(cached["scan_truncated"])
        self.assertTrue(cached["cached"])
        # Only the window needs full reporting metadata, not 700 nonmatching controls.
        self.assertEqual(metadata.call_count, 1)
        self.assertEqual(win.reads, 1)

    def test_complete_scan_at_cap_does_not_claim_omitted_provider_controls(self):
        win = _Window([_Control(f"Noise {index}", "Button") for index in range(699)]
                      + [_Control("Target", "Button")])
        with patch.object(ui, "_window", return_value=win), \
             patch.object(ui, "_foreground_hwnd", return_value=win.handle):
            result = json.loads(ui.ui_inspect(query="Target", control_type="Button", force_refresh=True))
        self.assertEqual([row["name"] for row in result["controls"]], ["Target"])
        self.assertFalse(result["truncated"])
        self.assertFalse(result["scan_truncated"])

    def test_provider_truncation_keeps_complete_resolution_fail_closed(self):
        win = _Window([_Control(f"Button {index}", "Button") for index in range(701)])
        with self.assertRaisesRegex(RuntimeError, "exceeds 700"):
            ui._descendants(win, require_complete=True, control_types=("Button",))

    @staticmethod
    def _buttons_with_noise(count=600):
        first = _Control("First", "Button", top=0, bottom=20)
        second = _Control("Second", "Button", top=30, bottom=50)
        noise = [_Control(f"Message {index}", "Text") for index in range(count)]
        win = _Window([second, *noise, first])
        for control in [first, second, *noise]:
            control.parent = Mock(return_value=win)
        return win, first, second, noise

    def test_ordinal_reads_only_candidate_metadata_and_direct_parent(self):
        win, first, second, noise = self._buttons_with_noise()
        with patch.object(ui, "_meta", wraps=ui._meta) as metadata:
            self.assertIs(ui._find_control(win, control_type="bUtToN", selector={"ordinal": 2}), second)
        # Before the filtered provider query, all 603 nodes required metadata.
        self.assertEqual(metadata.call_count, 3)
        first.parent.assert_called_once()
        second.parent.assert_called_once()
        for control in noise:
            control.parent.assert_not_called()
        self.assertEqual(win.reads, 1)

    def test_irrelevant_large_tree_does_not_truncate_complete_button_query(self):
        win, first, _, _ = self._buttons_with_noise(900)
        self.assertIs(ui._find_control(win, control_type="Button", selector={"ordinal": 1}), first)

    def test_ordinal_still_rejects_different_containers(self):
        win, _, second, _ = self._buttons_with_noise(0)
        second.parent.return_value = _Control("Other panel", "Pane")
        with self.assertRaisesRegex(RuntimeError, "containers"):
            ui._find_control(win, control_type="Button", selector={"ordinal": 1})

    def test_focused_editor_query_reads_no_unneeded_ancestors(self):
        editor = _Editor()
        document = _Control("Read-only document", "Document")
        document.iface_value = Mock(CurrentIsReadOnly=True)
        unrelated = _Control("Toolbar", "Button")
        win = _Window([unrelated, editor, document])
        for control in [editor, document, unrelated]:
            control.parent = Mock(return_value=win)
        with patch.object(ui, "_meta", wraps=ui._meta) as metadata:
            self.assertIs(ui._find_control(win, editable=True, selector={"focused": True}), editor)
        self.assertEqual(metadata.call_count, 3)
        for control in [editor, document, unrelated]:
            control.parent.assert_not_called()

    def test_provider_replacement_is_resolved_again_for_each_selector(self):
        win, first, second, _ = self._buttons_with_noise(0)
        self.assertIs(ui._find_control(win, control_type="Button", selector={"ordinal": 1}), first)
        replacement = _Control("Replacement", "Button", top=0, bottom=20)
        replacement.parent = Mock(return_value=win)
        win._controls = [second, replacement]
        self.assertIs(ui._find_control(win, control_type="Button", selector={"ordinal": 1}), replacement)
        self.assertEqual(win.reads, 2)

    def test_parent_selector_keeps_other_types_and_full_ancestry(self):
        panel = _Control("Messages", "Pane")
        container = _Control("Inner", "Group")
        button = _Control("Reply", "Button")
        win = _Window([panel, container, button])
        panel.parent = Mock(return_value=win)
        container.parent = Mock(return_value=panel)
        button.parent = Mock(return_value=container)
        with patch.object(ui, "_descendants", wraps=ui._descendants) as descendants:
            result = ui._find_control(win, control_type="Button", selector={"parent": {"target": "Messages"}})
        self.assertIs(result, button)
        descendants.assert_called_once_with(win, require_complete=True, control_types=(), visible_only=False)

    def test_adjacent_selector_keeps_different_type_anchor(self):
        search = _Control("Search", "Edit", top=0, bottom=20)
        button = _Control("Go", "Button", top=0, bottom=20)
        search._rect.right = 50
        button._rect.left, button._rect.right = 60, 90
        win = _Window([search, button])
        search.parent = Mock(return_value=win)
        button.parent = Mock(return_value=win)
        result = ui._find_control(win, control_type="Button", selector={
            "adjacent": {"target": "Search", "control_type": "Edit", "direction": "right"},
        })
        self.assertIs(result, button)


if __name__ == "__main__":
    unittest.main()
