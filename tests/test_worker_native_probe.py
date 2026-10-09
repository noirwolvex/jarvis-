import unittest
from unittest.mock import Mock, patch

from core import full_access_worker as worker


class NativeProbeBindingTests(unittest.TestCase):
    def test_foreground_replacement_never_receives_diagnostic_typing(self):
        for foreground in ({"hwnd": 6, "process_id": 2}, {"hwnd": 5, "process_id": 3}, None):
            with self.subTest(foreground=foreground):
                client = Mock()
                with patch.dict(worker.os.environ, {"JARVIS_RUST_LIVE_PROBE": "1"}), \
                     patch("core.rust_engine.native_engine_mode", return_value="rust"), \
                     patch("core.rust_engine._preflight", return_value=(client, {"foreground": foreground})):
                    with self.assertRaisesRegex(RuntimeError, "lost foreground"):
                        worker._native_input_probe({"probe": {"kind": "type_text", "text": "fixture",
                            "expected_window": {"hwnd": 5, "process_id": 2}}})
                client.type_text.assert_not_called()

    def test_invalid_or_missing_binding_never_dispatches(self):
        for expected in (None, {}, {"hwnd": True, "process_id": 2}, {"hwnd": 5, "process_id": 0}):
            with self.subTest(expected=expected):
                client = Mock()
                with patch.dict(worker.os.environ, {"JARVIS_RUST_LIVE_PROBE": "1"}), \
                     patch("core.rust_engine.native_engine_mode", return_value="rust"), \
                     patch("core.rust_engine._preflight", return_value=(client, {})):
                    with self.assertRaisesRegex(ValueError, "exact qualification window"):
                        worker._native_input_probe({"probe": {"kind": "click", "x": 1, "y": 2,
                            "expected_window": expected}})
                client.click.assert_not_called()

    def test_exact_bound_window_receives_unicode(self):
        client = Mock()
        client.type_text.return_value = {"executed": True, "simulation": False}
        binding = {"hwnd": 5, "process_id": 2}
        status = {"foreground": binding}
        with patch.dict(worker.os.environ, {"JARVIS_RUST_LIVE_PROBE": "1"}), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"), \
             patch("core.rust_engine._preflight", return_value=(client, status)):
            result = worker._native_input_probe({"probe": {"kind": "type_text", "text": "\u0645\u0631\u062d\u0628\u0627",
                "expected_window": binding}})
        client.type_text.assert_called_once_with("\u0645\u0631\u062d\u0628\u0627", status)
        self.assertEqual(result["backend"], "rust")

    def test_normal_worker_does_not_expose_probe(self):
        with patch.dict(worker.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                worker._native_input_probe({"probe": {"kind": "type_text", "text": "fixture"}})
