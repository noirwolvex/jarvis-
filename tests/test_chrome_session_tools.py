from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.chrome_session_tools import (
    cdp_owns_window,
    ensure_chrome_connection,
    ensure_managed_chrome,
    register_chrome_session_tools,
)
from core.tools import ToolRegistry


class ChromeSessionToolsTests(unittest.TestCase):
    def setUp(self):
        current = patch("core.chrome_session_tools.existing_chrome_connection", return_value=None)
        self.existing = current.start()
        self.addCleanup(current.stop)

    def test_existing_personal_chrome_is_returned_without_cdp_or_launch(self):
        self.existing.return_value = {"connected": True, "session_type": "existing-window", "window": {"hwnd": 42}}
        with patch("core.chrome_session_tools._probe_cdp") as probe, \
             patch("core.chrome_session_tools._start_managed_chrome") as launch, \
             patch("core.chrome_session_tools._runtime") as runtime:
            for ensure in (ensure_managed_chrome, ensure_chrome_connection):
                self.assertEqual(json.loads(ensure()), self.existing.return_value)
            probe.assert_not_called()
            launch.assert_not_called()
            runtime.assert_not_called()

    def test_window_appearing_during_start_lock_is_reused_without_launch(self):
        self.existing.side_effect = [None, {"session_type": "existing-window", "window": {"hwnd": 42}}]
        with patch("core.chrome_session_tools._probe_cdp", return_value=None), \
             patch("core.chrome_session_tools._start_managed_chrome") as launch:
            self.assertEqual(json.loads(ensure_managed_chrome())["session_type"], "existing-window")
            launch.assert_not_called()

    def test_connection_does_not_attach_unrelated_cdp_after_native_window_start_race(self):
        with patch("core.chrome_session_tools._probe_cdp", return_value=None), \
             patch("core.chrome_session_tools.ensure_managed_chrome", return_value=json.dumps({"session_type": "existing-window", "window": {"hwnd": 42}})), \
             patch("core.chrome_session_tools._runtime") as runtime, \
             patch("core.chrome_session_tools._wait_for_page_target") as wait:
            self.assertEqual(json.loads(ensure_chrome_connection())["session_type"], "existing-window")
            runtime.assert_not_called()
            wait.assert_not_called()

    @patch("psutil.net_connections")
    @patch("psutil.Process")
    def test_cdp_binding_requires_exact_fresh_listener_process(self, process_factory, connections):
        import psutil
        process = process_factory.return_value
        process.is_running.return_value = True
        process.create_time.return_value = 1234.5
        process.name.return_value = "chrome.exe"
        window = {"hwnd": 812, "process_id": 456, "process_created": 1234.5}
        listener = SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(ip="127.0.0.1", port=9222), pid=456)
        connections.return_value = [listener]
        with patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": "http://127.0.0.1:9222"}):
            self.assertTrue(cdp_owns_window(window))
            for field, value in (("pid", 789), ("pid", None)):
                setattr(listener, field, value)
                self.assertFalse(cdp_owns_window(window))
            listener.pid = 456
            process.create_time.return_value = 1235.5
            self.assertFalse(cdp_owns_window(window))
            process.create_time.return_value = 1234.5
            process.name.return_value = "unrelated.exe"
            self.assertFalse(cdp_owns_window(window))

    @patch("psutil.net_connections")
    @patch("psutil.Process")
    def test_cdp_binding_fails_closed_on_remote_missing_identity_and_inaccessible_listener(self, process_factory, connections):
        import psutil
        process = process_factory.return_value
        process.is_running.return_value = True
        process.create_time.return_value = 1234.5
        process.name.return_value = "chrome.exe"
        window = {"hwnd": 812, "process_id": 456, "process_created": 1234.5}
        for endpoint in ("http://example.com:9222", "http://127.0.0.1.evil:9222", "http://127.0.0.1:invalid", "http://user:pass@127.0.0.1:9222"):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": endpoint}):
                self.assertFalse(cdp_owns_window(window))
        connections.assert_not_called()
        with patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": "http://127.0.0.1:9222"}):
            for field in window:
                incomplete = dict(window)
                incomplete.pop(field)
                self.assertFalse(cdp_owns_window(incomplete))
            connections.side_effect = psutil.AccessDenied()
            self.assertFalse(cdp_owns_window(window))

    @patch("psutil.net_connections")
    @patch("psutil.Process")
    def test_cdp_binding_rechecks_process_after_listener_inspection_and_rejects_split_localhost(self, process_factory, connections):
        import psutil
        process = process_factory.return_value
        process.is_running.return_value = True
        process.name.return_value = "chrome.exe"
        process.create_time.side_effect = [1234.5, 1235.5]
        window = {"hwnd": 812, "process_id": 456, "process_created": 1234.5}
        listeners = [SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(ip="127.0.0.1", port=9222), pid=456)]
        connections.return_value = listeners
        with patch.dict(os.environ, {"JARVIS_CHROME_CDP_URL": "http://localhost:9222"}):
            self.assertFalse(cdp_owns_window(window))
            process.create_time.side_effect = None
            process.create_time.return_value = 1234.5
            listeners.append(SimpleNamespace(status=psutil.CONN_LISTEN, laddr=SimpleNamespace(ip="::1", port=9222), pid=789))
            self.assertFalse(cdp_owns_window(window))

    @patch("core.chrome_session_tools._dedupe_startup_blank_targets")
    @patch("core.chrome_session_tools._settle_page_targets")
    @patch("core.chrome_session_tools._runtime")
    @patch("core.chrome_session_tools._wait_for_page_target")
    @patch("core.chrome_session_tools.ensure_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_startup_probe_race_never_closes_existing_browser_tabs(self, probe, ensure, wait, runtime, settle, dedupe):
        probe.return_value = None
        ensure.return_value = json.dumps({"started": False, "reused": True})
        wait.return_value = [{"id": "user-blank-1", "url": "about:blank"}, {"id": "user-blank-2", "url": "about:blank"}]
        runtime.return_value.call.return_value = {"connected": True}
        result = json.loads(ensure_chrome_connection())
        settle.assert_not_called()
        dedupe.assert_not_called()
        wait.assert_called_once_with(timeout=1.5)
        runtime.return_value.call.assert_called_once_with("connect", endpoint="http://127.0.0.1:9222", session_type="real")
        self.assertTrue(result["reused"])
        self.assertEqual(result["startup_blank_duplicates_closed"], 0)

    @patch("core.chrome_session_tools._start_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_existing_cdp_is_reused_without_opening_another_window(self, probe, start_managed) -> None:
        probe.return_value = {"Browser": "Chrome/140"}

        result = json.loads(ensure_managed_chrome())

        start_managed.assert_not_called()
        self.assertFalse(result["started"])
        self.assertTrue(result["reused"])

    @patch("core.chrome_session_tools._start_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_managed_chrome_starts_once_when_cdp_is_missing(self, probe, start_managed) -> None:
        probe.side_effect = [None, None]
        start_managed.return_value = json.dumps({
            "started": True,
            "pid": 1234,
            "endpoint": "http://127.0.0.1:9222",
            "session_type": "managed",
        })

        result = json.loads(ensure_managed_chrome())

        start_managed.assert_called_once_with()
        self.assertTrue(result["started"])
        self.assertFalse(result["reused"])
        self.assertTrue(result["single_instance_guard"])

    @patch("core.chrome_session_tools._runtime")
    @patch("core.chrome_session_tools._wait_for_page_target")
    @patch("core.chrome_session_tools.ensure_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_fresh_connection_waits_for_real_startup_page_before_playwright_attach(
        self,
        probe,
        ensure_managed,
        wait_for_page,
        runtime_factory,
    ) -> None:
        probe.return_value = None
        ensure_managed.return_value = json.dumps({
            "started": True,
            "pid": 4321,
            "profile": "profile",
            "log": "startup.log",
        })
        wait_for_page.return_value = [{"id": "startup-page", "type": "page", "url": "about:blank"}]
        runtime = Mock()
        runtime.call.return_value = {
            "connected": True,
            "endpoint": "http://127.0.0.1:9222",
            "session_type": "managed",
            "pages": [{"index": 0, "title": "", "url": "about:blank"}],
            "active_url": "about:blank",
            "active_title": "",
        }
        runtime_factory.return_value = runtime

        result = json.loads(ensure_chrome_connection())

        wait_for_page.assert_called_once_with(timeout=5.0)
        runtime.call.assert_called_once_with(
            "connect",
            endpoint="http://127.0.0.1:9222",
            session_type="managed",
        )
        self.assertTrue(result["startup_guard"])
        self.assertTrue(result["page_target_ready"])
        self.assertEqual(result["startup_target_count"], 1)

    @patch("core.chrome_session_tools._runtime")
    @patch("core.chrome_session_tools._wait_for_page_target")
    @patch("core.chrome_session_tools.ensure_managed_chrome")
    @patch("core.chrome_session_tools._probe_cdp")
    def test_fresh_connection_refuses_duplicate_fallback_when_startup_page_never_appears(
        self,
        probe,
        ensure_managed,
        wait_for_page,
        runtime_factory,
    ) -> None:
        probe.return_value = None
        ensure_managed.return_value = json.dumps({"started": True, "pid": 4321})
        wait_for_page.return_value = []
        runtime = Mock()
        runtime_factory.return_value = runtime

        with self.assertRaisesRegex(RuntimeError, "refusing to create a second fallback"):
            ensure_chrome_connection()

        runtime.call.assert_not_called()

    def test_registration_replaces_original_start_and_connect_tools(self) -> None:
        registry = ToolRegistry()
        register_chrome_session_tools(registry)
        self.assertEqual(registry._tools["chrome_start_managed"].handler, ensure_managed_chrome)
        self.assertEqual(registry._tools["chrome_connect_cdp"].handler, ensure_chrome_connection)
        self.assertIn("idempotent", registry._tools["chrome_start_managed"].description)
        self.assertIn("duplicate about:blank", registry._tools["chrome_connect_cdp"].description)


if __name__ == "__main__":
    unittest.main()
