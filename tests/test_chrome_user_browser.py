from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.chrome_user_browser import existing_chrome_connection, preferred_existing_chrome


class ChromeUserBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.window = {"hwnd": 23, "process_id": 45, "process_created": 67.0}
        self.runtime = Mock()
        self.runtime._endpoint = "http://127.0.0.1:9222"
        self.find = self.enterContext(patch("core.chrome_existing_window.find_chrome_window", return_value=self.window))
        self.owns = self.enterContext(patch("core.chrome_session_tools.cdp_owns_window", return_value=False))
        self.connected = self.enterContext(patch("core.chrome_cdp.chrome_is_connected", return_value=True))
        self.enterContext(patch("core.chrome_cdp._RUNTIME", self.runtime))
        self.runtime_getter = self.enterContext(patch("core.chrome_cdp._runtime", return_value=self.runtime))
        self.enterContext(patch("core.chrome_cdp._cdp_url", return_value="http://127.0.0.1:9222"))
        self.cancel = self.enterContext(patch("core.process_control.check_cancelled"))

    def test_open_personal_window_wins_over_an_already_connected_cdp_browser(self) -> None:
        self.assertEqual(preferred_existing_chrome(), self.window)
        self.owns.assert_called_once_with(self.window)
        self.runtime.call.assert_called_once_with("clear_selection")
        self.runtime_getter.assert_not_called()

    def test_no_chrome_window_leaves_managed_connection_policy_to_caller(self) -> None:
        self.find.return_value = None
        self.assertIsNone(preferred_existing_chrome())
        self.owns.assert_not_called()
        self.runtime.call.assert_not_called()

    def test_first_native_window_does_not_instantiate_a_cdp_runtime(self) -> None:
        with patch("core.chrome_cdp._RUNTIME", None):
            self.assertEqual(preferred_existing_chrome(), self.window)
        self.runtime_getter.assert_not_called()

    def test_matching_cdp_window_selects_current_window_without_reconnecting(self) -> None:
        self.owns.return_value = True
        self.assertIsNone(preferred_existing_chrome())
        self.runtime.call.assert_called_once_with("select_current_window")

    def test_matching_unconnected_window_attaches_before_selecting(self) -> None:
        self.owns.return_value = True
        self.connected.return_value = False
        self.assertIsNone(preferred_existing_chrome())
        self.assertEqual(
            [(call.args, call.kwargs) for call in self.runtime.call.call_args_list],
            [(("connect",), {"endpoint": "http://127.0.0.1:9222", "session_type": "real"}),
             (("select_current_window",), {})],
        )

    def test_healthy_connection_to_another_endpoint_cannot_steal_selection(self) -> None:
        self.owns.return_value = True
        self.runtime._endpoint = "http://127.0.0.1:9999"
        self.assertIsNone(preferred_existing_chrome())
        self.assertEqual([call.args for call in self.runtime.call.call_args_list],
                         [("connect",), ("select_current_window",)])
        self.assertEqual(self.runtime.call.call_args_list[0].kwargs["endpoint"], "http://127.0.0.1:9222")

    def test_ambiguous_cdp_selection_retires_old_target_before_native_fallback(self) -> None:
        self.owns.return_value = True
        self.runtime.call.side_effect = [RuntimeError("ambiguous tabs"), None]
        self.assertEqual(preferred_existing_chrome(), self.window)
        self.assertEqual([call.args for call in self.runtime.call.call_args_list],
                         [("select_current_window",), ("clear_selection",)])

    def test_unavailable_matching_cdp_connection_uses_original_window(self) -> None:
        self.owns.return_value = True
        self.connected.return_value = False
        self.runtime.call.side_effect = [TimeoutError("CDP not responding"), None]
        self.assertEqual(preferred_existing_chrome(), self.window)
        self.assertEqual([call.args for call in self.runtime.call.call_args_list],
                         [("connect",), ("clear_selection",)])

    def test_window_inspection_failure_does_not_launch_or_retarget(self) -> None:
        self.find.side_effect = RuntimeError("window inspection failed")
        with self.assertRaisesRegex(RuntimeError, "inspection failed"):
            preferred_existing_chrome()
        self.owns.assert_not_called()
        self.runtime.call.assert_not_called()

    def test_cancellation_is_not_converted_to_native_fallback(self) -> None:
        self.owns.return_value = True
        self.runtime.call.side_effect = RuntimeError("Emergency stop is active")
        self.cancel.side_effect = [None, RuntimeError("Emergency stop is active")]
        with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
            preferred_existing_chrome()
        self.runtime.call.assert_called_once_with("select_current_window")

    def test_connection_reports_the_existing_window_without_claiming_startup(self) -> None:
        with patch("core.chrome_user_browser.preferred_existing_chrome", return_value=self.window), \
             patch("core.chrome_existing_window.read_existing_chrome", return_value={
                 "url": "https://www.google.com/", "title": "Google",
             }) as read:
            result = existing_chrome_connection()
        self.assertTrue(result["connected"])
        self.assertTrue(result["reused"])
        self.assertFalse(result["started"])
        self.assertEqual(result["window"], self.window)
        self.assertEqual(result["session_type"], "existing-window")
        self.assertEqual(result["active_url"], "https://www.google.com/")
        read.assert_called_once_with(self.window)

    def test_connection_absence_does_not_read_a_different_window(self) -> None:
        with patch("core.chrome_user_browser.preferred_existing_chrome", return_value=None), \
             patch("core.chrome_existing_window.read_existing_chrome") as read:
            self.assertIsNone(existing_chrome_connection())
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
