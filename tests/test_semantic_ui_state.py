from __future__ import annotations

import unittest
from unittest.mock import patch

from core.ui_state import SnapshotCache, cancellable_delay, wait_until


class SemanticUiStateTests(unittest.TestCase):
    def test_target_binding_reduces_provider_reads_without_losing_identity_evidence(self):
        from scripts.benchmark_ui_target_binding import measure
        result = measure()
        self.assertTrue(result["identical_binding"])
        self.assertLess(result["fixture_provider_reads_after"], result["fixture_provider_reads_before"])

    def test_changed_window_process_still_invalidates_optimized_target_binding(self):
        from scripts.benchmark_ui_target_binding import Control
        from core import semantic_ui_tools as ui
        from core.desktop_input import InputDeliveryError
        window, editor = Control([0], [1]), Control([0], [2])
        editor.top_level_parent = lambda: window
        binding = ui._target_binding(window, editor)
        window.element_info.values["process_id"] = 99
        with patch.object(ui, "_guard_foreground"):
            with self.assertRaises(InputDeliveryError):
                ui._validate_target(window, editor, binding)

    def test_wait_returns_immediately_on_ready_state(self):
        with patch("core.ui_state.time.sleep") as sleep:
            self.assertEqual(wait_until(lambda: "ready"), "ready")
        sleep.assert_not_called()

    def test_wait_reobserves_only_and_respects_timeout(self):
        with patch("core.ui_state.time.monotonic", side_effect=[1.0, 1.1]), patch("core.ui_state.time.sleep") as sleep:
            with self.assertRaises(TimeoutError):
                wait_until(lambda: False, timeout=0.05)
        sleep.assert_not_called()

    def test_cancel_interrupts_wait_and_delay_before_sleep(self):
        for operation in (lambda: wait_until(lambda: False), lambda: cancellable_delay(3)):
            with self.subTest(operation=operation), patch("core.ui_state.check_cancelled", side_effect=RuntimeError("Emergency stop")), \
                 patch("core.ui_state.time.sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                    operation()
                sleep.assert_not_called()

    def test_cache_scope_invalidation_retains_other_window(self):
        cache = SnapshotCache()
        cache.put((1, "query"), {"controls": [{"name": "One"}]}, cache.generation)
        cache.put((2, "query"), {"controls": [{"name": "Two"}]}, cache.generation)
        cache.invalidate(1)
        self.assertIsNone(cache.get((1, "query")))
        self.assertEqual(cache.get((2, "query"))["controls"][0]["name"], "Two")

    def test_cache_deepcopy_ttl_capacity_and_generation(self):
        cache = SnapshotCache(ttl=0.25, capacity=1)
        with patch("core.ui_state.time.monotonic", return_value=1.0):
            cache.put((1,), {"controls": [{"name": "One"}]}, cache.generation)
            cached = cache.get((1,))
            cached["controls"][0]["name"] = "altered"
            self.assertEqual(cache.get((1,))["controls"][0]["name"], "One")
            cache.put((2,), {}, cache.generation)
            self.assertIsNone(cache.get((1,)))
            generation = cache.generation
            cache.invalidate()
            cache.put((3,), {}, generation)
            self.assertIsNone(cache.get((3,)))
            cache.put((4,), {}, cache.generation)
        with patch("core.ui_state.time.monotonic", return_value=1.26):
            self.assertIsNone(cache.get((4,)))
