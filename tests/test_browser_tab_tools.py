from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.browser_tab_tools import _normalize_tab_url, chrome_new_tab, register_browser_tab_tools
from core.tools import ToolRegistry


class BrowserTabToolsTests(unittest.TestCase):
    def test_normalize_tab_url_accepts_http_and_blank(self) -> None:
        self.assertEqual(_normalize_tab_url("about:blank"), "about:blank")
        self.assertEqual(_normalize_tab_url("https://www.google.com/"), "https://www.google.com/")
        with self.assertRaises(ValueError):
            _normalize_tab_url("file:///C:/Windows/System32")

    def test_tool_is_registered_as_medium_risk(self) -> None:
        registry = ToolRegistry()
        register_browser_tab_tools(registry)
        self.assertIn("chrome_new_tab", registry._tools)
        self.assertEqual(registry._tools["chrome_new_tab"].risk.name, "MEDIUM")

    @patch("core.browser_tab_tools.chrome_current_tab")
    @patch("core.browser_tab_tools.chrome_use_tab")
    @patch("core.browser_tab_tools._devtools_new_target")
    @patch("core.browser_tab_tools.chrome_tabs")
    @patch("core.browser_tab_tools.chrome_is_connected")
    def test_new_google_tab_is_selected_for_followup_browser_actions(
        self,
        is_connected,
        tabs,
        new_target,
        use_tab,
        current_tab,
    ) -> None:
        is_connected.return_value = True
        tabs.side_effect = [
            json.dumps([{"index": 0, "title": "Existing", "url": "https://example.com/"}]),
            json.dumps([
                {"index": 0, "title": "Existing", "url": "https://example.com/"},
                {"index": 1, "title": "Google", "url": "https://www.google.com/"},
            ]),
        ]
        new_target.return_value = {"id": "target-google"}
        current_tab.return_value = json.dumps({
            "session_type": "managed",
            "title": "Google",
            "url": "https://www.google.com/",
        })

        result = chrome_new_tab("https://www.google.com/")

        use_tab.assert_called_once_with(1)
        self.assertTrue(result.startswith("VERIFIED:"))
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertEqual(payload["selected"], 1)
        self.assertEqual(payload["url"], "https://www.google.com/")
        self.assertTrue(payload["created"])
        self.assertFalse(payload["reused_managed_placeholder"])

    @patch("core.browser_tab_tools.chrome_page_operation")
    @patch("core.browser_tab_tools.chrome_current_tab")
    @patch("core.browser_tab_tools.chrome_use_tab")
    @patch("core.browser_tab_tools._devtools_new_target")
    @patch("core.browser_tab_tools.chrome_tabs")
    @patch("core.browser_tab_tools.chrome_is_connected")
    def test_managed_startup_blank_is_reused_instead_of_leaving_extra_blank_tab(
        self,
        is_connected,
        tabs,
        new_target,
        use_tab,
        current_tab,
        page_operation,
    ) -> None:
        is_connected.return_value = True
        tabs.return_value = json.dumps([{"index": 0, "title": "", "url": "about:blank"}])
        current_tab.side_effect = [
            json.dumps({"session_type": "managed", "title": "", "url": "about:blank"}),
            json.dumps({"session_type": "managed", "title": "Google", "url": "https://www.google.com/"}),
        ]
        page_operation.return_value = {"title": "Google", "url": "https://www.google.com/"}

        result = chrome_new_tab("https://www.google.com/")

        new_target.assert_not_called()
        use_tab.assert_called_once_with(0)
        page_operation.assert_called_once_with("goto", url="https://www.google.com/")
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertFalse(payload["created"])
        self.assertTrue(payload["reused_managed_placeholder"])
        self.assertEqual(payload["selected"], 0)
        self.assertEqual(payload["url"], "https://www.google.com/")


if __name__ == "__main__":
    unittest.main()
