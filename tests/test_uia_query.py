from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import semantic_ui_tools as ui
from test_semantic_ui_tools import _Control, _Window


class COMError(Exception):
    def __init__(self, hresult=-2147220991):
        self.hresult = hresult
        super().__init__(hresult, "fixture provider changed during observation")


class UiaProviderQueryTests(unittest.TestCase):
    def setUp(self):
        self.editor = _Control("Type a message", "Edit")
        self.root = _Control("Page", "Document", automation_id="RootWebArea")
        self.window = _Window([])
        for index, element in enumerate((self.editor, self.root)):
            element.GetCachedPropertyValue = lambda prop, index=index: (42, index) if prop == 30000 else 42
        self.elements = Mock(Length=2)
        self.elements.GetElement.side_effect = [self.editor, self.root]
        self.provider = Mock()
        self.provider.FindAllBuildCache.return_value = self.elements
        self.window.element_info = SimpleNamespace(_element=self.provider)
        self.window.backend = SimpleNamespace(name="uia", element_info_class=lambda row: row,
                                             generic_wrapper_class=Mock(side_effect=lambda row: row))
        self.automation = Mock(tree_scope={"descendants": 4})
        self.automation.UIA_dll = SimpleNamespace(UIA_RuntimeIdPropertyId=30000, UIA_ProcessIdPropertyId=30002,
                                                UIA_IsOffscreenPropertyId=30022)
        self.automation.build_condition.side_effect = lambda **kwargs: kwargs
        self.automation.iuia.CreateOrConditionFromArray.return_value = "union condition"
        module = SimpleNamespace(IUIA=lambda: self.automation)
        module_patch = patch.dict(sys.modules, {"pywinauto.uia_defines": module})
        module_patch.start()
        self.addCleanup(module_patch.stop)

    def test_edit_and_document_types_share_one_native_provider_traversal(self):
        self.assertEqual(ui._descendants(self.window, control_types=("Edit", "Document")),
                         [self.editor, self.root])
        self.automation.iuia.CreateOrConditionFromArray.assert_called_once_with(
            [{"control_type": "Edit"}, {"control_type": "Document"}])
        self.provider.FindAllBuildCache.assert_called_once_with(4, "union condition", self.automation.iuia.CreateCacheRequest.return_value)
        self.assertEqual(self.window.reads, 0)

    def test_provider_cached_identity_avoids_redundant_live_identity_reads(self):
        with patch.object(ui, "_process_id", side_effect=AssertionError("live PID read not needed")), \
             patch.object(ui, "_node_identity", side_effect=AssertionError("live RuntimeId read not needed")):
            rows = ui._descendants(self.window, control_types=("Edit", "Document"), visible_only=True)
        self.assertEqual(rows, [self.editor, self.root])
        self.assertEqual(getattr(self.editor, "_jarvis_provider_identity"), (42, ("uia", 42, 0)))
        self.assertEqual(getattr(self.root, "_jarvis_provider_identity"), (42, ("uia", 42, 1)))

    def test_provider_error_does_not_retry_broad_enumeration(self):
        self.provider.FindAllBuildCache.side_effect = RuntimeError("provider disconnected")
        with self.assertRaisesRegex(RuntimeError, "provider disconnected"):
            ui._descendants(self.window, control_types=("Edit", "Document"))
        self.provider.FindAllBuildCache.assert_called_once()
        self.assertEqual(self.window.reads, 0)

    def test_transient_provider_error_reissues_only_the_read(self):
        self.provider.FindAllBuildCache.side_effect = [COMError(), self.elements]
        rows = ui._descendants(self.window, control_types=("Edit", "Document"))
        self.assertEqual(rows, [self.editor, self.root])
        self.assertEqual(self.provider.FindAllBuildCache.call_count, 2)
        self.assertEqual(self.window.reads, 0)

    def test_persistent_provider_error_is_bounded_and_unknown_errors_are_not_retried(self):
        for code, count in ((-2147220991, 2), (-1, 1)):
            with self.subTest(code=code):
                self.provider.FindAllBuildCache.reset_mock()
                self.provider.FindAllBuildCache.side_effect = COMError(code)
                with self.assertRaisesRegex(RuntimeError, "COMError"):
                    ui._descendants(self.window, control_types=("Edit", "Document"))
                self.assertEqual(self.provider.FindAllBuildCache.call_count, count)

    def test_partial_failed_enumeration_is_discarded_before_retry(self):
        calls = []
        def query(*args, **kwargs):
            calls.append(None)
            if len(calls) == 1:
                yield self.editor
                raise COMError()
            yield self.root
        with patch.object(ui, "_query_descendants", side_effect=query):
            self.assertEqual(ui._descendants(self.window), [self.root])
        self.assertEqual(len(calls), 2)

    def test_provider_aliases_are_deduplicated_before_live_wrapper_construction(self):
        self.elements.Length = 1001
        self.elements.GetElement.side_effect = [self.editor] * 1000 + [self.root]
        rows = ui._descendants(self.window, control_types=("Edit", "Document"), visible_only=True)
        self.assertEqual(rows, [self.editor, self.root])
        self.assertEqual(self.window.backend.generic_wrapper_class.call_count, 2)
        self.automation.iuia.CreatePropertyCondition.assert_called_once_with(30022, False)

    def test_absent_cached_identity_does_not_merge_distinct_elements(self):
        for control in (self.editor, self.root):
            control.GetCachedPropertyValue = lambda prop: None
        self.assertEqual(len(ui._descendants(self.window, control_types=("Edit", "Document"))), 2)


if __name__ == "__main__":
    unittest.main()
