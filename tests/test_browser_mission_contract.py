from __future__ import annotations

import unittest

from core.browser_mission_contract import (
    google_search_result_count,
    minimum_tab_count,
    required_google_searches,
    required_new_tabs,
)


class BrowserMissionContractTests(unittest.TestCase):
    def test_multi_search_new_tab_request_is_counted(self) -> None:
        mission = "open Google and search for cat and then open new tab search for a car"
        self.assertEqual(required_new_tabs(mission), 1)
        self.assertEqual(required_google_searches(mission), 2)
        self.assertEqual(minimum_tab_count(1, 1), 2)

    def test_google_result_tabs_require_real_query_urls(self) -> None:
        rows = [
            {"url": "https://www.google.com/search?q=cat"},
            {"url": "https://www.google.com/search?q=car"},
            {"url": "about:blank"},
            {"url": "https://www.google.com/"},
        ]
        self.assertEqual(google_search_result_count(rows), 2)

    def test_same_tab_navigation_cannot_satisfy_explicit_new_tab(self) -> None:
        self.assertEqual(minimum_tab_count(1, required_new_tabs("then open another tab")), 2)


if __name__ == "__main__":
    unittest.main()
