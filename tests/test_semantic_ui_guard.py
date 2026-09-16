from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from core.semantic_ui_guard import _browser_block, guard_semantic_ui_tools
from core.semantic_ui_tools import register_semantic_ui_tools
from core.tools import ToolRegistry
from core import semantic_ui_tools as ui


class SemanticUiGuardTests(unittest.TestCase):
    @patch("core.semantic_ui_guard._foreground_is_chrome", return_value=False)
    def test_non_browser_desktop_ui_is_not_blocked(self, _foreground) -> None:
        self.assertIsNone(_browser_block())

    @patch("core.chrome_cdp.chrome_is_connected", return_value=True)
    @patch("core.browser_guard.browser_check_challenge", return_value=json.dumps({"challenge_detected": True, "url": "https://example.com", "title": "Verify", "evidence": ["captcha"]}))
    @patch("core.semantic_ui_guard._foreground_is_chrome", return_value=True)
    def test_chrome_challenge_blocks_semantic_mutation(self, _foreground, _challenge, _connected) -> None:
        blocked = _browser_block()
        self.assertIsNotNone(blocked)
        assert blocked is not None
        self.assertTrue(blocked.startswith("BROWSER_ACTION_BLOCKED:"))

    def test_guard_wraps_all_semantic_mutations(self) -> None:
        registry = ToolRegistry()
        register_semantic_ui_tools(registry)
        before = {name: registry._tools[name].handler for name in ("ui_activate", "ui_type", "ui_hotkey", "ui_batch")}
        guard_semantic_ui_tools(registry)
        for name, handler in before.items():
            self.assertIsNot(registry._tools[name].handler, handler)
        self.assertNotEqual(registry._tools["ui_inspect"].handler, registry._tools["ui_activate"].handler)

    @patch("core.semantic_ui_guard._foreground_is_chrome", side_effect=RuntimeError("identity unavailable"))
    def test_foreground_identity_failure_blocks_input(self, foreground):
        self.assertTrue(_browser_block().startswith("BROWSER_ACTION_BLOCKED:"))

    @patch("core.chrome_cdp.chrome_is_connected", return_value=True)
    @patch("core.browser_guard.browser_check_challenge", return_value=json.dumps({"challenge_detected": False}))
    @patch("core.semantic_ui_guard._foreground_is_chrome", return_value=True)
    def test_selected_cdp_tab_does_not_authorize_an_unbound_foreground_window(self, foreground, challenge, connected):
        self.assertIn("cannot be bound", _browser_block())

    def test_boundary_is_checked_after_focusing_actual_target(self):
        foreground = 123
        def focus(win):
            nonlocal foreground
            foreground = 456
            return foreground
        registry = ToolRegistry()
        ui.register_semantic_ui_tools(registry)
        guard_semantic_ui_tools(registry)
        with patch.object(ui, "_window", return_value=Mock(handle=456)), patch.object(ui, "_focus_window", side_effect=focus), \
             patch.object(ui, "_foreground_hwnd", side_effect=lambda: foreground), \
             patch("core.semantic_ui_guard._foreground_is_chrome", side_effect=lambda: foreground == 456), \
             patch("core.chrome_cdp.chrome_is_connected", return_value=False), patch.object(ui, "_find_control") as find:
            result = registry.execute("ui_type", {"text": "hello", "target": "Message", "title": "Chrome"}, approved=True)
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        find.assert_not_called()

    def test_batch_preserves_browser_boundary_and_stops_later_actions(self):
        actions = [{"op": "type", "target": "Message", "text": "first"},
                   {"op": "type", "target": "Message", "text": "second", "title": "Chrome"},
                   {"op": "assert_visible", "target": "Ready"}]
        with patch.object(ui, "ui_type", side_effect=["VERIFIED: first",
             ui.BrowserBoundaryError("BROWSER_ACTION_BLOCKED: Browser target changed")]) as type_text, \
             patch.object(ui, "ui_wait_state") as verify:
            result = ui.ui_batch(actions)
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        self.assertIn('"completed_count": 1', result)
        self.assertEqual(type_text.call_count, 2)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
