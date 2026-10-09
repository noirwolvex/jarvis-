from __future__ import annotations

import tempfile
import json
import socket
import threading
import unittest
from dataclasses import replace
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
    def fixture_client(self):
        client = RustDaemonClient(RustEngineConfig("127.0.0.1", 7443, "localhost",
            Path("ca"), Path("cert"), Path("key"), {}, {}))
        client._socket = Mock()
        client._session = "fixture"
        return client

    def test_idle_and_aged_sessions_renew_before_a_single_dispatch(self):
        for connected_at, last_activity, should_reconnect in [(100, 199, False), (100, 174, True), (-80, 199, True)]:
            with self.subTest(connected_at=connected_at, last_activity=last_activity):
                client = self.fixture_client()
                client._connected_at, client._last_activity = connected_at, last_activity
                def response(sock):
                    request = json.loads(sock.sendall.call_args.args[0][4:])
                    return {"type": "result", "request_id": request["request_id"], "ok": True, "data": {"fixture": True}}
                with patch("core.rust_engine.time.monotonic", return_value=200), \
                     patch.object(client, "_connect") as reconnect, \
                     patch.object(client, "_read_frame", side_effect=response):
                    self.assertEqual(client.status(), {"fixture": True})
                self.assertEqual(reconnect.call_count, int(should_reconnect))
                client._socket.sendall.assert_called_once()

    def test_stop_remains_deliverable_when_local_cancellation_is_latched(self):
        client = self.fixture_client()
        control_socket = Mock()
        def connect(control):
            control._socket = control_socket
            control._session = "control"
        def response(sock):
            request = json.loads(sock.sendall.call_args.args[0][4:])
            return {"type": "result", "request_id": request["request_id"], "ok": True, "data": {"emergency_stopped": True}}
        with patch("core.process_control.check_cancelled", side_effect=RuntimeError("stopped")), \
             patch.object(RustDaemonClient, "_connect", connect), \
             patch.object(RustDaemonClient, "_read_frame", side_effect=response):
            self.assertTrue(client.emergency_stop()["emergency_stopped"])
        control_socket.sendall.assert_called_once()
        control_socket.close.assert_called_once()
        client._socket.sendall.assert_not_called()

    def test_stop_uses_control_connection_while_execution_lock_is_held(self):
        client = self.fixture_client()
        dispatched = threading.Event()
        results = []
        def request(control, action, **kwargs):
            self.assertIsNot(control, client)
            self.assertIs(control.config, client.config)
            self.assertEqual(action, {"kind": "emergency_stop"})
            self.assertTrue(kwargs["mutating"])
            dispatched.set()
            return {"emergency_stopped": True}
        with patch.object(RustDaemonClient, "_request", request):
            with client._lock:
                worker = threading.Thread(target=lambda: results.append(client.emergency_stop()), daemon=True)
                worker.start()
                delivered_while_locked = dispatched.wait(1)
            worker.join(1)
        self.assertTrue(delivered_while_locked, "Stop must not wait for the execution connection lock")
        self.assertEqual(results, [{"emergency_stopped": True}])

    def test_stop_closes_control_connection_after_uncertain_failure_without_retry(self):
        client = self.fixture_client()
        with patch.object(RustDaemonClient, "_request", side_effect=RustEngineExecutionError("uncertain")) as request, \
             patch.object(RustDaemonClient, "close") as close:
            with self.assertRaises(RustEngineExecutionError):
                client.emergency_stop()
        request.assert_called_once()
        close.assert_called_once()

    def test_cancelled_native_action_never_reaches_socket(self):
        client = self.fixture_client()
        with patch("core.process_control.check_cancelled", side_effect=RuntimeError("stopped")):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                client._request({"kind": "pointer_move"}, mutating=True)
        client._socket.sendall.assert_not_called()

    def test_connection_disables_nagle_without_relaxing_tls(self):
        client = self.fixture_client()
        with patch("core.rust_engine.ssl.create_default_context") as context, \
             patch("core.rust_engine.socket.create_connection") as connect, \
             patch.object(client, "_read_frame", return_value={"type": "hello", "protocol": 1,
                        "session": "fixture", "max_frame_bytes": 262144}):
            tls = context.return_value.wrap_socket.return_value
            tls.selected_alpn_protocol.return_value = "jarvis-execution/1"
            client._connect()
        connect.return_value.setsockopt.assert_called_once_with(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.assertIsNotNone(client._last_activity)

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


    def test_foreground_binding_is_hwnd_stable_and_allows_empty_title(self) -> None:
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
        self.assertEqual(
            client._foreground({"foreground": {"hwnd": 1001, "process_id": 42, "title": ""}}),
            {"hwnd": 1001, "process_id": 42, "title": ""},
        )
        with self.assertRaises(RustEngineUnavailable):
            client._foreground({"foreground": {"process_id": 42, "title": "Fixture"}})
        with self.assertRaises(RustEngineUnavailable):
            client._foreground({"foreground": {"hwnd": 0, "process_id": 42, "title": "Fixture"}})

    def test_keyboard_context_prefers_foreground_window_display(self) -> None:
        config = RustEngineConfig(
            host="127.0.0.1",
            port=7443,
            server_name="localhost",
            ca_path=Path("ca.pem"),
            client_cert_path=Path("client.pem"),
            client_key_path=Path("client-key.pem"),
            observe_capabilities={0: "observe-0", 1: "observe-1"},
            input_capabilities={0: "input-0", 1: "input-1"},
        )
        client = RustDaemonClient(config)
        status = {
            "displays": [
                {"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080},
                {"id": 1, "x": 1920, "y": 0, "width": 1920, "height": 1080},
            ],
            "foreground": {"hwnd": 1001, "process_id": 42, "title": "Fixture"},
        }
        frame = {"id": "frame-1"}
        with patch.object(client, "_foreground_center", return_value=(2200, 400)), \
             patch.object(client, "_input_context_for_display", return_value=(status["foreground"], frame)) as context:
            display_id, foreground, selected_frame = client._keyboard_input_context(status)
            second_display, second_foreground, second_frame = client._keyboard_input_context(status)
        self.assertEqual(display_id, 1)
        self.assertEqual(second_display, 1)
        self.assertEqual(foreground["hwnd"], 1001)
        self.assertEqual(second_foreground["hwnd"], 1001)
        self.assertEqual(selected_frame, frame)
        self.assertEqual(second_frame, frame)
        # A tight keyboard burst reuses one daemon capture; every action still carries
        # its own fresh foreground binding and short IPC request TTL.
        context.assert_called_once_with(status, 1)

    def test_keyboard_frame_cache_is_rejected_after_foreground_change(self) -> None:
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
        first = {
            "displays": [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080}],
            "foreground": {"hwnd": 1001, "process_id": 42, "title": "First"},
        }
        second = {
            "displays": first["displays"],
            "foreground": {"hwnd": 2002, "process_id": 84, "title": "Second"},
        }
        frames = [
            (first["foreground"], {"id": "frame-1"}),
            (second["foreground"], {"id": "frame-2"}),
        ]
        with patch.object(client, "_foreground_center", return_value=(500, 400)), \
             patch.object(client, "_input_context_for_display", side_effect=frames) as context:
            self.assertEqual(client._keyboard_input_context(first)[2]["id"], "frame-1")
            self.assertEqual(client._keyboard_input_context(second)[2]["id"], "frame-2")
        self.assertEqual(context.call_count, 2)

    def test_mouse_then_keyboard_reuses_frame_without_skipping_dispatch_guards(self):
        client = self.fixture_client()
        client.config = replace(client.config, observe_capabilities={0: "observe"}, input_capabilities={0: "input"})
        status = {"displays": [{"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080}],
                  "foreground": {"hwnd": 1001, "process_id": 42, "title": "Fixture"}}
        guard = Mock()
        clock = [100.0]
        with patch("core.rust_engine.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(client, "capture", return_value={"frame": {"id": "fresh"}}) as capture, \
                patch.object(client, "_foreground_center", return_value=(300, 100)), \
                patch.object(client, "status", return_value=status) as read_status, \
                patch.object(client, "_request", return_value={"executed": True}) as request:
            client.click(300, 100, before_dispatch=guard)
            client.type_text("draft", before_dispatch=guard)
            self.assertEqual(capture.call_count, 1)
            self.assertEqual(read_status.call_count, 2)
            self.assertEqual([item.kwargs["before_dispatch"] for item in request.call_args_list], [guard, guard])
            # Every new mouse action still captures, even inside the same burst.
            client.pointer_move(320, 100, status)
            self.assertEqual(capture.call_count, 2)
            client.type_text("more", status, before_dispatch=guard)
            self.assertEqual(capture.call_count, 2)
            clock[0] += 0.75
            client.type_text("expired", status, before_dispatch=guard)
            self.assertEqual(capture.call_count, 3)
            changed = {**status, "foreground": {**status["foreground"], "process_id": 43}}
            client.type_text("new process", changed, before_dispatch=guard)
            self.assertEqual(capture.call_count, 4)
            self.assertEqual(request.call_args.args[0]["foreground"]["process_id"], 43)

    def test_slow_mouse_capture_does_not_extend_keyboard_cache_lifetime(self):
        client = self.fixture_client()
        client.config = replace(client.config, observe_capabilities={0: "observe"}, input_capabilities={0: "input"})
        status = {"displays": [{"id": 0, "x": 0, "y": 0, "width": 100, "height": 100}],
                  "foreground": {"hwnd": 1001, "process_id": 42, "title": "Fixture"}}
        clock = [100.0]
        def capture(_display):
            clock[0] += 0.8
            return {"frame": {"id": "fresh"}}
        with patch("core.rust_engine.time.monotonic", side_effect=lambda: clock[0]), \
                patch.object(client, "capture", side_effect=capture) as observed, \
                patch.object(client, "_foreground_center", return_value=(10, 10)), \
                patch.object(client, "_request", return_value={"executed": True}):
            client.click(10, 10, status)
            client.type_text("draft", status)
        self.assertEqual(observed.call_count, 2)

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
            "foreground": {"hwnd": 1001, "process_id": 42, "title": "Fixture"},
        }
        foreground = {"hwnd": 1001, "process_id": 42, "title": "Fixture"}
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

    def test_atomic_overlay_requires_positive_native_delivery_acknowledgment(self) -> None:
        from core.execution_telemetry import input_not_dispatched
        from core.rust_engine import register_rust_engine_tools
        from core.tools import ToolRegistry
        registry = ToolRegistry()
        original = Mock(return_value="PYTHON_EXECUTED")
        registry.register(replace(registry._tools["desktop_type"], handler=original))
        register_rust_engine_tools(registry)
        client = Mock()
        for payload in ({}, {"executed": False, "simulation": False},
                        {"executed": True, "simulation": True}, {"executed": True}):
            with self.subTest(payload=payload), \
                 patch("core.rust_engine._preflight", return_value=(client, {})), \
                 patch("core.rust_engine.native_engine_mode", return_value="auto"):
                client.type_text.return_value = payload
                result = registry.execute("desktop_type", {"text": "fixture"}, approved=True)
            self.assertTrue(result.startswith("ERROR executing desktop_type:"), result)
            self.assertIn("did not confirm", result)
            self.assertFalse(input_not_dispatched(result))
            original.assert_not_called()

    def test_strict_atomic_preflight_failure_does_not_create_delivery_review(self) -> None:
        from core.execution_telemetry import input_not_dispatched
        from core.rust_engine import register_rust_engine_tools
        from core.tools import ToolRegistry
        registry = ToolRegistry()
        original = Mock(return_value="PYTHON_EXECUTED")
        registry.register(replace(registry._tools["desktop_type"], handler=original))
        register_rust_engine_tools(registry)
        with patch("core.rust_engine._preflight", side_effect=RustEngineUnavailable("Daemon disconnected")), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"):
            result = registry.execute("desktop_type", {"text": "fixture"}, approved=True)
        self.assertTrue(input_not_dispatched(result), result)
        original.assert_not_called()

    def test_auto_atomic_emergency_latch_cannot_fall_back_to_python(self) -> None:
        from core.execution_telemetry import input_not_dispatched
        from core.rust_engine import register_rust_engine_tools
        from core.tools import ToolRegistry
        registry = ToolRegistry()
        original = Mock(return_value="PYTHON_EXECUTED")
        registry.register(replace(registry._tools["desktop_type"], handler=original))
        register_rust_engine_tools(registry)
        client = Mock()
        client.status.return_value = {"emergency_stopped": True}
        with patch("core.rust_engine._client", return_value=client), \
             patch("core.rust_engine.native_engine_mode", return_value="auto"):
            result = registry.execute("desktop_type", {"text": "fixture"}, approved=True)
        self.assertTrue(input_not_dispatched(result), result)
        self.assertIn("emergency stop is latched", result)
        original.assert_not_called()
        client.type_text.assert_not_called()

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
