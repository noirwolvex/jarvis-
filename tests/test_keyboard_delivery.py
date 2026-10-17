import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.desktop_input import InputDeliveryError, InputNotDispatchedError, _clipboard_paste, _send_unicode_text, paste_text


class KeyboardDeliveryTests(unittest.TestCase):
    def test_missing_named_editor_never_falls_back_into_current_window(self):
        for verify in (False, True):
            with self.subTest(verify=verify), \
                 patch("core.desktop_input._uia_type_and_verify", return_value=(False, "No Edit control")), \
                 patch("core.desktop_input._send_unicode_text") as native, \
                 patch("core.desktop_input._clipboard_paste") as clipboard:
                with self.assertRaises(InputNotDispatchedError):
                    paste_text("draft", window_title="Requested editor", verify=verify)
                native.assert_not_called()
                clipboard.assert_not_called()

    def test_named_editor_exact_readback_does_not_repeat_the_write(self):
        target, window = Mock(), Mock()
        target.is_visible.return_value = target.is_enabled.return_value = True
        target.get_value.side_effect = ["", "draft"]
        window.descendants.return_value = [target]
        desktop = Mock()
        desktop.return_value.window.return_value = window
        with patch.dict("sys.modules", {"pywinauto": SimpleNamespace(Desktop=desktop)}), \
             patch("core.desktop_input._send_unicode_text") as native, \
             patch("core.desktop_input._clipboard_paste") as clipboard:
            result = paste_text("draft", window_title="Requested editor", verify=True)
        self.assertTrue(result.startswith("VERIFIED:"))
        target.set_edit_text.assert_called_once_with("draft")
        self.assertEqual(target.get_value.call_count, 2)
        native.assert_not_called()
        clipboard.assert_not_called()

    def test_clipboard_fallback_has_no_fixed_delay_and_restores_library_settings(self):
        gui = SimpleNamespace(PAUSE=0.1, FAILSAFE=True, hotkey=Mock())
        clipboard = SimpleNamespace(copy=Mock(), paste=Mock(return_value="draft"))
        with patch.dict("sys.modules", {"pyperclip": clipboard, "pyautogui": gui}), \
             patch("core.desktop_observation.foreground_identity", return_value=7), \
             patch("time.sleep") as sleep:
            _clipboard_paste("draft")
        gui.hotkey.assert_called_once_with("ctrl", "v", _pause=False)
        clipboard.copy.assert_called_once_with("draft")
        sleep.assert_not_called()
        self.assertEqual((gui.PAUSE, gui.FAILSAFE), (0.1, True))

    def test_clipboard_preparation_changes_prevent_paste(self):
        for change in ("foreground", "clipboard", "cancel", "missing_window"):
            with self.subTest(change=change):
                gui = SimpleNamespace(PAUSE=0.1, FAILSAFE=True, hotkey=Mock())
                clipboard = SimpleNamespace(copy=Mock(), paste=Mock(return_value="draft"))
                cancelled = [False]
                def copy(value):
                    cancelled[0] = change == "cancel"
                clipboard.copy.side_effect = copy
                if change == "clipboard":
                    clipboard.paste.return_value = "another copy"
                def check():
                    if cancelled[0]:
                        raise RuntimeError("stopped")
                identities = [0] if change == "missing_window" else [7, 8 if change == "foreground" else 7]
                with patch.dict("sys.modules", {"pyperclip": clipboard, "pyautogui": gui}), \
                     patch("core.desktop_observation.foreground_identity", side_effect=identities), \
                     patch("core.process_control.check_cancelled", side_effect=check):
                    with self.assertRaises(InputNotDispatchedError):
                        _clipboard_paste("draft")
                gui.hotkey.assert_not_called()
                self.assertEqual((gui.PAUSE, gui.FAILSAFE), (0.1, True))

    def test_clipboard_failed_shortcut_is_uncertain_and_releases_modifiers(self):
        gui = SimpleNamespace(PAUSE=0.1, FAILSAFE=True, hotkey=Mock(side_effect=RuntimeError("partial")), keyUp=Mock())
        clipboard = SimpleNamespace(copy=Mock(), paste=Mock(return_value="draft"))
        with patch.dict("sys.modules", {"pyperclip": clipboard, "pyautogui": gui}), \
             patch("core.desktop_observation.foreground_identity", return_value=7):
            with self.assertRaises(InputDeliveryError) as caught:
                _clipboard_paste("draft")
        self.assertNotIsInstance(caught.exception, InputNotDispatchedError)
        gui.hotkey.assert_called_once()
        self.assertEqual([call.args[0] for call in gui.keyUp.call_args_list], ["v", "ctrl"])
        self.assertEqual((gui.PAUSE, gui.FAILSAFE), (0.1, True))

    def test_uia_cancellation_before_or_during_discovery_never_types_or_falls_back(self):
        for stage in ("before", "wait", "discovery", "focus"):
            with self.subTest(stage=stage):
                window, target = Mock(), Mock()
                window.descendants.return_value = [target]
                target.is_visible.return_value = target.is_enabled.return_value = True
                cancelled = [stage == "before"]
                def stop(*args, **kwargs):
                    cancelled[0] = True
                if stage == "wait":
                    window.wait.side_effect = stop
                elif stage == "discovery":
                    window.descendants.side_effect = lambda **kwargs: (stop() or [target])
                elif stage == "focus":
                    target.set_focus.side_effect = stop
                def check():
                    if cancelled[0]:
                        raise RuntimeError("stopped")
                desktop = Mock()
                desktop.return_value.window.return_value = window
                with patch.dict("sys.modules", {"pywinauto": SimpleNamespace(Desktop=desktop)}), \
                     patch("core.process_control.check_cancelled", side_effect=check), \
                     patch("core.desktop_input._send_unicode_text") as native, \
                     patch("core.desktop_input._clipboard_paste") as clipboard:
                    with self.assertRaises(InputDeliveryError):
                        paste_text("fixture", window_title="Fixture")
                target.set_edit_text.assert_not_called()
                native.assert_not_called()
                clipboard.assert_not_called()
                if stage in {"before", "wait"}:
                    window.set_focus.assert_not_called()

    def test_surrogates_are_not_split_and_foreground_loss_stops_remaining_batches(self):
        chunks = []
        def send(count, inputs, size):
            units = [item.ki.wScan for item in inputs if item.ki.dwFlags == 4]
            decoded = b"".join(unit.to_bytes(2, "little") for unit in units).decode("utf-16-le")
            chunks.append(decoded)
            return count
        with patch("core.desktop_input.ctypes.windll", SimpleNamespace(user32=SimpleNamespace(SendInput=send)), create=True), \
             patch("core.desktop_observation.foreground_identity", side_effect=[7, 7, 8]):
            with self.assertRaises(InputDeliveryError):
                _send_unicode_text("a" * 63 + "😀" * 100)
        self.assertEqual(chunks, ["a" * 63])

    def test_partial_delivery_releases_only_and_never_pastes_fallback(self):
        flags = []
        def send(count, inputs, size):
            flags.append([item.ki.dwFlags for item in inputs])
            return 1 if len(flags) == 1 else count
        with patch("core.desktop_input.ctypes.windll", SimpleNamespace(user32=SimpleNamespace(SendInput=send)), create=True), \
             patch("core.desktop_observation.foreground_identity", return_value=7), \
             patch("core.desktop_input._clipboard_paste") as paste:
            with self.assertRaises(InputDeliveryError):
                paste_text("hello")
        self.assertEqual(len(flags), 2)
        self.assertTrue(all(flag & 2 for flag in flags[1]))
        paste.assert_not_called()

    def test_delivery_has_no_fixed_typing_delay_or_unearned_verification(self):
        with patch("core.desktop_input._send_unicode_text") as send, \
             patch("time.sleep") as sleep:
            result = paste_text("fixture" * 500)
        send.assert_called_once()
        sleep.assert_not_called()
        self.assertNotIn("VERIFIED:", result)

    def test_cancellation_between_batches_never_falls_back(self):
        sent = Mock(side_effect=lambda count, inputs, size: count)
        with patch("core.desktop_input.ctypes.windll", SimpleNamespace(user32=SimpleNamespace(SendInput=sent)), create=True), \
             patch("core.desktop_observation.foreground_identity", return_value=7), \
             patch("core.process_control.check_cancelled", side_effect=[None, None, RuntimeError("stopped")]), \
             patch("core.desktop_input._clipboard_paste") as paste:
            with self.assertRaises(InputDeliveryError):
                paste_text("a" * 200)
        self.assertEqual(sent.call_count, 1)
        paste.assert_not_called()

    def test_desktop_key_aliases_normalize_to_pyautogui_keys(self):
        from core.desktop_control_tools import _key, _KEY_ALIASES
        for alias, target in [
            ("control", "ctrl"), ("windows", "win"), ("super", "win"), ("meta", "win"),
            ("escape", "esc"), ("return", "enter"), ("del", "delete"), ("ins", "insert"),
            ("page_up", "pageup"), ("pgup", "pageup"), ("page_down", "pagedown"), ("pgdn", "pagedown"),
            ("leftarrow", "left"), ("rightarrow", "right"), ("uparrow", "up"), ("downarrow", "down"),
            ("menu", "apps"), ("lctrl", "ctrlleft"), ("rctrl", "ctrlright"),
        ]:
            self.assertEqual(_key(alias), target, f"Alias {alias} did not map to {target}")

    def test_desktop_hotkey_cleanup_handles_individual_release_exceptions(self):
        from core.tools import _desktop_hotkey
        downs = []
        ups = []
        def fake_down(k):
            downs.append(k)
        def fake_up(k):
            ups.append(k)
            if k == "ctrl":
                raise OSError("Synthetic key release glitch")
        with patch("core.desktop_control_tools.desktop_key_down", side_effect=fake_down), \
             patch("core.desktop_control_tools.desktop_key_up", side_effect=fake_up):
            result = _desktop_hotkey(["ctrl", "c"])
        self.assertEqual(downs, ["ctrl", "c"])
        self.assertEqual(ups, ["c", "ctrl"])
        self.assertIn("ctrl+c", result)

    def test_browser_hotkey_aliases_support_super_return_ins_del_page_keys(self):
        from core.universal_interaction import _browser_hotkey
        self.assertEqual(_browser_hotkey(["ctrl", "super", "c"]), "Control+Meta+C")
        self.assertEqual(_browser_hotkey(["return"]), "Enter")
        self.assertEqual(_browser_hotkey(["del"]), "Delete")
        self.assertEqual(_browser_hotkey(["ins"]), "Insert")
        self.assertEqual(_browser_hotkey(["pgdn"]), "PageDown")
        self.assertEqual(_browser_hotkey(["page_up"]), "PageUp")

    def test_ui_hotkey_tokens_support_navigation_and_modifiers(self):
        from core.semantic_ui_tools import _hotkey_tokens
        tokens = _hotkey_tokens(["super", "return", "ins", "del", "pgup", "pgdn", "ctrlleft"])
        self.assertEqual(tokens, ["super", "return", "ins", "del", "pgup", "pgdn", "ctrlleft"])
