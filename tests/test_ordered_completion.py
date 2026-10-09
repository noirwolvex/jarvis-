from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from core.orchestrator import TaskOrchestrator
from core.ordered_completion import explicit_action_count, verified_action_count


class OrderedCompletionContractTests(unittest.TestCase):
    def test_counts_explicit_sequential_actions_not_quoted_text(self):
        cases = (
            ("OPEN WHATSAPP AND PRESS THE FIRST CHAT AND WRITE H AFTER OPEN DISCORD", 4),
            ("open discord then select the first chat then type hello", 3),
            ("open new tab and search for a cat", 2),
            ('type "open Discord and send text"', 0),
            ("Please inspect the current desktop", 0),
            ("search for cats and dogs", 0),
            ("open an app and open a different app then write hello", 3),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(explicit_action_count(text), expected)

    def test_plain_read_only_evidence_does_not_cover_missing_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("open one app then click second target")
            state.record_tool("launch_installed_app", {}, "VERIFIED: window", 1, 1, mutation=True)
            state.record_tool("ui_inspect", {}, "VERIFIED: control exists", 1, 1)
            state.verify("opened", True, "window verified")
            self.assertEqual(
                verified_action_count(state.current, lambda name: name == "launch_installed_app"),
                1,
            )

    def test_unverified_click_not_counted_as_completed_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("click button then type text")
            state.record_tool("interaction_click", {}, "DELIVERED: click", 1, 1, mutation=True)
            state.record_tool("ui_inspect", {}, "VERIFIED: button still exists", 1, 1)
            self.assertEqual(
                verified_action_count(state.current, lambda name: name == "interaction_click"),
                0,
            )

    def test_combined_verified_tool_covers_two_exact_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("open file explorer and go to downloads")
            state.record_tool(
                "open_known_folder", {"folder": "Downloads"},
                "VERIFIED: exact foreground location", 1, 1, mutation=True,
            )
            self.assertEqual(
                verified_action_count(state.current, lambda name: name == "open_known_folder"),
                2,
            )

    def test_completed_plan_nodes_count_only_completed_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = TaskOrchestrator(tmp)
            state.begin("open file then write")
            state.set_plan([
                {"id": "a", "description": "Open File"},
                {"id": "b", "description": "Write"},
            ])
            state.update_step("a", "completed")
            self.assertEqual(verified_action_count(state.current, lambda _: False), 1)
            state.update_step("b", "failed")
            self.assertEqual(verified_action_count(state.current, lambda _: False), 1)


if __name__ == "__main__":
    unittest.main()
