from __future__ import annotations

import unittest
from pathlib import Path


class FullAccessAgentPolicyTests(unittest.TestCase):
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
