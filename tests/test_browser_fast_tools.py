from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.browser_fast_tools import (
    _external_result_target,
    _first_google_result,
    browser_google_search_first_result,
    google_search,
)
from core.browser_semantic import BrowserChallengeBlocked


class BrowserFastToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.existing = self.enterContext(patch("core.browser_fast_tools.preferred_existing_chrome", return_value=None))

    def test_google_search_reuses_personal_window_even_when_cdp_is_connected(self) -> None:
        window = {"hwnd": 12, "process_id": 34, "process_created": 56.0}
        self.existing.return_value = window
        with patch("core.chrome_existing_window.navigate_existing_chrome", return_value={
            "verified": True, "session_type": "existing-window", "window": window,
            "url": "https://www.google.com/search?q=cat", "title": "cat - Google Search",
        }) as navigate, patch("core.browser_fast_tools.chrome_is_connected", return_value=True), \
             patch("core.browser_fast_tools.ensure_chrome_connection") as ensure, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op, \
             patch("core.browser_fast_tools.chrome_current_tab") as current:
            result = google_search("cat")
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertEqual(payload["session_type"], "existing-window")
        navigate.assert_called_once_with("https://www.google.com/search?q=cat", new_tab=False, window=window)
        ensure.assert_not_called()
        page_op.assert_not_called()
        current.assert_not_called()

    def test_native_search_forwards_explicit_new_tab_and_unicode_query(self) -> None:
        self.existing.return_value = {"hwnd": 12}
        with patch("core.chrome_existing_window.navigate_existing_chrome") as navigate:
            navigate.return_value = {"verified": True, "url": "https://www.google.com/search?q=%D9%82%D8%B7%D8%B7+%26+caf%C3%A9"}
            result = google_search("قطط & café", new_tab=True)
        self.assertTrue(result.startswith("VERIFIED:"))
        navigate.assert_called_once_with("https://www.google.com/search?q=%D9%82%D8%B7%D8%B7+%26+caf%C3%A9", new_tab=True, window={"hwnd": 12})

    def test_native_search_unverified_or_wrong_query_cannot_count_as_complete(self) -> None:
        self.existing.return_value = {"hwnd": 12}
        for result in (
            {"verified": False, "url": "https://www.google.com/search?q=cat"},
            {"verified": True, "url": "https://www.google.com/search?q=dog"},
            {"verified": True, "url": "https://www.google.com.attacker.test/search?q=cat"},
        ):
            with self.subTest(result=result), \
                 patch("core.chrome_existing_window.navigate_existing_chrome", return_value=result), \
                 patch("core.browser_fast_tools.ensure_chrome_connection") as ensure, \
                 patch("core.browser_fast_tools.chrome_page_operation") as page_op:
                with self.assertRaisesRegex(RuntimeError, "verified Google results"):
                    google_search("cat")
                ensure.assert_not_called()
                page_op.assert_not_called()

    def test_native_search_failure_never_falls_back_to_another_browser(self) -> None:
        self.existing.return_value = {"hwnd": 12}
        with patch("core.chrome_existing_window.navigate_existing_chrome", side_effect=RuntimeError("lost foreground")), \
             patch("core.browser_fast_tools.ensure_chrome_connection") as ensure, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            with self.assertRaisesRegex(RuntimeError, "lost foreground"):
                google_search("cat")
        ensure.assert_not_called()
        page_op.assert_not_called()

    def test_inspection_failure_does_not_launch_managed_chrome(self) -> None:
        self.existing.side_effect = RuntimeError("Chrome toolbar ambiguous")
        with patch("core.browser_fast_tools.ensure_chrome_connection") as ensure, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            with self.assertRaisesRegex(RuntimeError, "toolbar ambiguous"):
                google_search("cat")
        ensure.assert_not_called()
        page_op.assert_not_called()

    def test_no_existing_chrome_uses_connection_policy_and_current_tab(self) -> None:
        with patch("core.browser_fast_tools.chrome_is_connected", return_value=False), \
             patch("core.browser_fast_tools.ensure_chrome_connection", return_value=json.dumps({"connected": True, "session_type": "managed"})) as ensure, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op, \
             patch("core.browser_fast_tools.chrome_new_tab") as new_tab, \
             patch("core.browser_fast_tools.chrome_current_tab", return_value=json.dumps({
                 "url": "https://www.google.com/search?q=cat", "title": "cat - Google Search",
             })), patch("core.browser_fast_tools.browser_check_challenge", return_value=json.dumps({"challenge_detected": False})):
            result = google_search("cat")
        self.assertTrue(result.startswith("VERIFIED:"))
        ensure.assert_called_once_with()
        page_op.assert_called_once_with("goto", url="https://www.google.com/search?q=cat")
        new_tab.assert_not_called()

    def test_window_appearing_during_connection_is_used_for_search(self) -> None:
        window = {"hwnd": 12}
        with patch("core.browser_fast_tools.chrome_is_connected", return_value=False), \
             patch("core.browser_fast_tools.ensure_chrome_connection", return_value=json.dumps({
                 "session_type": "existing-window", "window": window,
             })) as ensure, \
             patch("core.chrome_existing_window.navigate_existing_chrome", return_value={
                 "verified": True, "url": "https://www.google.com/search?q=cat",
             }) as navigate, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            result = google_search("cat")
        self.assertTrue(result.startswith("VERIFIED:"))
        ensure.assert_called_once_with()
        navigate.assert_called_once_with("https://www.google.com/search?q=cat", new_tab=False, window=window)
        page_op.assert_not_called()

    def test_native_challenge_stops_compound_search_without_reading_cdp_or_links(self) -> None:
        self.existing.return_value = {"hwnd": 12}
        with patch("core.chrome_existing_window.navigate_existing_chrome", side_effect=BrowserChallengeBlocked(
            "BROWSER_ACTION_BLOCKED: complete human verification manually",
        )) as navigate, patch("core.chrome_existing_window.read_existing_chrome") as read, \
             patch("core.browser_fast_tools.chrome_current_tab") as current, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            result = browser_google_search_first_result("cat")
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        navigate.assert_called_once()
        read.assert_not_called()
        current.assert_not_called()
        page_op.assert_not_called()

    def test_native_compound_search_stays_bound_to_exact_window(self) -> None:
        window = {"hwnd": 12, "process_id": 34, "process_created": 56.0}
        searched = {"verified": True, "session_type": "existing-window", "window": window,
                    "url": "https://www.google.com/search?q=cat", "title": "cat - Google Search"}
        for preserve in (False, True):
            with self.subTest(preserve=preserve), \
                 patch("core.browser_fast_tools.google_search", return_value="VERIFIED: " + json.dumps(searched)) as search, \
                 patch("core.chrome_existing_window.read_existing_chrome", return_value={
                     **searched, "challenge_detected": False, "links": [{"text": "Cat", "href": "https://example.com/cat"}],
                 }) as read, \
                 patch("core.chrome_existing_window.navigate_existing_chrome", return_value={
                     "verified": True, "window": window, "session_type": "existing-window",
                     "url": "https://example.com/cat", "title": "Cat article",
                 }) as navigate, \
                 patch("core.browser_fast_tools.chrome_current_tab") as current, \
                 patch("core.browser_fast_tools.chrome_page_operation") as page_op, \
                 patch("core.browser_fast_tools.chrome_new_tab") as new_tab:
                result = browser_google_search_first_result("cat", preserve_search_tab=preserve)
                payload = json.loads(result.removeprefix("VERIFIED: "))
                self.assertEqual(payload["window"], window)
                self.assertEqual(payload["result_url"], "https://example.com/cat")
                self.assertEqual(payload["preserved_search_tab"], preserve)
                search.assert_called_once_with("cat", new_tab=False)
                read.assert_called_once_with(window, include_links=True)
                navigate.assert_called_once_with("https://example.com/cat", new_tab=preserve, window=window)
                current.assert_not_called()
                page_op.assert_not_called()
                new_tab.assert_not_called()

    def test_native_compound_search_does_not_follow_links_after_results_change(self) -> None:
        searched = {"session_type": "existing-window", "window": {"hwnd": 12}}
        with patch("core.browser_fast_tools.google_search", return_value="VERIFIED: " + json.dumps(searched)), \
             patch("core.chrome_existing_window.read_existing_chrome", return_value={
                 "url": "https://www.google.com/search?q=dog", "title": "dog", "challenge_detected": False,
                 "links": [{"text": "Cat", "href": "https://example.com/cat"}],
             }), patch("core.chrome_existing_window.navigate_existing_chrome") as navigate, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            with self.assertRaisesRegex(RuntimeError, "left the verified results"):
                browser_google_search_first_result("cat")
        navigate.assert_not_called()
        page_op.assert_not_called()

    def test_native_compound_search_stops_at_challenge_before_result_navigation(self) -> None:
        searched = {"session_type": "existing-window", "window": {"hwnd": 12}}
        with patch("core.browser_fast_tools.google_search", return_value="VERIFIED: " + json.dumps(searched)), \
             patch("core.chrome_existing_window.read_existing_chrome", return_value={
                 "url": "https://www.google.com/search?q=cat", "title": "cat", "challenge_detected": True,
                 "evidence": ["captcha"], "links": [{"text": "Cat", "href": "https://example.com/cat"}],
             }), patch("core.chrome_existing_window.navigate_existing_chrome") as navigate, \
             patch("core.browser_fast_tools.chrome_page_operation") as page_op:
            result = browser_google_search_first_result("cat")
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        navigate.assert_not_called()
        page_op.assert_not_called()

    def test_google_redirect_unwraps_real_external_result(self) -> None:
        href = "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fcats&sa=U"
        self.assertEqual(_external_result_target(href), "https://example.com/cats")

    def test_first_result_skips_google_navigation_and_ad_tracking_hosts(self) -> None:
        links = [
            {"text": "Images", "href": "https://www.google.com/search?tbm=isch&q=cat"},
            {"text": "Sponsored", "href": "https://www.googleadservices.com/pagead/aclk?x=1"},
            {"text": "Cat article", "href": "https://example.com/cat"},
            {"text": "Second result", "href": "https://example.org/cat"},
        ]
        self.assertEqual(
            _first_google_result(links),
            {"text": "Cat article", "href": "https://example.com/cat"},
        )

    @patch("core.browser_fast_tools.browser_check_challenge", return_value=json.dumps({"challenge_detected": False}))
    @patch("core.browser_fast_tools.chrome_new_tab")
    @patch("core.browser_fast_tools.chrome_page_operation")
    @patch("core.browser_fast_tools.chrome_current_tab")
    @patch("core.browser_fast_tools.google_search")
    def test_combined_search_can_preserve_results_tab(
        self,
        google_search,
        chrome_current_tab,
        chrome_page_operation,
        chrome_new_tab,
        _browser_check_challenge,
    ) -> None:
        google_search.return_value = "VERIFIED: {}"
        chrome_current_tab.side_effect = [
            json.dumps({"url": "https://www.google.com/search?q=cat", "title": "cat - Google Search"}),
            json.dumps({"url": "https://example.com/cat", "title": "Cat article"}),
        ]
        chrome_page_operation.return_value = [
            {"text": "Images", "href": "https://www.google.com/search?tbm=isch&q=cat"},
            {"text": "Cat article", "href": "https://example.com/cat"},
        ]
        chrome_new_tab.return_value = "VERIFIED: {}"

        result = browser_google_search_first_result("cat", new_tab=True)
        self.assertTrue(result.startswith("VERIFIED: "))
        payload = json.loads(result[len("VERIFIED: "):])
        self.assertEqual(payload["action"], "browser_google_search_first_result")
        self.assertEqual(payload["result_url"], "https://example.com/cat")
        self.assertTrue(payload["preserved_search_tab"])
        google_search.assert_called_once_with("cat", new_tab=True)
        chrome_new_tab.assert_called_once_with("https://example.com/cat")

    @patch("core.browser_fast_tools.browser_check_challenge", return_value=json.dumps({"challenge_detected": False}))
    @patch("core.browser_fast_tools.chrome_new_tab")
    @patch("core.browser_fast_tools.chrome_page_operation")
    @patch("core.browser_fast_tools.chrome_current_tab")
    @patch("core.browser_fast_tools.google_search")
    def test_fast_lane_navigates_first_result_in_same_tab(
        self,
        google_search,
        chrome_current_tab,
        chrome_page_operation,
        chrome_new_tab,
        _browser_check_challenge,
    ) -> None:
        google_search.return_value = "VERIFIED: {}"
        chrome_current_tab.side_effect = [
            json.dumps({"url": "https://www.google.com/search?q=cat", "title": "cat - Google Search"}),
            json.dumps({"url": "https://example.com/cat", "title": "Cat article"}),
        ]
        chrome_page_operation.side_effect = [
            [
                {"text": "Images", "href": "https://www.google.com/search?tbm=isch&q=cat"},
                {"text": "Cat article", "href": "https://example.com/cat"},
            ],
            {"title": "Cat article", "url": "https://example.com/cat"},
        ]

        result = browser_google_search_first_result(
            "cat",
            new_tab=True,
            preserve_search_tab=False,
        )
        self.assertTrue(result.startswith("VERIFIED: "))
        payload = json.loads(result[len("VERIFIED: "):])
        self.assertFalse(payload["preserved_search_tab"])
        self.assertEqual(payload["result_url"], "https://example.com/cat")
        chrome_new_tab.assert_not_called()
        self.assertEqual(
            chrome_page_operation.call_args_list[-1].kwargs,
            {"url": "https://example.com/cat"},
        )


if __name__ == "__main__":
    unittest.main()
