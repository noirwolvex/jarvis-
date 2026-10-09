from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core import computer_perception as perception
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


def scene(nodes=None, **kwargs):
    values = nodes if nodes is not None else [
        {"id": "uia:1", "role": "Button", "name": "Continue", "actionable": True,
         "visible": True, "enabled": True,
         "target": {"surface": "desktop", "target": "Continue", "expected_hwnd": 42}},
    ]
    return "VERIFIED: " + json.dumps({
        "scene_id": "scene-1", "surface": "desktop", "nodes": values,
        "context": {"hwnd": 42, "title": "Fixture", "foreground": True},
        "cached_source": False, "source_version": 5, "node_count": len(values),
        "actionable_count": sum(bool(node.get("actionable")) for node in values),
        **kwargs,
    })


class ComputerPerceptionTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        for name in ("interaction_scene", "screen_observe"):
            self.registry.register(ToolSpec(name, "fixture", Risk.LOW, {"type": "object"}, lambda: ""))
        perception.register_perception_tools(self.registry)
        self.semantic = patch.object(perception, "interaction_scene", return_value=scene()).start()
        self.capture = patch.object(perception, "screen_observe", return_value="VERIFIED: " + json.dumps({
            "path": "fixture.jpg", "foreground_hwnd": 42, "stable": True, "scene_bound": True,
            "sha256": "fixture-sha", "source_width": 1920, "source_height": 1080,
        })).start()
        self.foreground = patch.object(perception, "foreground_identity", return_value=42).start()
        self.cancel = patch.object(perception, "check_cancelled").start()
        self.addCleanup(patch.stopall)

    def observe(self, **kwargs):
        value = perception.computer_observe(registry=self.registry, **kwargs)
        self.assertTrue(value.startswith("VERIFIED: "))
        return json.loads(value[len("VERIFIED: "):])

    def test_complete_semantics_avoid_screenshot_and_preserve_live_target(self):
        result = self.observe()
        self.capture.assert_not_called()
        self.assertFalse(result["visual_included"])
        self.assertFalse(result["scene_bound"])
        self.assertFalse(result["stable"])
        self.assertNotIn("path", result)
        self.assertEqual(result["semantic"]["nodes"][0]["target"],
                         {"surface": "desktop", "target": "Continue", "expected_hwnd": 42})
        self.assertEqual(result["coverage_gaps"], [])
        self.assertTrue(self.semantic.call_args.kwargs["force_refresh"])
        self.assertEqual(self.semantic.call_args.kwargs["scope"], "structure")
        self.assertEqual(self.semantic.call_args.kwargs["mode"], "full")

    def test_forced_visual_uses_existing_screen_frame_once(self):
        result = self.observe(visual="always", settle_ms=50)
        self.capture.assert_called_once_with(settle_ms=50)
        self.assertTrue(result["scene_bound"])
        self.assertEqual(result["alignment"], "same_foreground_window")
        self.assertEqual(result["path"], "fixture.jpg")
        self.assertGreater(result["observed_at_ms"], 0)
        self.assertGreaterEqual(result["perception_ms"], 0)

    def test_unlabeled_controls_trigger_visual_fallback(self):
        self.semantic.return_value = scene([{"id": "icon", "role": "Button", "name": "", "actionable": True}])
        result = self.observe()
        self.capture.assert_called_once()
        self.assertIn("unlabeled_controls", result["coverage_gaps"])

    def test_automation_id_is_a_semantic_target_without_display_name(self):
        self.semantic.return_value = scene([{"id": "icon", "role": "Button", "name": "", "automation_id": "Next", "actionable": True}])
        self.observe()
        self.capture.assert_not_called()

    def test_truncated_or_empty_semantics_add_visual_context(self):
        for value, reason in ((scene(truncated=True), "semantic_map_truncated"),
                              (scene([]), "no_visible_semantic_controls")):
            with self.subTest(reason=reason):
                self.capture.reset_mock()
                self.semantic.return_value = value
                result = self.observe()
                self.assertIn(reason, result["coverage_gaps"])
                self.capture.assert_called_once()

    def test_metadata_only_does_not_capture_even_if_map_incomplete(self):
        self.semantic.return_value = scene([], truncated=True)
        result = self.observe(visual="never", force_refresh=False)
        self.capture.assert_not_called()
        self.assertFalse(self.semantic.call_args.kwargs["force_refresh"])
        self.assertTrue(result["coverage_gaps"])

    def test_failed_semantics_recover_with_visual_and_report_error(self):
        self.semantic.side_effect = RuntimeError("UIA unavailable")
        result = self.observe()
        self.assertEqual(result["alignment"], "visual_only")
        self.assertIsNone(result["semantic"])
        self.assertEqual(result["errors"][0]["message"], "UIA unavailable")
        self.assertEqual(result["coverage_gaps"], ["semantic_unavailable"])

    def test_denied_semantic_backend_is_not_invoked(self):
        self.registry.permissions.deny_tools.add("interaction_scene")
        result = self.observe()
        self.semantic.assert_not_called()
        self.assertEqual(result["errors"][0]["type"], "PermissionError")
        self.capture.assert_called_once()

    def test_human_verification_does_not_fall_back_to_visual_control(self):
        self.semantic.side_effect = perception.BrowserChallengeBlocked("Human verification requires the user")
        with self.assertRaisesRegex(perception.BrowserChallengeBlocked, "Human verification"):
            self.observe(visual="always")
        self.capture.assert_not_called()

    def test_labeled_control_does_not_hide_uninspected_visual_regions(self):
        base = json.loads(scene()[10:])["nodes"]
        for tag, reason in (("iframe", "uninspected_embedded_content"),
                            ("object", "uninspected_embedded_content"),
                            ("canvas", "visual_only_regions")):
            with self.subTest(tag=tag):
                self.capture.reset_mock()
                self.semantic.return_value = scene(base + [{
                    "id": "region", "role": "generic", "tag": tag, "name": "Workspace",
                    "visible": True, "actionable": False,
                }])
                result = self.observe()
                self.assertIn(reason, result["coverage_gaps"])
                self.capture.assert_called_once()

    def test_source_coverage_gaps_survive_bounded_map(self):
        self.semantic.return_value = scene(coverage_gaps=["visual_only_regions"])
        result = self.observe()
        self.assertIn("visual_only_regions", result["coverage_gaps"])
        self.capture.assert_called_once()

    def test_hidden_visual_region_does_not_require_screenshot(self):
        base = json.loads(scene()[10:])["nodes"]
        self.semantic.return_value = scene(base + [{"id": "region", "tag": "canvas", "visible": False}])
        self.observe()
        self.capture.assert_not_called()

    def test_denied_screen_backend_keeps_metadata_without_input_authority(self):
        self.registry.permissions.deny_tools.add("screen_observe")
        result = self.observe(visual="always")
        self.capture.assert_not_called()
        self.assertEqual(result["errors"][0]["source"], "vision")
        self.assertFalse(result["scene_bound"])

    def test_no_successful_source_fails_instead_of_claiming_observation(self):
        self.registry.permissions.deny_tools.update({"screen_observe", "interaction_scene"})
        with self.assertRaisesRegex(RuntimeError, "No perception source succeeded"):
            self.observe()
        self.semantic.assert_not_called()
        self.capture.assert_not_called()

    def test_cancellation_during_semantics_never_starts_fallback_capture(self):
        self.semantic.side_effect = RuntimeError("Emergency stop is active")
        self.cancel.side_effect = [None, RuntimeError("Emergency stop is active")]
        with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
            self.observe()
        self.capture.assert_not_called()

    def test_focus_change_between_reads_rejects_mixed_state(self):
        self.foreground.side_effect = [42, 43]
        with self.assertRaisesRegex(RuntimeError, "Foreground changed"):
            self.observe(visual="always")
        self.capture.assert_not_called()

    def test_focus_change_after_capture_cannot_return_coordinate_authority(self):
        self.foreground.side_effect = [42, 42, 43]
        with self.assertRaisesRegex(RuntimeError, "Foreground changed"):
            self.observe(visual="always")

    def test_background_window_is_not_claimed_to_match_screen(self):
        self.semantic.return_value = scene(context={"hwnd": 77, "title": "Background", "foreground": False})
        result = self.observe(visual="always")
        self.assertEqual(result["alignment"], "separate_surfaces")
        self.assertEqual(result["foreground_hwnd"], 42)

    def test_unknown_window_identities_are_not_claimed_to_be_aligned(self):
        self.semantic.return_value = scene(context={"title": "Unknown"})
        self.capture.return_value = 'VERIFIED: {"path":"fixture.jpg","stable":false,"scene_bound":false}'
        result = self.observe(visual="always")
        self.assertEqual(result["alignment"], "separate_surfaces")

    def test_unstable_screenshot_is_not_promoted_to_stable(self):
        self.capture.return_value = 'VERIFIED: {"path":"fixture.jpg","stable":false,"scene_bound":true}'
        result = self.observe(visual="always")
        self.assertFalse(result["stable"])

    def test_large_nodes_omitted_not_truncated_into_wrong_target(self):
        self.semantic.return_value = scene([
            {"id": "large", "name": "x" * 10_000, "target": {"target": "x" * 10_000}},
            {"id": "small", "name": "Continue", "target": {"target": "Continue"}, "actionable": True},
        ])
        result = self.observe(visual="never")
        self.assertEqual(result["semantic"]["omitted_node_count"], 1)
        self.assertEqual(result["semantic"]["nodes"][0]["id"], "small")
        self.assertTrue(result["semantic"]["truncated"])

    def test_payload_budget_and_count_are_bounded(self):
        self.semantic.return_value = scene([
            {"id": f"node{i}", "role": "Button", "name": "x" * 1800,
             "target": {"target": "x" * 1800}, "actionable": True}
            for i in range(250)
        ])
        result = self.observe(visual="never", max_controls=250)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 100_000)
        self.assertGreater(result["semantic"]["omitted_node_count"], 0)

    def test_dialog_alert_loading_and_disabled_cues_are_explicit(self):
        self.semantic.return_value = scene([
            {"id": "modal", "role": "dialog", "name": "Save file"},
            {"id": "alert", "role": "alert", "name": "Connection lost"},
            {"id": "busy", "role": "ProgressBar", "name": "Download"},
            {"id": "btn", "role": "Button", "name": "Save", "actionable": True, "enabled": False},
            {"id": "hidden", "role": "dialog", "name": "Hidden", "visible": False},
        ])
        result = self.observe(visual="never")
        self.assertEqual([row["id"] for row in result["signals"]["dialogs"]], ["modal"])
        self.assertEqual(result["signals"]["alerts"][0]["name"], "Connection lost")
        self.assertEqual(result["signals"]["loading_indicators"][0]["id"], "busy")
        self.assertEqual(result["signals"]["disabled_controls"], 1)

    def test_schema_rejects_invalid_options_before_observation(self):
        for args in ({"visual": "sometimes"}, {"max_controls": 0}, {"settle_ms": 1001}, {"unknown": True}):
            self.assertTrue(self.registry.execute("computer_observe", args).startswith("ERROR"))
        self.semantic.assert_not_called()
        self.capture.assert_not_called()

    def test_registry_denial_is_authoritative(self):
        self.registry.permissions.deny_tools.add("computer_observe")
        self.assertTrue(self.registry.execute("computer_observe", {}).startswith("PERMISSION_DENIED"))
        self.semantic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
