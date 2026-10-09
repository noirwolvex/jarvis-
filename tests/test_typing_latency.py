from __future__ import annotations

from collections import Counter
import unittest
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.desktop_input import InputDeliveryError, InputNotDispatchedError
from core.native_ui_input import ui_type_native
from core.rust_engine import RustEngineUnavailable
from test_semantic_ui_tools import _Control, _Window


class _ValuePattern:
    CurrentIsReadOnly = False

    def __init__(self, editor):
        self.editor = editor
        self.SetValue = Mock(side_effect=lambda value: setattr(editor, "value", value))

    @property
    def CurrentValue(self):
        self.editor.calls["CurrentValue"] += 1
        return self.editor.current_value()


class _TextRange:
    def __init__(self, editor):
        self.editor = editor
        # A snapshot makes accidental DocumentRange reuse visible in readback.
        self.text = editor.current_value()

    def GetText(self, limit):
        self.editor.calls["GetText"] += 1
        return self.text[:limit]


class _TextPattern:
    def __init__(self, editor):
        self.editor = editor

    @property
    def DocumentRange(self):
        self.editor.calls["DocumentRange"] += 1
        return _TextRange(self.editor)


class _ProviderEditor(_Control):
    def __init__(self, source="text"):
        super().__init__("Message", "Edit")
        self.source = source
        self.value = ""
        self.failed = False
        self.calls = Counter()
        self.value_pattern = _ValuePattern(self)
        self.text_pattern = _TextPattern(self)

    def current_value(self):
        if self.failed:
            raise RuntimeError("Provider disconnected")
        return self.value

    def get_value(self):
        self.calls["get_value"] += 1
        if self.source != "get_value":
            self.calls["unsupported"] += 1
            raise AttributeError("get_value is unsupported")
        return self.current_value()

    @property
    def iface_value(self):
        self.calls["iface_value"] += 1
        if self.source == "text":
            self.calls["unsupported"] += 1
            raise AttributeError("ValuePattern is unsupported")
        return self.value_pattern

    @property
    def iface_text(self):
        self.calls["iface_text"] += 1
        return self.text_pattern

    def has_keyboard_focus(self):
        return True


