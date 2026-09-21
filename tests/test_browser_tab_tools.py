from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_tool_uses_one_owned_runtime_command(self):
        with patch("core.browser_tab_tools.chrome_is_connected", return_value=True), \
             patch("core.browser_tab_tools._runtime") as runtime:
            runtime.return_value.call.return_value = {"verified": True, "url": "https://example.com/"}
            result = chrome_new_tab("https://example.com/")
        runtime.return_value.call.assert_called_once_with("new_tab", url="https://example.com/")
        self.assertTrue(result.startswith("VERIFIED:"))

    def runtime_fixture(self, managed=False):
        from core.chrome_cdp import _ChromeRuntime
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._active_cancel = None
        context = SimpleNamespace(pages=[])
        def page(url):
            value = SimpleNamespace(url=url, title=Mock(return_value=""), context=context,
                                    bring_to_front=Mock(), is_closed=Mock(return_value=False))
            value.goto = Mock(side_effect=lambda target, **kwargs: setattr(value, "url", target))
            context.pages.append(value)
            return value
        original = page("about:blank" if managed else "https://existing.example/")
        context.new_page = Mock(side_effect=lambda: page("about:blank"))
        runtime._browser = SimpleNamespace(contexts=[context])
        runtime._page = original
        runtime._session_type = "managed" if managed else "real"
        return runtime, context, page

    @patch("core.browser_semantic.require_clear_page")
    def test_new_tab_stays_bound_when_another_tab_appears_and_old_tab_closes(self, guard):
        runtime, context, page = self.runtime_fixture()
        created = []
        def create_with_concurrent_tabs():
            context.pages.clear()
            target = page("about:blank")
            created.append(target)
            page("https://unrelated.example/")
            return target
        context.new_page.side_effect = create_with_concurrent_tabs
        result = runtime._cmd_new_tab("https://www.google.com/")
        self.assertIs(runtime._page, created[0])
        created[0].goto.assert_called_once()
        context.pages[-1].goto.assert_not_called()
        self.assertEqual(result["selected"], 0)
        self.assertEqual(result["url"], "https://www.google.com/")
        self.assertTrue(result["created"])
        self.assertEqual(context.new_page.call_count, 1)

    @patch("core.browser_semantic.require_clear_page")
    def test_managed_startup_blank_is_reused(self, guard):
        runtime, context, _ = self.runtime_fixture(managed=True)
        result = runtime._cmd_new_tab("https://www.google.com/")
        context.new_page.assert_not_called()
        self.assertFalse(result["created"])
        self.assertTrue(result["reused_managed_placeholder"])
        self.assertEqual(result["url"], "https://www.google.com/")

    @patch("core.browser_semantic.require_clear_page")
    def test_failed_navigation_keeps_exact_created_target_without_retry(self, guard):
        runtime, context, page = self.runtime_fixture()
        target = page("about:blank")
        target.goto.side_effect = TimeoutError("uncertain navigation")
        context.new_page.side_effect = None
        context.new_page.return_value = target
        with self.assertRaisesRegex(TimeoutError, "uncertain"):
            runtime._cmd_new_tab("https://www.google.com/")
        self.assertIs(runtime._page, target)
        context.new_page.assert_called_once()
        target.goto.assert_called_once()

    @patch("core.browser_semantic.require_clear_page")
    def test_blank_result_never_counts_as_verified_web_navigation(self, guard):
        runtime, context, page = self.runtime_fixture()
        target = page("about:blank")
        target.goto.side_effect = None
        context.new_page.side_effect = None
        context.new_page.return_value = target
        with self.assertRaisesRegex(RuntimeError, "stayed on about:blank"):
            runtime._cmd_new_tab("https://www.google.com/")

    @patch("core.browser_semantic.require_clear_page")
    def test_cancellation_after_creation_prevents_navigation_and_recreation(self, guard):
        runtime, context, _ = self.runtime_fixture()
        runtime._check_cancelled = Mock(side_effect=[None, None, RuntimeError("stopped")])
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            runtime._cmd_new_tab("https://www.google.com/")
        context.new_page.assert_called_once()
        runtime._page.goto.assert_not_called()

    def test_closed_selected_tab_is_not_replaced_by_an_unrelated_tab(self):
        runtime, context, page = self.runtime_fixture()
        context.pages.clear()
        other = page("https://unrelated.example/")
        runtime._cmd_tabs()
        self.assertIsNone(runtime._page)
        with self.assertRaisesRegex(RuntimeError, "No browser page"):
            runtime._cmd_page("click", selector="#send")
        other.bring_to_front.assert_not_called()


if __name__ == "__main__":
    unittest.main()
