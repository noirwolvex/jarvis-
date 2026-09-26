from __future__ import annotations

import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import discord_tools as discord
from core.desktop_input import InputDeliveryError
from core.discord_tools import register_discord_tools
from core.tools import ToolRegistry


class Control:
    def __init__(self, name, kind="TreeItem", *, selected=False, children=(), value="", uid=None):
        self.element_info = SimpleNamespace(name=name, control_type=kind, runtime_id=[uid or id(self)],
                                            automation_id="", process_id=100)
        self.children = list(children)
        self.selected = selected
        self.value = value
        self.handle = 0

    def descendants(self, **kwargs):
        rows = [child for entry in self.children for child in (entry, *entry.descendants())]
        kind = kwargs.get("control_type")
        return [row for row in rows if not kind or row.element_info.control_type == kind]

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def is_selected(self):
        return self.selected

    def get_value(self):
        return self.value


class Window(Control):
    def __init__(self, *children):
        super().__init__("Discord", "Window", children=children)
        self.handle = 42


@contextmanager
def desktop_fixture(window):
    with ExitStack() as stack:
        patches = {
            "_discord_window": {"return_value": window},
            "_identity": {"return_value": (42, 100, 1.0)},
            "_foreground_hwnd": {"return_value": 42},
            "_focus_window": {"return_value": 42},
            "_invoke": {},
            "ui_type": {"return_value": 'VERIFIED: {"submitted":true}'},
        }
        mocks = {name: stack.enter_context(patch.object(discord, name, **options)) for name, options in patches.items()}
        yield mocks


