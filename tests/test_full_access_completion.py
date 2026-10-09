from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from core.full_access_completion import (
    is_read_only_observation_goal,
    read_only_observation_verified,
    native_browser_progress,
)


class FullAccessCompletionTests(unittest.TestCase):
    def native_trace(self, query="example", **changes):
        payload = {"session_type": "existing-window", "verified": True,
                   "window": {"hwnd": 11, "process_id": 22, "process_created": 33.0},
                   "initial_tab_count": 4, "query": query,
                   "url": "https://www.google.com/search?q=" + query}
        payload.update(changes)
        return SimpleNamespace(name="google_search", success=True, result="VERIFIED: " + json.dumps(payload))

    def test_native_search_completion_uses_bound_window_and_history(self):
        traces = [self.native_trace(), self.native_trace("second", initial_tab_count=5)]
        with patch("core.chrome_existing_window.read_existing_chrome", return_value={"tab_count": 6}) as read:
            result = native_browser_progress(SimpleNamespace(traces=traces))
        self.assertEqual(result, {"tab_count": 6, "google_search_count": 2, "initial_tab_count": 4})
        read.assert_called_once_with({"hwnd": 11, "process_id": 22, "process_created": 33.0})

    def test_native_search_then_first_result_counts_verified_search_history(self):
        trace = self.native_trace(url="https://example.com/", search_url="https://www.google.com/search?q=example")
        trace.name = "browser_google_search_first_result"
        with patch("core.chrome_existing_window.read_existing_chrome", return_value={"tab_count": 4}):
            result = native_browser_progress(SimpleNamespace(traces=[trace]))
        self.assertEqual(result["google_search_count"], 1)

    def test_native_unverified_and_failed_evidence_never_counts(self):
        failed = self.native_trace()
        failed.success = False
        forged = self.native_trace()
        forged.name = "task_verify"
        with patch("core.chrome_existing_window.read_existing_chrome") as read:
            self.assertIsNone(native_browser_progress(SimpleNamespace(traces=[
                failed, forged, self.native_trace(verified=False)])))
        read.assert_not_called()

    def test_native_completion_never_switches_windows_or_accepts_challenge(self):
        for case in ("changed_window", "closed_window", "challenge"):
            with self.subTest(case=case), patch("core.chrome_existing_window.read_existing_chrome") as read:
                traces = [self.native_trace()]
                if case == "changed_window":
                    traces.append(self.native_trace(window={"hwnd": 99}))
                elif case == "closed_window":
                    read.side_effect = RuntimeError("closed")
                else:
                    read.return_value = {"tab_count": 4, "challenge_detected": True}
                result = native_browser_progress(SimpleNamespace(traces=traces))
                self.assertEqual(result["google_search_count"], 0)
                self.assertIn("error", result)
                if case == "changed_window":
                    read.assert_not_called()

    def test_mismatched_query_and_duplicate_retry_do_not_inflate_completion(self):
        with patch("core.chrome_existing_window.read_existing_chrome", return_value={"tab_count": 5}):
            result = native_browser_progress(SimpleNamespace(traces=[self.native_trace(), self.native_trace(),
                self.native_trace("second", url="https://www.google.com/search?q=wrong")]))
        self.assertEqual(result["google_search_count"], 1)

    def test_screen_observe_description_without_clicking_is_read_only(self) -> None:
        goal = "use screen_observe to look at my current screen and describe the foreground app without clicking anything"
        self.assertTrue(is_read_only_observation_goal(goal))

    def test_mutating_followup_is_not_read_only(self) -> None:
        self.assertFalse(is_read_only_observation_goal("look at the screen and then click the OK button"))
        self.assertFalse(is_read_only_observation_goal("describe the window then type hello"))

    def test_verified_screen_observation_is_sufficient_read_only_evidence(self) -> None:
        current = SimpleNamespace(
            traces=[
                SimpleNamespace(
                    name="screen_observe",
                    success=True,
                    result='VERIFIED: {"path":"screen.jpg"}',
                )
            ]
        )
        goal = "use screen_observe to look at my current screen and describe the foreground app without clicking anything"
        self.assertTrue(read_only_observation_verified(goal, current))

    def test_mutating_trace_prevents_read_only_completion(self) -> None:
        current = SimpleNamespace(
            traces=[
                SimpleNamespace(name="screen_observe", success=True, result="VERIFIED: {}"),
                SimpleNamespace(name="desktop_click", success=True, result="Clicked"),
            ]
        )
        self.assertFalse(read_only_observation_verified("describe my current screen", current))

    def test_failed_observation_is_not_evidence(self) -> None:
        current = SimpleNamespace(
            traces=[
                SimpleNamespace(name="screen_observe", success=False, result="ERROR: capture failed"),
            ]
        )
        self.assertFalse(read_only_observation_verified("describe my current screen", current))


if __name__ == "__main__":
    unittest.main()
