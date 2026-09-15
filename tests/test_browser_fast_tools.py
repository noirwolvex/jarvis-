from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.browser_fast_tools import (
    _external_result_target,
    _first_google_result,
    browser_google_search_first_result,
)


class BrowserFastToolsTests(unittest.TestCase):
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
    def test_combined_search_and_first_result_is_verified_in_one_tool_call(
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
        self.assertEqual(payload["query"], "cat")
        self.assertEqual(payload["result_url"], "https://example.com/cat")
        self.assertTrue(payload["preserved_search_tab"])
        google_search.assert_called_once_with("cat", new_tab=True)
        chrome_new_tab.assert_called_once_with("https://example.com/cat")


if __name__ == "__main__":
    unittest.main()
