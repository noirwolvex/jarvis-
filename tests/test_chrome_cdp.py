import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from core.chrome_cdp import _ChromeRuntime, _cdp_url


class ChromeCdpTests(unittest.TestCase):
    def test_shutdown_uses_the_same_reply_protocol_and_joins_owner_thread(self):
        runtime = _ChromeRuntime()
        self.assertIsNone(runtime.call("shutdown"))
        runtime._thread.join(timeout=1)
        self.assertFalse(runtime._thread.is_alive())

    def test_shutdown_cleanup_error_is_returned_without_hanging(self):
        runtime = _ChromeRuntime()
        runtime._browser = Mock()
        runtime._browser.close.side_effect = RuntimeError("fixture shutdown failure")
        runtime._playwright = Mock()
        with self.assertRaisesRegex(RuntimeError, "fixture shutdown failure"):
            runtime.call("shutdown")
        runtime._thread.join(timeout=1)
        runtime._playwright.stop.assert_called_once()
        self.assertFalse(runtime._thread.is_alive())

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

    def observed_page(self, url, *, focused=False, visible=False):
        page = Mock()
        page.url = url
        page.title.return_value = url
        page.evaluate.return_value = {"focused": focused, "visible": visible}
        return page

    def connect_pages(self, pages):
        runtime = self.fixture()
        context = SimpleNamespace(pages=pages, new_page=Mock())
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.start.return_value.chromium.connect_over_cdp.return_value = SimpleNamespace(contexts=[context])
            result = runtime._cmd_connect("http://127.0.0.1:9222")
        context.new_page.assert_not_called()
        for page in pages:
            page.bring_to_front.assert_not_called()
            page.close.assert_not_called()
        return runtime, result

    def test_initial_connection_selects_focused_window_instead_of_last_page(self):
        current = self.observed_page("https://current.example/", focused=True, visible=True)
        last = self.observed_page("https://background-window.example/", visible=True)
        runtime, result = self.connect_pages([current, last])
        self.assertIs(runtime._page, current)
        self.assertEqual(result["active_url"], current.url)

    def test_initial_connection_leaves_ambiguous_windows_unselected(self):
        pages = [self.observed_page(f"https://window-{i}.example/", visible=True) for i in range(2)]
        runtime, result = self.connect_pages(pages)
        self.assertIsNone(runtime._page)
        self.assertEqual(result["active_url"], "")

    def test_initial_connection_selects_unique_visible_page_if_no_page_focused(self):
        visible = self.observed_page("https://visible.example/", visible=True)
        hidden = self.observed_page("https://hidden.example/")
        runtime, _ = self.connect_pages([visible, hidden])
        self.assertIs(runtime._page, visible)

    def test_current_window_selection_observes_fresh_pages_without_activation(self):
        runtime = self.fixture()
        current = self.observed_page("https://current.example/", focused=True, visible=True)
        old = self.observed_page("https://old.example/", visible=True)
        context = SimpleNamespace(pages=[current, old], new_page=Mock())
        runtime._browser = SimpleNamespace(contexts=[context])
        runtime._session_type = "real"
        runtime._page = old
        result = runtime._cmd_select_current_window()
        self.assertEqual(result["selected"], 0)
        self.assertIs(runtime._page, current)
        context.new_page.assert_not_called()
        for page in (current, old):
            page.bring_to_front.assert_not_called()
            page.close.assert_not_called()

    def test_current_window_selection_fails_closed_on_unavailable_or_ambiguous_focus(self):
        for case in ("two_focused", "two_visible", "unreadable", "no_visible"):
            with self.subTest(case=case):
                runtime = self.fixture()
                pages = [self.observed_page(f"https://window-{i}.example/",
                    focused=case == "two_focused", visible=case != "no_visible") for i in range(2)]
                if case == "unreadable":
                    pages[0].evaluate.return_value["focused"] = True
                    pages[1].evaluate.side_effect = RuntimeError("disconnected")
                runtime._browser = SimpleNamespace(contexts=[SimpleNamespace(pages=pages)])
                runtime._page = pages[-1]
                with self.assertRaisesRegex(RuntimeError, "ambiguous or changed"):
                    runtime._cmd_select_current_window()
                self.assertIsNone(runtime._page)

    def test_current_window_selection_rejects_changed_page_list(self):
        runtime = self.fixture()
        current = self.observed_page("https://current.example/", focused=True, visible=True)
        replacement = self.observed_page("https://replacement.example/", focused=True, visible=True)
        context = SimpleNamespace(pages=[current])
        runtime._browser = SimpleNamespace(contexts=[context])
        def observe(_script):
            context.pages = [replacement]
            return {"focused": True, "visible": True}
        current.evaluate.side_effect = observe
        with self.assertRaisesRegex(RuntimeError, "ambiguous or changed"):
            runtime._cmd_select_current_window()
        self.assertIsNone(runtime._page)

    def test_disconnected_reconnect_preserves_lost_selection(self):
        runtime = self.fixture()
        runtime._endpoint = "http://127.0.0.1:9222"
        runtime._browser = SimpleNamespace(is_connected=lambda: False)
        page = self.observed_page("https://unrelated.example/", focused=True, visible=True)
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.start.return_value.chromium.connect_over_cdp.return_value = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
            result = runtime._cmd_connect(runtime._endpoint)
        self.assertIsNone(runtime._page)
        self.assertEqual(result["active_url"], "")
        page.evaluate.assert_not_called()

    def test_clear_selection_keeps_connection_and_does_not_mutate_tabs(self):
        runtime = self.fixture()
        page = self.observed_page("https://previous.example/")
        runtime._page = page
        browser = runtime._browser = Mock()
        runtime._cmd_clear_selection()
        self.assertIsNone(runtime._page)
        self.assertIs(runtime._browser, browser)
        page.assert_not_called()
        self.assertEqual(page.method_calls, [])

    @patch("core.browser_semantic.require_clear_page")
    def test_open_chrome_verifies_focus_before_claiming_success(self, guard):
        runtime = self.fixture()
        page = self.observed_page("https://example.com/", focused=True, visible=True)
        runtime._page = page
        runtime._browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])])
        self.assertTrue(runtime._cmd_focus_selected()["verified"])
        page.bring_to_front.assert_called_once()
        page.evaluate.return_value = {"focused": False, "visible": True}
        with self.assertRaisesRegex(RuntimeError, "foreground"):
            runtime._cmd_focus_selected()

    def page_fixture(self):
        runtime = self.fixture()
        runtime._session_type = "real"
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://example.com/"
        page.title.return_value = "Example"
        page.locator.return_value.count.return_value = 1
        page.get_by_text.return_value.count.return_value = 1
        for locator in (page.locator.return_value, page.get_by_text.return_value):
            target = MagicMock()
            target.is_visible.return_value = target.is_enabled.return_value = True
            target.evaluate.return_value = True
            locator.element_handles.return_value = [target]
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

    def test_semantic_scroll_reaches_guarded_owner_thread_adapter(self):
        from core.execution_telemetry import begin_execution, finish_execution
        runtime, page = self.page_fixture()
        result = {"executed": True, "verified": True, "after": {"x": 0, "y": 400}}
        span, token = begin_execution()
        with patch("core.browser_semantic.run_browser_operation", return_value=result) as operation:
            self.assertEqual(runtime._cmd_page("semantic_scroll", delta_y=400, delta_x=0), result)
        report = finish_execution(span, token, "VERIFIED", dispatched=True)
        operation.assert_called_once_with(page, "semantic_scroll", {"delta_y": 400, "delta_x": 0}, runtime._check_cancelled)
        self.assertIn({"engine": "chrome_cdp", "phase": "execute", "detail": "semantic_scroll"}, report.execution["operations"])

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
                    page.get_by_text.return_value.element_handles.return_value[0].click.assert_not_called()
                    page.locator.return_value.element_handles.return_value[0].fill.assert_not_called()
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
                page.get_by_text.return_value.element_handles.return_value[0].click.assert_not_called()
                page.locator.return_value.element_handles.return_value[0].fill.assert_not_called()
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
                    locator = page.get_by_text.return_value
                    target = locator.element_handles.return_value[0]
                    target.click.assert_called_once_with(timeout=1500)
                    locator.click.assert_not_called()
                    target.dispose.assert_called_once()
                elif operation == "fill":
                    locator = page.locator.return_value
                    target = locator.element_handles.return_value[0]
                    target.fill.assert_called_once_with("draft", timeout=1500)
                    self.assertEqual(target.evaluate.call_args.args[1], "draft")
                    locator.fill.assert_not_called()
                    target.dispose.assert_called_once()
                else:
                    page.keyboard.press.assert_called_once_with("Enter")

    def test_legacy_mutation_pins_element_before_page_inspection(self):
        for action in ("click", "fill"):
            with self.subTest(action=action):
                runtime, page = self.page_fixture()
                locator = page.get_by_text.return_value if action == "click" else page.locator.return_value
                target = locator.element_handles.return_value[0]
                replacement = MagicMock()
                def rerender(_script):
                    locator.element_handles.return_value = [replacement]
                    target.is_visible.return_value = False
                    return {"inspection_available": True, "challenge_detected": False}
                page.evaluate.side_effect = rerender
                with self.assertRaisesRegex(RuntimeError, "no longer visible"):
                    runtime._cmd_page(action, selector="input", text="draft")
                target.click.assert_not_called()
                target.fill.assert_not_called()
                target.dispose.assert_called_once()
                replacement.click.assert_not_called()
                replacement.fill.assert_not_called()
                locator.click.assert_not_called()
                locator.fill.assert_not_called()

    def test_legacy_target_race_releases_all_handles_without_input(self):
        runtime, page = self.page_fixture()
        targets = [MagicMock(), MagicMock()]
        page.locator.return_value.element_handles.return_value = targets
        with self.assertRaisesRegex(RuntimeError, "changed or became ambiguous"):
            runtime._cmd_page("fill", selector="input", text="draft")
        for target in targets:
            target.dispose.assert_called_once()
            target.fill.assert_not_called()
        page.evaluate.assert_not_called()

    def test_legacy_fill_rejects_readback_failure_without_retry(self):
        for observed in (False, None):
            with self.subTest(observed=observed):
                runtime, page = self.page_fixture()
                page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
                target = page.locator.return_value.element_handles.return_value[0]
                target.evaluate.return_value = observed
                with self.assertRaisesRegex(RuntimeError, "readback did not match"):
                    runtime._cmd_page("fill", selector="input", text="draft")
                target.fill.assert_called_once_with("draft", timeout=1500)
                target.dispose.assert_called_once()
                self.assertIn("isConnected", target.evaluate.call_args.args[0])

    def test_legacy_input_timeout_never_retries_and_releases_target(self):
        runtime, page = self.page_fixture()
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        target = page.get_by_text.return_value.element_handles.return_value[0]
        target.click.side_effect = TimeoutError("delivery uncertain")
        with self.assertRaisesRegex(TimeoutError, "delivery uncertain"):
            runtime._cmd_page("click", selector="Send")
        target.click.assert_called_once_with(timeout=1500)
        target.dispose.assert_called_once()

    def test_legacy_fill_stop_during_input_does_not_report_success(self):
        import threading
        runtime, page = self.page_fixture()
        runtime._active_cancel = threading.Event()
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        target = page.locator.return_value.element_handles.return_value[0]
        target.fill.side_effect = lambda *_args, **_kwargs: runtime._active_cancel.set()
        with self.assertRaisesRegex(RuntimeError, "abandoned"):
            runtime._cmd_page("fill", selector="input", text="draft")
        target.evaluate.assert_not_called()
        target.dispose.assert_called_once()

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
