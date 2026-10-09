from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from core import app_tools
from core.app_tools import (
    _BLOCKED_EXECUTABLE_STEMS,
    _candidate,
    _clean_query,
    _decode_start_apps,
    _focus,
    _is_blocked_executable,
    _parse_display_icon,
    _rank_candidates,
    _score,
    register_app_tools,
    launch_installed_app,
)
from core.orchestrator import TaskOrchestrator
from core.task_tools import register_task_tools
from core.tools import ToolRegistry


class AppToolsTests(unittest.TestCase):
    def test_decode_start_apps_accepts_single_and_multiple_rows(self) -> None:
        single = _decode_start_apps(
            '{"Name":"WhatsApp","AppID":"WhatsApp_123!App"}'
        )
        self.assertEqual(single[0]["name"], "WhatsApp")
        self.assertEqual(single[0]["source"], "start_apps")
        self.assertEqual(single[0]["launch_type"], "apps_folder")

        multiple = _decode_start_apps(
            '[{"Name":"WhatsApp","AppID":"a"},'
            '{"Name":"WhatsApp Beta","AppID":"b"}]'
        )
        self.assertEqual(len(multiple), 2)

    def test_app_name_scoring_prefers_exact_match(self) -> None:
        exact = _score("WhatsApp app", "WhatsApp")
        beta = _score("WhatsApp app", "WhatsApp Beta")
        unrelated = _score("WhatsApp app", "Calculator")
        self.assertGreater(exact, beta)
        self.assertGreater(beta, unrelated)

    def test_clean_query_removes_app_suffix_but_preserves_path(self) -> None:
        self.assertEqual(_clean_query("  WhatsApp   app "), "WhatsApp")
        self.assertEqual(
            _clean_query(r"C:\Program Files\Example\Example.exe"),
            r"C:\Program Files\Example\Example.exe",
        )

    def test_rank_candidates_prefers_exact_and_stronger_source(self) -> None:
        candidates = [
            _candidate(
                "Visual Studio Code",
                "common_install",
                "executable",
                r"C:\Program Files\Code\Code.exe",
                process_hint="Code",
            ),
            _candidate(
                "Visual Studio Code",
                "app_paths",
                "executable",
                r"C:\Program Files\Code\Code.exe",
                process_hint="Code",
            ),
            _candidate(
                "Visual Studio Code Insiders",
                "start_menu",
                "shortcut",
                r"C:\Menu\VS Code Insiders.lnk",
            ),
        ]
        ranked = _rank_candidates("Visual Studio Code", candidates)
        self.assertEqual(ranked[0]["name"], "Visual Studio Code")
        self.assertEqual(ranked[0]["source"], "app_paths")
        self.assertGreater(ranked[0]["score"], ranked[-1]["score"])

    def test_restricted_executable_set_is_enforced(self) -> None:
        for stem in sorted(_BLOCKED_EXECUTABLE_STEMS):
            with self.subTest(stem=stem):
                self.assertTrue(_is_blocked_executable(stem + ".exe"))
        self.assertFalse(_is_blocked_executable("Spotify.exe"))

    def test_display_icon_parser_removes_quotes_and_icon_index(self) -> None:
        self.assertEqual(
            _parse_display_icon(r'"C:\Program Files\App\App.exe",0'),
            r"C:\Program Files\App\App.exe",
        )
        self.assertEqual(
            _parse_display_icon(r"C:\Program Files\App\App.exe,5"),
            r"C:\Program Files\App\App.exe",
        )

    def test_launch_waits_for_visible_focused_window_after_process_appears(self) -> None:
        app = {
            "name": "WhatsApp",
            "source": "fixture",
            "launch_type": "executable",
            "launch_value": r"C:\\WhatsApp.exe",
            "process_hint": "WhatsApp",
            "score": 100.0,
        }
        process = {"pid": 42, "name": "WhatsApp.exe", "exe": r"C:\\WhatsApp.exe"}
        window = {"hwnd": 77, "title": "WhatsApp", "pid": 42}
        with patch("core.app_tools.os", SimpleNamespace(name="nt", path=os.path)), \
             patch("core.app_tools._resolve", return_value=app), \
             patch("core.app_tools._launch_candidate", return_value=123), \
             patch("core.app_tools._visible_windows", side_effect=[[], [], [window]]), \
             patch("core.app_tools._processes", side_effect=[[], [process]]), \
             patch("core.app_tools._focus", return_value=True) as focus, \
             patch("core.ui_state.cancellable_delay"):
            result = launch_installed_app("WhatsApp", timeout_seconds=2)

        self.assertTrue(result.startswith("VERIFIED: "))
        payload = json.loads(result[len("VERIFIED: "):])
        self.assertTrue(payload["interaction_ready"])
        self.assertTrue(payload["visible_window_verified"])
        self.assertTrue(payload["focused"])
        focus.assert_called_once_with(77)

    def test_process_only_launch_is_not_reported_interaction_ready(self) -> None:
        app = {
            "name": "Background App",
            "source": "fixture",
            "launch_type": "executable",
            "launch_value": r"C:\\Background.exe",
            "process_hint": "Background",
            "score": 100.0,
        }
        process = {"pid": 43, "name": "Background.exe", "exe": r"C:\\Background.exe"}
        with patch("core.app_tools.os", SimpleNamespace(name="nt", path=os.path)), \
             patch("core.app_tools._resolve", return_value=app), \
             patch("core.app_tools._launch_candidate", return_value=124), \
             patch("core.app_tools._visible_windows", side_effect=[[], []]), \
             patch("core.app_tools._processes", side_effect=[[], [process]]), \
             patch("core.app_tools.time.monotonic", side_effect=[0.0, 0.1, 2.1]), \
             patch("core.ui_state.cancellable_delay"):
            result = launch_installed_app("Background App", timeout_seconds=2)

        self.assertTrue(result.startswith("DELIVERED: "))
        payload = json.loads(result[len("DELIVERED: "):])
        self.assertFalse(payload["interaction_ready"])
        self.assertTrue(payload["process_verified"])
        self.assertFalse(payload["visible_window_verified"])

    def test_visible_window_focus_failure_does_not_retry_until_launch_timeout(self) -> None:
        app = {"name": "File Explorer", "source": "fixture", "launch_type": "executable",
               "launch_value": r"C:\Windows\explorer.exe", "process_hint": "explorer", "score": 100.0}
        window = {"hwnd": 77, "title": "Home - File Explorer", "pid": 42}
        with patch("core.app_tools.os", SimpleNamespace(name="nt", path=os.path)), \
             patch("core.app_tools._resolve", return_value=app), \
             patch("core.app_tools._launch_candidate", return_value=123) as launch, \
             patch("core.app_tools._visible_windows", side_effect=[[], [window]]) as windows, \
             patch("core.app_tools._processes", return_value=[]) as processes, \
             patch("core.app_tools._focus", return_value=False) as focus, \
             patch("core.ui_state.cancellable_delay") as delay:
            result = launch_installed_app("File Explorer", timeout_seconds=25)

        self.assertTrue(result.startswith("DELIVERED: "))
        payload = json.loads(result.removeprefix("DELIVERED: "))
        self.assertFalse(payload["interaction_ready"])
        self.assertTrue(payload["visible_window_verified"])
        self.assertEqual(payload["process_id"], 42)
        self.assertIn("do not relaunch", payload["recovery_action"])
        launch.assert_called_once()
        focus.assert_called_once_with(77)
        self.assertEqual(windows.call_count, 2)
        processes.assert_called_once()
        delay.assert_not_called()

    def test_app_tools_are_registered_without_arbitrary_shell_tooling(self) -> None:
        registry = ToolRegistry()
        register_app_tools(registry)
        self.assertIn("find_installed_app", registry._tools)
        self.assertIn("launch_installed_app", registry._tools)
        self.assertEqual(
            registry._tools["find_installed_app"].risk.name,
            "LOW",
        )
        self.assertEqual(
            registry._tools["launch_installed_app"].risk.name,
            "MEDIUM",
        )
        self.assertNotEqual(
            registry._tools["launch_installed_app"].name,
            "run_powershell",
        )


class AppResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        app_tools._RESOLUTION_CACHE.clear()
        self.addCleanup(app_tools._RESOLUTION_CACHE.clear)

    def app(self, name="Example", *, launch_type="apps_folder", launch_value="Example!App"):
        return {**_candidate(name, "start_apps", launch_type, launch_value), "score": 114.0}

    def discovery_sources(self, stack, *, start_apps=(), explicit=()):
        stack.enter_context(patch("core.app_tools.os", SimpleNamespace(name="nt", path=os.path)))
        stack.enter_context(patch("core.app_tools._explicit_path_candidates", return_value=list(explicit)))
        names = ("_start_apps", "_registry_app_path_candidates", "_path_candidates",
                 "_start_menu_candidates", "_registry_uninstall_candidates", "_common_install_candidates")
        return [stack.enter_context(patch("core.app_tools." + name,
                                         return_value=list(start_apps) if index == 0 else []))
                for index, name in enumerate(names)]

    def test_unique_exact_start_app_uses_one_source_instead_of_six(self):
        with ExitStack() as stack:
            sources = self.discovery_sources(stack, start_apps=[self.app()])
            result = app_tools._discover_candidates("Example", stop_when_exact=True)
        self.assertEqual(result[0]["name"], "Example")
        self.assertEqual(sum(source.call_count for source in sources), 1)

    def test_fuzzy_or_duplicate_exact_start_apps_keep_full_discovery(self):
        for rows in ([self.app("Example Beta")],
                     [self.app(), self.app(launch_value="OtherExample!App")]):
            with self.subTest(rows=rows), ExitStack() as stack:
                sources = self.discovery_sources(stack, start_apps=rows)
                app_tools._discover_candidates("Example", stop_when_exact=True)
                self.assertEqual(sum(source.call_count for source in sources), 6)

    def test_find_installed_app_keeps_all_sources_even_for_exact_match(self):
        with ExitStack() as stack:
            sources = self.discovery_sources(stack, start_apps=[self.app()])
            app_tools.find_installed_app("Example")
        self.assertEqual(sum(source.call_count for source in sources), 6)

    def test_explicit_path_skips_name_discovery_and_is_confident(self):
        path = r"C:\Program Files\Some Very Long Directory\Example.exe"
        app = _candidate("Example", "explicit_path", "executable", path)
        with ExitStack() as stack:
            sources = self.discovery_sources(stack, explicit=[app])
            result = app_tools._discover_candidates(path, stop_when_exact=True)
        self.assertEqual(result[0]["launch_value"], path)
        self.assertGreaterEqual(result[0]["score"], 100)
        self.assertEqual(sum(source.call_count for source in sources), 0)

    def test_successful_resolution_is_reused_without_shared_mutable_metadata(self):
        with patch("core.app_tools._discover_candidates", return_value=[self.app()]) as discover:
            first = app_tools._resolve("Example app")
            first["launch_value"] = "modified by caller"
            second = app_tools._resolve("Example")
        self.assertEqual(second["launch_value"], "Example!App")
        discover.assert_called_once_with("Example app", stop_when_exact=True)

    def test_resolution_expires_after_ttl(self):
        with patch("core.app_tools.time.monotonic", return_value=100.0) as clock, \
             patch("core.app_tools._discover_candidates", return_value=[self.app()]) as discover:
            app_tools._resolve("Example")
            clock.return_value = 159.9
            app_tools._resolve("Example")
            self.assertEqual(discover.call_count, 1)
            clock.return_value = 160.0
            app_tools._resolve("Example")
        self.assertEqual(discover.call_count, 2)

    def test_environment_and_working_directory_changes_invalidate_resolution(self):
        with patch("core.app_tools._discover_candidates", return_value=[self.app()]) as discover, \
             patch("core.app_tools.os.getcwd", return_value="first-directory") as cwd:
            app_tools._resolve("Example")
            with patch.dict(os.environ, {"PATH": os.environ.get("PATH", "") + os.pathsep + "fixture"}):
                app_tools._resolve("Example")
            cwd.return_value = "second-directory"
            app_tools._resolve("Example")
        self.assertEqual(discover.call_count, 3)

    def test_expanded_path_environment_changes_invalidate_resolution(self):
        with patch("core.app_tools._discover_candidates", return_value=[self.app()]) as discover:
            with patch.dict(os.environ, {"JARVIS_TEST_APP_ROOT": "first-directory"}):
                app_tools._resolve("$JARVIS_TEST_APP_ROOT/Example.exe")
            with patch.dict(os.environ, {"JARVIS_TEST_APP_ROOT": "second-directory"}):
                app_tools._resolve("$JARVIS_TEST_APP_ROOT/Example.exe")
        self.assertEqual(discover.call_count, 2)

    def test_deleted_executable_invalidates_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "Example.exe"
            executable.touch()
            app = self.app(launch_type="executable", launch_value=str(executable))
            with patch("core.app_tools._discover_candidates", side_effect=[[app], []]) as discover:
                app_tools._resolve("Example")
                executable.unlink()
                with self.assertRaises(FileNotFoundError):
                    app_tools._resolve("Example")
            self.assertEqual(discover.call_count, 2)

    def test_failed_resolution_is_not_cached(self):
        with patch("core.app_tools._discover_candidates", side_effect=[[], [self.app()]]) as discover:
            with self.assertRaises(FileNotFoundError):
                app_tools._resolve("Example")
            self.assertEqual(app_tools._resolve("Example")["name"], "Example")
        self.assertEqual(discover.call_count, 2)

    def test_cache_is_bounded_and_evicts_least_recently_used_entry(self):
        with patch("core.app_tools._RESOLUTION_CACHE_LIMIT", 2), \
             patch("core.app_tools._discover_candidates", return_value=[self.app()]) as discover:
            for query in ("one", "two", "one", "three"):
                app_tools._resolve(query)
            self.assertEqual(discover.call_count, 3)
            self.assertEqual(len(app_tools._RESOLUTION_CACHE), 2)
            app_tools._resolve("two")
        self.assertEqual(discover.call_count, 4)


