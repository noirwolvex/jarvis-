from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.full_access_agent import FullAccessJarvisAgent


class FullAccessAgentPolicyTests(unittest.TestCase):
    def test_native_input_guard_inspects_actual_chrome_without_cdp(self):
        agent = object.__new__(FullAccessJarvisAgent)
        with patch("core.full_access_agent._foreground_is_chrome", return_value=True), \
                patch("core.semantic_ui_tools._foreground_hwnd", return_value=12), \
                patch("core.chrome_existing_window._identity", return_value={"hwnd": 12}), \
                patch("core.chrome_existing_window.read_existing_chrome", return_value={"challenge_detected": True}) as read:
            result = agent._browser_action_guard("desktop_type")
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"))
        read.assert_called_once_with({"hwnd": 12})

    def test_unreadable_foreground_chrome_does_not_authorize_native_input(self):
        agent = object.__new__(FullAccessJarvisAgent)
        with patch("core.full_access_agent._foreground_is_chrome", return_value=True), \
                patch("core.semantic_ui_tools._foreground_hwnd", return_value=12), \
                patch("core.chrome_existing_window._identity", side_effect=RuntimeError("window changed")):
            result = agent._browser_action_guard("desktop_type")
        self.assertTrue(result.startswith("ERROR:"))

    def test_explicit_browser_action_does_not_inspect_unrelated_desktop(self):
        agent = object.__new__(FullAccessJarvisAgent)
        with patch("core.agent.JarvisAgent._browser_action_guard", return_value=None) as guard, \
                patch("core.chrome_existing_window.read_existing_chrome") as read:
            self.assertIsNone(agent._browser_action_guard("interaction_type", {"surface": "browser"}))
        guard.assert_called_once_with("browser_click")
        read.assert_not_called()

    def test_human_verification_pause_precedes_recovery(self) -> None:
        source = Path("core/full_access_agent.py").read_text(encoding="utf-8")
        pause_index = source.index("if challenge_pause:")
        waiting_index = source.index('self.orchestrator.finish("waiting_user", pause_result)', pause_index)
        return_index = source.index("return pause_result", pause_index)
        recovery_index = source.index("hint = self.orchestrator.recovery_hint", pause_index)

        self.assertLess(waiting_index, return_index)
        self.assertLess(return_index, recovery_index)
        self.assertIn("left the page open", source)
        self.assertIn("without closing, switching, navigating, or interacting", source)


if __name__ == "__main__":
    unittest.main()
