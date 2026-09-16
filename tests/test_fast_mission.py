from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.fast_mission import compile_fast_mission


class FastMissionCompilerTests(unittest.TestCase):
    def test_simple_arabic_commands_compile_in_order_without_model(self):
        steps = compile_fast_mission("افتح ديسكورد ثم افتح الحاسبة ثم افتح المفكرة")
        self.assertEqual([step.arguments["query"] for step in steps], ["Discord", "Calculator", "Notepad"])

    def test_arabic_media_and_search_use_verified_semantic_operations(self):
        steps = compile_fast_mission('شغل "اسم الأغنية" على يوتيوب')
        self.assertEqual(steps[0].tool, "youtube_search_open")
        self.assertEqual(steps[0].arguments, {"query": "اسم الأغنية", "new_tab": False, "play": True})
        self.assertEqual(compile_fast_mission('ابحث في جوجل عن "تعلم بايثون"')[0].arguments["query"], "تعلم بايثون")
        self.assertEqual(compile_fast_mission("اوقف الفيديو مؤقتا")[0].arguments["action"], "pause")

    def test_unknown_arabic_step_falls_back_whole_mission_without_skipping(self):
        for goal in ("افتح ديسكورد ثم ارسل رسالة", "افتح ديسكورد وارسل رسالة", 'شغل "أغنية" على يوتيوب ثم احفظها',
                     "افتح تطبيق غير معروف", "لا تفتح ديسكورد"):
            self.assertIsNone(compile_fast_mission(goal), goal)
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
