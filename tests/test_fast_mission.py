from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.fast_mission import compile_fast_mission, execute_fast_mission


class FastMissionCompilerTests(unittest.TestCase):
    def test_reported_google_search_suffix_is_not_part_of_query(self):
        for command in ("SEARCH FOR A CAT IN GOOGLE", "search for A CAT on Google",
                        "search for A CAT using Google Chrome"):
            with self.subTest(command=command):
                steps = compile_fast_mission(command)
                self.assertEqual(len(steps), 1)
                self.assertEqual((steps[0].tool, steps[0].arguments),
                                 ("google_search", {"query": "A CAT", "new_tab": False}))

    def test_new_tab_search_defaults_to_google_and_preserves_tab_request(self):
        for command in ("OPEN NEW TAB AND SEARCH FOR A CAT",
                        "open a new browser tab and search for A CAT",
                        "create another tab and search for A CAT",
                        "open new tab in Chrome and search for A CAT",
                        "please open new tab and search for A CAT in Google"):
            with self.subTest(command=command):
                steps = compile_fast_mission(command)
                self.assertEqual(len(steps), 1)
                self.assertEqual((steps[0].tool, steps[0].arguments),
                                 ("google_search", {"query": "A CAT", "new_tab": True}))

    def test_reported_whatsapp_discord_google_chain_preserves_all_steps(self):
        steps = compile_fast_mission(
            "OPEN WHATSAPP AND PRESS THE FIRST CHAT AND WRITE H "
            "AFTER OPEN DISCORD AFTER SEARCH FOR A CAT IN GOOGLE"
        )
        self.assertEqual([step.id for step in steps], [f"fast-{index}" for index in range(1, 6)])
        self.assertEqual([step.tool for step in steps],
                         ["launch_installed_app", "whatsapp_select_chat_native", "interaction_type",
                          "launch_installed_app", "google_search"])
        self.assertEqual(steps[1].arguments, {"position": 1})
        self.assertEqual(steps[2].arguments, {"text": "H", "title": "WhatsApp", "submit": False})
        self.assertEqual(steps[3].arguments["query"], "DISCORD")
        self.assertEqual(steps[4].arguments, {"query": "A CAT", "new_tab": False})
        steps = compile_fast_mission("open Discord then open new tab and search for a cat")
        self.assertEqual(steps[-1].arguments, {"query": "a cat", "new_tab": True})

    def test_quoted_google_terms_preserve_literal_engine_and_tab_words(self):
        for query in ("a cat in google", "new tab", "  A CAT  ", "cats then open a book"):
            for prefix in ("search for", "open Discord then search for"):
                with self.subTest(query=query, prefix=prefix):
                    step = compile_fast_mission(f'{prefix} "{query}" in Google')[-1]
                    self.assertEqual(step.arguments, {"query": query, "new_tab": False})
        self.assertEqual(compile_fast_mission('search for "a cat in google"')[0].arguments,
                         {"query": "a cat in google", "new_tab": False})
        self.assertEqual(compile_fast_mission("open Discord then search for new tab")[-1].arguments,
                         {"query": "new tab", "new_tab": False})

    def test_google_first_result_applies_engine_suffix_and_explicit_tab(self):
        steps = compile_fast_mission(
            "open new tab and search for a cat in Google and click the first result then open Discord"
        )
        self.assertEqual([step.tool for step in steps],
                         ["browser_google_search_first_result", "launch_installed_app"])
        self.assertEqual(steps[0].arguments,
                         {"query": "a cat", "new_tab": True, "preserve_search_tab": False})

    def test_incomplete_and_unknown_search_or_app_work_never_compiles_partially(self):
        for command in ("OPEN FILE EXPLORE AND", "OPEN FILE EXPLORE THEN",
                        "open new tab and", "search for cats then", "search for cats and",
                        "search for cats in Google then", "open Discord then search for cats and",
                        "search for cats in Google and purchase a toy",
                        "open new tab and search for cats and delete the file",
                        'search for "cats" and purchase "a toy"',
                        "open Discord then search for cats and run a command"):
            with self.subTest(command=command):
                self.assertIsNone(compile_fast_mission(command))

    def test_polite_app_requests_and_launch_synonyms_compile_without_a_model(self):
        for command in ("please open Notepad", "launch Notepad", "start Notepad",
                        "PLEASE LAUNCH Notepad", "please start Notepad application"):
            with self.subTest(command=command):
                steps = compile_fast_mission(command)
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].tool, "launch_installed_app")
                self.assertEqual(steps[0].arguments, {"query": "Notepad", "timeout_seconds": 12})

    def test_polite_launch_sequences_preserve_order_binding_and_quoted_text(self):
        text = ' please launch Calculator; then start a conversation  '
        steps = compile_fast_mission(
            f'please launch Notepad then please type "{text}" then start Calculator'
        )
        self.assertEqual([step.id for step in steps], ["fast-1", "fast-2", "fast-3"])
        self.assertEqual([step.tool for step in steps],
                         ["launch_installed_app", "interaction_type", "launch_installed_app"])
        self.assertEqual(steps[1].arguments,
                         {"text": text, "submit": False, "title": "notepad", "surface": "desktop"})
        self.assertEqual(steps[2].arguments["query"], "Calculator")
        self.assertEqual(compile_fast_mission(f'please type "{text}"')[0].arguments,
                         {"text": text, "submit": False})
        steps = compile_fast_mission("please start Notepad and please launch Calculator")
        self.assertEqual([step.arguments["query"] for step in steps], ["Notepad", "Calculator"])

    def test_launch_and_polite_requests_do_not_swallow_unknown_work(self):
        for command in (
            "please open Notepad and save the file", "launch Notepad if it is installed",
            "start Notepad without opening a new window", "please open Notepad or Calculator",
            "please do not open Notepad", "can you please open Notepad?",
            "launch Notepad then delete files", "launch Notepad and please send hello",
            "start Notepad then open Calculator and purchase something",
            'please open Notepad and type "hello" then delete files',
            "start Notepad and type hello and please launch Calculator",
            "search for cats and launch Calculator", "launch https://example.test",
        ):
            with self.subTest(command=command):
                self.assertIsNone(compile_fast_mission(command))

    def test_polite_navigation_in_sequence_preserves_url_punctuation(self):
        url = "https://example.test/path."
        steps = compile_fast_mission(f"please launch Notepad then please open {url}")
        self.assertEqual(steps[1].arguments, {"url": url})

    def test_exact_quoted_typing_compiles_without_guessing_text_or_sending(self):
        for command in ('type " hello مرحبا "', "write ' hello مرحبا '", 'اكتب " hello مرحبا "'):
            with self.subTest(command=command):
                steps = compile_fast_mission(command)
                self.assertEqual(len(steps), 1)
                self.assertEqual(steps[0].tool, "interaction_type")
                self.assertEqual(steps[0].arguments, {"text": " hello مرحبا ", "submit": False})
        self.assertEqual(compile_fast_mission('type "hello" in "Notepad"')[0].arguments,
                         {"text": "hello", "submit": False, "title": "Notepad"})
        self.assertEqual(compile_fast_mission('type "then click send"')[0].arguments["text"], "then click send")

    def test_literal_typing_rejects_ambiguous_or_unsupported_instructions(self):
        for command in ('type hello into chat', 'type "hi" and send it', 'type "hi',
                        'do not type "hi"', 'type "line\nbreak"', 'type "hi" then delete a file'):
            with self.subTest(command=command):
                self.assertIsNone(compile_fast_mission(command))

    def test_direct_website_navigation_reuses_browser_unless_new_tab_is_explicit(self):
        url = "https://example.test/path?q=hello%20world#section"
        for prefix in ("open", "go to", "navigate to", "افتح"):
            with self.subTest(prefix=prefix):
                step = compile_fast_mission(f"{prefix} {url}")[0]
                self.assertEqual((step.tool, step.arguments), ("browser_navigate", {"url": url}))
        self.assertEqual(compile_fast_mission(f"open {url} in a new tab")[0].tool, "chrome_new_tab")

    def test_invalid_urls_and_unknown_navigation_clauses_never_become_app_names(self):
        for command in ("open https://user:password@example.test", "open https://example.test:99999",
                        "open file://C:/private", "open https://example.test then delete files",
                        "open https://example.test and purchase something"):
            with self.subTest(command=command):
                self.assertIsNone(compile_fast_mission(command))

    def test_literal_actions_in_ordered_sequence_preserve_app_binding(self):
        steps = compile_fast_mission('open Notepad then type "hello" then open https://example.test')
        self.assertEqual([step.tool for step in steps], ["launch_installed_app", "interaction_type", "browser_navigate"])
        self.assertEqual(steps[1].arguments, {"text": "hello", "submit": False, "title": "notepad", "surface": "desktop"})
        self.assertIsNone(compile_fast_mission('open Discord then type "hello" in "Notepad" then press the first chat'))

    def test_literal_sequence_keeps_multiword_window_titles_and_exact_url_punctuation(self):
        steps = compile_fast_mission('open Visual Studio Code then type "hello" then open https://example.test/path.')
        self.assertEqual(steps[1].arguments["title"], "visual studio code")
        self.assertEqual(steps[2].arguments["url"], "https://example.test/path.")
        steps = compile_fast_mission('open Notepad then type "hello" in "Visual Studio Code" then type "again"')
        self.assertEqual(steps[2].arguments["title"], "visual studio code")

    def test_reported_whatsapp_draft_then_google_mission_compiles_every_step(self):
        goal = "open WhatsApp and press the first chat and write h after open new tab in google and search for a cat"
        steps = compile_fast_mission(goal)
        self.assertEqual([step.tool for step in steps], ["launch_installed_app", "whatsapp_select_chat_native", "interaction_type", "google_search"])
        self.assertEqual(steps[2].arguments, {"text": "h", "title": "WhatsApp", "submit": False})
        self.assertEqual(steps[3].arguments, {"query": "a cat", "new_tab": True})
        self.assertEqual([step.id for step in steps], ["fast-1", "fast-2", "fast-3", "fast-4"])

    def test_quoted_draft_sequence_words_stay_literal_and_unknown_clauses_are_preserved(self):
        prefix = 'open WhatsApp and press the first chat and write "h; then open a book"'
        steps = compile_fast_mission(prefix + " after open new tab in google and search for a cat")
        self.assertEqual(steps[2].arguments["text"], "h; then open a book")
        self.assertIsNone(compile_fast_mission(prefix + " then delete the file"))

    def test_simple_arabic_commands_compile_in_order_without_model(self):
        steps = compile_fast_mission("افتح ديسكورد ثم افتح الحاسبة ثم افتح المفكرة")
        self.assertEqual(
            [step.arguments["query"] for step in steps],
            ["Discord", "Calculator", "Notepad"],
        )

    def test_arabic_media_and_search_use_verified_semantic_operations(self):
        steps = compile_fast_mission('شغل "اسم الأغنية" على يوتيوب')
        self.assertEqual(steps[0].tool, "youtube_search_open")
        self.assertEqual(
            steps[0].arguments,
            {"query": "اسم الأغنية", "new_tab": False, "play": True},
        )
        self.assertEqual(
            compile_fast_mission('ابحث في جوجل عن "تعلم بايثون"')[0].arguments["query"],
            "تعلم بايثون",
        )
        self.assertEqual(
            compile_fast_mission("اوقف الفيديو مؤقتا")[0].arguments["action"],
            "pause",
        )

    def test_unknown_arabic_step_falls_back_whole_mission_without_skipping(self):
        for goal in (
            "افتح ديسكورد ثم ارسل رسالة",
            "افتح ديسكورد وارسل رسالة",
            'شغل "أغنية" على يوتيوب ثم احفظها',
            "افتح تطبيق غير معروف",
            "لا تفتح ديسكورد",
        ):
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

    def test_reported_mixed_whatsapp_apps_google_chain_compiles_without_model(self) -> None:
        steps = compile_fast_mission(
            "open WhatsApp and press the first chat, then open Discord, then open Instagram, then open Google and search for cats"
        )
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.tool for step in steps],
            [
                "launch_installed_app",
                "whatsapp_select_chat_native",
                "launch_installed_app",
                "launch_installed_app",
                "google_search",
            ],
        )
        self.assertEqual(steps[0].arguments["query"], "WhatsApp")
        self.assertEqual(steps[1].arguments, {"position": 1})
        self.assertEqual(steps[2].arguments["query"], "Discord")
        self.assertEqual(steps[3].arguments["query"], "Instagram")
        self.assertEqual(steps[4].arguments, {"query": "cats", "new_tab": False})

    def test_mixed_chat_clause_supports_discord_context(self) -> None:
        steps = compile_fast_mission("open Discord, then press the first chat, then open Instagram")
        self.assertEqual([s.tool for s in steps], ["launch_installed_app", "discord_select_chat", "launch_installed_app"])
        self.assertEqual(steps[1].arguments, {"position": 1})

    def test_mixed_chat_clause_requires_supported_chat_app(self) -> None:
        self.assertIsNone(
            compile_fast_mission(
                "open Calculator, then press the first chat, then open Instagram"
            )
        )

    def test_reported_discord_mission_compiles_without_a_model(self):
        for verb in ("PRESS", "CLICK", "OPEN", "SELECT"):
            steps = compile_fast_mission(f"OPEN DISCORD AND {verb} THE FIRST CHAT")
            self.assertEqual([s.tool for s in steps], ["launch_installed_app", "discord_select_chat"])
            self.assertEqual(steps[1].arguments, {"position": 1})

    def test_discord_ordered_draft_preserves_unicode_and_spacing(self):
        steps = compile_fast_mission('open Discord and select the 2 chat after write "hello  \u0639\u0627\u0644\u0645"')
        self.assertEqual([s.tool for s in steps], ["launch_installed_app", "discord_select_chat", "interaction_type"])
        self.assertEqual(steps[1].arguments, {"position": 2})
        self.assertEqual(steps[-1].arguments, {"text": "hello  \u0639\u0627\u0644\u0645", "title": "Discord", "surface": "desktop", "submit": False})

    def test_exact_reported_discord_write_then_send_compiles_without_model(self):
        steps = compile_fast_mission(
            "OPEN DISCORD AND PRESS THE FIRST CHAT THEN WRITE FDD THEN SEND IT"
        )
        self.assertEqual(
            [s.tool for s in steps],
            ["launch_installed_app", "discord_select_chat", "discord_send_message"],
        )
        self.assertEqual(steps[1].arguments, {"position": 1})
        self.assertEqual(steps[2].arguments, {"text": "FDD"})

    def test_discord_write_then_send_preserves_quoted_unicode(self):
        steps = compile_fast_mission(
            'open Discord and select the 2 chat then write "hello  \u0639\u0627\u0644\u0645" then send it'
        )
        self.assertEqual(steps[-1].tool, "discord_send_message")
        self.assertEqual(steps[-1].arguments, {"text": "hello  \u0639\u0627\u0644\u0645"})

    def test_discord_unknown_or_out_of_range_work_is_not_dropped(self):
        for tail in ("and send hi", "and delete it", "after write HI then delete it"):
            self.assertIsNone(compile_fast_mission("open Discord and press the first chat " + tail))
        for ordinal in ("0", "21", "100"):
            self.assertIsNone(compile_fast_mission(f"open Discord and press the {ordinal} chat"))

    def test_unknown_mixed_side_effect_falls_back_to_model(self) -> None:
        self.assertIsNone(
            compile_fast_mission(
                "open Discord, then send hello, then open Instagram"
            )
        )

    def test_app_only_chain_compiles_without_model_round_trip(self) -> None:
        steps = compile_fast_mission("open discord app and then open whatsapp app")
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.arguments["query"].casefold() for step in steps],
            ["discord", "whatsapp"],
        )

    def test_app_chain_then_write_uses_verified_rust_native_type(self) -> None:
        steps = compile_fast_mission(
            "open discord app and then open WhatsApp app and write c"
        )
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.tool for step in steps],
            ["launch_installed_app", "launch_installed_app", "ui_type_native"],
        )
        self.assertEqual(steps[0].arguments["query"].casefold(), "discord")
        self.assertEqual(steps[1].arguments["query"].casefold(), "whatsapp")
        self.assertEqual(steps[2].arguments, {"text": "c"})

    def test_quoted_native_type_preserves_spaces(self) -> None:
        steps = compile_fast_mission('open WhatsApp app and type "hello from rust"')
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(steps[-1].tool, "ui_type_native")
        self.assertEqual(steps[-1].arguments["text"], "hello from rust")

    def test_unknown_clause_falls_back_instead_of_being_skipped(self) -> None:
        goal = (
            "open new tab in google and search for a cat and click the first result "
            "and then open discord app and send a message"
        )
        self.assertIsNone(compile_fast_mission(goal))

    def test_type_clause_does_not_swallow_a_later_action(self) -> None:
        self.assertIsNone(
            compile_fast_mission(
                "open WhatsApp app and write c and then open calculator"
            )
        )

    def test_fast_execution_can_be_disabled_explicitly(self) -> None:
        with patch.dict(os.environ, {"JARVIS_FAST_EXECUTION": "false"}, clear=False):
            self.assertIsNone(compile_fast_mission("open discord app"))

    def test_action_clauses_are_not_swallowed_into_search_query_or_app_name(self) -> None:
        for goal in (
            "search for cats then send hello",
            "search for cats and play a song",
            "open Discord → navigate to channel",
            "open Discord and join voice",
            "search for cats then save the image and click the first result",
        ):
            self.assertIsNone(compile_fast_mission(goal), goal)


