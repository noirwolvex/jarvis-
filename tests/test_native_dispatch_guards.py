import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.desktop_input import InputDeliveryError
from core.native_ui_input import ui_type_native
from core.rust_engine import RustDaemonClient, RustEngineConfig, RustEngineUnavailable
from test_semantic_ui_tools import _Editor, _Window


class NativeDispatchGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = RustDaemonClient(RustEngineConfig(
            "127.0.0.1", 7443, "localhost", Path("ca"), Path("cert"), Path("key"),
            {0: "observe-0", 1: "observe-1"}, {0: "input-0", 1: "input-1"},
        ))
        self.socket = Mock()
        self.client._socket = self.socket
        self.client._session = "fixture"
        self.status = {
            "foreground": {"hwnd": 123, "process_id": 42, "title": "Demo"},
            "displays": [
                {"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080},
                {"id": 1, "x": -1920, "y": 0, "width": 1920, "height": 1080},
            ],
            "pointer": {"x": -300, "y": 100},
        }
        self.editor = _Editor()
        self.editor.has_keyboard_focus = Mock(return_value=True)
        self.window = _Window([self.editor])
        for item in (
            patch.object(ui, "_window", return_value=self.window),
            patch.object(ui, "_focus_window", return_value=123),
            patch.object(ui, "_guard_foreground"),
            patch("core.rust_engine._preflight", return_value=(self.client, self.status)),
            patch("core.rust_engine.native_engine_mode", return_value="auto"),
            patch.object(self.client, "_foreground_center", return_value=(300, 100)),
        ):
            item.start()
            self.addCleanup(item.stop)

    def reply(self, sock):
        request = json.loads(sock.sendall.call_args.args[0][4:])
        return {"type": "result", "request_id": request["request_id"], "ok": True,
                "data": {"executed": True, "simulation": False}}

    def test_scroll_uses_pointer_monitor_and_reuses_frame_for_rapid_scroll(self):
        with patch.object(self.client, "capture", return_value={"frame": {"id": "frame-1"}}) as capture, \
             patch.object(self.client, "_read_frame", side_effect=self.reply):
            self.client.scroll(64, self.status)
            self.client.scroll(-8, self.status)
        capture.assert_called_once_with(1)
        requests = [json.loads(call.args[0][4:]) for call in self.socket.sendall.call_args_list]
        self.assertEqual([request["capability_id"] for request in requests], ["input-1", "input-1"])
        self.assertEqual([request["action"]["display_id"] for request in requests], [1, 1])

    def test_scroll_refuses_missing_pointer_or_outside_display_before_capture(self):
        for pointer in (None, {}, {"x": True, "y": 100}, {"x": 5000, "y": 100}):
            with self.subTest(pointer=pointer), \
                 patch.object(self.client, "capture") as capture:
                self.status["pointer"] = pointer
                with self.assertRaises(ValueError):
                    self.client.scroll(8, self.status)
                capture.assert_not_called()
                self.socket.sendall.assert_not_called()

    def test_scroll_cannot_borrow_another_monitor_grant(self):
        for missing in ("observe_capabilities", "input_capabilities"):
            with self.subTest(missing=missing), \
                 patch.dict(getattr(self.client.config, missing), {0: "fixture"}, clear=True), \
                 patch.object(self.client, "capture") as capture:
                with self.assertRaisesRegex(ValueError, "authorized displays"):
                    self.client.scroll(8, self.status)
                capture.assert_not_called()
                self.socket.sendall.assert_not_called()

    def test_editor_changes_during_capture_block_both_semantic_typing_paths(self):
        for native in (True, False):
            for change in ("focus", "draft", "geometry"):
                with self.subTest(native=native, change=change):
                    self.editor = _Editor()
                    self.editor.has_keyboard_focus = Mock(return_value=True)
                    self.window._controls = [self.editor]
                    self.editor._owner = self.window
                    if not native:
                        del self.editor.iface_value
                    self.client._keyboard_frame_cache = None
                    def capture(display_id):
                        if change == "focus":
                            self.editor.has_keyboard_focus.return_value = False
                        elif change == "draft":
                            self.editor.value = "user draft"
                        else:
                            self.editor._rect.left += 10
                        return {"frame": {"id": "frame-1"}}
                    with patch.object(self.client, "capture", side_effect=capture) as observed, \
                         patch.object(ui, "paste_text") as fallback:
                        with self.assertRaises(InputDeliveryError):
                            (ui_type_native if native else ui.ui_type)("fixture")
                        observed.assert_called_once()
                        fallback.assert_not_called()
                    self.socket.sendall.assert_not_called()
                    self.assertEqual(self.client._sequence, 0)

    def test_semantic_click_rechecks_geometry_owner_and_generation_after_capture(self):
        from test_semantic_ui_tools import _Control
        for change in ("geometry", "owner", "generation"):
            with self.subTest(change=change):
                control = _Control("Open", "Button")
                control._owner = self.window
                self.window._controls = [control]
                control.click_input = Mock()
                def capture(display_id):
                    if change == "geometry":
                        control._rect.left += 10
                    elif change == "owner":
                        control._owner = Mock(handle=456)
                    else:
                        ui._SNAPSHOTS.invalidate(123)
                    return {"frame": {"id": "frame-1"}}
                with patch.object(self.client, "capture", side_effect=capture):
                    with self.assertRaises(InputDeliveryError):
                        ui.ui_activate("Open")
                control.click_input.assert_not_called()
                self.socket.sendall.assert_not_called()
                self.assertEqual(self.client._sequence, 0)

    def test_semantic_click_sends_once_after_capture_and_live_guard(self):
        from test_semantic_ui_tools import _Control
        control = _Control("Open", "Button")
        control._owner = self.window
        self.window._controls = [control]
        with patch.object(self.client, "capture", return_value={"frame": {"id": "frame-1"}}), \
             patch.object(self.client, "_read_frame", side_effect=self.reply):
            result = ui.ui_activate("Open")
        self.assertTrue(result.startswith("DELIVERED:"), result)
        self.socket.sendall.assert_called_once()
        action = json.loads(self.socket.sendall.call_args.args[0][4:])["action"]
        self.assertEqual((action["kind"], action["x"], action["y"]), ("click_button", 250, 50))

    def test_submit_focus_change_during_capture_never_sends_enter_or_falls_back(self):
        def capture(display_id):
            self.editor.has_keyboard_focus.return_value = False
            return {"frame": {"id": "frame-1"}}
        with patch.object(self.client, "capture", side_effect=capture), \
             patch("core.tools._desktop_press") as fallback:
            with self.assertRaises(InputDeliveryError):
                ui.ui_type("fixture", submit=True)
        self.assertEqual(self.editor.value, "fixture")
        fallback.assert_not_called()
        self.socket.sendall.assert_not_called()

    def test_guard_runs_after_reconnection_without_consuming_sequence_on_rejection(self):
        self.client._socket = None
        events = []
        def connect():
            events.append("connected")
            self.client._socket = self.socket
            self.client._session = "renewed"
        def guard():
            events.append("guarded")
            raise RustEngineUnavailable("target changed")
        with patch.object(self.client, "_connect", side_effect=connect):
            with self.assertRaises(InputDeliveryError):
                self.client._request({"kind": "hotkey"}, mutating=True, before_dispatch=guard)
        self.assertEqual(events, ["connected", "guarded"])
        self.assertEqual(self.client._sequence, 0)
        self.socket.sendall.assert_not_called()

    def test_semantic_unicode_input_succeeds_through_real_binding_and_dispatch_guards(self):
        text = "hello مرحبا 😀"
        for native in (True, False):
            with self.subTest(native=native):
                self.editor = _Editor()
                self.window._controls = [self.editor]
                self.editor._owner = self.window
                if not native:
                    del self.editor.iface_value
                self.socket.reset_mock()
                self.client._keyboard_frame_cache = None
                def deliver(payload):
                    self.editor.value = json.loads(payload[4:])["action"]["text"]
                self.socket.sendall.side_effect = deliver
                with patch.object(self.client, "capture", return_value={"frame": {"id": "frame-1"}}), \
                     patch.object(self.client, "_read_frame", side_effect=self.reply):
                    result = (ui_type_native if native else ui.ui_type)(text)
                self.assertTrue(result.startswith("VERIFIED:"), result)
                self.assertEqual(self.editor.value, text)
                self.socket.sendall.assert_called_once()

    def test_native_typing_then_submit_preserves_binding_and_uses_one_capture(self):
        del self.editor.iface_value
        actions = []
        def deliver(payload):
            action = json.loads(payload[4:])["action"]
            actions.append(action["kind"])
            self.editor.value = action["text"] if action["kind"] == "type_text" else ""
        self.socket.sendall.side_effect = deliver
        with patch.object(self.client, "capture", return_value={"frame": {"id": "frame-1"}}) as capture, \
             patch.object(self.client, "_read_frame", side_effect=self.reply):
            result = ui.ui_type("fixture", submit=True)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual(actions, ["type_text", "hotkey"])
        self.assertEqual(self.editor.value, "")
        capture.assert_called_once()

    def test_extra_state_invalidation_during_capture_blocks_delivery(self):
        def capture(display_id):
            ui._SNAPSHOTS.invalidate(123)
            return {"frame": {"id": "frame-1"}}
        with patch.object(self.client, "capture", side_effect=capture), \
             self.assertRaisesRegex(InputDeliveryError, "target changed"):
            ui_type_native("fixture")
        self.socket.sendall.assert_not_called()

    def test_native_challenge_after_capture_retains_manual_intervention_signal(self):
        from core.native_ui_input import register_native_ui_input_tools
        from core.tools import ToolRegistry
        registry = ToolRegistry()
        registry.permissions.set_access_mode("full")
        register_native_ui_input_tools(registry)
        def capture(display_id):
            ui._guard_foreground.side_effect = ui.BrowserBoundaryError("BROWSER_ACTION_BLOCKED: human verification")
            return {"frame": {"id": "frame-1"}}
        with patch.object(self.client, "capture", side_effect=capture), \
             patch.object(ui, "paste_text") as fallback:
            result = registry.execute("ui_type_native", {"text": "fixture"}, approved=True)
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED:"), result)
        fallback.assert_not_called()
        self.socket.sendall.assert_not_called()

    def test_guard_runs_for_each_mutation_even_when_capture_is_reused(self):
        events = []
        def capture(display_id):
            events.append("capture")
            return {"frame": {"id": "frame-1"}}
        self.socket.sendall.side_effect = lambda payload: events.append("send")
        with patch.object(self.client, "capture", side_effect=capture), \
             patch.object(self.client, "_read_frame", side_effect=self.reply):
            self.client.type_text("fixture", self.status, before_dispatch=lambda: events.append("type guard"))
            self.client.hotkey(["enter"], self.status, before_dispatch=lambda: events.append("submit guard"))
        self.assertEqual(events, ["capture", "type guard", "send", "submit guard", "send"])


if __name__ == "__main__":
    unittest.main()