class DiscordToolsTests(unittest.TestCase):
    def test_compound_discord_tools_register_as_medium_risk(self) -> None:
        registry = ToolRegistry()
        register_discord_tools(registry)
        for name in ("discord_go_to", "discord_send_message", "discord_navigate_and_send"):
            self.assertIn(name, registry._tools)
            self.assertEqual(registry._tools[name].risk.name, "MEDIUM")

    def test_compound_schema_requires_destination_and_text(self) -> None:
        registry = ToolRegistry()
        register_discord_tools(registry)
        schema = registry._tools["discord_navigate_and_send"].input_schema
        self.assertEqual(set(schema["required"]), {"destination", "text"})
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("server", schema["properties"])

    def test_dm_accessibility_suffix_normalizes_to_composer_destination(self):
        self.assertEqual(discord._channel_name("Alice (direct message), Pinned,"), "alice")
        self.assertEqual(discord._channel_name("Project Team (group message),"), "project team")

    def test_context_pushes_relevant_types_into_one_uia_query(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"),
                     Control("decorative noise", "Text"))
        with desktop_fixture(win), patch.object(discord, "_descendants", wraps=discord._descendants) as descendants:
            context = discord._context(win, (42, 100, 1.0), "general")
        self.assertEqual(context.destination, "general")
        descendants.assert_called_once()
        self.assertEqual(descendants.call_args.kwargs["control_types"], discord._CONTEXT_TYPES)
        self.assertTrue(descendants.call_args.kwargs["visible_only"])
        self.assertTrue(descendants.call_args.kwargs["require_complete"])

    def test_already_selected_navigation_reuses_one_context_snapshot(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win), patch.object(discord, "_controls", wraps=discord._controls) as controls:
            result = discord.discord_go_to("general")
        self.assertTrue(result.startswith("VERIFIED:"))
        self.assertEqual(controls.call_count, 1)
        self.assertEqual(controls.call_args.args[1], discord._CONTEXT_TYPES)

    def test_selected_channel_and_matching_composer_are_both_required(self):
        selected = Control("general", selected=False)
        composer = Control("Message #general", "Edit")
        win = Window(selected, composer, Control("general has new activity", "Text"))
        with desktop_fixture(win):
            self.assertIsNone(discord._context(win, (42, 100, 1.0), "general"))
            selected.selected = True
            composer.element_info.name = "Message #general-chat"
            self.assertIsNone(discord._context(win, (42, 100, 1.0), "general"))
            composer.element_info.name = "Message #general"
            self.assertEqual(discord._context(win, (42, 100, 1.0), "general").destination, "general")

    def test_visible_server_name_is_not_selected_server_evidence(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"),
                     Control("Work", selected=False), Control("Other", selected=True))
        with desktop_fixture(win):
            self.assertIsNone(discord._context(win, (42, 100, 1.0), "general", "Work"))

    def test_duplicate_selected_channels_fail_closed(self):
        win = Window(Control("general", selected=True), Control("general", selected=True),
                     Control("Message #general", "Edit"))
        with desktop_fixture(win), self.assertRaisesRegex(RuntimeError, "ambiguous"):
            discord.discord_send_message("hello", destination="general")

    def test_compound_navigates_server_then_channel_then_sends_once(self):
        server = Control("Work")
        channel = Control("general")
        composer = Control("Message #elsewhere", "Edit")
        win = Window(server, channel, composer)
        events = []
        with desktop_fixture(win) as mocks:
            def invoke(control):
                events.append(control.element_info.name)
                control.selected = True
                if control is channel:
                    composer.element_info.name = "Message #general"
                return "select"
            def send(**kwargs):
                kwargs["state_guard"]()
                events.append("send")
                self.assertEqual(kwargs["text"], "hello")
                self.assertTrue(kwargs["submit"])
                return 'VERIFIED: {"submitted":true}'
            mocks["_invoke"].side_effect = invoke
            mocks["ui_type"].side_effect = send
            result = discord.discord_navigate_and_send("general", "hello", server="Work")
            self.assertTrue(result.startswith("VERIFIED:"))
            self.assertEqual(events, ["Work", "general", "send"])
            self.assertEqual(mocks["ui_type"].call_count, 1)

    def test_already_selected_channel_does_not_reopen_switcher_or_navigate(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win) as mocks, patch.object(discord, "_hotkey") as hotkey:
            self.assertTrue(discord.discord_go_to("general").startswith("VERIFIED:"))
            mocks["_invoke"].assert_not_called()
            hotkey.assert_not_called()

    def test_missing_or_ambiguous_server_never_sends(self):
        for servers in ([], [Control("Work"), Control("Work")]):
            with self.subTest(servers=len(servers)):
                win = Window(*servers, Control("general", selected=True), Control("Message #general", "Edit"))
                with desktop_fixture(win) as mocks, self.assertRaises(RuntimeError):
                    discord.discord_navigate_and_send("general", "hello", server="Work")
                mocks["ui_type"].assert_not_called()
                mocks["_invoke"].assert_not_called()

    def test_focus_loss_between_navigation_and_send_does_not_refocus(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win) as mocks:
            original = discord._navigate
            def navigate(*args):
                result = original(*args)
                mocks["_foreground_hwnd"].return_value = 99
                return result
            with patch.object(discord, "_navigate", side_effect=navigate), self.assertRaisesRegex(RuntimeError, "focus"):
                discord.discord_navigate_and_send("general", "hello")
            mocks["ui_type"].assert_not_called()
            self.assertEqual(mocks["_focus_window"].call_count, 1)

    def test_context_change_before_enter_blocks_submit(self):
        channel = Control("general", selected=True)
        composer = Control("Message #general", "Edit")
        win = Window(channel, composer)
        entered = []
        with desktop_fixture(win) as mocks:
            def write(**kwargs):
                kwargs["state_guard"]()
                channel.element_info.runtime_id = [999]
                kwargs["state_guard"]()
                entered.append("enter")
            mocks["ui_type"].side_effect = write
            with self.assertRaisesRegex(RuntimeError, "conversation or composer changed"):
                discord.discord_navigate_and_send("general", "hello")
            self.assertEqual(entered, [])
            self.assertEqual(mocks["ui_type"].call_count, 1)

    def test_uncertain_delivery_never_retries(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win) as mocks:
            mocks["ui_type"].side_effect = InputDeliveryError("Enter may have been delivered")
            with self.assertRaises(InputDeliveryError):
                discord.discord_navigate_and_send("general", "hello")
            self.assertEqual(mocks["ui_type"].call_count, 1)

    def test_existing_draft_and_unreadable_composer_block_send(self):
        for value in ("existing unsent draft", None):
            win = Window(Control("general", selected=True), Control("Message #general", "Edit", value=value))
            with desktop_fixture(win) as mocks, patch.object(discord, "_control_value", return_value=value):
                with self.assertRaisesRegex(RuntimeError, "draft"):
                    discord.discord_send_message("hello")
                mocks["ui_type"].assert_not_called()

    def test_invalid_message_is_rejected_before_navigation(self):
        with patch.object(discord, "_discord_window") as window, self.assertRaises(ValueError):
            discord.discord_navigate_and_send("general", "\0")
        window.assert_not_called()

    def test_quick_switcher_disambiguates_exact_channel_by_server(self):
        work = Control("general Work", "ListItem", children=[Control("general", "Text"), Control("Work", "Text")])
        other = Control("general Other", "ListItem", children=[Control("general", "Text"), Control("Other", "Text")])
        prefix = Control("general-chat", "ListItem")
        panel = Window(work, other, prefix)
        self.assertIs(discord._quick_result(panel, "general", "Work"), work)
        self.assertIsNone(discord._quick_result(panel, "general", "Missing"))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            discord._quick_result(panel, "general", "")

    def test_hotkey_cancellation_releases_all_attempted_keys(self):
        events = []
        guard = Mock(side_effect=[None, RuntimeError("Emergency stop is active")])
        with patch.object(discord, "desktop_key_down", side_effect=lambda key: events.append(("down", key))), \
                patch.object(discord, "desktop_key_up", side_effect=lambda key: events.append(("up", key))):
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                discord._hotkey(["ctrl", "k"], guard)
        self.assertEqual(events, [("down", "ctrl"), ("up", "ctrl")])

    def test_state_wait_stops_promptly_on_cancellation(self):
        with patch("core.ui_state.check_cancelled", side_effect=RuntimeError("Emergency stop is active")), \
                patch("core.ui_state.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                discord.wait_until(lambda: False, timeout=3)
            sleep.assert_not_called()

    def test_title_match_cannot_impersonate_discord_process(self):
        win = Window()
        process = SimpleNamespace(name=lambda: "notepad.exe", create_time=lambda: 1.0)
        with patch("psutil.Process", return_value=process), self.assertRaisesRegex(RuntimeError, "not owned"):
            discord._identity(win)

    def test_dm_marker_maps_to_exact_selected_conversation(self):
        win = Window(Control("Alice", selected=True), Control("Message @Alice", "Edit"))
        with desktop_fixture(win):
            self.assertEqual(discord._context(win, (42, 100, 1.0), "Alice").destination, "alice")

    def test_dm_title_and_composer_bind_context_when_selection_state_is_missing(self):
        row = Control("bel (direct message),", selected=False)
        composer = Control("Message @bel", "Edit")
        win = Window(row, composer)
        win.element_info.name = "@bel - Discord"
        with desktop_fixture(win):
            context = discord._context(win, (42, 100, 1.0), "")
        self.assertIsNotNone(context)
        self.assertEqual(context.destination, "bel")
        self.assertEqual(context.destination_id, discord._control_id(row))

    def test_dm_title_fallback_requires_unique_matching_row(self):
        first = Control("bel (direct message),", selected=False)
        duplicate = Control("bel (direct message),", selected=False)
        composer = Control("Message @bel", "Edit")
        win = Window(first, duplicate, composer)
        win.element_info.name = "@bel - Discord"
        with desktop_fixture(win), self.assertRaisesRegex(RuntimeError, "ambiguous"):
            discord._context(win, (42, 100, 1.0), "")

    def test_explicit_channel_marker_does_not_match_a_dm(self):
        win = Window(Control("Alice", selected=True), Control("Message @Alice", "Edit"))
        with desktop_fixture(win):
            self.assertIsNone(discord._context(win, (42, 100, 1.0), "#Alice"))

    def test_process_replacement_invalidates_bound_context(self):
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win) as mocks:
            context = discord._context(win, (42, 100, 1.0), "general")
            mocks["_identity"].return_value = (42, 100, 2.0)
            with self.assertRaisesRegex(RuntimeError, "process changed"):
                discord._send(win, context, "hello")
            mocks["ui_type"].assert_not_called()

    def test_compound_honors_inner_tool_denies_before_navigation(self):
        for denied in ("discord_send_message", "discord_go_to", "ui_type", "desktop_press", "ui_hotkey"):
            with self.subTest(denied=denied):
                registry = ToolRegistry()
                register_discord_tools(registry)
                registry.permissions.deny_tools.add(denied)
                with patch.object(discord, "_discord_window") as window:
                    result = registry.execute("discord_navigate_and_send", {"destination": "general", "text": "hello"}, approved=True)
                self.assertIn("explicitly denied", result)
                window.assert_not_called()

    def test_permission_revocation_after_navigation_prevents_send(self):
        registry = ToolRegistry()
        register_discord_tools(registry)
        win = Window(Control("general", selected=True), Control("Message #general", "Edit"))
        with desktop_fixture(win) as mocks:
            original = discord._navigate
            def navigate(*args):
                result = original(*args)
                registry.permissions.deny_tools.add("discord_send_message")
                return result
            with patch.object(discord, "_navigate", side_effect=navigate):
                result = registry.execute("discord_navigate_and_send", {"destination": "general", "text": "hello"}, approved=True)
            self.assertIn("explicitly denied", result)
            mocks["ui_type"].assert_not_called()

    def test_quick_switcher_waits_for_exact_selection_then_enters_once(self):
        editor = Control("Where would you like to go?", "Edit")
        row = Control("general", "ListItem")
        panel = Control("Quick Switcher", "Pane", children=[editor, row])
        composer = Control("Message #elsewhere", "Edit")
        win = Window(composer)
        events = []
        with desktop_fixture(win) as mocks:
            def down(key):
                events.append(("down", key))
                if key == "k":
                    win.children.append(panel)
                if key == "enter":
                    win.children = [row, composer]
                    composer.element_info.name = "Message #general"
            def invoke(control):
                self.assertIs(control, row)
                row.selected = True
                return "select"
            def typed(**kwargs):
                kwargs["state_guard"]()
                events.append(("search", kwargs["text"]))
                return 'VERIFIED: {"submitted":false}'
            mocks["_invoke"].side_effect = invoke
            mocks["ui_type"].side_effect = typed
            with patch.object(discord, "desktop_key_down", side_effect=down), \
                    patch.object(discord, "desktop_key_up", side_effect=lambda key: events.append(("up", key))):
                result = discord.discord_go_to("general")
            self.assertTrue(result.startswith("VERIFIED:"))
            self.assertEqual(events, [("down", "ctrl"), ("down", "k"), ("up", "k"), ("up", "ctrl"),
                                      ("search", "general"), ("down", "enter"), ("up", "enter")])

    def test_unknown_quick_switcher_destination_never_submits(self):
        editor = Control("Where would you like to go?", "Edit")
        panel = Control("Quick Switcher", "Pane", children=[editor, Control("general-chat", "ListItem")])
        win = Window(panel)
        real_wait = discord.wait_until
        with desktop_fixture(win) as mocks, patch.object(discord, "_hotkey") as hotkey, \
                patch.object(discord, "wait_until", side_effect=lambda probe, **kwargs: real_wait(probe, timeout=0)):
            with self.assertRaises(TimeoutError):
                discord.discord_go_to("general")
            mocks["_invoke"].assert_not_called()
            self.assertEqual(hotkey.call_count, 1)
            self.assertEqual(hotkey.call_args.args[0], ["ctrl", "k"])


if __name__ == "__main__":
    unittest.main()
