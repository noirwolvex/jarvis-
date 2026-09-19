import tempfile
import unittest
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from core.desktop_input import InputDeliveryError
from core.native_ui_input import ui_type_native
from core.orchestrator import TaskOrchestrator
from core.task_tools import register_task_tools
from core.tools import ToolRegistry
from test_semantic_ui_tools import _Editor, _Window


class NativeSemanticBindingTests(unittest.TestCase):
    def setUp(self):
        self.editor = _Editor()
        self.win = _Window([self.editor])
        self.client = Mock()
        self.status = {"foreground": {"hwnd": 123, "process_id": 42, "title": "Demo"}}
        for mocked in (patch.object(ui, "_window", return_value=self.win),
                       patch.object(ui, "_focus_window", return_value=123), patch.object(ui, "_guard_foreground"),
                       patch("core.rust_engine.native_engine_mode", return_value="rust")):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_changed_foreground_status_never_delivers_to_other_application(self):
        self.status["foreground"]["hwnd"] = 999
        with patch("core.rust_engine._preflight", return_value=(self.client, self.status)), \
             self.assertRaisesRegex(InputDeliveryError, "does not match"):
            ui_type_native("fixture")
        self.client.type_text.assert_not_called()

    def test_native_preflight_cannot_steal_composer_focus(self):
        self.editor.has_keyboard_focus = Mock(return_value=True)
        def preflight():
            self.editor.has_keyboard_focus.return_value = False
            return self.client, self.status
        with patch("core.rust_engine._preflight", side_effect=preflight), self.assertRaisesRegex(InputDeliveryError, "focus or draft"):
            ui_type_native("fixture")
        self.client.type_text.assert_not_called()

    def test_existing_draft_or_native_newline_requires_semantic_write(self):
        for draft, text in [("draft", "fixture"), ("", "one\ntwo")]:
            self.editor.value = draft
            with patch("core.rust_engine._preflight", return_value=(self.client, self.status)), \
                 self.assertRaisesRegex(InputDeliveryError, "Value pattern"):
                ui_type_native(text)
        self.client.type_text.assert_not_called()

    def test_native_success_requires_exact_uia_readback(self):
        def deliver(text, status):
            self.editor.value = text
            return {"executed": True, "simulation": False}
        self.client.type_text.side_effect = deliver
        with patch("core.rust_engine._preflight", return_value=(self.client, self.status)):
            result = ui_type_native("fixture")
        self.assertTrue(result.startswith("VERIFIED:"))
        self.client.type_text.assert_called_once()

    def test_step_completion_rejects_delivery_and_stale_verified_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = TaskOrchestrator(directory)
            orchestrator.begin("fixture")
            orchestrator.set_plan(["type"])
            registry = ToolRegistry()
            register_task_tools(registry, orchestrator)
            orchestrator.record_tool("ui_inspect", {}, "VERIFIED: before", 1, 1)
            orchestrator.record_tool("desktop_type", {}, "DELIVERED: typed", 1, 1, mutation=True)
            request = {"step_id": "step-1", "status": "completed"}
            self.assertTrue(registry.execute("task_update_step", request).startswith("ERROR"))
            self.assertEqual(orchestrator.current.plan[0].status, "pending")
            orchestrator.record_tool("ui_inspect", {}, "VERIFIED: after", 1, 1)
            self.assertFalse(registry.execute("task_update_step", request).startswith("ERROR"))
            self.assertEqual(orchestrator.current.plan[0].status, "completed")


if __name__ == "__main__":
    unittest.main()
