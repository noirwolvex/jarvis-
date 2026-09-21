import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.desktop_input import InputDeliveryError, _send_unicode_text, paste_text


class KeyboardDeliveryTests(unittest.TestCase):
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
             patch("core.desktop_input.time.sleep") as sleep:
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
