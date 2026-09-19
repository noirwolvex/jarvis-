from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import jarvis_runtime as runtime


class RuntimeBootstrapTests(unittest.TestCase):
    def test_default_runtime_builds_and_selects_optimized_native_executable(self):
        with patch.dict(runtime.os.environ, {}, clear=True):
            self.assertIn("--release", runtime._daemon_build_command("cargo"))
            self.assertEqual(runtime._daemon_executable().parent.name, "release")

    def test_debug_profile_is_explicit_and_invalid_profile_cannot_select_arbitrary_path(self):
        with patch.dict(runtime.os.environ, {"JARVIS_RUST_PROFILE": "debug"}):
            self.assertNotIn("--release", runtime._daemon_build_command("cargo"))
            self.assertEqual(runtime._daemon_executable().parent.name, "debug")
        with patch.dict(runtime.os.environ, {"JARVIS_RUST_PROFILE": "../unexpected"}):
            with self.assertRaises(ValueError):
                runtime._daemon_executable()

    def test_stale_daemon_cleanup_targets_only_exact_project_binary(self) -> None:
        target = Path("/repo/daemon/rust/target/debug/jarvis-daemon.exe")

        matching = Mock()
        matching.pid = 4242
        matching.info = {
            "pid": 4242,
            "name": "jarvis-daemon.exe",
            "exe": str(target),
        }

        unrelated = Mock()
        unrelated.pid = 5252
        unrelated.info = {
            "pid": 5252,
            "name": "jarvis-daemon.exe",
            "exe": "/other/project/jarvis-daemon.exe",
        }

        with patch.object(runtime.os, "name", "nt"), \
             patch("psutil.process_iter", side_effect=[[matching, unrelated], []]) as process_iter, \
             patch("psutil.wait_procs", return_value=([], [])) as wait_procs:
            stopped = runtime._stop_stale_project_daemons(target)

        self.assertEqual(stopped, [4242])
        matching.terminate.assert_called_once_with()
        unrelated.terminate.assert_not_called()
        unrelated.kill.assert_not_called()
        wait_procs.assert_called_once()
        self.assertEqual(process_iter.call_count, 2)

    def test_stale_daemon_cleanup_is_noop_without_exact_match(self) -> None:
        target = Path("/repo/daemon/rust/target/debug/jarvis-daemon.exe")
        unrelated = Mock()
        unrelated.pid = 5252
        unrelated.info = {
            "pid": 5252,
            "name": "jarvis-daemon.exe",
            "exe": "/other/project/jarvis-daemon.exe",
        }

        with patch.object(runtime.os, "name", "nt"), \
             patch("psutil.process_iter", return_value=[unrelated]), \
             patch("psutil.wait_procs") as wait_procs:
            stopped = runtime._stop_stale_project_daemons(target)

        self.assertEqual(stopped, [])
        unrelated.terminate.assert_not_called()
        wait_procs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
