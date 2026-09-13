from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.chrome_session_tools import ensure_managed_chrome, register_chrome_session_tools
from core.tools import ToolRegistry


class ChromeSessionToolsTests(unittest.TestCase):
    @patch("core.chrome_session_tools._start_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_existing_cdp_is_reused_without_opening_another_window(self, probe, start_managed) -> None:
        probe.return_value = {"Browser": "Chrome/140"}

        result = json.loads(ensure_managed_chrome())

        start_managed.assert_not_called()
        self.assertFalse(result["started"])
        self.assertTrue(result["reused"])

    @patch("core.chrome_session_tools._start_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_managed_chrome_starts_once_when_cdp_is_missing(self, probe, start_managed) -> None:
        probe.side_effect = [None, None]
        start_managed.return_value = json.dumps({
            "started": True,
            "pid": 1234,
            "endpoint": "http://127.0.0.1:9222",
            "session_type": "managed",
        })

        result = json.loads(ensure_managed_chrome())

        start_managed.assert_called_once_with()
        self.assertTrue(result["started"])
        self.assertFalse(result["reused"])
        self.assertTrue(result["single_instance_guard"])

    def test_registration_replaces_original_start_tool(self) -> None:
        registry = ToolRegistry()
        original = registry._tools.get("chrome_start_managed")
        register_chrome_session_tools(registry)
        replacement = registry._tools["chrome_start_managed"]
        self.assertIsNot(replacement, original)
        self.assertEqual(replacement.handler, ensure_managed_chrome)
        self.assertIn("idempotent", replacement.description)


if __name__ == "__main__":
    unittest.main()
