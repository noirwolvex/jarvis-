import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.rust_engine import RustDaemonClient, RustEngineConfig, RustEngineUnavailable, register_rust_engine_tools
from core.tools import ToolRegistry
from core.advanced_tools import register_advanced_tools


class PointerTimingTests(unittest.TestCase):
    def setUp(self):
        self.client = RustDaemonClient(RustEngineConfig(
            host="127.0.0.1", port=7443, server_name="localhost",
            ca_path=Path("fixture"), client_cert_path=Path("fixture"), client_key_path=Path("fixture"),
            observe_capabilities={0: "observe"}, input_capabilities={0: "input"}))
        self.state = {"input_features": ["timed_pointer_move"],
            "foreground": {"hwnd": 123, "process_id": 42, "title": "fixture"},
            "displays": [{"id": 0, "x": -1920, "y": 0, "width": 1920, "height": 1080}]}

    def test_duration_reaches_authenticated_rust_request(self):
        with patch.object(self.client, "_input_context_for_display", return_value=(self.state["foreground"], {"id": "frame"})), \
             patch.object(self.client, "_request", return_value={"executed": True}) as request:
            self.client.pointer_move(-100, 200, self.state, duration=.125)
        self.assertEqual(request.call_args.args[0]["duration_ms"], 125)
        self.assertEqual(request.call_args.args[1], "input")
        self.assertTrue(request.call_args.kwargs["mutating"])

    def test_old_daemon_supports_instant_but_cannot_silently_discard_duration(self):
        self.state.pop("input_features")
        with patch.object(self.client, "_request") as request, \
             patch.object(self.client, "_input_context_for_display", return_value=(self.state["foreground"], {"id": "frame"})) as capture:
            with self.assertRaises(RustEngineUnavailable):
                self.client.pointer_move(-100, 200, self.state, duration=.1)
            request.assert_not_called()
            capture.assert_not_called()
            self.client.pointer_move(-100, 200, self.state)
            self.assertNotIn("duration_ms", request.call_args.args[0])

    def test_invalid_durations_never_capture_or_dispatch(self):
        with patch.object(self.client, "_request") as request:
            for duration in [-1, 2.001, float("nan"), float("inf")]:
                with self.assertRaises(ValueError):
                    self.client.pointer_move(-100, 200, self.state, duration=duration)
        request.assert_not_called()

    def test_desktop_tool_preserves_requested_timing(self):
        registry = ToolRegistry()
        register_advanced_tools(registry)
        registry.permissions.check = Mock(return_value=(True, "fixture"))
        client = Mock()
        client.pointer_move.return_value = {"executed": True, "simulation": False}
        register_rust_engine_tools(registry)
        with patch("core.rust_engine._preflight", return_value=(client, self.state)):
            result = registry.execute("desktop_move", {"x": -100, "y": 200, "duration": .08})
        self.assertTrue(result.startswith("RUST_EXECUTED:"), result)
        client.pointer_move.assert_called_once_with(-100, 200, self.state, duration=.08)

    def test_default_move_is_adaptive_and_remains_compatible_with_older_daemons(self):
        registry = ToolRegistry()
        register_advanced_tools(registry)
        registry.permissions.check = Mock(return_value=(True, "fixture"))
        client = Mock()
        client._display_for_point.side_effect = RustDaemonClient._display_for_point
        client.pointer_move.return_value = {"executed": True, "simulation": False}
        register_rust_engine_tools(registry)
        for pointer, expected_duration in [({"x": -200, "y": 200}, .05), ({"x": 200, "y": 200}, 0), (None, 0)]:
            self.state["pointer"] = pointer
            with patch("core.rust_engine._preflight", return_value=(client, self.state)):
                result = registry.execute("desktop_move", {"x": -100, "y": 200})
            self.assertTrue(result.startswith("RUST_EXECUTED:"), result)
            self.assertEqual(client.pointer_move.call_args.kwargs["duration"], expected_duration)
        self.state.pop("input_features")
        self.state["pointer"] = {"x": -200, "y": 200}
        with patch("core.rust_engine._preflight", return_value=(client, self.state)):
            registry.execute("desktop_move", {"x": -100, "y": 200})
        self.assertEqual(client.pointer_move.call_args.kwargs["duration"], 0)


if __name__ == "__main__":
    unittest.main()
