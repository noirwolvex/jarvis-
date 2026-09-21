"""Pointer regressions with virtual cursor state only; no device input."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import desktop_control_tools as control
from core.desktop_input import InputDeliveryError


class PointerDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.point = SimpleNamespace(x=10, y=20)
        self.clock = 0.0
        self.fake = SimpleNamespace(position=Mock(side_effect=lambda: self.point),
                                    moveTo=Mock(side_effect=self.move), click=Mock())
        for mock in (patch.dict(sys.modules, {"pyautogui": self.fake}),
                     patch.object(control, "_windows_only"),
                     patch("core.desktop_observation.foreground_identity", return_value=7),
                     patch.object(control.time, "monotonic", side_effect=lambda: self.clock),
                     patch.object(control.time, "sleep", side_effect=self.sleep)):
            mock.start()
            self.addCleanup(mock.stop)

    def move(self, x, y, **kwargs):
        self.point = SimpleNamespace(x=x, y=y)

    def sleep(self, seconds):
        self.clock += seconds

    def test_stationary_move_returns_without_delay_or_duplicate_input(self):
        control.move_pointer(10, 20, 0.8)
        self.assertEqual(self.clock, 0)
        self.fake.moveTo.assert_not_called()

    def test_short_move_avoids_repeated_identical_samples_and_verifies_destination(self):
        control.move_pointer(11, 20, 0.1)
        self.fake.moveTo.assert_called_once_with(11, 20, _pause=False)
        self.assertEqual((self.point.x, self.point.y), (11, 20))
        self.assertLessEqual(self.clock, 0.1)

    def test_pointer_delivery_failure_cannot_claim_destination(self):
        self.fake.moveTo.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            control.move_pointer(99, 20, 0)

    def test_single_click_has_no_fixed_delay_or_unearned_verification(self):
        result = control.desktop_click_button(10, 20)
        self.fake.click.assert_called_once_with(button="left", _pause=False)
        self.assertEqual(self.clock, 0)
        self.assertTrue(result.startswith("DELIVERED:"))

    def test_double_click_has_only_one_bounded_gap(self):
        control.desktop_click_button(10, 20, clicks=2)
        self.assertEqual(self.fake.click.call_count, 2)
        self.assertAlmostEqual(self.clock, 0.045)

    def test_pointer_drift_between_clicks_does_not_reposition_or_replay(self):
        self.fake.click.side_effect = lambda **kwargs: self.move(99, 99)
        with self.assertRaisesRegex(InputDeliveryError, "target changed"):
            control.desktop_click_button(10, 20, clicks=3)
        self.fake.click.assert_called_once()
        self.fake.moveTo.assert_not_called()

    def test_cancellation_during_click_gap_prevents_remaining_clicks(self):
        def check():
            if self.fake.click.call_count:
                raise RuntimeError("cancelled")
        with patch("core.process_control.check_cancelled", side_effect=check), \
             patch("core.ui_state.check_cancelled", side_effect=check):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                control.desktop_click_button(10, 20, clicks=2)
        self.fake.click.assert_called_once()

    def test_failed_click_pair_is_not_replayed(self):
        self.fake.click.side_effect = OSError("uncertain send")
        with self.assertRaises(InputDeliveryError):
            control.desktop_click_button(10, 20, clicks=2)
        self.fake.click.assert_called_once()
        self.assertEqual(self.clock, 0)

    def test_focus_change_before_click_prevents_delivery(self):
        with patch("core.desktop_observation.foreground_identity", side_effect=[7, 7, 8]):
            with self.assertRaises(InputDeliveryError):
                control.desktop_click_button(10, 20)
        self.fake.click.assert_not_called()

    def test_legacy_click_routes_through_same_guarded_delivery(self):
        from core.tools import _desktop_click
        with patch.object(control, "desktop_click_button", return_value="DELIVERED: fixture") as click:
            self.assertEqual(_desktop_click(10, 20), "DELIVERED: fixture")
        click.assert_called_once_with(10, 20)
