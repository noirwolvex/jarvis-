from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.app_tools import register_app_tools
from core.full_access_browser_routing import register_full_access_browser_routing
from core.tools import ToolRegistry, ToolSpec


class FullAccessBrowserRoutingTests(unittest.TestCase):
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
    def test_browser_navigation_forces_managed_cdp(self, connected, ensure, page_op) -> None:
        connected.return_value = False
        ensure.return_value = json.dumps({"startup_guard": True})
        page_op.return_value = {"title": "Google", "url": "https://www.google.com/"}
        registry, _ = self._registry()

        result = registry._tools["browser_navigate"].handler(url="https://www.google.com/")

        ensure.assert_called_once_with()
        page_op.assert_called_once_with("goto", url="https://www.google.com/")
        self.assertIn("managed Chrome CDP", result)

    @patch("core.full_access_browser_routing.ensure_chrome_connection")
    @patch("core.full_access_browser_routing.chrome_is_connected")
    def test_chrome_app_launch_is_redirected_to_cdp(self, connected, ensure) -> None:
        connected.return_value = False
        ensure.return_value = json.dumps({"startup_guard": True})
        registry, calls = self._registry()

        result = registry._tools["launch_installed_app"].handler(query="Google Chrome")

        ensure.assert_called_once_with()
        self.assertEqual(calls["normal_launch"], 0)
        self.assertIn("routed through managed CDP", result)

    def test_non_browser_app_keeps_normal_launcher(self) -> None:
        registry, calls = self._registry()

        result = registry._tools["launch_installed_app"].handler(query="Notepad", timeout_seconds=4)

        self.assertEqual(calls["normal_launch"], 1)
        self.assertEqual(result, "normal:Notepad:4")


if __name__ == "__main__":
    unittest.main()
