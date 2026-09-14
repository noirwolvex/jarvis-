from __future__ import annotations

import unittest

from core.full_access_routing import browser_focused_goal, needs_full_toolset


class FullAccessSchemaRoutingTests(unittest.TestCase):
    def test_browser_missions_are_detected(self) -> None:
        self.assertTrue(browser_focused_goal("open Google and search for a cat"))
        self.assertTrue(browser_focused_goal("افتح كروم وابحث عن سيارة"))
        self.assertTrue(browser_focused_goal("open another tab"))

    def test_non_browser_desktop_mission_is_not_forced_into_browser_profile(self) -> None:
        self.assertFalse(browser_focused_goal("open Notepad and type hello"))

    def test_dev_or_filesystem_terms_keep_full_toolset(self) -> None:
        self.assertTrue(needs_full_toolset("open GitHub and inspect the repository code"))
        self.assertTrue(needs_full_toolset("open a website then save a file"))
        self.assertTrue(needs_full_toolset("افتح جوجل ثم عدل ملف كود"))

    def test_plain_browser_request_can_use_reduced_schema(self) -> None:
        mission = "open Google and search for a cat in a new tab"
        self.assertTrue(browser_focused_goal(mission))
        self.assertFalse(needs_full_toolset(mission))


if __name__ == "__main__":
    unittest.main()
