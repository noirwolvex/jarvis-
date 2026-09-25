from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from core.desktop_input import InputNotDispatchedError
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec
from core.browser_semantic import register_browser_semantic_tools
from core.semantic_ui_tools import register_semantic_ui_tools
from core.native_ui_input import register_native_ui_input_tools
from core.universal_interaction import (
    interaction_click,
    interaction_hotkey,
    interaction_type,
    register_universal_interaction_tools,
)


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    register_semantic_ui_tools(tools)
    register_native_ui_input_tools(tools)
    register_browser_semantic_tools(tools)
    register_universal_interaction_tools(tools)
    return tools


class UniversalInteractionTests(unittest.TestCase):
    def test_auto_click_routes_managed_browser_to_exact_dom_action(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_action", return_value='ACTION_EXECUTED: {"executed":true}') as action:
            result = interaction_click(registry=tools, target="Send", control_type="button")
        self.assertTrue(result.startswith("ACTION_EXECUTED:"))
        action.assert_called_once_with(
            "click",
            {"role": "button", "name": "Send"},
            expected_version="",
            frame_selector="",
        )

    def test_auto_click_routes_desktop_to_uia(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_activate", return_value='DELIVERED: {"action":"ui_activate"}') as activate:
            result = interaction_click(registry=tools, target="Save", control_type="Button", title="Notepad")
        self.assertTrue(result.startswith("DELIVERED:"))
        activate.assert_called_once_with(target="Save", title="Notepad", control_type="Button", selector=None)

    def test_desktop_type_prefers_exact_native_semantic_editor(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.native_ui_input.ui_type_native", return_value='VERIFIED: {"method":"rust_native_input"}') as native:
            result = interaction_type("hello", registry=tools, target="Message", title="Any App")
        self.assertTrue(result.startswith("VERIFIED:"))
        native.assert_called_once_with(text="hello", target="Message", title="Any App", selector=None)

    def test_custom_desktop_editor_requires_explicit_focused_fallback(self):
        tools = registry()
        focused = Mock(return_value="RUST_EXECUTED: typed")
        tools.register(ToolSpec(
            "desktop_type", "fixture focused type", Risk.MEDIUM,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            focused,
        ))
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.native_ui_input.ui_type_native", side_effect=InputNotDispatchedError("no editor")):
            with self.assertRaises(InputNotDispatchedError):
                interaction_type("hello", registry=tools)
            result = interaction_type("hello", registry=tools, focused_fallback=True)
        self.assertEqual(result, "RUST_EXECUTED: typed")
        focused.assert_called_once_with(text="hello")

    def test_focused_fallback_never_retargets_a_failed_named_control(self):
        tools = registry()
        focused = Mock(return_value="should not run")
        tools.register(ToolSpec(
            "desktop_type", "fixture focused type", Risk.MEDIUM,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            focused,
        ))
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.native_ui_input.ui_type_native", side_effect=InputNotDispatchedError("ambiguous")):
            with self.assertRaises(InputNotDispatchedError):
                interaction_type("hello", registry=tools, target="Message", focused_fallback=True)
        focused.assert_not_called()

    def test_browser_type_uses_verified_append_and_never_native_fallback(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_action", return_value='VERIFIED: {"verified":true}') as action, \
             patch("core.native_ui_input.ui_type_native") as native:
            result = interaction_type(" world", registry=tools, target="Message", control_type="textbox")
        self.assertTrue(result.startswith("VERIFIED:"))
        action.assert_called_once_with(
            "append",
            {"role": "textbox", "name": "Message"},
            value=" world",
            expected_version="",
            frame_selector="",
        )
        native.assert_not_called()

    def test_browser_submit_rejects_snapshot_node_before_any_partial_write(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_action") as action:
            with self.assertRaisesRegex(ValueError, "node_id"):
                interaction_type(
                    "hello",
                    registry=tools,
                    browser_target={"node_id": "n1"},
                    expected_version="epoch:1",
                    submit=True,
                )
        action.assert_not_called()

    def test_browser_hotkey_uses_exact_dom_target_not_desktop_side_channel(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_action", return_value='ACTION_EXECUTED: {}') as action, \
             patch("core.semantic_ui_tools.ui_hotkey") as desktop:
            interaction_hotkey(
                ["ctrl", "a"],
                registry=tools,
                target="Draft",
                control_type="textbox",
            )
        action.assert_called_once_with(
            "press",
            {"role": "textbox", "name": "Draft"},
            value="Control+A",
            expected_version="",
            frame_selector="",
        )
        desktop.assert_not_called()

    def test_underlying_browser_deny_is_honored_by_universal_router(self):
        tools = registry()
        tools.permissions.deny_tools.add("browser_semantic_action")
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_action") as action:
            with self.assertRaisesRegex(PermissionError, "explicitly denied"):
                interaction_click(registry=tools, target="Send", control_type="button")
        action.assert_not_called()

    def test_restricted_mode_allows_inspection_but_blocks_universal_mutations(self):
        tools = registry()
        tools.permissions.set_access_mode("restricted")
        denied = tools.execute("interaction_click", {"surface": "desktop", "target": "Save"}, approved=True)
        self.assertIn("Desktop interaction is disabled", denied)
        allowed, reason = tools.permissions.check("interaction_inspect", Risk.LOW, approved=True)
        self.assertTrue(allowed, reason)


if __name__ == "__main__":
    unittest.main()