class ExistingApplicationTests(unittest.TestCase):
    def setUp(self):
        self.app = {**_candidate("Example", "app_paths", "executable", r"C:\Example\Example.exe"),
                    "score": 112.0}
        self.process = {"pid": 42, "name": "Example.exe", "exe": self.app["launch_value"]}
        self.window = {"hwnd": 77, "pid": 42, "title": "An unrelated document title"}

    def fixtures(self, stack, *, focused=True, windows=None, identity=None):
        stack.enter_context(patch("core.app_tools.os", SimpleNamespace(name="nt", path=os.path)))
        stack.enter_context(patch("core.app_tools._resolve", return_value=self.app))
        user32 = Mock()
        user32.GetForegroundWindow.return_value = self.window["hwnd"]
        stack.enter_context(patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=user32), create=True))
        return SimpleNamespace(
            foreground=user32.GetForegroundWindow,
            launch=stack.enter_context(patch("core.app_tools._launch_candidate", return_value=100)),
            windows=stack.enter_context(patch("core.app_tools._visible_windows",
                                             return_value=windows if windows is not None else [self.window])),
            processes=stack.enter_context(patch("core.app_tools._processes", return_value=[self.process])),
            identity=stack.enter_context(patch("core.app_tools._read_process_identity",
                                              return_value=identity if identity is not None else self.process)),
            focus=stack.enter_context(patch("core.app_tools._focus", return_value=focused)),
            delay=stack.enter_context(patch("core.ui_state.cancellable_delay")),
        )

    def test_existing_executable_reuses_window_without_launch_or_polling(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack)
            result = launch_installed_app("Example")
        payload = json.loads(result.removeprefix("VERIFIED: "))
        self.assertTrue(payload["reused_existing"])
        self.assertTrue(payload["interaction_ready"])
        self.assertIsNone(payload["launcher_pid"])
        mocks.launch.assert_not_called()
        mocks.delay.assert_not_called()
        mocks.processes.assert_called_once()
        self.assertEqual(mocks.windows.call_count, 2)
        mocks.identity.assert_called_once_with(42)
        mocks.focus.assert_called_once_with(77)

    def test_existing_focus_failure_returns_evidence_without_relaunch(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack, focused=False)
            result = launch_installed_app("Example")
        payload = json.loads(result.removeprefix("DELIVERED: "))
        self.assertFalse(payload["interaction_ready"])
        self.assertTrue(payload["reused_existing"])
        mocks.launch.assert_not_called()
        mocks.focus.assert_called_once_with(77)
        mocks.delay.assert_not_called()

    def test_focus_lost_during_identity_reads_is_not_reported_ready(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack)
            mocks.identity.side_effect = lambda pid: (
                setattr(mocks.foreground, "return_value", 88) or self.process
            )
            result = launch_installed_app("Example")
        payload = json.loads(result.removeprefix("DELIVERED: "))
        self.assertFalse(payload["interaction_ready"])
        self.assertFalse(payload["focused"])
        mocks.launch.assert_not_called()
        mocks.focus.assert_called_once_with(77)
        mocks.foreground.assert_called_once()

    def test_multiple_matching_windows_are_rejected_without_launch_or_focus(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack, windows=[self.window, {**self.window, "hwnd": 88}])
            with self.assertRaisesRegex(RuntimeError, "multiple matching"):
                launch_installed_app("Example")
        mocks.launch.assert_not_called()
        mocks.focus.assert_not_called()

    def test_changed_process_or_window_identity_is_rejected_without_relaunch(self):
        for change in ("process", "window"):
            with self.subTest(change=change), ExitStack() as stack:
                mocks = self.fixtures(stack)
                if change == "process":
                    mocks.identity.return_value = {**self.process, "exe": r"C:\Other\Example.exe"}
                else:
                    mocks.windows.side_effect = [[self.window], [{**self.window, "pid": 43}]]
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    launch_installed_app("Example")
                mocks.launch.assert_not_called()

    def test_matching_title_or_process_name_alone_does_not_prove_installed_identity(self):
        impostor = {**self.process, "exe": r"C:\Other\Example.exe", "title": "Example"}
        self.assertFalse(app_tools._same_installed_process(self.app, impostor))
        self.assertFalse(app_tools._same_installed_process(self.app, {"name": "Example.exe"}))
        for launch_type in ("apps_folder", "shortcut"):
            self.assertFalse(app_tools._same_installed_process(
                {**self.app, "launch_type": launch_type, "process_hint": "Example"}, self.process))

    def test_launch_failure_invalidates_cached_resolution_without_retry(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack, windows=[])
            mocks.launch.side_effect = OSError("fixture launch failure")
            forget = stack.enter_context(patch("core.app_tools._forget_resolution"))
            with self.assertRaisesRegex(OSError, "fixture launch failure"):
                launch_installed_app("Example")
        mocks.launch.assert_called_once()
        forget.assert_called_once_with("Example")

    def test_launch_without_evidence_invalidates_resolution_without_retry(self):
        with ExitStack() as stack:
            mocks = self.fixtures(stack, windows=[])
            mocks.processes.return_value = []
            stack.enter_context(patch("core.app_tools.time.monotonic", side_effect=[0.0, 0.1, 2.1]))
            forget = stack.enter_context(patch("core.app_tools._forget_resolution"))
            with self.assertRaisesRegex(RuntimeError, "could not verify"):
                launch_installed_app("Example", timeout_seconds=2)
        mocks.launch.assert_called_once()
        forget.assert_called_once_with("Example")


