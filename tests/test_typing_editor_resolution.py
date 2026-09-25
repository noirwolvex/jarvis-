from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.native_ui_input import ui_type_native
from test_semantic_ui_tools import _Control, _Editor, _Info, _Window


def identified(control, runtime_id, process_id=42):
    control.element_info.runtime_id = runtime_id
    control.element_info.process_id = process_id
    return control


def editor(name="Type a message", runtime_id=(42, 3), *, process_id=42, readonly=False):
    control = identified(_Editor(), runtime_id, process_id)
    control.element_info.name = name
    control.iface_value.CurrentIsReadOnly = readonly
    return control


def whatsapp_tree():
    # A WebView can expose eight wrappers for only three distinct UIA nodes.
    roots = [identified(_Control("WhatsApp", "Document", automation_id="RootWebArea"), (42, 1))
             for _ in range(3)]
    searches = [editor("Search or start a new chat", (42, 2)) for _ in range(2)]
    composers = [editor() for _ in range(3)]
    win = _Window([*roots, *searches, *composers])
    win.element_info = _Info("WhatsApp", "Window")
    identified(win, (42, 0))
    for control in win._controls:
        control.parent = lambda: win
    return win, composers[0]


class TypingEditorResolutionTests(unittest.TestCase):
    def setUp(self):
        ui._SNAPSHOTS.invalidate()
        for mocked in (patch.object(ui, "_foreground_hwnd", return_value=123),
                       patch("core.semantic_ui_guard._browser_block", return_value=None)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.addCleanup(ui._SNAPSHOTS.invalidate)

    def test_webview_aliases_resolve_one_composer_without_coordinates(self):
        win, composer = whatsapp_tree()

        self.assertEqual(len(win._controls), 8)
        self.assertEqual(len(ui._descendants(win)), 3)
        self.assertIs(ui._find_control(win, editable=True), composer)

    def test_search_labels_never_compete_with_an_omitted_message_composer(self):
        for label in ("Search or start a new chat", "Search messages", "Find chat", "Filter replies"):
            for kind in ("Edit", "Document"):
                with self.subTest(label=label, kind=kind):
                    search = editor(label, (42, 2))
                    composer = editor("Type a message to Fixture Contact")
                    composer.element_info.control_type = kind
                    win = _Window([search, composer])
                    self.assertIs(ui._find_control(win, editable=True), composer)
                    # Explicit search typing remains supported.
                    self.assertIs(ui._find_control(win, label, editable=True), search)

    def test_typing_queries_provider_for_editors_without_materializing_unrelated_controls(self):
        win, composer = whatsapp_tree()
        unrelated = identified(_Control("Button", "Button"), (42, 99))
        win._controls.extend([unrelated] * 1000)
        win.descendants = Mock(wraps=win.descendants)
        with patch.object(ui, "_process_id", wraps=ui._process_id) as pid:
            self.assertIs(ui._find_control(win, editable=True), composer)
        self.assertEqual([call.kwargs for call in win.descendants.call_args_list],
                         [{"control_type": "Edit"}, {"control_type": "Document"}])
        self.assertTrue(all(call.args[0] is not unrelated for call in pid.call_args_list))

    def test_button_resolution_uses_canonical_provider_type_and_rejects_duplicates(self):
        button = identified(_Control("Send", "Button"), (42, 9))
        win = _Window([button, editor()])
        win.descendants = Mock(wraps=win.descendants)
        self.assertIs(ui._find_control(win, "Send", "button"), button)
        win.descendants.assert_called_once_with(control_type="Button")
        win._controls.append(identified(_Control("Send", "Button"), (42, 10)))
        with self.assertRaisesRegex(RuntimeError, "2 exact"):
            ui._find_control(win, "Send", "Button")

    def test_native_typing_uses_resolved_editor_and_exact_unicode_readback(self):
        win, composer = whatsapp_tree()
        text = "مرحبا hello 😀"
        status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "WhatsApp"}}
        client = Mock()
        guarded_dispatches = []

        def deliver(value, observed_status, *, before_dispatch):
            self.assertEqual(observed_status, status)
            self.assertTrue(callable(before_dispatch))
            # Exercise the real identity/generation/focus guard before mutation.
            before_dispatch()
            guarded_dispatches.append(value)
            composer.value = value
            return {"executed": True, "simulation": False}

        client.type_text.side_effect = deliver
        with patch.object(ui, "_window", return_value=win), \
             patch("core.rust_engine._preflight", return_value=(client, status)), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"):
            result = ui_type_native(text)

        self.assertTrue(result.startswith("VERIFIED:"), result)
        data = json.loads(result.split(": ", 1)[1])
        self.assertEqual(data["method"], "rust_native_input")
        self.assertEqual(data["control"]["runtime_id"], [42, 3])
        self.assertEqual(data["characters"], len(text))
        self.assertEqual(composer.get_value(), text)
        self.assertEqual(guarded_dispatches, [text])
        self.assertFalse(data["submitted"])
        client.type_text.assert_called_once()
        client.click.assert_not_called()
        client.hotkey.assert_not_called()

    def test_reported_whatsapp_mission_completes_typing_without_model_or_coordinate_recovery(self):
        from test_workflow_execution import WorkflowExecutionTests
        from core.native_ui_input import register_native_ui_input_tools
        from core.permissions import Risk
        fixture = WorkflowExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        win, composer = whatsapp_tree()
        # Live WhatsApp reports a newline-only initial value through ValuePattern.
        composer.value = "\n"
        from test_native_editor_caret import TextPattern
        composer.iface_text = TextPattern(composer)
        register_native_ui_input_tools(fixture.agent.tools)
        launches = Mock(return_value="VERIFIED: WhatsApp window ready")
        selections = Mock(return_value="VERIFIED: requested chat selected")
        fixture.register("launch_installed_app", Risk.MEDIUM, launches, {"type": "object"})
        fixture.register("whatsapp_select_chat_native", Risk.MEDIUM, selections, {"type": "object"})
        client = Mock()
        status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "WhatsApp"}}
        def deliver(text, observed, *, before_dispatch):
            before_dispatch()
            composer.value = text
            return {"executed": True, "simulation": False}
        client.type_text.side_effect = deliver
        with patch.object(ui, "_window", return_value=win) as resolve_window, \
             patch("core.rust_engine._preflight", return_value=(client, status)), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"), \
             patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = fixture.agent.run("OPEN WHATSAPP AND PRESS THE FIRST CHAT THEN WRITE HI")
        self.assertIn("Completed and verified", result)
        self.assertEqual(composer.value, "HI")
        resolve_window.assert_called_once_with("WhatsApp")
        launches.assert_called_once()
        selections.assert_called_once_with(position=1)
        composer.iface_value.SetValue.assert_not_called()
        client.type_text.assert_called_once()
        client.click.assert_not_called()
        self.assertEqual([t.name for t in fixture.agent.orchestrator.current.traces],
                         ["launch_installed_app", "whatsapp_select_chat_native", "ui_type_native"])
        fixture.agent.client.chat.completions.create.assert_not_called()
        metrics = fixture.agent.orchestrator.current.metrics
        self.assertEqual(metrics["whatsapp_ordinal_fast_path"], 1)
        self.assertTrue(all(type(value) is int and value >= 0 for value in metrics.values()))

    def test_distinct_composer_identities_remain_ambiguous(self):
        win, _ = whatsapp_tree()
        other = editor(runtime_id=(42, 4))
        other._owner = win
        win._controls.append(other)

        with self.assertRaisesRegex(RuntimeError, "exact visible enabled matches"):
            ui._find_control(win, editable=True)

    def test_exact_reported_mission_recovers_post_click_com_error_then_types_once(self):
        from test_workflow_execution import WorkflowExecutionTests
        from test_native_editor_caret import TextPattern
        from test_uia_query import COMError
        from core.native_ui_input import register_native_ui_input_tools
        from core.permissions import Risk
        fixture = WorkflowExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        win, composer = whatsapp_tree()
        win.rectangle = lambda: SimpleNamespace(left=0, top=0, right=1200, bottom=800)
        row = identified(_Control("Fixture chat", "DataItem", top=130, bottom=200), (42, 10))
        row._owner = win
        row.is_selected = Mock(return_value=False)
        row.iface_selection_item = Mock()
        win._controls.insert(0, row)
        composer._rect.left, composer._rect.right = 650, 1100
        composer.value = "\n"
        composer.iface_text = TextPattern(composer)
        register_native_ui_input_tools(fixture.agent.tools)
        launches = Mock(return_value="VERIFIED: WhatsApp window ready")
        fixture.register("launch_installed_app", Risk.MEDIUM, launches, {"type": "object"})
        client = Mock()
        status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "WhatsApp"}}
        clicked = [False]
        failed_read = [False]
        original_query = ui._query_descendants
        def query(*args, **kwargs):
            if clicked[0] and not failed_read[0]:
                failed_read[0] = True
                raise COMError()
            return original_query(*args, **kwargs)
        def click(x, y, observed, *, before_dispatch):
            before_dispatch()
            clicked[0] = True
            composer.element_info.name = "Type a message to Fixture chat"
            return {"executed": True, "simulation": False}
        def type_text(text, observed, *, before_dispatch):
            before_dispatch()
            composer.value = text
            return {"executed": True, "simulation": False}
        client.click.side_effect = click
        client.type_text.side_effect = type_text
        with patch.object(ui, "_window", return_value=win), \
             patch.object(ui, "_query_descendants", side_effect=query), \
             patch("core.rust_engine._preflight", return_value=(client, status)), \
             patch("core.rust_engine.native_engine_mode", return_value="rust"), \
             patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = fixture.agent.run("OPEN WHATSAPP AND PRESS THE FIRST CHAT AFTER WRITE HI")
        self.assertIn("Completed and verified", result)
        self.assertTrue(failed_read[0])
        self.assertEqual(composer.value, "HI")
        launches.assert_called_once()
        client.click.assert_called_once()
        client.type_text.assert_called_once()
        client.hotkey.assert_not_called()
        self.assertEqual([trace.name for trace in fixture.agent.orchestrator.current.traces],
                         ["launch_installed_app", "whatsapp_select_chat_native", "ui_type_native"])
        fixture.agent.client.chat.completions.create.assert_not_called()

    def test_native_stop_in_fast_mission_does_not_start_model_recovery(self):
        from test_workflow_execution import WorkflowExecutionTests
        from core.permissions import Risk
        fixture = WorkflowExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.register("launch_installed_app", Risk.MEDIUM, Mock(return_value="VERIFIED: app ready"), {"type": "object"})
        select = Mock(return_value="ERROR executing whatsapp_select_chat_native: RustEngineUnavailable: Rust daemon emergency stop is latched; restart the daemon before native execution")
        fixture.register("whatsapp_select_chat_native", Risk.MEDIUM, select, {"type": "object"})
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = fixture.agent.run("OPEN WHATSAPP AND SELECT THE FIRST CHAT AND WRITE HI")
        self.assertIn("Restart JARVIS", result)
        self.assertEqual(fixture.agent.orchestrator.current.status, "waiting_user")
        select.assert_called_once()
        self.assertEqual([t.name for t in fixture.agent.orchestrator.current.traces],
                         ["launch_installed_app", "whatsapp_select_chat_native"])
        fixture.agent.client.chat.completions.create.assert_not_called()

    def test_unknown_runtime_ids_do_not_merge_distinct_controls(self):
        for runtime_id in (None, [], ()):
            with self.subTest(runtime_id=runtime_id):
                win = _Window([editor(runtime_id=runtime_id), editor(runtime_id=runtime_id)])
                self.assertEqual(len(ui._descendants(win)), 2)
                with self.assertRaisesRegex(RuntimeError, "2 exact"):
                    ui._find_control(win, editable=True)

        win = _Window([_Editor(), _Editor()])
        self.assertEqual(len(ui._descendants(win)), 2)
        with self.assertRaisesRegex(RuntimeError, "2 exact"):
            ui._find_control(win, editable=True)

    def test_identical_runtime_ids_from_distinct_processes_do_not_merge(self):
        win = _Window([editor(process_id=42), editor(process_id=84)])

        self.assertEqual(len(ui._descendants(win)), 2)
        with self.assertRaisesRegex(RuntimeError, "2 exact"):
            ui._find_control(win, editable=True)

    def test_readonly_edit_and_document_roots_are_not_automatic_typing_targets(self):
        root = identified(_Control("Message history", "Document", automation_id="RootWebArea"), (42, 1))
        readonly = editor(runtime_id=(42, 2), readonly=True)
        composer = editor(runtime_id=(42, 3))

        for excluded in (root, readonly):
            with self.subTest(control_type=excluded.element_info.control_type):
                with self.assertRaisesRegex(RuntimeError, "No visible enabled"):
                    ui._find_control(_Window([excluded]), editable=True)
                self.assertIs(ui._find_control(_Window([excluded, composer]), editable=True), composer)

    def test_focused_selector_resolves_deduplicated_composer(self):
        win, composer = whatsapp_tree()

        self.assertIs(ui._find_control(win, "Type a message", editable=True,
                                      selector={"focused": True}), composer)

    def test_selector_excludes_readonly_fields_and_document_roots(self):
        for excluded in (
            identified(_Control("Message", "Document", automation_id="RootWebArea"), (42, 1)),
            editor("Message", readonly=True),
        ):
            excluded.has_keyboard_focus = lambda: True
            with self.subTest(control_type=excluded.element_info.control_type):
                with self.assertRaisesRegex(RuntimeError, "0 exact"):
                    ui._find_control(_Window([excluded]), "Message", editable=True,
                                     selector={"focused": True})

    def test_aliases_do_not_consume_unique_tree_limit_or_hide_later_editor(self):
        aliases = [identified(_Control("Search", "Edit"), (42, 1)) for _ in range(701)]
        composer = editor()
        win = _Window([*aliases, composer])

        self.assertEqual(len(ui._descendants(win, require_complete=True)), 2)
        self.assertIs(ui._find_control(win, "Type a message", editable=True,
                                      selector={"focused": True}), composer)

    def test_genuinely_oversized_tree_still_rejects_incomplete_selection(self):
        controls = [identified(_Control("Item", "Button"), (42, index)) for index in range(701)]

        with self.assertRaisesRegex(RuntimeError, "exceeds 700"):
            ui._descendants(_Window(controls), require_complete=True)


if __name__ == "__main__":
    unittest.main()
