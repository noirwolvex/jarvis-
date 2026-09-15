from __future__ import annotations

import unittest

from core.app_discovery_fast import _confident


class FastAppDiscoveryTests(unittest.TestCase):
    def test_exact_high_confidence_match_is_accepted(self) -> None:
        self.assertTrue(_confident([{"name": "Discord", "score": 114.0}]))

    def test_clear_high_confidence_winner_is_accepted(self) -> None:
        self.assertTrue(
            _confident(
                [
                    {"name": "WhatsApp", "score": 114.0},
                    {"name": "WhatsApp Beta", "score": 106.0},
                ]
            )
        )

    def test_ambiguous_matches_fall_back_to_exhaustive_discovery(self) -> None:
        self.assertFalse(
            _confident(
                [
                    {"name": "Visual Studio Code", "score": 110.0},
                    {"name": "Visual Studio", "score": 108.0},
                ]
            )
        )

    def test_weak_match_falls_back(self) -> None:
        self.assertFalse(_confident([{"name": "Something", "score": 91.0}]))


if __name__ == "__main__":
    unittest.main()