class FastMissionExecutionTests(unittest.TestCase):
    def _agent(self):
        orchestrator = Mock()
        orchestrator.current.metrics = {}
        return SimpleNamespace(
            orchestrator=orchestrator, workspace_context=Mock(), messages=[], memory=Mock(),
            _is_stopped=Mock(return_value=False), _is_mutation=Mock(return_value=True),
            approval=Mock(return_value=True), _execute_tool=Mock(return_value="VERIFIED: done"),
        )

    @patch("core.full_access_agent._chrome_tab_rows")
    def test_polite_launch_executes_in_order_through_normal_approval(self, tab_rows):
        agent = self._agent()
        calls = Mock()
        calls.attach_mock(agent.approval, "approve")
        calls.attach_mock(agent._execute_tool, "execute")
        result = execute_fast_mission(agent, 'please launch Notepad then please type "hello"')
        self.assertTrue(result.startswith("Completed and verified:"))
        self.assertEqual([call[0] for call in calls.mock_calls], ["approve", "execute", "approve", "execute"])
        self.assertEqual([call.args[0] for call in agent._execute_tool.call_args_list],
                         ["launch_installed_app", "interaction_type"])
        self.assertTrue(all(call.kwargs == {"approved": True} for call in agent._execute_tool.call_args_list))
        plan = agent.orchestrator.set_plan.call_args.args[0]
        self.assertEqual([step.depends_on for step in plan], [[], ["fast-1"]])
        tab_rows.assert_not_called()

    @patch("core.full_access_agent._chrome_tab_rows")
    def test_denied_launch_stops_before_typing_and_is_not_recorded_as_mutation(self, tab_rows):
        agent = self._agent()
        agent.approval.return_value = False
        agent._execute_tool.return_value = "PERMISSION_DENIED: launch not approved"
        result = execute_fast_mission(agent, 'please start Notepad then type "hello"')
        self.assertIn("PERMISSION_DENIED", result)
        agent._execute_tool.assert_called_once_with(
            "launch_installed_app", {"query": "Notepad", "timeout_seconds": 12}, approved=False)
        self.assertFalse(agent.orchestrator.record_tool.call_args.kwargs["mutation"])
        agent.orchestrator.finish.assert_called_once_with("incomplete", result)
        tab_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