class TypingLatencyTests(unittest.TestCase):
    def setUp(self):
        ui._SNAPSHOTS.invalidate()
        self.addCleanup(ui._SNAPSHOTS.invalidate)
        self.client = Mock()
        self.status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "Demo"}}
        self.resolve_window = self._patch(ui, "_window")
        self._patch(ui, "_foreground_hwnd", return_value=123)
        for name, kwargs in (
            ("core.semantic_ui_guard._browser_block", {"return_value": None}),
            ("core.rust_engine._preflight", {"return_value": (self.client, self.status)}),
            ("core.rust_engine.native_engine_mode", {"return_value": "rust"}),
        ):
            mocked = patch(name, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.install_editor()

    def _patch(self, obj, name, **kwargs):
        mocked = patch.object(obj, name, **kwargs)
        self.addCleanup(mocked.stop)
        return mocked.start()

    def install_editor(self, source="text"):
        self.editor = _ProviderEditor(source)
        self.window = _Window([self.editor])
        self.resolve_window.return_value = self.window
        self.resolve_window.reset_mock()
        self.client.reset_mock(side_effect=True)
        self.client.type_text.side_effect = self.deliver

    def deliver(self, text, status, *, before_dispatch):
        before_dispatch()
        self.editor.value += text
        return {"executed": True, "simulation": False}

    @staticmethod
    def poll_once(probe, **kwargs):
        if not probe():
            raise TimeoutError("Exact readback failed")

    def test_text_only_typing_probes_unsupported_providers_once_and_keeps_every_live_read(self):
        for typing, reads, old_failures, new_failures in (
            (ui_type_native, 4, 9, 3),
            (ui.ui_type, 5, 12, 4),
        ):
            with self.subTest(typing=typing.__name__):
                # Exercise the same action with its previous per-read resolution.
                self.install_editor()
                with patch.object(ui, "_ControlValueReader", side_effect=lambda control: lambda: ui._control_value(control)):
                    self.assertTrue(typing("hello").startswith("VERIFIED:"))
                baseline = self.editor.calls.copy()
                self.install_editor()
                self.assertTrue(typing("hello").startswith("VERIFIED:"))
                self.assertEqual(baseline["unsupported"], old_failures)
                self.assertEqual(self.editor.calls["unsupported"], new_failures)
                self.assertEqual(self.editor.calls["get_value"], 1)
                self.assertEqual(self.editor.calls["iface_text"], 1)
                for key in ("DocumentRange", "GetText"):
                    self.assertEqual(baseline[key], reads)
                    self.assertEqual(self.editor.calls[key], reads)
                self.assertEqual(self.editor.value, "hello")
                self.client.type_text.assert_called_once()

    def test_value_pattern_binding_reads_current_value_without_repeated_interface_resolution(self):
        self.install_editor("value")
        self.assertTrue(ui_type_native("hello").startswith("VERIFIED:"))
        self.assertEqual(self.editor.calls["get_value"], 1)
        self.assertEqual(self.editor.calls["iface_value"], 2)  # read-only capability + reader
        self.assertEqual(self.editor.calls["CurrentValue"], 4)
        self.assertEqual(self.editor.calls["iface_text"], 0)

    def test_bound_reader_failure_does_not_switch_providers(self):
        for source in ("get_value", "value", "text"):
            with self.subTest(source=source):
                editor = _ProviderEditor(source)
                read = ui._ControlValueReader(editor)
                self.assertEqual(read(), "")
                editor.failed = True
                before = editor.calls.copy()
                self.assertIsNone(read())
                self.assertEqual(editor.calls["iface_value"], before["iface_value"])
                self.assertEqual(editor.calls["iface_text"], before["iface_text"])

    def test_reader_is_action_scoped_and_one_argument_reader_retains_fallback(self):
        editor = _ProviderEditor("get_value")
        read = ui._ControlValueReader(editor)
        self.assertEqual(read(), "")
        editor.source = "text"
        editor.value = "new draft"
        self.assertIsNone(read())
        self.assertEqual(ui._control_value(editor), "new draft")
        self.assertEqual(ui._ControlValueReader(editor)(), "new draft")

    def test_text_reader_rechecks_length_on_each_fresh_range(self):
        editor = _ProviderEditor()
        read = ui._ControlValueReader(editor)
        self.assertEqual(read(), "")
        editor.value = "x" * 4097
        self.assertIsNone(read())
        editor.value = "x" * 4096
        self.assertEqual(read(), editor.value)
        self.assertEqual(editor.calls["DocumentRange"], 3)

    def test_capture_time_draft_provider_identity_and_generation_changes_prevent_dispatch(self):
        for typing in (ui_type_native, ui.ui_type):
            for change in ("draft", "provider", "identity", "generation"):
                with self.subTest(typing=typing.__name__, change=change):
                    self.install_editor()
                    delivered = []
                    def prepare(text, status, *, before_dispatch):
                        if change == "draft":
                            self.editor.value = "human draft"
                        elif change == "provider":
                            self.editor.failed = True
                        elif change == "identity":
                            self.editor.element_info.runtime_id = (99, 3)
                        else:
                            ui._SNAPSHOTS.invalidate(123)
                        before_dispatch()
                        delivered.append(text)
                    self.client.type_text.side_effect = prepare
                    with self.assertRaises(InputNotDispatchedError):
                        typing("hello")
                    self.assertEqual(delivered, [])
                    self.editor.value_pattern.SetValue.assert_not_called()

    def test_failed_or_inexact_readback_never_verifies_or_replays(self):
        for typing in (ui_type_native, ui.ui_type):
            for failure in ("provider", "partial"):
                with self.subTest(typing=typing.__name__, failure=failure):
                    self.install_editor()
                    def deliver(text, status, *, before_dispatch):
                        before_dispatch()
                        self.editor.value = "hel"
                        self.editor.failed = failure == "provider"
                        return {"executed": True, "simulation": False}
                    self.client.type_text.side_effect = deliver
                    with patch.object(ui, "wait_until", side_effect=self.poll_once), \
                         patch("core.ui_state.wait_until", side_effect=self.poll_once):
                        with self.assertRaises(InputDeliveryError):
                            typing("hello")
                    self.client.type_text.assert_called_once()

    def test_compatibility_fallback_reuses_target_and_preserves_generation_guard(self):
        for fallback in ("no_client", "unavailable"):
            for stale in (False, True):
                with self.subTest(fallback=fallback, stale=stale):
                    self.install_editor("value")
                    initial_generation = ui._SNAPSHOTS.generation
                    def preflight():
                        if fallback == "no_client" and stale:
                            ui._SNAPSHOTS.invalidate(123)
                        return (None if fallback == "no_client" else self.client), self.status
                    def unavailable(*args, **kwargs):
                        if stale:
                            ui._SNAPSHOTS.invalidate(123)
                        raise RustEngineUnavailable("Unavailable before dispatch")
                    self.client.type_text.side_effect = unavailable
                    with patch("core.rust_engine._preflight", side_effect=preflight), \
                         patch("core.rust_engine.native_engine_mode", return_value="auto"), \
                         patch.object(ui, "_resolve_input_control", wraps=ui._resolve_input_control) as resolve:
                        if stale:
                            with self.assertRaises(InputNotDispatchedError):
                                ui_type_native("hello")
                            self.editor.value_pattern.SetValue.assert_not_called()
                        else:
                            self.assertTrue(ui_type_native("hello").startswith("VERIFIED:"))
                            self.editor.value_pattern.SetValue.assert_called_once_with("hello")
                            self.assertEqual(ui._SNAPSHOTS.generation, initial_generation + (1 if fallback == "no_client" else 2))
                            self.assertEqual(self.editor.calls["get_value"], 1)
                    resolve.assert_called_once()
                    self.resolve_window.assert_called_once()

    def test_compatibility_fallback_cannot_replace_a_failed_bound_provider(self):
        for fallback in ("no_client", "unavailable"):
            with self.subTest(fallback=fallback):
                self.install_editor("get_value")
                def preflight():
                    if fallback == "no_client":
                        self.editor.source = "value"
                        return None, self.status
                    return self.client, self.status
                def unavailable(*args, **kwargs):
                    self.editor.source = "value"
                    raise RustEngineUnavailable("Unavailable before dispatch")
                self.client.type_text.side_effect = unavailable
                with patch("core.rust_engine._preflight", side_effect=preflight), \
                     patch("core.rust_engine.native_engine_mode", return_value="auto"):
                    with self.assertRaises(InputNotDispatchedError):
                        ui_type_native("hello")
                self.assertEqual(self.editor.calls["CurrentValue"], 0)
                self.editor.value_pattern.SetValue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