class WindowFocusTests(unittest.TestCase):
    def user32(self, foreground=88):
        api = Mock()
        api.GetForegroundWindow.return_value = foreground
        api.IsWindow.return_value = True
        api.IsWindowVisible.return_value = True
        api.IsIconic.return_value = False
        return api

    def test_denied_win32_focus_recovers_semantically_and_reads_foreground(self):
        api = self.user32()

        def semantic_focus(hwnd):
            self.assertEqual(hwnd, 77)
            api.GetForegroundWindow.return_value = 77

        with patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=api), create=True), \
             patch("core.app_tools._focus_via_uia", side_effect=semantic_focus) as fallback:
            self.assertTrue(_focus(77))
        api.SetForegroundWindow.assert_called_once_with(77)
        api.ShowWindow.assert_not_called()
        fallback.assert_called_once_with(77)

    def test_successful_direct_focus_skips_semantic_fallback(self):
        api = self.user32()
        api.SetForegroundWindow.side_effect = lambda hwnd: setattr(api.GetForegroundWindow, "return_value", hwnd)
        with patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=api), create=True), \
             patch("core.app_tools._focus_via_uia") as fallback:
            self.assertTrue(_focus(77))
        fallback.assert_not_called()

    def test_already_foreground_window_has_no_focus_mutation(self):
        api = self.user32(foreground=77)
        with patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=api), create=True), \
             patch("core.app_tools._focus_via_uia") as fallback:
            self.assertTrue(_focus(77))
        api.ShowWindow.assert_not_called()
        api.SetForegroundWindow.assert_not_called()
        fallback.assert_not_called()

    def test_focus_provider_success_without_foreground_change_still_fails(self):
        api = self.user32()

        def timed_out(probe, **kwargs):
            self.assertFalse(probe())
            self.assertLessEqual(kwargs["timeout"], 0.8)
            raise TimeoutError("fixture window remains in background")

        with patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=api), create=True), \
             patch("core.app_tools._focus_via_uia") as fallback, \
             patch("core.ui_state.wait_until", side_effect=timed_out):
            self.assertFalse(_focus(77))
        api.SetForegroundWindow.assert_called_once_with(77)
        fallback.assert_called_once_with(77)

    def test_stale_handle_is_rejected_without_focus_input(self):
        api = self.user32()
        api.IsWindow.return_value = False
        with patch("core.app_tools.ctypes.windll", SimpleNamespace(user32=api), create=True), \
             patch("core.app_tools._focus_via_uia") as fallback:
            self.assertFalse(_focus(77))
        api.SetForegroundWindow.assert_not_called()
        fallback.assert_not_called()

    def test_advanced_focus_reuses_verified_helper_and_rejects_ambiguity(self):
        from core.advanced_tools import focus_window_advanced
        with patch("core.advanced_tools._windows", return_value=[(77, "Home - File Explorer", 42)]), \
             patch("core.app_tools._focus", return_value=True) as focus:
            self.assertTrue(focus_window_advanced("Home - File Explorer").startswith("VERIFIED:"))
            focus.assert_called_once_with(77)
        with patch("core.advanced_tools._windows", return_value=[(77, "Home", 42), (88, "Home", 43)]), \
             patch("core.app_tools._focus") as focus:
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                focus_window_advanced("Home")
            focus.assert_not_called()


