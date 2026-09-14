from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from core.chrome_session_tools import (
    ensure_chrome_connection,
    ensure_managed_chrome,
    register_chrome_session_tools,
)
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

    @patch("core.chrome_session_tools._runtime")
    @patch("core.chrome_session_tools._wait_for_page_target")
    @patch("core.chrome_session_tools.ensure_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_fresh_connection_waits_for_real_startup_page_before_playwright_attach(
        self,
        probe,
        ensure_managed,
        wait_for_page,
        runtime_factory,
    ) -> None:
        probe.return_value = None
        ensure_managed.return_value = json.dumps({
            "started": True,
            "pid": 4321,
            "profile": "profile",
            "log": "startup.log",
        })
        wait_for_page.return_value = [{"id": "startup-page", "type": "page", "url": "about:blank"}]
        runtime = Mock()
        runtime.call.return_value = {
            "connected": True,
            "endpoint": "http://127.0.0.1:9222",
            "session_type": "managed",
            "pages": [{"index": 0, "title": "", "url": "about:blank"}],
            "active_url": "about:blank",
            "active_title": "",
        }
        runtime_factory.return_value = runtime

        result = json.loads(ensure_chrome_connection())

        wait_for_page.assert_called_once_with(timeout=5.0)
        runtime.call.assert_called_once_with(
            "connect",
            endpoint="http://127.0.0.1:9222",
            session_type="managed",
        )
        self.assertTrue(result["startup_guard"])
        self.assertTrue(result["page_target_ready"])
        self.assertEqual(result["startup_target_count"], 1)

    @patch("core.chrome_session_tools._runtime")
    @patch("core.chrome_session_tools._wait_for_page_target")
    @patch("core.chrome_session_tools.ensure_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_fresh_connection_refuses_duplicate_fallback_when_startup_page_never_appears(
        self,
        probe,
        ensure_managed,
        wait_for_page,
        runtime_factory,
    ) -> None:
        probe.return_value = None
        ensure_managed.return_value = json.dumps({"started": True, "pid": 4321})
        wait_for_page.return_value = []
        runtime = Mock()
        runtime_factory.return_value = runtime

        with self.assertRaisesRegex(RuntimeError, "refusing to create a second fallback"):
            ensure_chrome_connection()

        runtime.call.assert_not_called()

    def test_registration_replaces_original_start_and_connect_tools(self) -> None:
        registry = ToolRegistry()
        register_chrome_session_tools(registry)
        self.assertEqual(registry._tools["chrome_start_managed"].handler, ensure_managed_chrome)
        self.assertEqual(registry._tools["chrome_connect_cdp"].handler, ensure_chrome_connection)
        self.assertIn("idempotent", registry._tools["chrome_start_managed"].description)
        self.assertIn("duplicate about:blank", registry._tools["chrome_connect_cdp"].description)


if __name__ == "__main__":
    unittest.main()
