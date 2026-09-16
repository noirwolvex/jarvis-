from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from core.tools import ToolRegistry
from core.youtube_fast_tools import _youtube_watch_target, register_youtube_fast_tools, youtube_search_open, youtube_playback


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
        self.assertIn("youtube_playback", registry._tools)

    def _operations(self, *, final_id="chosen", challenge_at=None, missing_inspection=False):
        calls = []
        def operation(name, **args):
            calls.append((name,args))
            if name == "challenge_state":
                count = sum(1 for n, _ in calls if n == "challenge_state")
                return {} if missing_inspection else {"inspection_available": True, "challenge_detected": count == challenge_at}
            if name == "youtube_results":
                return [{"text": "Requested song", "href": "https://www.youtube.com/watch?v=chosen"}]
            if name == "goto":
                return {"url": args["url"] if "/results?" in args["url"] else "https://www.youtube.com/watch?v=" + final_id, "title": "Requested song"}
            if name == "youtube_playback":
                return {"verified": True, "selected_video_id": "chosen", "paused": args["action"] == "pause", "current_time": 2}
            raise AssertionError(name)
        return calls, operation

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    @patch("core.youtube_fast_tools.chrome_new_tab")
    @patch("core.youtube_fast_tools.chrome_current_tab", return_value=json.dumps({"url": "https://www.youtube.com/results?search_query=song"}))
    def test_search_play_reuses_tab_and_verifies_playback_in_one_call(self, current, new_tab, connected):
        calls, operation = self._operations()
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            result = youtube_search_open("song", play=True)
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertTrue(payload["playback"]["verified"])
        self.assertFalse(payload["new_tab"])
        new_tab.assert_not_called()
        self.assertEqual(sum(n == "goto" for n, _ in calls), 2)
        self.assertEqual(sum(n == "youtube_playback" for n, _ in calls), 1)

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    @patch("core.youtube_fast_tools.chrome_new_tab", return_value="VERIFIED: {}")
    @patch("core.youtube_fast_tools.chrome_current_tab", return_value=json.dumps({"url": "https://www.youtube.com/results?search_query=song"}))
    def test_explicit_new_tab_remains_supported(self, current, new_tab, connected):
        calls, operation = self._operations()
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            result = youtube_search_open("song", new_tab=True)
        self.assertTrue(result.startswith("VERIFIED:"))
        new_tab.assert_called_once()
        self.assertFalse(any(n == "youtube_playback" for n, _ in calls))

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    def test_unavailable_preflight_inspection_fails_closed_without_navigation(self, connected):
        calls, operation = self._operations(missing_inspection=True)
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            result = youtube_search_open("song", play=True)
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        self.assertEqual([n for n, _ in calls], ["challenge_state"])

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    @patch("core.youtube_fast_tools.chrome_current_tab", return_value=json.dumps({"url": "https://www.youtube.com/results?search_query=song"}))
    def test_post_navigation_challenge_stops_before_play(self, current, connected):
        calls, operation = self._operations(challenge_at=4)
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            result = youtube_search_open("song", play=True)
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        self.assertEqual(sum(n == "goto" for n, _ in calls), 2)
        self.assertFalse(any(n == "youtube_playback" for n, _ in calls))

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    @patch("core.youtube_fast_tools.chrome_current_tab", return_value=json.dumps({"url": "https://www.youtube.com/results?search_query=song"}))
    def test_wrong_selected_video_is_never_reported_verified(self, current, connected):
        calls, operation = self._operations(final_id="different")
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            with self.assertRaisesRegex(RuntimeError, "different video"):
                youtube_search_open("song", play=True)
        self.assertFalse(any(n == "youtube_playback" for n, _ in calls))

    @patch("core.youtube_fast_tools.chrome_is_connected", return_value=True)
    @patch("core.youtube_fast_tools.chrome_current_tab", return_value=json.dumps({"url": "https://www.youtube.com/watch?v=chosen"}))
    def test_playback_rejects_unexpected_current_video_before_acting(self, current, connected):
        calls, operation = self._operations()
        with patch("core.youtube_fast_tools.chrome_page_operation", side_effect=operation):
            with self.assertRaisesRegex(RuntimeError, "expected_video_id"):
                youtube_playback("play", expected_video_id="different")
        self.assertFalse(any(n == "youtube_playback" for n, _ in calls))


if __name__ == "__main__":
    unittest.main()
