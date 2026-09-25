from __future__ import annotations

import unittest

from core.whatsapp_fast_mission import compile_whatsapp_ordinal_mission


class WhatsAppOrdinalFastMissionTests(unittest.TestCase):
    def test_exact_reported_first_chat_phrase_compiles(self) -> None:
        steps = compile_whatsapp_ordinal_mission(
            "open WhatsApp and press the first chat"
        )
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.tool for step in steps],
            ["launch_installed_app", "whatsapp_select_chat_native"],
        )
        self.assertEqual(steps[1].arguments, {"position": 1})

    def test_reported_after_write_phrase_compiles_unsent_without_model(self):
        for connector in ("AFTER", "AFTER THAT", "AND AFTER THAT", "THEN", "AND THEN"):
            with self.subTest(connector=connector):
                steps = compile_whatsapp_ordinal_mission(
                    f"OPEN WHATSAPP AND PRESS THE FIRST CHAT {connector} WRITE HI")
                self.assertIsNotNone(steps)
                self.assertEqual([step.tool for step in steps],
                                 ["launch_installed_app", "whatsapp_select_chat_native", "ui_type_native"])
                self.assertEqual(steps[-1].arguments, {"text": "HI", "title": "WhatsApp"})

    def test_typing_payload_preserves_internal_spacing(self):
        steps = compile_whatsapp_ordinal_mission('open WhatsApp and select the 1 chat after write "hello  world"')
        self.assertIsNotNone(steps)
        self.assertEqual(steps[1].arguments, {"position": 1})
        self.assertEqual(steps[-1].arguments["text"], "hello  world")

    def test_after_clause_does_not_swallow_send_or_other_requested_actions(self):
        for tail in ("HI after send it", "HI and send it", "HI then open Discord"):
            with self.subTest(tail=tail):
                self.assertIsNone(compile_whatsapp_ordinal_mission(
                    "open WhatsApp and select the first chat after write " + tail))

    def test_conversation_synonym_and_select_verb_compile(self) -> None:
        steps = compile_whatsapp_ordinal_mission(
            "open WhatsApp and select the second conversation"
        )
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(steps[-1].tool, "whatsapp_select_chat_native")
        self.assertEqual(steps[-1].arguments, {"position": 2})

    def test_reported_second_chat_then_type_compiles_in_exact_order(self) -> None:
        steps = compile_whatsapp_ordinal_mission(
            "open discord app and then open WhatsApp app and then press the second chat and write c"
        )
        self.assertIsNotNone(steps)
        assert steps is not None
        self.assertEqual(
            [step.tool for step in steps],
            [
                "launch_installed_app",
                "launch_installed_app",
                "whatsapp_select_chat_native",
                "ui_type_native",
            ],
        )
        self.assertEqual(steps[0].arguments["query"].casefold(), "discord")
        self.assertEqual(steps[1].arguments["query"].casefold(), "whatsapp")
        self.assertEqual(steps[2].arguments, {"position": 2})
        self.assertEqual(steps[3].arguments, {"text": "c", "title": "WhatsApp"})

    def test_numeric_and_named_ordinals_are_supported(self) -> None:
        for phrase, expected in (("third", 3), ("4th", 4), ("tenth", 10)):
            steps = compile_whatsapp_ordinal_mission(
                f"open WhatsApp app and click the {phrase} chat"
            )
            self.assertIsNotNone(steps, phrase)
            assert steps is not None
            self.assertEqual(steps[-1].tool, "whatsapp_select_chat_native")
            self.assertEqual(steps[-1].arguments["position"], expected)

    def test_chat_ordinal_requires_whatsapp_as_last_opened_app(self) -> None:
        self.assertIsNone(
            compile_whatsapp_ordinal_mission(
                "open WhatsApp app and then open Discord app and then press the second chat and write c"
            )
        )

    def test_unknown_action_is_not_swallowed_into_chat_or_text(self) -> None:
        self.assertIsNone(
            compile_whatsapp_ordinal_mission(
                "open WhatsApp app and then press the second chat and then delete it and write c"
            )
        )


if __name__ == "__main__":
    unittest.main()
