"""Coordinate/OCR tests on synthetic images, without capturing the desktop."""
from __future__ import annotations

import time
import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image

from core.control_engines.geometry import CoordinateMap, Rect, VisualTarget, relative_point
from core.control_engines.vision import TextRegion, change_score, exact_template_matches, localize


class GeometryTests(unittest.TestCase):
    def test_negative_monitor_origin_and_scaling(self):
        mapping = CoordinateMap(Rect(-1920, -200, 1920, 1960), 1920, 1080)
        self.assertEqual(mapping.point(100, 100), (-1720, 0))
        self.assertEqual(mapping.rectangle(Rect(100, 100, 140, 120)), Rect(-1720, 0, -1640, 40))
        with self.assertRaises(ValueError):
            mapping.point(1920, 0)

    def test_window_relative_points_and_safe_margins(self):
        mapping = CoordinateMap(Rect(200, 100, 1000, 700), 400, 300)
        self.assertEqual(mapping.point(10, 20), (220, 140))
        self.assertEqual(Rect(0, 0, 20, 20).center(3), (10, 10))
        with self.assertRaises(ValueError):
            Rect(0, 0, 2, 2).center(1)

    def test_relative_movement_rejects_monitor_gaps(self):
        displays = [Rect(-100, 0, 0, 100), Rect(10, 0, 110, 100)]
        self.assertEqual(relative_point(-50, 30, 100, 0, displays), (50, 30))
        with self.assertRaises(ValueError):
            relative_point(-50, 30, 55, 0, displays)

    def test_visual_targets_require_fresh_bound_evidence_and_confidence(self):
        def target(**changes):
            return VisualTarget(**{"description": "Button", "bounds": Rect(1, 1, 10, 10),
                "observation_id": "frame", "observed_at": time.monotonic(), "window_hwnd": 7,
                "confidence": 0.95, "method": "fixture", **changes})
        target().validate(observation_id="frame", window_hwnd=7)
        for changes in ({"confidence": None}, {"confidence": 0.2}, {"confidence": float("nan")},
                        {"window_hwnd": 8}, {"observed_at": time.monotonic() - 10}, {"observation_id": "old"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                target(**changes).validate(observation_id="frame", window_hwnd=7)


class LocalVisionTests(unittest.TestCase):
    def test_ocr_regions_map_to_desktop_without_fabricated_confidence(self):
        matches = localize([TextRegion("Save file", Rect(10, 20, 40, 35))],
                          CoordinateMap(Rect(-100, 50, 100, 250), 100, 100),
                          observation_id="frame", hwnd=7, observed_at=time.monotonic(), method="windows_ocr", query="save")
        self.assertEqual(matches[0].bounds, Rect(-80, 90, -20, 120))
        self.assertIsNone(matches[0].confidence)

    def test_invalid_ocr_bounding_boxes_are_rejected(self):
        with self.assertRaises(ValueError):
            localize([TextRegion("wrong", Rect(90, 0, 120, 20))],
                     CoordinateMap(Rect(0, 0, 100, 100), 100, 100), observation_id="x", hwnd=1,
                     observed_at=time.monotonic(), method="fixture")

    def test_exact_template_localization_and_ambiguity(self):
        image = Image.new("RGB", (30, 20), "white")
        needle = Image.new("RGB", (4, 4), "black")
        image.paste(needle, (11, 9))
        self.assertEqual(exact_template_matches(image, needle), [Rect(11, 9, 15, 13)])
        with self.assertRaises(ValueError):
            exact_template_matches(image, needle, max_positions=2)

    def test_screen_change_and_geometry_change(self):
        white = Image.new("RGB", (30, 20), "white")
        self.assertEqual(change_score(white, white.copy()), 0)
        self.assertEqual(change_score(white, Image.new("RGB", (30, 20), "black")), 1)
        self.assertEqual(change_score(white, Image.new("RGB", (20, 20))), 1)


class CaptureOCRTests(unittest.TestCase):
    """Exercise capture/OCR integration with owned image files and no desktop access."""
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / ".jarvis" / "vision" / "fixture.png"
        self.path.parent.mkdir(parents=True)
        Image.new("RGB", (200, 100), "white").save(self.path)
        self.frame = {"path": str(self.path), "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                      "foreground_hwnd": 7, "stable": True, "virtual_origin_x": -200, "virtual_origin_y": 0,
                      "source_width": 400, "source_height": 200, "captured_at_ms": 12345}
        self.provider = Mock(name="ocr_provider")
        self.provider.name = "fixture_ocr"
        self.provider.recognize.return_value = [TextRegion("Known label", Rect(10, 5, 20, 15))]

    def observe(self, foreground=None):
        from core.control_engines.vision import vision_ocr
        with patch("core.vision_tools._workspace", return_value=self.root), \
                patch("core.vision_tools.screen_observe", return_value="VERIFIED: " + json.dumps(self.frame)), \
                patch("core.desktop_observation.foreground_identity", side_effect=foreground or [7, 7]), \
                patch("core.control_engines.vision._active_bounds", return_value=Rect(-100, 20, 100, 100)):
            return vision_ocr(provider=self.provider)

    def test_fresh_window_capture_is_cropped_and_mapped_exactly(self):
        result = self.observe()
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertEqual(self.provider.recognize.call_args.args[0].size, (100, 40))
        self.assertEqual(payload["targets"][0]["bounds"], {"left": -80, "top": 30, "right": -60, "bottom": 50})
        self.assertTrue(payload["local_only"])
        self.assertIsNone(payload["targets"][0]["confidence"])

    def test_foreground_change_rejects_ocr_result(self):
        with self.assertRaisesRegex(RuntimeError, "Window changed"):
            self.observe([7, 8])

    def test_modified_capture_cannot_be_read_as_fresh_evidence(self):
        self.frame["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "image changed"):
            self.observe()
        self.provider.recognize.assert_not_called()

    def test_screen_permission_cannot_be_bypassed_by_ocr_tool(self):
        from core.control_engines.vision import register_local_vision_tools
        from core.tools import ToolRegistry, ToolSpec
        from core.permissions import Risk
        registry = ToolRegistry()
        registry.register(ToolSpec("screen_observe", "fixture", Risk.LOW, {"type": "object"}, Mock()))
        registry.permissions.deny_tools.add("screen_observe")
        register_local_vision_tools(registry)
        with patch("core.control_engines.vision.vision_ocr") as capture:
            self.assertIn("Screen observation is unavailable or denied", registry.execute("vision_ocr", {}))
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
