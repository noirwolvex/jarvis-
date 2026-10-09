from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.app_tools import register_app_tools
from core.browser_semantic import BrowserChallengeBlocked
from core.full_access_browser_routing import _is_chrome_query, register_full_access_browser_routing
from core.tools import ToolRegistry, ToolSpec


class FullAccessBrowserRoutingTests(unittest.TestCase):
    def test_google_aliases_follow_chrome_window_reuse(self):
        for query in ("Google", "Google Chrome", "جوجل", "غوغل", "كروم"):
            with self.subTest(query=query):
                self.assertTrue(_is_chrome_query(query))
        self.assertFalse(_is_chrome_query("Google Drive"))

    def setUp(self) -> None:
        self.existing = self.enterContext(patch("core.full_access_browser_routing.existing_chrome_connection", return_value=None))
        self.focus = self.enterContext(patch("core.chrome_existing_window.focus_existing_chrome", return_value={"focused": True, "verified": True}))
        self.runtime = self.enterContext(patch("core.full_access_browser_routing._runtime")).return_value
        self.runtime.call.return_value = {"focused": True, "verified": True}

    def _registry(self) -> tuple[ToolRegistry, dict[str, int]]:
        registry = ToolRegistry()
        register_app_tools(registry)
        calls = {"normal_launch": 0}
        spec = registry._tools["launch_installed_app"]

        def normal_launch(query: str, timeout_seconds: float = 15.0) -> str:
            calls["normal_launch"] += 1
            return f"normal:{query}:{timeout_seconds}"

        registry._tools["launch_installed_app"] = ToolSpec(
            name=spec.name,
            description=spec.description,
            risk=spec.risk,
            input_schema=spec.input_schema,
            handler=normal_launch,
        )
        register_full_access_browser_routing(registry)
        return registry, calls

    @patch("core.full_access_browser_routing.chrome_page_operation")
    @patch("core.full_access_browser_routing.ensure_chrome_connection")
    @patch("core.full_access_browser_routing.chrome_is_connected")
    def test_browser_navigation_uses_cdp_when_no_existing_window(self, connected, ensure, page_op) -> None:
        connected.return_value = False
        ensure.return_value = json.dumps({"startup_guard": True})
        page_op.return_value = {"title": "Google", "url": "https://www.google.com/"}
        registry, _ = self._registry()

        result = registry._tools["browser_navigate"].handler(url="https://www.google.com/")

        ensure.assert_called_once_with()
        page_op.assert_called_once_with("goto", url="https://www.google.com/")
        self.assertIn("Chrome CDP", result)

    @patch("core.full_access_browser_routing.ensure_chrome_connection")
    @patch("core.full_access_browser_routing.chrome_is_connected")
    def test_chrome_app_launch_is_redirected_to_cdp(self, connected, ensure) -> None:
        connected.return_value = False
        ensure.return_value = json.dumps({"startup_guard": True})
        registry, calls = self._registry()

        result = registry._tools["launch_installed_app"].handler(query="Google Chrome")

        ensure.assert_called_once_with()
        self.assertEqual(calls["normal_launch"], 0)
        self.assertIn("Chrome session ready", result)
        self.runtime.call.assert_called_once_with("focus_selected")

    def test_chrome_launch_reuses_open_window_without_launching_any_process(self) -> None:
        self.existing.return_value = {"connected": True, "reused": True, "started": False,
                                      "session_type": "existing-window", "window": {"hwnd": 12}}
        registry, calls = self._registry()
        with patch("core.full_access_browser_routing.ensure_chrome_connection") as ensure:
            result = registry._tools["launch_installed_app"].handler(query="Google Chrome")
        self.assertIn("Chrome session ready", result)
        self.assertIn('"started": false', result)
        self.assertEqual(calls["normal_launch"], 0)
        ensure.assert_not_called()
        self.focus.assert_called_once_with({"hwnd": 12})

    def test_navigation_prefers_open_window_even_with_stale_connected_cdp(self) -> None:
        window = {"hwnd": 12, "process_id": 34, "process_created": 56.0}
        self.existing.return_value = {"session_type": "existing-window", "window": window}
        registry, _ = self._registry()
        with patch("core.chrome_existing_window.navigate_existing_chrome", return_value={
            "verified": True, "url": "https://www.google.com/", "window": window,
        }) as navigate, patch("core.full_access_browser_routing.chrome_is_connected", return_value=True), \
             patch("core.full_access_browser_routing.chrome_page_operation") as page_op, \
             patch("core.full_access_browser_routing.ensure_chrome_connection") as ensure:
            result = registry._tools["browser_navigate"].handler(url="https://www.google.com/")
        self.assertTrue(result.startswith("VERIFIED:"))
        navigate.assert_called_once_with("https://www.google.com/", window=window)
        page_op.assert_not_called()
        ensure.assert_not_called()

    def test_native_navigation_requires_verified_result(self) -> None:
        self.existing.return_value = {"session_type": "existing-window", "window": {"hwnd": 12}}
        registry, _ = self._registry()
        with patch("core.chrome_existing_window.navigate_existing_chrome", return_value={"verified": False}), \
             patch("core.full_access_browser_routing.chrome_page_operation") as page_op, \
             patch("core.full_access_browser_routing.ensure_chrome_connection") as ensure:
            with self.assertRaisesRegex(RuntimeError, "verified"):
                registry._tools["browser_navigate"].handler(url="https://www.google.com/")
        page_op.assert_not_called()
        ensure.assert_not_called()

    def test_navigation_inspection_error_does_not_launch_another_chrome(self) -> None:
        self.existing.side_effect = RuntimeError("ambiguous Chrome window")
        registry, _ = self._registry()
        with patch("core.full_access_browser_routing.ensure_chrome_connection") as ensure, \
             patch("core.full_access_browser_routing.chrome_page_operation") as page_op:
            with self.assertRaisesRegex(RuntimeError, "ambiguous Chrome"):
                registry._tools["browser_navigate"].handler(url="https://www.google.com/")
        page_op.assert_not_called()
        ensure.assert_not_called()

    def test_window_appearing_during_connection_receives_navigation(self) -> None:
        window = {"hwnd": 12}
        registry, _ = self._registry()
        with patch("core.full_access_browser_routing.chrome_is_connected", return_value=False), \
             patch("core.full_access_browser_routing.ensure_chrome_connection", return_value=json.dumps({
                 "session_type": "existing-window", "window": window,
             })), patch("core.chrome_existing_window.navigate_existing_chrome", return_value={
                 "verified": True, "url": "https://www.google.com/",
             }) as navigate, patch("core.full_access_browser_routing.chrome_page_operation") as page_op:
            result = registry._tools["browser_navigate"].handler(url="https://www.google.com/")
        self.assertTrue(result.startswith("VERIFIED:"))
        navigate.assert_called_once_with("https://www.google.com/", window=window)
        page_op.assert_not_called()

    def test_native_navigation_challenge_never_falls_back_to_cdp(self) -> None:
        self.existing.return_value = {"session_type": "existing-window", "window": {"hwnd": 12}}
        registry, _ = self._registry()
        with patch("core.chrome_existing_window.navigate_existing_chrome", side_effect=BrowserChallengeBlocked(
            "BROWSER_ACTION_BLOCKED: complete human verification manually",
        )), patch("core.full_access_browser_routing.chrome_page_operation") as page_op:
            result = registry._tools["browser_navigate"].handler(url="https://www.google.com/")
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        page_op.assert_not_called()

    def test_non_browser_app_keeps_normal_launcher(self) -> None:
        registry, calls = self._registry()

        result = registry._tools["launch_installed_app"].handler(query="Notepad", timeout_seconds=4)

        self.assertEqual(calls["normal_launch"], 1)
        self.assertEqual(result, "normal:Notepad:4")

    def test_browser_launch_never_claims_unverified_focus(self) -> None:
        self.existing.return_value = {"session_type": "existing-window", "window": {"hwnd": 12}}
        self.focus.return_value = {"focused": False, "verified": False}
        registry, _ = self._registry()
        with self.assertRaisesRegex(RuntimeError, "foreground"):
            registry._tools["launch_installed_app"].handler(query="Chrome")


if __name__ == "__main__":
    unittest.main()
