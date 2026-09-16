from __future__ import annotations

import unittest

from core.whatsapp_fast_mission import compile_whatsapp_ordinal_mission


class WhatsAppOrdinalFastMissionTests(unittest.TestCase):
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
        self.assertEqual(steps[3].arguments, {"text": "c"})

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
