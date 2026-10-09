from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import app_discovery_cache as cache
from core import app_discovery_fast as fast
from core import app_tools


class AppDiscoveryOverlayIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        original = app_tools._discover_candidates
        if original is cache._cached_discover:
            original = cache._ORIGINAL
        if original is fast._fast_discover:
            original = fast._ORIGINAL
        self.stack.enter_context(patch.object(app_tools, "_discover_candidates", original))
        for name, value in (
            ("_CACHE", OrderedDict()), ("_CACHE_CONTEXT", None), ("_GENERATION", 0),
            ("_ORIGINAL", None), ("_INSTALLED", False),
        ):
            self.stack.enter_context(patch.object(cache, name, value))
        self.stack.enter_context(patch.object(fast, "_ORIGINAL", None))
        self.stack.enter_context(patch.object(fast, "_INSTALLED", False))
        self.stack.enter_context(patch.object(cache, "_ttl_seconds", return_value=90.0))
        self.stack.enter_context(patch.object(app_tools, "_RESOLUTION_CACHE", OrderedDict()))
        self.stack.enter_context(patch.object(app_tools, "os", SimpleNamespace(
            name="nt", path=os.path, getcwd=lambda: os.getcwd(), environ=os.environ,
        )))
        # Keep the real discovery and ranking code, replacing only OS data sources.
        self.sources = {
            name: self.stack.enter_context(patch.object(app_tools, name, return_value=[]))
            for name in (
                "_explicit_path_candidates", "_start_apps", "_registry_app_path_candidates",
                "_path_candidates", "_start_menu_candidates", "_registry_uninstall_candidates",
                "_common_install_candidates",
            )
        }
        self.sources["_start_apps"].return_value = [self.app()]
        # Match the production full-access builder's installation order.
        fast.enable_fast_app_discovery()
        cache.enable_app_discovery_cache()

    @staticmethod
    def app(launch_value="Example!App"):
        return app_tools._candidate("Example", "start_apps", "apps_folder", launch_value)

    def test_installed_overlay_resolves_cold_and_warm_without_truncating_listing(self):
        original = cache._ORIGINAL
        cache.enable_app_discovery_cache()
        self.assertIs(cache._ORIGINAL, original)
        self.assertIs(cache._ORIGINAL, fast._fast_discover)
        self.assertIs(app_tools._discover_candidates, cache._cached_discover)
        self.sources["_registry_app_path_candidates"].return_value = [
            app_tools._candidate("Example Editor", "app_paths", "executable", r"C:\ExampleEditor.exe"),
        ]

        first = app_tools._resolve("Example")
        first["launch_value"] = "changed by caller"
        self.assertEqual(app_tools._resolve("Example")["launch_value"], "Example!App")
        self.assertEqual(self.sources["_start_apps"].call_count, 1)
        self.sources["_registry_app_path_candidates"].assert_not_called()

        listed = json.loads(app_tools.find_installed_app("Example"))
        self.assertEqual({row["name"] for row in listed}, {"Example", "Example Editor"})
        app_tools.find_installed_app("Example")
        self.assertEqual(self.sources["_start_apps"].call_count, 2)
        self.sources["_registry_app_path_candidates"].assert_called_once_with("Example")

    def test_forgotten_resolution_refetches_even_after_listing_warms_overlay(self):
        app_tools.find_installed_app("Example")
        app_tools._resolve("Example")
        self.sources["_start_apps"].return_value = [self.app("Replacement!App")]

        # Failed launches call this invalidation before another resolution attempt.
        app_tools._forget_resolution("Example")

        self.assertEqual(app_tools._resolve("Example")["launch_value"], "Replacement!App")
        self.assertEqual(self.sources["_start_apps"].call_count, 3)

    def test_exact_resolution_expiry_is_not_extended_by_overlay_ttl(self):
        with patch.object(app_tools.time, "monotonic", return_value=100.0) as clock:
            app_tools._resolve("Example")
            self.sources["_start_apps"].return_value = [self.app("Replacement!App")]
            clock.return_value = 160.0
            self.assertEqual(app_tools._resolve("Example")["launch_value"], "Replacement!App")
        self.assertEqual(self.sources["_start_apps"].call_count, 2)

    def test_missing_resolution_is_not_negatively_cached_by_overlay(self):
        self.sources["_start_apps"].return_value = []
        with self.assertRaises(FileNotFoundError):
            app_tools._resolve("Example")
        self.sources["_start_apps"].return_value = [self.app()]
        self.assertEqual(app_tools._resolve("Example")["launch_value"], "Example!App")
        self.assertEqual(self.sources["_start_apps"].call_count, 2)

    def test_deleted_executable_is_not_resurrected_by_overlay(self):
        self.sources["_start_apps"].return_value = []
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "Example.exe"
            executable.touch()
            self.sources["_path_candidates"].return_value = [
                app_tools._candidate("Example", "path", "executable", str(executable)),
            ]
            app_tools._resolve("Example")
            executable.unlink()
            self.sources["_path_candidates"].return_value = []
            with self.assertRaises(FileNotFoundError):
                app_tools._resolve("Example")
        self.assertEqual(self.sources["_path_candidates"].call_count, 2)

    def test_environment_and_cwd_changes_refresh_listing_and_resolution(self):
        with patch.object(os, "getcwd", return_value="first-directory") as cwd:
            app_tools.find_installed_app("Example")
            app_tools._resolve("Example")
            self.sources["_start_apps"].return_value = [self.app("ChangedPath!App")]
            with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "") + os.pathsep + "fixture"}):
                app_tools.find_installed_app("Example")
                self.assertEqual(app_tools._resolve("Example")["launch_value"], "ChangedPath!App")
                self.sources["_start_apps"].return_value = [self.app("ChangedCwd!App")]
                cwd.return_value = "second-directory"
                app_tools.find_installed_app("Example")
                self.assertEqual(app_tools._resolve("Example")["launch_value"], "ChangedCwd!App")
        self.assertEqual(self.sources["_start_apps"].call_count, 6)
        self.assertEqual(len(cache._CACHE), 1)

    def test_expanded_explicit_paths_bypass_listing_cache(self):
        with patch.dict(os.environ, {"JARVIS_CACHE_TEST_APP": r"C:\Example.exe"}):
            app_tools.find_installed_app("$JARVIS_CACHE_TEST_APP")
            app_tools.find_installed_app("$JARVIS_CACHE_TEST_APP")
        self.assertEqual(self.sources["_start_apps"].call_count, 2)
        self.assertEqual(len(cache._CACHE), 0)


if __name__ == "__main__":
    unittest.main()
