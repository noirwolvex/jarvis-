from __future__ import annotations

import unittest

from core.tools import ToolRegistry
from core.youtube_fast_tools import _youtube_watch_target, register_youtube_fast_tools


class YouTubeFastToolsTests(unittest.TestCase):
    def test_first_watch_result_skips_navigation_and_external_links(self) -> None:
        links = [
            {"text": "Home", "href": "https://www.youtube.com/"},
            {"text": "Ad", "href": "https://example.com/ad"},
            {"text": "Song One", "href": "https://www.youtube.com/watch?v=abc123&list=xyz"},
            {"text": "Song Two", "href": "https://www.youtube.com/watch?v=def456"},
        ]
        target = _youtube_watch_target(links)
        self.assertEqual(target["video_id"], "abc123")
        self.assertEqual(target["text"], "Song One")

    def test_watch_result_requires_video_id(self) -> None:
        with self.assertRaises(RuntimeError):
            _youtube_watch_target([
                {"text": "Invalid", "href": "https://www.youtube.com/watch"},
                {"text": "Shorts", "href": "https://www.youtube.com/shorts/abc"},
            ])

    def test_tool_registers_as_medium_risk(self) -> None:
        registry = ToolRegistry()
        register_youtube_fast_tools(registry)
        self.assertIn("youtube_search_open", registry._tools)
        self.assertEqual(registry._tools["youtube_search_open"].risk.name, "MEDIUM")


if __name__ == "__main__":
    unittest.main()
