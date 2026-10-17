from __future__ import annotations

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from core import known_folder_tools as known
from core.desktop_input import InputDeliveryError, InputNotDispatchedError
from core.permissions import PermissionEngine, Risk
from core.tools import ToolRegistry


class VerifiedKnownFolderTests(unittest.TestCase):
    def test_reuses_exact_downloads_window_without_reopening(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path", return_value=r"C:\Users\Fixture\Downloads"),
            patch.object(known, "_folder_windows", return_value=[321]),
            patch.object(known, "_verify_existing_folder_window", return_value=True) as verify,
            patch.object(known.os, "startfile", create=True) as startfile,
        ):
            result = known.open_known_folder("Downloads")
        self.assertTrue(result.startswith("VERIFIED:"), result)
        payload = json.loads(result.removeprefix("VERIFIED:"))
        self.assertTrue(payload["location_verified"])
        self.assertTrue(payload["foreground_verified"])
        self.assertTrue(payload["reused_existing_window"])
        verify.assert_called_once_with(321, r"C:\Users\Fixture\Downloads", activate=True)
        startfile.assert_not_called()

    def test_launches_exact_folder_once_and_verifies_new_window(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path", return_value=r"C:\Users\Fixture\Downloads"),
            patch.object(known, "_folder_windows", side_effect=[[], [654]]),
            patch.object(known, "_verify_existing_folder_window", return_value=True),
            patch.object(known.os, "startfile", create=True) as startfile,
        ):
            result = known.open_known_folder("Downloads")
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertFalse(json.loads(result.removeprefix("VERIFIED:"))["reused_existing_window"])
        startfile.assert_called_once_with(r"C:\Users\Fixture\Downloads")

    def test_rejects_ambiguous_windows_before_launch(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path", return_value=r"C:\Users\Fixture\Downloads"),
            patch.object(known, "_folder_windows", return_value=[123, 456]),
            patch.object(known.os, "startfile", create=True) as startfile,
        ):
            with self.assertRaisesRegex(InputNotDispatchedError, "Several Explorer windows"):
                known.open_known_folder()
        startfile.assert_not_called()

    def test_rejects_unknown_folder_without_executing(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path") as path,
        ):
            with self.assertRaisesRegex(ValueError, "allowlisted Downloads"):
                known.open_known_folder("Documents")
            with self.assertRaises(ValueError):
                known.open_known_folder("../../Windows/System32")
        path.assert_not_called()

    def test_registered_tool_requires_policy_and_exact_folder(self):
        registry = ToolRegistry()
        known.register_known_folder_tools(registry)
        spec = registry._tools["open_known_folder"]
        self.assertEqual(spec.risk, Risk.MEDIUM)
        self.assertEqual(spec.input_schema["properties"]["folder"]["enum"], ["Downloads"])
        self.assertEqual(spec.input_schema["required"], ["folder"])

    def test_restricted_mode_blocks_navigation_even_with_approval(self):
        permissions = PermissionEngine()
        permissions.set_access_mode("restricted")
        allowed, reason = permissions.check("open_known_folder", Risk.MEDIUM, approved=True)
        self.assertFalse(allowed)
        self.assertIn("restricted", reason)
        permissions.set_access_mode("full")
        self.assertTrue(permissions.check("open_known_folder", Risk.MEDIUM, approved=True)[0])

    def test_focus_change_during_location_read_never_verifies(self):
        with (
            patch("core.app_tools._focus", return_value=True),
            patch.object(known, "_foreground_hwnd", side_effect=[321, 999]),
            patch.object(known, "_folder_windows", return_value=[321]),
        ):
            self.assertFalse(known._verify_existing_folder_window(321, "fixture"))

    def test_read_only_recovery_does_not_repeat_activation(self):
        with (
            patch("core.app_tools._focus") as focus,
            patch.object(known, "_foreground_hwnd", return_value=321),
            patch.object(known, "_folder_windows", return_value=[321]),
        ):
            self.assertTrue(known._verify_existing_folder_window(321, "fixture", activate=False))
        focus.assert_not_called()

    def test_timeout_never_relaunches_or_repeats_activation(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path", return_value="fixture"),
            patch.object(known, "_folder_windows", side_effect=[[], [321], [321]]),
            patch.object(known, "_verify_existing_folder_window", return_value=False) as verify,
            patch.object(known.os, "startfile", create=True) as launch,
            patch.object(known.time, "monotonic", side_effect=[0, 0.1, 0.2, 9]),
            patch.object(known.time, "sleep"),
        ):
            with self.assertRaisesRegex(InputDeliveryError, "could not be verified"):
                known.open_known_folder()
        launch.assert_called_once_with("fixture")
        self.assertEqual(verify.call_args_list, [
            call(321, "fixture", activate=True), call(321, "fixture", activate=False),
        ])

    def test_cancellation_after_launch_does_not_replay_or_verify(self):
        with (
            patch.object(known, "_windows_only"),
            patch.object(known, "_downloads_path", return_value="fixture"),
            patch.object(known, "_folder_windows", return_value=[]),
            patch.object(known, "_verify_existing_folder_window") as verify,
            patch.object(known.os, "startfile", create=True) as launch,
            patch("core.process_control.check_cancelled", side_effect=[None, None, RuntimeError("Emergency stop")]),
        ):
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                known.open_known_folder()
        launch.assert_called_once()
        verify.assert_not_called()


@unittest.skipUnless(known.os.name == "nt", "Windows file URL conversion")
class ExplorerLocationTests(unittest.TestCase):
    def test_shell_locations_decode_exactly_once_and_ignore_unreadable_windows(self):
        class ComError(Exception):
            pass

        class ClosedWindow:
            @property
            def FullName(self):
                raise ComError("window closed during enumeration")

        def window(url, hwnd=321, exe=r"C:\Windows\explorer.exe"):
            return SimpleNamespace(FullName=exe, LocationURL=url, HWND=hwnd)

        windows = [
            ClosedWindow(),
            window("file:///C:/Fixture/Downloads%2520Literal"),
            window("file:///c:/fixture/downloads%2520literal"),
            window("file:///C:/Fixture/Downloads%20Literal", hwnd=999),
            window("https://example.test/Downloads%2520Literal", hwnd=998),
            window("file:///C:/Fixture/Downloads%2520Literal", hwnd=None),
            window("file:///C:/Fixture/Downloads%2520Literal", hwnd=0),
            window("file:///C:/Fixture/Downloads%2520Literal", exe=r"C:\Other\app.exe"),
        ]
        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock(), com_error=ComError)
        dispatch = Mock(return_value=SimpleNamespace(Windows=lambda: windows))
        with patch.dict(sys.modules, {"pythoncom": com, "win32com.client": SimpleNamespace(Dispatch=dispatch)}):
            self.assertEqual(known._folder_windows(r"C:\Fixture\Downloads%20Literal"), [321])
        dispatch.assert_called_once_with("Shell.Application")
        com.CoInitialize.assert_called_once()
        com.CoUninitialize.assert_called_once()

    def test_com_apartment_is_released_when_shell_enumeration_fails(self):
        com = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock(), com_error=RuntimeError)
        with patch.dict(sys.modules, {"pythoncom": com,
            "win32com.client": SimpleNamespace(Dispatch=Mock(side_effect=RuntimeError("Shell unavailable")))}):
            with self.assertRaisesRegex(RuntimeError, "Shell unavailable"):
                known._folder_windows(r"C:\Fixture\Downloads")
        com.CoUninitialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
