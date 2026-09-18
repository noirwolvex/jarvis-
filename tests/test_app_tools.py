from __future__ import annotations

import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from core.app_tools import (
    _BLOCKED_EXECUTABLE_STEMS,
    _candidate,
    _clean_query,
    _decode_start_apps,
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
        with patch("core.app_tools.os", SimpleNamespace(name="nt")), \
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
        with patch("core.app_tools.os", SimpleNamespace(name="nt")), \
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
