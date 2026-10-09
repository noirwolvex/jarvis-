from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core import known_folder_tools as known
from core.permissions import Risk
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
        verify.assert_called_once_with(321, r"C:\Users\Fixture\Downloads")
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
            with self.assertRaisesRegex(RuntimeError, "Several Explorer windows"):
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


if __name__ == "__main__":
    unittest.main()
