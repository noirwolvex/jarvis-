from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.semantic_ui_guard import _browser_block, guard_semantic_ui_tools
from core.semantic_ui_tools import register_semantic_ui_tools
from core.tools import ToolRegistry


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


if __name__ == "__main__":
    unittest.main()