class TaskProgressTests(unittest.TestCase):
    def test_in_progress_alias_is_normalized_to_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {"JARVIS_WORKSPACE": tmp},
                clear=False,
            ):
                registry = ToolRegistry()
                orchestrator = TaskOrchestrator()
                orchestrator.begin("test")
                orchestrator.set_plan(["inspect"])
                register_task_tools(registry, orchestrator)
                result = registry.execute(
                    "task_update_step",
                    {
                        "step_id": "step-1",
                        "status": "in_progress",
                    },
                    approved=True,
                )
                self.assertFalse(result.startswith("ERROR"))
                self.assertEqual(
                    orchestrator.current.plan[0].status,
                    "running",
                )

    def test_completion_requires_successful_non_task_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                "os.environ",
                {"JARVIS_WORKSPACE": tmp},
                clear=False,
            ):
                registry = ToolRegistry()
                orchestrator = TaskOrchestrator()
                orchestrator.begin("test")
                orchestrator.set_plan(["open app"])
                register_task_tools(registry, orchestrator)
                blocked = registry.execute(
                    "task_update_step",
                    {
                        "step_id": "step-1",
                        "status": "completed",
                    },
                    approved=True,
                )
                self.assertTrue(blocked.startswith("ERROR"))

                orchestrator.record_tool(
                    "launch_installed_app",
                    {"query": "WhatsApp"},
                    'VERIFIED: {"window_title":"WhatsApp"}',
                    1.0,
                    1,
                )
                allowed = registry.execute(
                    "task_update_step",
                    {
                        "step_id": "step-1",
                        "status": "completed",
                        "result": "Verified WhatsApp window",
                    },
                    approved=True,
                )
                self.assertFalse(allowed.startswith("ERROR"))
                self.assertEqual(
                    orchestrator.current.plan[0].status,
                    "completed",
                )
                self.assertTrue(orchestrator.summary()["verified"])


if __name__ == "__main__":
    unittest.main()
