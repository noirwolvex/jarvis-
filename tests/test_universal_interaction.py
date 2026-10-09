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
    interaction_focus,
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
    def test_implicit_browser_typing_rejects_unfocused_disabled_and_submit(self):
        import json
        scene = {"version": "v1", "focused_node": "n1", "has_focus": True,
                 "nodes": [{"node_id": "n1", "focused": True, "input_kind": "text", "disabled": False}]}
        cases = ({**scene, "has_focus": False},
                 {**scene, "nodes": [{**scene["nodes"][0], "disabled": True}]},
                 {**scene, "nodes": [{**scene["nodes"][0], "input_kind": "select"}]})
        with patch("core.universal_interaction._browser_active", return_value=True), \
                patch("core.browser_semantic.browser_semantic_action") as action:
            for invalid in cases:
                with self.subTest(scene=invalid), \
                        patch("core.browser_semantic.browser_semantic_snapshot", return_value=json.dumps(invalid)):
                    with self.assertRaisesRegex(InputNotDispatchedError, "focused browser editor"):
                        interaction_type("hello", registry=registry())
            with patch("core.browser_semantic.browser_semantic_snapshot") as snapshot:
                with self.assertRaisesRegex(InputNotDispatchedError, "explicit target"):
                    interaction_type("hello", registry=registry(), submit=True)
                snapshot.assert_not_called()
        action.assert_not_called()

    def test_desktop_inspect_accepts_control_type_and_passes_to_provider(self):
        tools = registry()
        with patch("core.semantic_ui_tools.ui_inspect", return_value='{"controls":[]}') as inspect:
            result = tools.execute("interaction_inspect", {
                "surface": "desktop", "title": "WhatsApp", "control_type": "Edit", "force_refresh": True,
            }, approved=True)
        self.assertNotIn("ERROR", result)
        inspect.assert_called_once_with(title="WhatsApp", query="", actionable_only=True, max_controls=120,
                                        force_refresh=True, control_type="Edit")

    def test_desktop_selected_checkpoint_uses_read_only_backend(self):
        tools = registry()
        with patch("core.semantic_ui_tools.ui_wait_state", return_value='VERIFIED: {"selected":true}') as wait:
            result = tools.execute("interaction_wait", {
                "surface": "desktop", "target": "Downloads (pinned)", "control_type": "TreeItem",
                "state": "selected", "timeout_ms": 1000,
            }, approved=True)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        wait.assert_called_once_with(target="Downloads (pinned)", title="", control_type="TreeItem",
                                     state="selected", timeout_ms=1000, selector=None)

    def test_desktop_only_inspection_and_selection_arguments_do_not_scan_browser(self):
        with patch("core.chrome_cdp.chrome_is_connected", return_value=True), \
             patch("core.browser_semantic.browser_semantic_snapshot") as snapshot, \
             patch("core.browser_semantic.browser_wait_state") as wait:
            with self.assertRaisesRegex(ValueError, "Windows UIA"):
                interaction_inspect(registry=registry(), surface="browser", control_type="Edit")
            with self.assertRaisesRegex(ValueError, "desktop UIA"):
                interaction_wait(registry=registry(), surface="browser", target="Tab", control_type="tab", state="selected")
        snapshot.assert_not_called()
        wait.assert_not_called()

    def test_named_desktop_window_is_not_rerouted_to_foreground_browser(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=True), \
             patch("core.semantic_ui_tools.ui_activate", return_value="DELIVERED: {}") as activate, \
             patch("core.browser_semantic.browser_semantic_action") as browser:
            interaction_click(registry=tools, target="Save", title="Notepad", control_type="Button")
        activate.assert_called_once_with(target="Save", title="Notepad", control_type="Button", selector=None)
        browser.assert_not_called()

    def test_browser_target_on_desktop_is_rejected_without_input(self):
        tools = registry()
        with patch("core.universal_interaction._browser_active", return_value=False), \
             patch("core.semantic_ui_tools.ui_activate") as desktop, \
             patch("core.browser_semantic.browser_semantic_action") as browser:
            result = tools.execute("interaction_click", {"browser_target": {"role": "listitem", "name": "bel"}}, approved=True)
        self.assertIn("browser target cannot control", result)
        self.assertEqual(result.execution["input_delivery"], "not_dispatched")
        desktop.assert_not_called()
        browser.assert_not_called()

    def test_browser_surface_cannot_silently_ignore_desktop_binding(self):
        with patch("core.browser_semantic.browser_semantic_action") as action:
            with self.assertRaisesRegex(InputNotDispatchedError, "Desktop window/selector"):
                interaction_click(registry=registry(), surface="browser", target="Save", title="Notepad")
        action.assert_not_called()

    def test_scene_window_change_blocks_mouse_keyboard_and_typing(self):
        tools = registry()
        with patch("core.semantic_ui_tools._foreground_hwnd", return_value=99), \
             patch("core.semantic_ui_tools.ui_activate") as activate, \
             patch("core.semantic_ui_tools.ui_focus") as focus, \
             patch("core.semantic_ui_tools.ui_type") as typing, \
             patch("core.semantic_ui_tools.ui_hotkey") as hotkey:
            for action, arguments in (
                (interaction_click, {}), (interaction_focus, {}),
                (interaction_type, {"text": "draft"}), (interaction_hotkey, {"keys": ["ctrl", "a"]}),
            ):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(InputNotDispatchedError, "no longer foreground"):
                    action(registry=tools, surface="desktop", title="App", target="Editor", expected_hwnd=42, **arguments)
        for backend in (activate, focus, typing, hotkey):
            backend.assert_not_called()

    def test_desktop_hotkey_resolves_requested_control_before_dispatch(self):
        tools = registry()
        calls = Mock()
        with patch("core.semantic_ui_tools.ui_focus", side_effect=lambda **kwargs: calls.focus(**kwargs)), \
             patch("core.semantic_ui_tools.ui_hotkey", side_effect=lambda **kwargs: calls.hotkey(**kwargs)):
            result = tools.execute("interaction_hotkey", {
                "keys": ["ctrl", "a"], "surface": "desktop", "title": "App",
                "control_type": "Edit", "selector": {"ordinal": 2},
            }, approved=True)
        self.assertNotIn("ERROR", str(result))
        self.assertEqual([call[0] for call in calls.mock_calls], ["focus", "hotkey"])
        calls.focus.assert_called_once_with(target="", title="App", control_type="Edit", selector={"ordinal": 2})
        calls.hotkey.assert_called_once_with(keys=["ctrl", "a"], title="App")

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

    def test_desktop_text_checkpoint_forwards_exact_text_without_input(self):
        tools = registry()
        with patch("core.semantic_ui_tools.ui_wait_state", return_value='VERIFIED: {}') as wait, \
             patch("core.semantic_ui_tools.ui_type") as typing:
            result = tools.execute("interaction_wait", {
                "surface": "desktop", "target": "Draft", "state": "text", "text": " Exact draft ",
            }, approved=True)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        wait.assert_called_once_with(target="Draft", title="", control_type="", state="text",
                                     timeout_ms=1500, selector=None, text=" Exact draft ")
        typing.assert_not_called()

    def test_browser_focus_checkpoint_routes_without_mutation(self):
        with patch("core.chrome_cdp.chrome_is_connected", return_value=True), \
             patch("core.browser_semantic.browser_wait_state", return_value='VERIFIED: {}') as wait:
            result = interaction_wait(registry=registry(), surface="browser", target="Draft",
                                      control_type="textbox", state="focused")
        self.assertTrue(result.startswith("VERIFIED:"))
        wait.assert_called_once_with({"role": "textbox", "name": "Draft"}, state="focused",
                                     timeout_ms=1500, text="", frame_selector="")

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
