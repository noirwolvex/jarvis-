from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.full_access_completion import (
    is_read_only_observation_goal,
    read_only_observation_verified,
)


class FullAccessCompletionTests(unittest.TestCase):
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
