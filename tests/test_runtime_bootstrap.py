from __future__ import annotations

import unittest
import io
import socket
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import jarvis_runtime as runtime


class RuntimeBootstrapTests(unittest.TestCase):
    def test_busy_dashboard_port_rejects_before_bootstrap_for_both_entrypoints(self):
        with tempfile.TemporaryDirectory() as directory, \
             socket.socket(socket.AF_INET, socket.SOCK_STREAM) as existing:
            existing.bind(("127.0.0.1", 0))
            existing.listen()
            with patch.object(runtime, "RUNTIME_ROOT", Path(directory) / "rust-runtime"), \
                 patch.object(runtime, "DASHBOARD_PORT", existing.getsockname()[1]), \
                 patch.object(runtime, "_require_windows"), \
                 patch.object(runtime, "_dashboard_command", return_value=["node", "next", "dev"]), \
                 patch.object(runtime, "bootstrap") as bootstrap:
                for launch in (lambda: runtime.run_dashboard("dev"), runtime.live_qualify):
                    with self.assertRaisesRegex(RuntimeError, "already in use"):
                        launch()
                bootstrap.assert_not_called()

    def test_runtime_lock_blocks_concurrent_launch_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, "RUNTIME_ROOT", Path(directory) / "rust-runtime"), \
             patch.object(runtime, "_require_windows"), \
             patch.object(runtime, "_dashboard_command", return_value=["node", "next", "dev"]), \
             patch.object(runtime, "bootstrap") as bootstrap, \
             patch.object(runtime, "_assert_dashboard_port_available") as port_check:
            with self.assertRaisesRegex(ValueError, "fixture"):
                with runtime._runtime_lock():
                    for launch in (lambda: runtime.run_dashboard("dev"), runtime.live_qualify):
                        with self.assertRaisesRegex(RuntimeError, "already starting or running"):
                            launch()
                    raise ValueError("fixture")
            with runtime._runtime_lock():
                pass
            bootstrap.assert_not_called()
            port_check.assert_not_called()

    def test_runtime_lock_is_released_by_os_after_launcher_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            source = (
                "from scripts import jarvis_runtime as r; from pathlib import Path; import sys; "
                "r.RUNTIME_ROOT=Path(sys.argv[1])/'rust-runtime'; "
                "lock=r._runtime_lock(); lock.__enter__(); print('LOCKED',flush=True); sys.stdin.read()"
            )
            child = subprocess.Popen([sys.executable, "-u", "-c", source, directory], cwd=runtime.ROOT,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self.assertEqual(child.stdout.readline().strip(), "LOCKED")
                with patch.object(runtime, "RUNTIME_ROOT", Path(directory) / "rust-runtime"):
                    with self.assertRaisesRegex(RuntimeError, "already starting"):
                        with runtime._runtime_lock():
                            pass
                    child.kill()
                    child.wait(timeout=5)
                    with runtime._runtime_lock():
                        pass
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_orphaned_npm_parent_retires_owned_runtime_and_releases_lock(self):
        dashboard, daemon, log = Mock(pid=41), Mock(), Mock()
        dashboard.wait.side_effect = subprocess.TimeoutExpired(["next"], 0.25)
        with patch.object(runtime, "_launcher_parent_identity", return_value=(900, 1.0)), \
             patch.object(runtime, "_launcher_parent_alive", return_value=False), \
             patch.object(runtime, "bootstrap", return_value=(daemon, log, Path("session"), {}, {})), \
             patch.object(runtime.subprocess, "Popen", return_value=dashboard), \
             patch.object(runtime, "_wait_dashboard"), \
             patch.object(runtime, "_open_paired_dashboard"), \
             patch.object(runtime, "_stop_dashboard") as stop_dashboard, \
             patch.object(runtime, "_stop_process") as stop_process, redirect_stdout(io.StringIO()):
            self.assertEqual(runtime._run_dashboard(["node", "next", "dev"]), 130)
        stop_dashboard.assert_called_once_with(dashboard)
        stop_process.assert_called_once_with(daemon)
        log.close.assert_called_once_with()

    def test_virtual_key_qualification_pumps_target_event_loop_between_probes(self):
        class FakeTk:
            SEL_FIRST = "sel.first"
            SEL_LAST = "sel.last"
            INSERT = "insert"

        class FakeEntry:
            def __init__(self, value):
                self.value = value
                self.selection = None
                self.insert = len(value)
            def get(self):
                return self.value
            def selection_present(self):
                return self.selection is not None
            def index(self, marker):
                if marker == FakeTk.SEL_FIRST:
                    if self.selection is None:
                        raise RuntimeError("no selection")
                    return self.selection[0]
                if marker == FakeTk.SEL_LAST:
                    if self.selection is None:
                        raise RuntimeError("no selection")
                    return self.selection[1]
                if marker == FakeTk.INSERT:
                    return self.insert
                raise AssertionError(marker)

        class FakeRoot:
            def __init__(self):
                self.queue = []
                self.updates = 0
            def update_idletasks(self):
                pass
            def update(self):
                self.updates += 1
                queued, self.queue = self.queue, []
                for action in queued:
                    action()

        token = "RUST-LIVE-65537-fixture"
        entry, root = FakeEntry(token), FakeRoot()
        def probe(_worker, _reader, payload):
            kind = payload["kind"]
            if kind == "hotkey":
                root.queue.append(lambda: setattr(entry, "selection", (0, len(entry.value))))
            elif kind == "press_key":
                def home():
                    entry.selection = None
                    entry.insert = 0
                root.queue.append(home)
            elif kind == "type_text":
                text = payload["text"]
                def type_text():
                    if entry.selection is not None:
                        start, end = entry.selection
                        entry.value = entry.value[:start] + text + entry.value[end:]
                        entry.insert = start + len(text)
                        entry.selection = None
                    else:
                        entry.value = entry.value[:entry.insert] + text + entry.value[entry.insert:]
                        entry.insert += len(text)
                root.queue.append(type_text)
            else:
                raise AssertionError(payload)
            return {}

        with patch.object(runtime, "_worker_probe", side_effect=probe), \
             patch.object(runtime.uuid, "uuid4", return_value=SimpleNamespace(hex="12345678abcdef")):
            replacement = runtime._qualify_virtual_key_delivery(
                root, entry, FakeTk, Mock(), Mock(), 65537, token
            )
        self.assertEqual(replacement, "HOTKEY-65537-12345678")
        self.assertEqual(entry.get(), "VK-HOTKEY-65537-12345678")
        self.assertGreaterEqual(root.updates, 4)

    def test_dashboard_command_handles_spaces_without_batch_shell(self):
        with tempfile.TemporaryDirectory(prefix="Jarvis project with spaces ") as directory:
            root = Path(directory)
            cli = root / "node_modules" / "next" / "dist" / "bin" / "next"
            cli.parent.mkdir(parents=True)
            cli.touch()
            with patch.object(runtime, "ROOT", root), \
                 patch.object(runtime, "_tool", return_value=r"C:\Program Files\nodejs\node.exe"):
                command = runtime._dashboard_command("dev")
            self.assertEqual(command, [r"C:\Program Files\nodejs\node.exe", str(cli), "dev",
                                       "--hostname", "127.0.0.1", "--port", "3000"])
            self.assertNotIn("cmd.exe", command)

    def test_missing_dashboard_dependency_does_not_start_or_replace_rust(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(runtime, "ROOT", Path(directory)), \
             patch.object(runtime, "_require_windows"), \
             patch.object(runtime, "_tool", return_value="node"), \
             patch.object(runtime, "bootstrap") as bootstrap:
            with self.assertRaisesRegex(RuntimeError, "npm ci"):
                runtime.run_dashboard("dev")
            bootstrap.assert_not_called()

    def test_readiness_never_accepts_a_foreign_dashboard_response(self):
        process = Mock(pid=1, returncode=1)
        process.poll.side_effect = [None, 1]
        output = io.StringIO()
        with patch.object(runtime, "_dashboard_owns_port", return_value=False), \
             patch.object(runtime.urllib.request, "urlopen") as request, \
             patch.object(runtime.time, "sleep"), redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                runtime._wait_dashboard(process)
        request.assert_not_called()
        self.assertNotIn("READY", output.getvalue())

    def test_readiness_requires_owned_listener_before_and_after_http_response(self):
        process = Mock(pid=1, returncode=None)
        process.poll.return_value = None
        response = Mock(status=200)
        with patch.object(runtime, "_dashboard_owns_port", return_value=True) as owns, \
             patch.object(runtime.urllib.request, "urlopen") as request, redirect_stdout(io.StringIO()):
            request.return_value.__enter__.return_value = response
            runtime._wait_dashboard(process)
        self.assertEqual(owns.call_count, 2)

    def test_listener_replacement_during_http_probe_cannot_report_ready(self):
        process = Mock(pid=1, returncode=1)
        process.poll.side_effect = [None, None, 1]
        output = io.StringIO()
        with patch.object(runtime, "_dashboard_owns_port", side_effect=[True, False]), \
             patch.object(runtime.urllib.request, "urlopen") as request, \
             patch.object(runtime.time, "sleep"), redirect_stdout(output):
            request.return_value.__enter__.return_value = Mock(status=200)
            with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                runtime._wait_dashboard(process)
        self.assertNotIn("READY", output.getvalue())

    def test_listener_must_belong_to_launched_next_process_or_child(self):
        parent = Mock(pid=41)
        parent.children.return_value = [Mock(pid=42)]
        for pid, ip, port, accepted in ((41, "127.0.0.1", 3000, True),
                                        (42, "127.0.0.1", 3000, True),
                                        (99, "127.0.0.1", 3000, False),
                                        (41, "127.0.0.1", 4000, False)):
            connection = SimpleNamespace(pid=pid, status="LISTEN", laddr=SimpleNamespace(ip=ip, port=port))
            with self.subTest(pid=pid, port=port), patch("psutil.Process", return_value=parent), \
                 patch("psutil.net_connections", return_value=[connection]):
                self.assertEqual(runtime._dashboard_owns_port(Mock(pid=41)), accepted)

    def test_dashboard_launch_is_direct_and_cleans_owned_tree_on_readiness_failure(self):
        command = [r"C:\Program Files\nodejs\node.exe", "next", "dev"]
        dashboard, daemon, log = Mock(), Mock(), Mock()
        with patch.object(runtime, "bootstrap", return_value=(daemon, log, Path("session"), {}, {})), \
             patch.object(runtime.subprocess, "Popen", return_value=dashboard) as spawn, \
             patch.object(runtime, "_wait_dashboard", side_effect=RuntimeError("fixture startup failure")), \
             patch.object(runtime, "_stop_dashboard") as stop_dashboard, \
             patch.object(runtime, "_stop_process") as stop_process:
            with self.assertRaisesRegex(RuntimeError, "fixture startup failure"):
                runtime._run_dashboard(command)
        self.assertEqual(spawn.call_args.args[0], command)
        self.assertEqual(spawn.call_args.kwargs["cwd"], runtime.ROOT / "apps" / "control-center")
        self.assertFalse(spawn.call_args.kwargs.get("shell", False))
        stop_dashboard.assert_called_once_with(dashboard)
        stop_process.assert_called_once_with(daemon)
        log.close.assert_called_once()

    def test_dashboard_shutdown_terminates_only_its_owned_process_tree(self):
        dashboard = Mock(pid=41)
        dashboard.poll.return_value = None
        parent, child = Mock(pid=41), Mock(pid=42)
        parent.children.return_value = [child]
        with patch("psutil.Process", return_value=parent) as lookup, \
             patch("psutil.wait_procs", return_value=([], [])) as wait:
            runtime._stop_dashboard(dashboard)
        lookup.assert_called_once_with(41)
        parent.terminate.assert_called_once()
        child.terminate.assert_called_once()
        wait.assert_called_once_with([child, parent], timeout=3)
        dashboard.wait.assert_called_once_with(timeout=3)

    def test_runtime_env_generates_fresh_browser_pairing_secret(self):
        with patch.dict(runtime.os.environ, {"JARVIS_CONTROL_PAIRING_TOKEN": "caller-fixed-token"}, clear=True):
            first = runtime._runtime_env(Path("session-a"), {"0": "observe"}, {"0": "input"})
            second = runtime._runtime_env(Path("session-b"), {"0": "observe"}, {"0": "input"})
        self.assertEqual(first["JARVIS_CONTROL_MODE"], "hybrid")
        self.assertGreaterEqual(len(first["JARVIS_CONTROL_PAIRING_TOKEN"]), 32)
        self.assertNotEqual(first["JARVIS_CONTROL_PAIRING_TOKEN"], "caller-fixed-token")
        self.assertNotEqual(first["JARVIS_CONTROL_PAIRING_TOKEN"], second["JARVIS_CONTROL_PAIRING_TOKEN"])

    def test_pairing_opens_secret_url_without_printing_secret(self):
        token = "runtime-pairing-secret-that-must-not-appear-in-output"
        output = io.StringIO()
        with patch.object(runtime.webbrowser, "open", return_value=True) as open_browser, \
             redirect_stdout(output):
            runtime._open_paired_dashboard({"JARVIS_CONTROL_PAIRING_TOKEN": token})
        opened = open_browser.call_args.args[0]
        self.assertIn("/api/pair?token=", opened)
        self.assertIn(token, opened)
        self.assertNotIn(token, output.getvalue())
        self.assertIn("JARVIS_CONTROL_SESSION_OPENED", output.getvalue())

    def test_pairing_fails_closed_when_browser_cannot_open(self):
        with patch.object(runtime.webbrowser, "open", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "authenticated Control Center"):
                runtime._open_paired_dashboard({"JARVIS_CONTROL_PAIRING_TOKEN": "x" * 40})

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
