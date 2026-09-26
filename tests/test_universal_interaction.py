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
    interaction_inspect,
    interaction_scroll,
    interaction_type,
    interaction_wait,
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

    def test_desktop_type_prefers_exact_uia_value_write_before_native(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_type", return_value='VERIFIED: {"method":"uia_value_pattern"}') as semantic, \
             patch("core.native_ui_input.ui_type_native") as native:
            result = interaction_type("hello", registry=tools, target="Message", title="Any App")
        self.assertTrue(result.startswith("VERIFIED:"))
        semantic.assert_called_once_with(text="hello", target="Message", title="Any App", submit=False, replace=False, selector=None)
        native.assert_not_called()

    def test_custom_desktop_editor_requires_explicit_focused_fallback(self):
        tools = registry()
        focused = Mock(return_value="RUST_EXECUTED: typed")
        tools.register(ToolSpec(
            "desktop_type", "fixture focused type", Risk.MEDIUM,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            focused,
        ))
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_type", side_effect=InputNotDispatchedError("Editor has no writable Value pattern; use guarded input to control selection explicitly")), \
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
             patch("core.semantic_ui_tools.ui_type", side_effect=InputNotDispatchedError("Target has 2 exact visible enabled matches")), \
             patch("core.native_ui_input.ui_type_native") as native:
            with self.assertRaises(InputNotDispatchedError):
                interaction_type("hello", registry=tools, target="Message", focused_fallback=True)
        native.assert_not_called()
        focused.assert_not_called()

    def test_missing_value_pattern_falls_back_once_to_native_semantic_typing(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_type", side_effect=InputNotDispatchedError("Editor has no writable Value pattern; use guarded input to control selection explicitly")) as semantic, \
             patch("core.native_ui_input.ui_type_native", return_value='VERIFIED: {"method":"rust_native_input"}') as native:
            result = interaction_type("hello", registry=tools, target="Message", title="Discord")
        self.assertTrue(result.startswith("VERIFIED:"))
        semantic.assert_called_once()
        native.assert_called_once_with(text="hello", target="Message", title="Discord", selector=None)

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

    def test_universal_wait_routes_browser_and_desktop_without_input(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_wait_state", return_value='VERIFIED: {"matched":true}') as browser_wait:
            result = interaction_wait(
                registry=tools,
                target="Save",
                control_type="button",
                state="visible",
            )
        self.assertTrue(result.startswith("VERIFIED:"))
        browser_wait.assert_called_once_with(
            {"role": "button", "name": "Save"},
            state="visible",
            timeout_ms=1500,
            text="",
            frame_selector="",
        )

        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_wait_state", return_value='VERIFIED: {"state":"visible"}') as desktop_wait:
            result = interaction_wait(
                registry=tools,
                target="Save",
                control_type="Button",
                state="visible",
                title="Notepad",
            )
        self.assertTrue(result.startswith("VERIFIED:"))
        desktop_wait.assert_called_once_with(
            target="Save",
            title="Notepad",
            control_type="Button",
            state="visible",
            timeout_ms=1500,
            selector=None,
        )

    def test_browser_scroll_routes_to_guarded_dom_scroll(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_scroll", return_value='VERIFIED: {"verified":true}') as scroll:
            result = interaction_scroll(420, registry=tools, delta_x=10)
        self.assertTrue(result.startswith("VERIFIED:"))
        scroll.assert_called_once_with(delta_y=420, delta_x=10, frame_selector="")

    def test_desktop_scroll_normalizes_positive_y_to_wheel_down(self):
        tools = registry()
        wheel = Mock(return_value="RUST_EXECUTED: scrolled")
        tools.register(ToolSpec(
            "desktop_scroll", "fixture wheel", Risk.MEDIUM,
            {"type": "object", "properties": {"clicks": {"type": "integer", "minimum": -1000, "maximum": 1000}}, "required": ["clicks"]},
            wheel,
        ))
        with patch("core.universal_interaction._browser_active", return_value=False):
            result = interaction_scroll(5, registry=tools)
        self.assertEqual(result, "RUST_EXECUTED: scrolled")
        wheel.assert_called_once_with(clicks=-5)

    def test_inspect_honors_underlying_read_deny(self):
        tools = registry()
        tools.permissions.deny_tools.add("browser_semantic_snapshot")
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.browser_semantic.browser_semantic_snapshot") as snapshot:
            with self.assertRaisesRegex(PermissionError, "explicitly denied"):
                interaction_inspect(registry=tools)
        snapshot.assert_not_called()

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
