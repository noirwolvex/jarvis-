from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import discord_navigation as nav
from core.desktop_input import InputDeliveryError, InputNotDispatchedError
from core.discord_tools import register_discord_tools
from core.permissions import Risk
from core.tools import ToolRegistry
from test_discord_tools import Control


class Element(Control):
    def __init__(self, name, kind, *, rect=(0, 0, 900, 700), **kwargs):
        super().__init__(name, kind, **kwargs)
        self.rect = rect

    def rectangle(self):
        return SimpleNamespace(left=self.rect[0], top=self.rect[1], right=self.rect[2], bottom=self.rect[3])


def once(probe, **kwargs):
    result = probe()
    if not result:
        raise TimeoutError("not verified")
    return result


class DiscordNavigationTests(unittest.TestCase):
    def setUp(self):
        self.first = Element("c\u034ear\U0001d595et (direct message), Pinned,", "Hyperlink", value="https://discord.com/channels/@me/111",
                             rect=(20, 210, 240, 250))
        self.second = Element("Second (direct message),", "Hyperlink", value="https://discord.com/channels/@me/222",
                              rect=(20, 260, 240, 300))
        self.friends = Element("Friends", "Hyperlink", value="https://discord.com/channels/@me", rect=(20, 100, 240, 140))
        self.scope = Element("Direct Messages", "List", children=[self.second, self.friends, self.first])
        self.document = Element("Discord", "Document", value="https://discord.com/channels/@me")
        self.document.element_info.automation_id = "RootWebArea"
        self.composer = Element("Message @Other", "Edit")
        self.home = Element("Direct Messages", "TreeItem")
        self.window = Element("Discord", "Window", children=[self.home, self.scope, self.document, self.composer])
        self.window.handle = 42
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def descendants(root, **kwargs):
            kinds = kwargs.get("control_types")
            return [c for c in root.descendants() if not kinds or c.element_info.control_type in kinds]
        for owner, name, options in (
            (nav.discord, "_discord_window", {"return_value": self.window}),
            (nav.discord, "_identity", {"return_value": (42, 100, 1.0)}),
            (nav.discord, "_foreground_hwnd", {"return_value": 42}),
            (nav.ui, "_focus_window", {"return_value": 42}),
            (nav.ui, "_descendants", {"side_effect": descendants}),
            (nav.ui, "_target_binding", {"return_value": {"control": {"rect": [20, 210, 240, 250]}}}),
            (nav.ui, "_validate_target", {}),
            (nav, "wait_until", {"side_effect": once}),
        ):
            self.stack.enter_context(patch.object(owner, name, **options))
        self.descendants = nav.ui._descendants
        self.invoke = self.stack.enter_context(patch.object(nav.ui, "_invoke", side_effect=self.navigate))

    def navigate(self, control, hwnd=None):
        if control is self.home:
            self.window.children.append(self.scope)
        else:
            self.document.value = control.value
            self.composer.element_info.name = "Message @" + nav._destination_name(control)
        return "invoke"

    def test_first_chat_uses_scoped_route_and_visible_order_not_friends_or_name_guess(self):
        result = json.loads(nav.discord_select_chat(1)[len("VERIFIED: "):])
        self.assertEqual(result["route"], "/channels/@me/111")
        self.assertTrue(result["route_verified"] and result["composer_verified"])
        self.assertEqual(result["method"], "uia_invoke")
        self.invoke.assert_called_once_with(self.first, 42)
        self.assertEqual(self.descendants.call_count, 2)
        self.assertEqual(self.descendants.call_args_list[0].kwargs["control_types"], nav._DISCOVERY_TYPES)
        self.assertEqual(self.descendants.call_args_list[1].kwargs["control_types"], nav._VERIFICATION_TYPES)

    def test_already_open_is_verified_without_repeating_input(self):
        self.navigate(self.first)
        result = json.loads(nav.discord_select_chat(1)[len("VERIFIED: "):])
        self.assertTrue(result["already_open"])
        self.invoke.assert_not_called()
        self.assertEqual(self.descendants.call_count, 1)
        self.assertEqual(self.descendants.call_args.kwargs["control_types"], nav._DISCOVERY_TYPES)

    def test_server_view_opens_dm_list_then_exact_chat(self):
        self.window.children.remove(self.scope)
        self.assertTrue(nav.discord_select_chat(2).startswith("VERIFIED:"))
        self.assertEqual([call.args[0] for call in self.invoke.call_args_list], [self.home, self.second])

    def test_matching_composer_alone_does_not_verify_wrong_route(self):
        self.composer.element_info.name = "Message @" + nav._destination_name(self.first)
        self.invoke.side_effect = lambda *args: "invoke"
        with self.assertRaises(InputDeliveryError):
            nav.discord_select_chat(1)
        self.invoke.assert_called_once()

    def test_route_alone_does_not_verify_stale_composer(self):
        self.invoke.side_effect = lambda control, hwnd: (setattr(self.document, "value", control.value), "invoke")[1]
        with self.assertRaises(InputDeliveryError):
            nav.discord_select_chat(1)
        self.invoke.assert_called_once()

    def test_uncertain_semantic_action_never_falls_back_or_repeats(self):
        self.invoke.side_effect = InputDeliveryError("Invoke failed after delivery")
        with patch("core.rust_engine._preflight") as preflight, self.assertRaises(InputDeliveryError):
            nav.discord_select_chat(1)
        preflight.assert_not_called()
        self.invoke.assert_called_once()

    def test_unsupported_pattern_uses_one_guarded_rust_click(self):
        self.invoke.side_effect = RuntimeError("Control has no supported semantic activation pattern; use fresh screen observation and the guarded desktop input tool")
        client = Mock()
        def click(x, y, status, *, before_dispatch):
            before_dispatch()
            self.navigate(self.first)
            return {"executed": True, "simulation": False}
        client.click.side_effect = click
        with patch("core.rust_engine._preflight", return_value=(client, {})), \
             patch.object(nav.ui, "_guard_native_target"):
            result = json.loads(nav.discord_select_chat(1)[len("VERIFIED: "):])
        self.assertEqual(result["method"], "rust_native_click")
        self.assertEqual(client.click.call_args.args[:2], (130, 230))
        client.click.assert_called_once()

    def test_route_recycled_during_capture_blocks_rust_input(self):
        self.invoke.side_effect = RuntimeError("Control has no supported semantic activation pattern; use fresh screen observation and the guarded desktop input tool")
        client = Mock()
        dispatched = []
        def click(x, y, status, *, before_dispatch):
            self.first.value = self.second.value
            before_dispatch()
            dispatched.append(True)
        client.click.side_effect = click
        with patch("core.rust_engine._preflight", return_value=(client, {})), \
             patch.object(nav.ui, "_guard_native_target"), self.assertRaises(InputNotDispatchedError):
            nav.discord_select_chat(1)
        self.assertEqual(dispatched, [])

    def test_changing_permission_after_resolution_prevents_activation(self):
        checks = Mock(side_effect=[None, None, None, None, PermissionError("revoked")])
        with self.assertRaises(PermissionError):
            nav.discord_select_chat(1, _policy_guard=checks)
        self.invoke.assert_not_called()

    def test_duplicate_scope_and_recycled_links_fail_before_input(self):
        self.window.children.append(Element("Direct Messages", "List"))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            nav.discord_select_chat(1)
        self.window.children.pop()
        self.second.value = self.first.value
        with self.assertRaises(InputNotDispatchedError):
            nav.discord_select_chat(1)
        self.invoke.assert_not_called()

    def test_invalid_missing_and_hidden_positions_never_click(self):
        for position in (True, 0, 21, 1.5):
            with self.assertRaises(ValueError):
                nav.discord_select_chat(position)
        self.first.rect = (20, 800, 240, 840)
        with self.assertRaisesRegex(RuntimeError, "1 visible"):
            nav.discord_select_chat(2)
        self.invoke.assert_not_called()

    def test_untrusted_non_discord_or_non_dm_routes_are_excluded(self):
        for url in ("https://evil.test/channels/@me/111", "https://discord.com.evil.test/channels/@me/111",
                    "https://discord.com/channels/111/222", "https://discord.com/channels/@me",
                    "javascript:alert(1)", "https://user@discord.com/channels/@me/111"):
            self.assertIsNone(nav._dm_route(url))

    def test_focus_change_prevents_activation(self):
        with patch.object(nav.discord, "_foreground_hwnd", side_effect=[42, 42, 99]):
            with self.assertRaisesRegex(RuntimeError, "focus"):
                nav.discord_select_chat(1)
        self.invoke.assert_not_called()

    def test_navigation_honors_permission_boundaries_without_requiring_send(self):
        registry = ToolRegistry()
        registry.permissions.set_access_mode("full")
        register_discord_tools(registry)
        registry.permissions.deny_tools.add("discord_send_message")
        result = registry.execute("discord_select_chat", {"position": 1}, approved=True)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.invoke.reset_mock()
        registry.permissions.deny_tools.add("ui_activate")
        self.assertIn("explicitly denied", registry.execute("discord_select_chat", {"position": 1}, approved=True))
        self.invoke.assert_not_called()

    def test_real_adapter_fast_mission_completes_when_model_is_unavailable(self):
        from test_workflow_execution import WorkflowExecutionTests
        fixture = WorkflowExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        register_discord_tools(fixture.agent.tools)
        launch = Mock(return_value="VERIFIED: Discord is foreground")
        fixture.register("launch_installed_app", Risk.MEDIUM, launch, {"type": "object"})
        fixture.agent.client.chat.completions.create.side_effect = RuntimeError("429 quota exhausted")
        with patch("core.full_access_agent._chrome_tab_rows", return_value=[]):
            result = fixture.agent.run("OPEN DISCORD AND PRESS THE FIRST CHAT")
        self.assertIn("Completed and verified", result)
        self.assertEqual([t.name for t in fixture.agent.orchestrator.current.traces], ["launch_installed_app", "discord_select_chat"])
        self.assertTrue(all(s.status == "completed" for s in fixture.agent.orchestrator.current.plan))
        self.invoke.assert_called_once()
        launch.assert_called_once()
        fixture.agent.client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
