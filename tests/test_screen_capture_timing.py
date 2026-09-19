import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from core import vision_tools as vision
from core.tools import ToolRegistry


class ScreenCaptureTimingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for mocked in (patch.object(vision, "os", SimpleNamespace(name="nt")),
                       patch.object(vision, "_workspace", return_value=Path(self.directory.name)),
                       patch("core.desktop_observation.foreground_identity", return_value=7),
                       patch.object(vision, "_virtual_origin", return_value=(0, 0)),
                       patch("core.process_control._cancelled", return_value=False)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_zero_settle_is_single_capture_and_does_not_claim_stability(self):
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (100, 100))) as grab, patch("core.ui_state.cancellable_delay") as delay:
            result = vision.screen_observe(settle_ms=0)
        self.assertFalse(vision._payload_from_result(result)["stable"])
        grab.assert_called_once()
        delay.assert_not_called()

    def test_registered_tool_accepts_fast_read_mode(self):
        registry = ToolRegistry()
        registry.permissions.check = Mock(return_value=(True, "fixture"))
        vision.register_vision_tools(registry)
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (100, 100))):
            result = registry.execute("screen_observe", {"settle_ms": 0})
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertFalse(vision._payload_from_result(result)["stable"])

    def test_small_popup_prevents_premature_stability(self):
        before = Image.new("RGB", (1000, 1000), "white")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((400, 400, 420, 420), fill="black")
        with patch("PIL.ImageGrab.grab", side_effect=[before, after, after]) as grab, patch("core.ui_state.cancellable_delay"):
            result = vision.screen_observe()
        self.assertTrue(vision._payload_from_result(result)["stable"])
        self.assertEqual(grab.call_count, 3)

    def test_cancelled_capture_does_not_publish_or_write_frame(self):
        stopped = [False]
        def capture(**kwargs):
            stopped[0] = True
            return Image.new("RGB", (100, 100))
        with patch("core.process_control._cancelled", side_effect=lambda: stopped[0]), patch("PIL.ImageGrab.grab", side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                vision.screen_observe()
        self.assertFalse(list(Path(self.directory.name).rglob("screen-*.jpg")))

    def test_monitor_size_change_invalidates_observation(self):
        with patch("PIL.ImageGrab.grab", side_effect=[Image.new("RGB", (100, 100)), Image.new("RGB", (200, 100))]), patch("core.ui_state.cancellable_delay"):
            with self.assertRaisesRegex(RuntimeError, "geometry changed"):
                vision.screen_observe()

    def test_capture_timestamp_does_not_become_encoding_completion_time(self):
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (100, 100))), \
             patch.object(vision.time, "time", side_effect=[10.0, 99.0]):
            result = vision.screen_observe(settle_ms=0)
        self.assertEqual(vision._payload_from_result(result)["captured_at_ms"], 10000)


if __name__ == "__main__":
    unittest.main()
