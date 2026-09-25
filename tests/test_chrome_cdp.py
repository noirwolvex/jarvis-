import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from core.chrome_cdp import _ChromeRuntime, _cdp_url


class ChromeCdpTests(unittest.TestCase):
    def fixture(self):
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._browser = runtime._playwright = runtime._page = None
        runtime._pages = []
        runtime._endpoint = ""
        runtime._active_cancel = None
        return runtime

    def test_failed_connect_stops_driver_without_leaking_or_creating_tabs(self):
        runtime = self.fixture()
        with patch("playwright.sync_api.sync_playwright") as factory:
            driver = factory.return_value.start.return_value
            driver.chromium.connect_over_cdp.side_effect = RuntimeError("fixture disconnect")
            with self.assertRaisesRegex(RuntimeError, "disconnect"):
                runtime._cmd_connect("http://127.0.0.1:9222")
            driver.stop.assert_called_once()
        self.assertIsNone(runtime._browser)
        self.assertIsNone(runtime._playwright)

    def test_empty_connection_does_not_create_unsolicited_blank_page(self):
        runtime = self.fixture()
        context = SimpleNamespace(pages=[], new_page=Mock())
        browser = SimpleNamespace(contexts=[context])
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.start.return_value.chromium.connect_over_cdp.return_value = browser
            result = runtime._cmd_connect("http://127.0.0.1:9222")
        context.new_page.assert_not_called()
        self.assertEqual(result["pages"], [])
        self.assertEqual(result["active_url"], "")

    def test_reconnect_to_same_browser_preserves_lost_selection(self):
        runtime = self.fixture()
        page = SimpleNamespace(url="https://unrelated.example/", title=lambda: "Unrelated")
        runtime._browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])], is_connected=lambda: True)
        runtime._endpoint = "http://127.0.0.1:9222"
        with patch("playwright.sync_api.sync_playwright") as factory:
            result = runtime._cmd_connect(runtime._endpoint)
        factory.assert_not_called()
        self.assertIsNone(runtime._page)
        self.assertTrue(runtime._cmd_connected())
        self.assertEqual(result["active_url"], "")

    def page_fixture(self):
        runtime = self.fixture()
        runtime._session_type = "real"
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/"
        page.title.return_value = "Example"
        page.locator.return_value.count.return_value = 1
        page.get_by_text.return_value.count.return_value = 1
        runtime._page = page
        return runtime, page

    def test_page_current_operation_supports_browser_read_adapter(self):
        runtime, page = self.page_fixture()
        self.assertEqual(runtime._cmd_page("current"), {
            "session_type": "real", "title": "Example", "url": "https://example.com/",
        })
        page.evaluate.assert_not_called()

    def test_snapshot_metadata_is_owned_by_exact_page_and_bounds_tab_reads(self):
        runtime, page = self.page_fixture()
        other_pages = [MagicMock(url=f"https://example.com/{index}") for index in range(50)]
        runtime._browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page, *other_pages])])
        with patch("core.browser_semantic.run_browser_operation", return_value={"title": "Snapshot title"}):
            result = runtime._cmd_page("semantic_snapshot")
        self.assertEqual(result["tab"], {
            "session_type": "real", "url": page.url, "title": "Snapshot title",
        })
        self.assertEqual(len(result["tabs"]), 40)
        for omitted in other_pages[39:]:
            omitted.title.assert_not_called()

    def test_legacy_input_fails_closed_on_challenge_or_unavailable_inspection(self):
        from core.browser_semantic import BrowserChallengeBlocked
        operations = [("click", {"selector": "Send"}),
                      ("fill", {"selector": "input", "text": "draft"}),
                      ("press", {"key": "Enter"})]
        for operation, args in operations:
            for unavailable in (False, True):
                with self.subTest(operation=operation, unavailable=unavailable):
                    runtime, page = self.page_fixture()
                    if unavailable:
                        page.evaluate.side_effect = RuntimeError("inspection disconnected")
                    else:
                        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": True}
                    with self.assertRaises(BrowserChallengeBlocked):
                        runtime._cmd_page(operation, **args)
                    page.get_by_text.return_value.click.assert_not_called()
                    page.locator.return_value.fill.assert_not_called()
                    page.keyboard.press.assert_not_called()

    def test_stop_during_challenge_read_prevents_legacy_input(self):
        import threading
        operations = [("click", {"selector": "Send"}),
                      ("fill", {"selector": "input", "text": "draft"}),
                      ("press", {"key": "Enter"})]
        for operation, args in operations:
            with self.subTest(operation=operation):
                runtime, page = self.page_fixture()
                runtime._active_cancel = threading.Event()
                def inspect(_script):
                    runtime._active_cancel.set()
                    return {"inspection_available": True, "challenge_detected": False}
                page.evaluate.side_effect = inspect
                with self.assertRaisesRegex(RuntimeError, "abandoned"):
                    runtime._cmd_page(operation, **args)
                page.get_by_text.return_value.click.assert_not_called()
                page.locator.return_value.fill.assert_not_called()
                page.keyboard.press.assert_not_called()

    def test_clear_page_legacy_input_dispatches_once_after_fresh_inspection(self):
        operations = [("click", {"selector": "Send"}),
                      ("fill", {"selector": "input", "text": "draft"}),
                      ("press", {"key": "Enter"})]
        for operation, args in operations:
            with self.subTest(operation=operation):
                runtime, page = self.page_fixture()
                page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
                self.assertTrue(runtime._cmd_page(operation, **args))
                page.evaluate.assert_called_once()
                if operation == "click":
                    page.get_by_text.return_value.click.assert_called_once_with(timeout=1500)
                elif operation == "fill":
                    page.locator.return_value.fill.assert_called_once_with("draft", timeout=1500)
                else:
                    page.keyboard.press.assert_called_once_with("Enter")

    def tab_selection_fixture(self):
        runtime, original = self.page_fixture()
        target = MagicMock()
        target.url = "https://target.example/"
        target.title.return_value = "Target"
        runtime._browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[original, target])])
        return runtime, original, target

    def test_cancelled_tab_selection_preserves_prior_target_before_dispatch(self):
        runtime, original, target = self.tab_selection_fixture()
        runtime._check_cancelled = Mock(side_effect=RuntimeError("stopped"))
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            runtime._cmd_use_tab(1)
        self.assertIs(runtime._page, original)
        target.bring_to_front.assert_not_called()

    def test_uncertain_tab_selection_clears_target_without_retry(self):
        runtime, _, target = self.tab_selection_fixture()
        target.bring_to_front.side_effect = RuntimeError("focus uncertain")
        with self.assertRaisesRegex(RuntimeError, "focus uncertain"):
            runtime._cmd_use_tab(1)
        self.assertIsNone(runtime._page)
        target.bring_to_front.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "No browser page"):
            runtime._cmd_page("press", key="Enter")
        target.keyboard.press.assert_not_called()

    def test_cancellation_during_tab_selection_clears_target(self):
        runtime, _, target = self.tab_selection_fixture()
        runtime._check_cancelled = Mock(side_effect=[None, RuntimeError("stopped")])
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            runtime._cmd_use_tab(1)
        self.assertIsNone(runtime._page)
        target.bring_to_front.assert_called_once()

    def test_successful_tab_selection_commits_target_after_delivery(self):
        runtime, original, target = self.tab_selection_fixture()
        target.bring_to_front.side_effect = lambda: self.assertIs(runtime._page, original)
        self.assertEqual(runtime._cmd_use_tab(1), {
            "selected": 1, "session_type": "real", "title": "Target", "url": "https://target.example/",
        })
        self.assertIs(runtime._page, target)

    def test_default_cdp_url(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_CHROME_CDP_URL", None)
            self.assertEqual(_cdp_url(), "http://127.0.0.1:9222")

    def test_custom_cdp_url_is_normalized(self) -> None:
        with patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": "http://127.0.0.1:9333/"}, clear=False):
            self.assertEqual(_cdp_url(), "http://127.0.0.1:9333")


if __name__ == "__main__":
    unittest.main()
