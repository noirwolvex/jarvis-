from __future__ import annotations

import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from core.rust_engine import (
    RustDaemonClient,
    RustEngineConfig,
    RustEngineExecutionError,
    RustEngineUnavailable,
    native_engine_mode,
)


class _FailingSocket:
    def __init__(self) -> None:
        self.closed = False

    def sendall(self, _payload: bytes) -> None:
        raise OSError("fixture transport failure")

    def close(self) -> None:
        self.closed = True


class RustEngineTests(unittest.TestCase):
    def test_engine_mode_is_explicit_and_rejects_unknown_values(self) -> None:
        self.assertEqual(native_engine_mode({}), "auto")
        self.assertEqual(native_engine_mode({"JARVIS_NATIVE_ENGINE": "python"}), "python")
        self.assertEqual(native_engine_mode({"JARVIS_NATIVE_ENGINE": "RUST"}), "rust")
        with self.assertRaises(ValueError):
            native_engine_mode({"JARVIS_NATIVE_ENGINE": "remote"})

    def test_config_is_loopback_only_and_supports_per_display_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("ca.pem", "client.pem", "client-key.pem"):
                (root / name).write_text("fixture", encoding="utf-8")
            env = {
                "JARVIS_NATIVE_ENGINE": "rust",
                "JARVIS_REPO_ROOT": str(root),
                "JARVIS_DAEMON_HOST": "127.0.0.1",
                "JARVIS_DAEMON_PORT": "7443",
                "JARVIS_DAEMON_SERVER_NAME": "localhost",
                "JARVIS_DAEMON_CA": "ca.pem",
                "JARVIS_DAEMON_CLIENT_CERT": "client.pem",
                "JARVIS_DAEMON_CLIENT_KEY": "client-key.pem",
                "JARVIS_DAEMON_OBSERVE_CAPABILITY": "observe-0",
                "JARVIS_DAEMON_INPUT_CAPABILITY": "input-0",
                "JARVIS_DAEMON_OBSERVE_CAPABILITIES_JSON": '{"1":"observe-1"}',
                "JARVIS_DAEMON_INPUT_CAPABILITIES_JSON": '{"1":"input-1"}',
            }
            config = RustEngineConfig.from_env(env)
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config.observe_capabilities, {0: "observe-0", 1: "observe-1"})
            self.assertEqual(config.input_capabilities, {0: "input-0", 1: "input-1"})
            self.assertEqual(config.capability("observe", 1), "observe-1")
            self.assertEqual(config.capability("input", 0), "input-0")

            hostile = dict(env)
            hostile["JARVIS_DAEMON_HOST"] = "10.0.0.5"
            with self.assertRaises(ValueError):
                RustEngineConfig.from_env(hostile)

    def test_auto_mode_without_tls_material_keeps_python_available(self) -> None:
        self.assertIsNone(RustEngineConfig.from_env({"JARVIS_NATIVE_ENGINE": "auto"}))
        with self.assertRaises(RustEngineUnavailable):
            RustEngineConfig.from_env({"JARVIS_NATIVE_ENGINE": "rust"})

    def test_display_selection_handles_negative_multi_monitor_coordinates(self) -> None:
        status = {
            "displays": [
                {"id": 7, "x": -1920, "y": 0, "width": 1920, "height": 1080, "scale": 1.0},
                {"id": 9, "x": 0, "y": 0, "width": 2560, "height": 1440, "scale": 1.25},
            ]
        }
        self.assertEqual(RustDaemonClient._display_for_point(status, -10, 100)["id"], 7)
        self.assertEqual(RustDaemonClient._display_for_point(status, 2000, 100)["id"], 9)
        with self.assertRaises(RustEngineUnavailable):
            RustDaemonClient._display_for_point(status, 5000, 100)

    def test_expanded_client_actions_bind_fresh_frame_and_input_capability(self) -> None:
        config = RustEngineConfig(
            host="127.0.0.1",
            port=7443,
            server_name="localhost",
            ca_path=Path("ca.pem"),
            client_cert_path=Path("client.pem"),
            client_key_path=Path("client-key.pem"),
            observe_capabilities={0: "observe-0"},
            input_capabilities={0: "input-0"},
        )
        client = RustDaemonClient(config)
        status = {
            "displays": [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080}],
            "foreground": {"process_id": 42, "title": "Fixture"},
        }
        foreground = {"process_id": 42, "title": "Fixture"}
        frame = {"id": "frame-1"}
        with patch.object(client, "_input_context_for_display", return_value=(foreground, frame)), \
             patch.object(client, "_request", return_value={"executed": True}) as request:
            client.click_button(50, 60, "right", 2, status)
            action = request.call_args.args[0]
            self.assertEqual(action["kind"], "click_button")
            self.assertEqual(action["button"], "right")
            self.assertEqual(action["clicks"], 2)
            self.assertEqual(request.call_args.args[1], "input-0")
            self.assertTrue(request.call_args.kwargs["mutating"])

            client.hotkey(["ctrl", "l"], status)
            action = request.call_args.args[0]
            self.assertEqual(action["kind"], "hotkey")
            self.assertEqual(action["keys"], ["ctrl", "l"])

            client.drag(1, 2, 20, 30, 0.25, "left", status)
            action = request.call_args.args[0]
            self.assertEqual(action["kind"], "drag")
            self.assertEqual(action["duration_ms"], 250)

    def test_strict_rust_overlay_routes_atomic_hotkey_and_blocks_stateful_hold(self) -> None:
        from core.advanced_tools import register_advanced_tools
        from core.desktop_control_tools import register_desktop_control_tools
        from core.rust_engine import register_rust_engine_tools
        from core.tools import ToolRegistry

        registry = ToolRegistry()
        register_advanced_tools(registry)
        register_desktop_control_tools(registry)
        client = Mock()
        client.hotkey.return_value = {"executed": True, "simulation": False}
        status = {"native_input": True}

        with patch.dict("os.environ", {"JARVIS_NATIVE_ENGINE": "rust"}, clear=False), \
             patch("core.rust_engine._preflight", return_value=(client, status)):
            register_rust_engine_tools(registry)
            result = registry.execute(
                "desktop_hotkey",
                {"keys": ["ctrl", "l"]},
                approved=True,
            )
            self.assertTrue(result.startswith("RUST_EXECUTED:"))
            client.hotkey.assert_called_once_with(["ctrl", "l"], status)

            blocked = registry.execute(
                "desktop_key_down",
                {"key": "ctrl"},
                approved=True,
            )
            self.assertTrue(blocked.startswith("ERROR executing desktop_key_down:"))
            self.assertIn("disabled in strict Rust mode", blocked)

    def test_mutating_transport_failure_is_uncertain_and_never_fallback_safe(self) -> None:
        config = RustEngineConfig(
            host="127.0.0.1",
            port=7443,
            server_name="localhost",
            ca_path=Path("ca.pem"),
            client_cert_path=Path("client.pem"),
            client_key_path=Path("client-key.pem"),
            observe_capabilities={},
            input_capabilities={},
        )
        client = RustDaemonClient(config)
        socket = _FailingSocket()
        client._socket = socket  # type: ignore[assignment]
        client._session = "fixture-session"
        with self.assertRaises(RustEngineExecutionError):
            client._request({"kind": "emergency_stop"}, mutating=True)
        self.assertTrue(socket.closed)

    def test_read_only_transport_failure_can_be_treated_as_unavailable(self) -> None:
        config = RustEngineConfig(
            host="127.0.0.1",
            port=7443,
            server_name="localhost",
            ca_path=Path("ca.pem"),
            client_cert_path=Path("client.pem"),
            client_key_path=Path("client-key.pem"),
            observe_capabilities={},
            input_capabilities={},
        )
        client = RustDaemonClient(config)
        socket = _FailingSocket()
        client._socket = socket  # type: ignore[assignment]
        client._session = "fixture-session"
        with self.assertRaises(RustEngineUnavailable):
            client._request({"kind": "status"}, mutating=False)
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
