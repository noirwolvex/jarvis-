from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.fast_mission import compile_fast_mission


class FastMissionCompilerTests(unittest.TestCase):
    def test_google_first_result_then_apps_compiles_in_exact_order(self) -> None:
        goal = (
            "open new tab in google and search for a cat and press the first link "
            "and then open discord app and then open whatsapp app"
        )
        steps = compile_fast_mission(goal)
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.tool for step in steps],
            [
                "browser_google_search_first_result",
                "launch_installed_app",
                "launch_installed_app",
            ],
        )
        self.assertEqual(
            steps[0].arguments,
            {"query": "a cat", "new_tab": True, "preserve_search_tab": False},
        )
        self.assertEqual(steps[1].arguments["query"].casefold(), "discord")
        self.assertEqual(steps[2].arguments["query"].casefold(), "whatsapp")

    def test_app_only_chain_compiles_without_model_round_trip(self) -> None:
        steps = compile_fast_mission("open discord app and then open whatsapp app")
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual([step.arguments["query"].casefold() for step in steps], ["discord", "whatsapp"])

    def test_unknown_clause_falls_back_instead_of_being_skipped(self) -> None:
        goal = (
            "open new tab in google and search for a cat and click the first result "
            "and then open discord app and send a message"
        )
        self.assertIsNone(compile_fast_mission(goal))

    def test_fast_execution_can_be_disabled_explicitly(self) -> None:
        with patch.dict(os.environ, {"JARVIS_FAST_EXECUTION": "false"}, clear=False):
            self.assertIsNone(compile_fast_mission("open discord app"))

    def test_action_clauses_are_not_swallowed_into_search_query_or_app_name(self) -> None:
        for goal in ("search for cats then send hello", "search for cats and play a song",
                     "open Discord → navigate to channel", "open Discord and join voice",
                     "search for cats then save the image and click the first result"):
            self.assertIsNone(compile_fast_mission(goal), goal)


if __name__ == "__main__":
    unittest.main()
