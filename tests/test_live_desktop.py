from __future__ import annotations

import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from core.live_desktop import LiveDesktopMonitor


class LiveDesktopTests(unittest.TestCase):
    def test_encode_time_cannot_make_an_old_capture_fresh(self):
        monitor = LiveDesktopMonitor(lambda: False, capture=lambda: (Image.new("RGB", (20, 20)), 1, (0, 0)))
        clock = [10.0]
        save = Image.Image.save
        def delayed_save(image, *args, **kwargs):
            clock[0] += 4
            return save(image, *args, **kwargs)
        with patch("core.live_desktop.time.monotonic", side_effect=lambda: clock[0]), \
             patch.object(Image.Image, "save", delayed_save):
            self.assertFalse(monitor.poll())
            self.assertIsNone(monitor.latest())

    def test_identical_pixels_cannot_authorize_changed_display_geometry(self):
        from core.desktop_observation import remember_signature, scene_matches
        remember_signature("geometry-fixture", Image.new("RGB", (192, 108), "blue"))
        frame = {"source_width": 1920, "source_height": 1080, "virtual_origin_x": 0, "virtual_origin_y": 0}
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (1280, 720), "blue")), \
             patch("core.vision_tools._virtual_origin", return_value=(0, 0)):
            self.assertFalse(scene_matches("geometry-fixture", frame))
        with patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (1920, 1080), "blue")), \
             patch("core.vision_tools._virtual_origin", return_value=(-1920, 0)):
            self.assertFalse(scene_matches("geometry-fixture", frame))

    def test_unchanged_scene_reuses_latest_frame_without_queue_or_new_emit(self):
        emit = Mock()
        capture = Mock(return_value=(Image.new("RGB", (1920, 1080), "blue"), 123, (-1920, 0)))
        monitor = LiveDesktopMonitor(lambda: False, emit, capture=capture)
        self.assertTrue(monitor.poll())
        self.assertFalse(monitor.poll())
        self.assertEqual((monitor.captures, monitor.changes, emit.call_count), (2, 1, 1))
        frame = monitor.latest()["frame"]
        self.assertEqual(frame["virtual_origin_x"], -1920)
        self.assertEqual(frame["width"], 960)
        self.assertFalse(frame["scene_bound"])
        self.assertFalse(frame["stable"])
        self.assertIn("not a stable coordinate authorization", monitor.model_message()["content"][0]["text"])
        monitor.stop()
        self.assertIsNone(monitor.latest())

    def test_busy_device_action_skips_capture_without_error(self):
        busy = threading.Event()
        capture = Mock(return_value=(Image.new("RGB", (100, 100)), 1, (0, 0)))
        monitor = LiveDesktopMonitor(lambda: False, capture=capture, busy=busy.is_set)
        busy.set()
        self.assertFalse(monitor.poll())
        capture.assert_not_called()
        self.assertEqual((monitor.captures, monitor.errors), (0, 0))
        busy.clear()
        self.assertTrue(monitor.poll())
        capture.assert_called_once()

    def test_scene_and_foreground_changes_replace_frame(self):
        blue, red = Image.new("RGB", (100, 100), "blue"), Image.new("RGB", (100, 100), "red")
        capture = Mock(side_effect=[(blue, 1, (0, 0)), (red, 1, (0, 0)), (red, 2, (0, 0))])
        monitor = LiveDesktopMonitor(lambda: False, capture=capture)
        for _ in range(3):
            self.assertTrue(monitor.poll())
        self.assertEqual(monitor.latest()["frame"]["foreground_hwnd"], 2)
        self.assertEqual(monitor.changes, 3)

    def test_capture_finishing_after_stop_cannot_publish(self):
        emit = Mock()
        monitor = LiveDesktopMonitor(lambda: False, emit)
        def capture():
            monitor.stop()
            return Image.new("RGB", (100, 100)), 1, (0, 0)
        monitor.capture = capture
        self.assertFalse(monitor.poll())
        emit.assert_not_called()
        self.assertIsNone(monitor.model_message())

    def test_stale_and_cancelled_observations_are_not_model_context(self):
        stopped = threading.Event()
        monitor = LiveDesktopMonitor(stopped.is_set, capture=lambda: (Image.new("RGB", (20, 20)), 1, (0, 0)))
        monitor.poll()
        with patch("core.live_desktop.time.monotonic", return_value=time.monotonic() + 10):
            self.assertIsNone(monitor.model_message())
        stopped.set()
        self.assertIsNone(monitor.latest())

    def test_failed_capture_discards_old_frame_without_model_calls(self):
        monitor = LiveDesktopMonitor(lambda: False, capture=lambda: (Image.new("RGB", (20, 20)), 1, (0, 0)))
        monitor.poll()
        def failed():
            monitor._stop.set()
            raise OSError("desktop unavailable")
        monitor.capture = failed
        monitor._run()
        self.assertIsNone(monitor._latest)
        self.assertEqual(monitor.errors, 1)


class MissionMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.permissions = SimpleNamespace(access_mode="full", deny_tools=set())
        self.agent = SimpleNamespace(reset=Mock(), _is_stopped=Mock(return_value=False),
            tools=SimpleNamespace(permissions=self.permissions), run=Mock(return_value="Done"),
            orchestrator=SimpleNamespace(current=None, summary=lambda: {}, _persist=Mock()))
        self.factory = patch("core.live_desktop.LiveDesktopMonitor").start()
        self.enabled = patch("core.live_desktop.enabled", return_value=True).start()
        self.release = patch("core.desktop_control_tools.release_held_inputs").start()
        patch("core.process_control.set_cancellation").start()
        self.addCleanup(patch.stopall)

    def run_mission(self, **kwargs):
        from core.full_access_bridge import run_agent_mission
        return run_agent_mission(self.agent, "fixture", **kwargs)

    def test_capture_requires_full_permission_callback_and_enabled_setting(self):
        self.run_mission()
        self.factory.assert_not_called()
        for mode, denied, enabled, stopped in (("standard", set(), True, False),
            ("full", {"screen_observe"}, True, False), ("full", set(), False, False),
            ("full", set(), True, True)):
            self.permissions.access_mode, self.permissions.deny_tools = mode, denied
            self.enabled.return_value, self.agent._is_stopped.return_value = enabled, stopped
            self.run_mission(observation_emit=Mock())
            self.factory.assert_not_called()

    def test_monitor_stops_at_mission_end_and_detects_permission_revocation(self):
        def execute(*args, **kwargs):
            cancelled = self.factory.call_args.args[0]
            self.assertFalse(cancelled())
            self.permissions.access_mode = "standard"
            self.assertTrue(cancelled())
            return "Done"
        self.agent.run.side_effect = execute
        self.run_mission(observation_emit=Mock())
        self.factory.return_value.start.assert_called_once()
        self.factory.return_value.stop.assert_called_once()
        self.release.assert_called_once()
        self.assertIsNone(self.agent.live_monitor)

    def test_mission_failure_stops_capture_and_releases_input(self):
        self.agent.run.side_effect = RuntimeError("provider failed")
        with self.assertRaisesRegex(RuntimeError, "provider failed"):
            self.run_mission(observation_emit=Mock())
        self.factory.return_value.stop.assert_called_once()
        self.release.assert_called_once()
        self.assertIsNone(self.agent.live_monitor)

    def test_checkpoint_failure_cannot_prevent_input_cleanup(self):
        self.agent.orchestrator.current = SimpleNamespace(metrics={})
        self.agent.orchestrator._persist.side_effect = OSError("disk full")
        with self.assertRaisesRegex(OSError, "disk full"):
            self.run_mission(observation_emit=Mock())
        self.factory.return_value.stop.assert_called_once()
        self.release.assert_called_once()
        self.assertIsNone(self.agent.live_monitor)


class ResponsiveInputTests(unittest.TestCase):
    def test_pointer_samples_have_no_pause_and_keep_foreground_binding(self):
        from core.desktop_control_tools import move_pointer
        fake = SimpleNamespace(position=Mock(side_effect=[SimpleNamespace(x=0, y=0), SimpleNamespace(x=-40, y=10)]), moveTo=Mock())
        with patch.dict(sys.modules, {"pyautogui": fake}), patch("core.desktop_control_tools._windows_only"), \
             patch("core.desktop_observation.foreground_identity", side_effect=[12, 12]), \
             patch("core.desktop_control_tools.time.sleep") as sleep:
            move_pointer(-40, 10, 0)
        fake.moveTo.assert_called_once_with(-40, 10, _pause=False)
        sleep.assert_not_called()

    def test_pointer_stops_without_moving_when_foreground_changes(self):
        from core.desktop_control_tools import move_pointer
        fake = SimpleNamespace(position=lambda: SimpleNamespace(x=0, y=0), moveTo=Mock())
        with patch.dict(sys.modules, {"pyautogui": fake}), patch("core.desktop_control_tools._windows_only"), \
             patch("core.desktop_observation.foreground_identity", side_effect=[12, 13]):
            with self.assertRaisesRegex(RuntimeError, "Foreground changed"):
                move_pointer(40, 10, 0)
        fake.moveTo.assert_not_called()

    def test_cancelled_drag_releases_tracked_button(self):
        from core.desktop_control_tools import desktop_drag
        fake = SimpleNamespace()
        with patch.dict(sys.modules, {"pyautogui": fake}), patch("core.desktop_control_tools._windows_only"), \
             patch("core.desktop_control_tools.move_pointer", side_effect=[None, RuntimeError("Emergency stop")]), \
             patch("core.desktop_observation.foreground_identity", return_value=12), \
             patch("core.desktop_control_tools.desktop_mouse_down") as down, \
             patch("core.desktop_control_tools.desktop_mouse_up") as up:
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                desktop_drag(0, 0, 10, 10)
        down.assert_called_once_with("left")
        up.assert_called_once_with("left")

    def test_drag_focus_change_before_mouse_down_does_not_press(self):
        from core.desktop_control_tools import desktop_drag
        with patch.dict(sys.modules, {"pyautogui": SimpleNamespace()}), patch("core.desktop_control_tools._windows_only"), \
             patch("core.desktop_control_tools.move_pointer"), \
             patch("core.desktop_observation.foreground_identity", side_effect=[12, 13]), \
             patch("core.desktop_control_tools.desktop_mouse_down") as down, \
             patch("core.desktop_control_tools.desktop_mouse_up"):
            with self.assertRaisesRegex(RuntimeError, "Foreground changed before drag"):
                desktop_drag(0, 0, 10, 10)
        down.assert_not_called()

    def test_scroll_preserves_requested_distance_and_stops_on_focus_loss(self):
        from core.advanced_tools import desktop_scroll
        fake = SimpleNamespace(scroll=Mock())
        with patch.dict(sys.modules, {"pyautogui": fake}), patch("core.desktop_observation.foreground_identity", return_value=12):
            desktop_scroll(-19)
        self.assertEqual([call.args[0] for call in fake.scroll.call_args_list], [-8, -8, -3])
        fake.scroll.reset_mock()
        with patch.dict(sys.modules, {"pyautogui": fake}), patch("core.desktop_observation.foreground_identity", side_effect=[12, 12, 13]):
            with self.assertRaisesRegex(RuntimeError, "Foreground changed"):
                desktop_scroll(19)
        self.assertEqual(fake.scroll.call_count, 1)


if __name__ == "__main__":
    unittest.main()
