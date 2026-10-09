"""Existing browser reuse qualification; all UI and native input is mocked."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

from core import chrome_existing_window as chrome
from core.browser_semantic import BrowserChallengeBlocked
from core.desktop_input import InputDeliveryError, InputNotDispatchedError


class Node:
    def __init__(self, kind, *, children=(), name="", value="", text="", rect=(0, 0, 50, 20), role=""):
        self.element_info = SimpleNamespace(control_type=kind, runtime_id=(42, id(self)),
            process_id=17, automation_id="", name=name,
            element=SimpleNamespace(CurrentAriaRole=role))
        self.nodes = list(children)
        self.value = value
        self.text = text
        self.visible = self.enabled = True
        self.focused = self.selected_all = False
        self.bounds = SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])

    @property
    def iface_value(self):
        return SimpleNamespace(CurrentValue=self.value)

    @property
    def iface_text(self):
        node = self
        class TextRange:
            def GetText(self, limit):
                return (node.value if node.element_info.control_type == "Edit" else node.text)[:limit]

            def CompareEndpoints(self, end, other, other_end):
                return 0 if node.selected_all else 1
        return SimpleNamespace(DocumentRange=TextRange(),
            GetSelection=lambda: SimpleNamespace(Length=1, GetElement=lambda _: TextRange()))

    def children(self):
        return self.nodes

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def has_keyboard_focus(self):
        return self.focused

    def rectangle(self):
        return self.bounds

    def window_text(self):
        return self.element_info.name


class BrowserFixture:
    identity = {"hwnd": 50, "process_id": 17, "process_created": 123.0}

    def __init__(self):
        self.document = Node("Document", name="Original page", value="https://old.example/", text="Original page")
        self.editor = Node("Edit", value=self.document.value)
        self.tabs = Node("Tab", children=[Node("TabItem")])
        self.window = Node("Window", children=[Node("ToolBar", children=[self.editor]), self.document, self.tabs])
        self.foreground = 50
        self.calls = []
        self.after_action = lambda _action: None
        self.navigate_on_enter = True
        self.ctrl_l_selects_all = True

    def hotkey(self, keys, *, hwnd, guard):
        assert hwnd == 50
        # Match native helper preflight + immediate dispatch verification.
        guard()
        guard()
        self.calls.append(tuple(keys))
        if keys == ["ctrl", "l"]:
            self.editor.focused = True
            self.editor.selected_all = self.ctrl_l_selects_all
        elif keys == ["ctrl", "a"]:
            self.editor.selected_all = True
        elif keys == ["enter"] and self.navigate_on_enter:
            self.document.value = self.editor.value
        elif keys == ["ctrl", "t"]:
            self.tabs.nodes.append(Node("TabItem"))
            self.document.element_info.runtime_id = (42, 9999)
            self.document.value = "chrome://newtab/"
            self.document.text = "New Tab"
            self.editor.value = ""
            self.editor.focused = True
        self.after_action(tuple(keys))
        return "rust_native_hotkey"

    def type_text(self, text, *, hwnd, guard):
        assert hwnd == 50
        guard()
        guard()
        self.calls.append(("type", text))
        self.editor.value = text
        self.editor.selected_all = False
        self.after_action(("type", text))
        return "rust_native_input"

    @staticmethod
    def wait(probe, **_kwargs):
        for _ in range(3):
            result = probe()
            if result:
                return result
        raise TimeoutError("fixture state not reached")

    def patches(self):
        stack = ExitStack()
        self.identity_probe = stack.enter_context(patch.object(chrome, "_identity", side_effect=lambda _: dict(self.identity)))
        stack.enter_context(patch.object(chrome, "_window", return_value=self.window))
        stack.enter_context(patch.object(chrome, "wait_until", side_effect=self.wait))
        stack.enter_context(patch("core.semantic_ui_tools._foreground_hwnd", side_effect=lambda: self.foreground))
        self.focus = stack.enter_context(patch("core.semantic_ui_tools._focus_window", return_value=50))
        stack.enter_context(patch("core.semantic_ui_tools._rust_hotkey_or_python", side_effect=self.hotkey))
        stack.enter_context(patch("core.semantic_ui_tools._rust_type_or_python", side_effect=self.type_text))
        return stack


class ExistingChromeNavigationTests(unittest.TestCase):
    url = "https://www.google.com/search?q=verified+query"

    def test_reuses_exact_window_and_verifies_document_not_just_omnibox(self):
        fixture = BrowserFixture()
        with fixture.patches(), patch.object(chrome, "_document_state", wraps=chrome._document_state) as scan:
            result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertTrue(result["verified"])
        self.assertEqual(result["window"], fixture.identity)
        self.assertEqual(result["url"], self.url)
        self.assertEqual(result["execution_engine"], "rust_native_input")
        self.assertEqual(fixture.calls, [("ctrl", "l"), ("type", self.url), ("enter",)])
        # Initial + two native guard checks per action + independent readback.
        self.assertEqual(scan.call_count, 8)

    def test_ctrl_a_only_when_ctrl_l_did_not_select_the_entire_address(self):
        fixture = BrowserFixture()
        fixture.ctrl_l_selects_all = False
        with fixture.patches():
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls, [("ctrl", "l"), ("ctrl", "a"), ("type", self.url), ("enter",)])

    def test_omnibox_draft_before_navigation_is_preserved(self):
        fixture = BrowserFixture()
        fixture.editor.value = "unsent address-bar search"
        with fixture.patches(), self.assertRaisesRegex(InputNotDispatchedError, "contains a draft"):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        fixture.focus.assert_not_called()
        self.assertEqual(fixture.calls, [])
        self.assertEqual(fixture.editor.value, "unsent address-bar search")

    def test_challenge_is_the_registry_recognized_exception(self):
        fixture = BrowserFixture()
        fixture.document.text = "Verify you are human"
        with fixture.patches(), self.assertRaisesRegex(BrowserChallengeBlocked, "^BROWSER_ACTION_BLOCKED:"):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        fixture.focus.assert_not_called()
        self.assertEqual(fixture.calls, [])

    def test_challenge_appearing_after_typing_stops_before_enter(self):
        fixture = BrowserFixture()
        def change(action):
            if action[0] == "type":
                fixture.document.text = "Unusual traffic; verify you are human"
        fixture.after_action = change
        with fixture.patches(), self.assertRaises(BrowserChallengeBlocked):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls, [("ctrl", "l"), ("type", self.url)])

    def test_stale_pid_or_creation_time_prevents_any_input(self):
        for key in ("process_id", "process_created"):
            with self.subTest(key=key):
                fixture = BrowserFixture()
                stale = {**fixture.identity, key: 888}
                with fixture.patches(), self.assertRaisesRegex(InputNotDispatchedError, "identity changed"):
                    chrome.navigate_existing_chrome(self.url, window=stale)
                self.assertEqual(fixture.calls, [])

    def test_lost_foreground_or_replaced_editor_never_types(self):
        for change in ("foreground", "editor"):
            with self.subTest(change=change):
                fixture = BrowserFixture()
                def changed(action):
                    if action == ("ctrl", "l"):
                        if change == "foreground":
                            fixture.foreground = 77
                        else:
                            fixture.editor.element_info.runtime_id = (42, 777)
                fixture.after_action = changed
                with fixture.patches(), self.assertRaises(InputDeliveryError) as error:
                    chrome.navigate_existing_chrome(self.url, window=fixture.identity)
                self.assertNotIsInstance(error.exception, InputNotDispatchedError)
                self.assertEqual(fixture.calls, [("ctrl", "l")])

    def test_lost_editor_focus_after_typing_never_submits_or_replays(self):
        fixture = BrowserFixture()
        def changed(action):
            if action[0] == "type":
                fixture.editor.focused = False
        fixture.after_action = changed
        with fixture.patches(), self.assertRaisesRegex(InputDeliveryError, "outcome is uncertain") as error:
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertNotIsInstance(error.exception, InputNotDispatchedError)
        self.assertEqual(fixture.calls, [("ctrl", "l"), ("type", self.url)])

    def test_address_bar_echo_does_not_verify_navigation_or_trigger_retry(self):
        fixture = BrowserFixture()
        fixture.navigate_on_enter = False
        with fixture.patches(), self.assertRaisesRegex(InputDeliveryError, "outcome is uncertain"):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls.count(("enter",)), 1)
        self.assertEqual(fixture.calls.count(("type", self.url)), 1)
        self.assertEqual(fixture.editor.value, self.url)
        self.assertNotEqual(fixture.document.value, self.url)

    def test_page_dialog_prevents_navigation_without_closing_dialog(self):
        fixture = BrowserFixture()
        fixture.document.nodes.append(Node("Pane", role="dialog", name="Unsaved changes"))
        with fixture.patches(), self.assertRaisesRegex(InputNotDispatchedError, "dialog"):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls, [])

    def test_new_blank_tab_verifies_count_and_document_without_url_typing(self):
        fixture = BrowserFixture()
        with fixture.patches():
            result = chrome.navigate_existing_chrome("about:blank", new_tab=True, window=fixture.identity)
        self.assertEqual(fixture.calls, [("ctrl", "t")])
        self.assertEqual(result["requested_url"], "about:blank")
        self.assertEqual(result["url"], "chrome://newtab/")
        self.assertEqual(result["tab_count"], 2)
        self.assertTrue(result["verified"])

    def test_new_tab_without_document_url_accepts_only_owned_creation_then_verifies_loaded_url(self):
        fixture = BrowserFixture()
        def new_tab(action):
            if action == ("ctrl", "t"):
                fixture.document.value = ""
                fixture.editor.focused = True
        fixture.after_action = new_tab
        with fixture.patches(), patch.object(chrome, "_document_state", wraps=chrome._document_state) as scan:
            result = chrome.navigate_existing_chrome(self.url, new_tab=True, window=fixture.identity)
        self.assertEqual(result["url"], self.url)
        self.assertTrue(result["verified"])
        self.assertEqual(fixture.calls, [("ctrl", "t"), ("type", self.url), ("enter",)])
        # The already-focused empty new-tab omnibox needs no extra Ctrl+L and
        # its two full guards; every actual dispatch retains both full checks.
        self.assertEqual(scan.call_count, 9)

    def test_new_tab_address_focus_is_restored_when_not_already_focused(self):
        fixture = BrowserFixture()
        def change(action):
            if action == ("ctrl", "t"):
                fixture.editor.focused = False
        fixture.after_action = change
        with fixture.patches():
            result = chrome.navigate_existing_chrome(self.url, new_tab=True, window=fixture.identity)
        self.assertTrue(result["verified"])
        self.assertEqual(fixture.calls, [("ctrl", "t"), ("ctrl", "l"), ("type", self.url), ("enter",)])

    def test_skipping_redundant_new_tab_shortcut_still_checks_focus_before_typing(self):
        fixture = BrowserFixture()
        def lose_focus(text, **kwargs):
            fixture.editor.focused = False
            return fixture.type_text(text, **kwargs)
        with fixture.patches(), patch("core.semantic_ui_tools._rust_type_or_python", side_effect=lose_focus), \
                self.assertRaisesRegex(InputDeliveryError, "lost focus"):
            chrome.navigate_existing_chrome(self.url, new_tab=True, window=fixture.identity)
        self.assertEqual(fixture.calls, [("ctrl", "t")])

    def test_new_tab_challenge_stops_before_typing_even_without_ctrl_l(self):
        fixture = BrowserFixture()
        def change(action):
            if action == ("ctrl", "t"):
                fixture.document.text = "Human verification required"
        fixture.after_action = change
        with fixture.patches(), self.assertRaises(BrowserChallengeBlocked):
            chrome.navigate_existing_chrome(self.url, new_tab=True, window=fixture.identity)
        self.assertEqual(fixture.calls, [("ctrl", "t")])

    def test_an_unreadable_existing_document_never_authorizes_input(self):
        fixture = BrowserFixture()
        fixture.document.value = ""
        fixture.editor.value = ""
        fixture.editor.focused = True
        with fixture.patches(), self.assertRaises(chrome.ChromeObservationPending):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls, [])

    def test_empty_new_tab_requires_new_document_and_focused_empty_address(self):
        for invalid in ("same_document", "wrong_focus", "draft"):
            with self.subTest(invalid=invalid):
                fixture = BrowserFixture()
                original = fixture.document.element_info.runtime_id
                def change(action):
                    if action == ("ctrl", "t"):
                        fixture.document.value = ""
                        fixture.editor.focused = invalid != "wrong_focus"
                        if invalid == "draft":
                            fixture.editor.value = "user draft"
                        if invalid == "same_document":
                            fixture.document.element_info.runtime_id = original
                fixture.after_action = change
                with fixture.patches(), self.assertRaises(InputDeliveryError):
                    chrome.navigate_existing_chrome(self.url, new_tab=True, window=fixture.identity)
                self.assertEqual(fixture.calls, [("ctrl", "t")])

    def test_loading_url_is_polled_without_repeating_navigation(self):
        fixture = BrowserFixture()
        real_observe = chrome._observe
        pending = [True]
        def observe(*args, **kwargs):
            if pending and fixture.calls and fixture.calls[-1] == ("enter",):
                pending.pop()
                raise chrome.ChromeObservationPending("URL not published yet")
            return real_observe(*args, **kwargs)
        with fixture.patches(), patch.object(chrome, "_observe", side_effect=observe):
            result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertTrue(result["verified"])
        self.assertEqual(fixture.calls.count(("enter",)), 1)

    def test_background_document_publication_is_awaited_before_input(self):
        fixture = BrowserFixture()
        fixture.foreground = 99
        inspect = chrome._browser_chrome
        pending = [True, True]

        def inventory(window):
            result = inspect(window)
            if pending:
                pending.pop()
                self.assertEqual(fixture.calls, [])
                result["documents"] = []
            return result

        with fixture.patches(), patch.object(chrome, "_browser_chrome", side_effect=inventory):
            fixture.focus.side_effect = lambda _window: setattr(fixture, "foreground", 50)
            result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
            fixture.focus.assert_called_once_with(fixture.window)
        self.assertTrue(result["verified"])
        self.assertEqual(pending, [])
        self.assertEqual(fixture.calls, [("ctrl", "l"), ("type", self.url), ("enter",)])

    def test_toolbar_transition_after_shortcut_waits_without_repeating_it(self):
        for transition in ("missing_editor", "duplicate_editor", "duplicate_document"):
            with self.subTest(transition=transition):
                fixture = BrowserFixture()
                inspect = chrome._browser_chrome
                pending = [True, True]

                def inventory(window):
                    result = inspect(window)
                    if pending and fixture.calls == [("ctrl", "l")]:
                        pending.pop()
                        if transition == "missing_editor":
                            result["editors"] = []
                        elif transition == "duplicate_editor":
                            result["editors"].append(Node("Edit"))
                        else:
                            result["documents"].append(Node("Document"))
                    return result

                with fixture.patches(), patch.object(chrome, "_browser_chrome", side_effect=inventory):
                    result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
                self.assertTrue(result["verified"])
                self.assertEqual(pending, [])
                self.assertEqual(fixture.calls, [("ctrl", "l"), ("type", self.url), ("enter",)])

    def test_navigation_document_transition_does_not_repeat_enter(self):
        fixture = BrowserFixture()
        inspect = chrome._browser_chrome
        pending = [True, True]

        def inventory(window):
            result = inspect(window)
            if pending and fixture.calls and fixture.calls[-1] == ("enter",):
                pending.pop()
                result["documents"].append(Node("Document"))
            return result

        with fixture.patches(), patch.object(chrome, "_browser_chrome", side_effect=inventory):
            result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertTrue(result["verified"])
        self.assertEqual(pending, [])
        self.assertEqual(fixture.calls.count(("enter",)), 1)

    def test_persistent_ambiguity_stops_without_guessing_or_replaying_input(self):
        for after_shortcut in (False, True):
            with self.subTest(after_shortcut=after_shortcut):
                fixture = BrowserFixture()
                inspect = chrome._browser_chrome

                def inventory(window):
                    result = inspect(window)
                    if not after_shortcut or fixture.calls:
                        result["editors"].append(Node("Edit"))
                    return result

                error = InputDeliveryError if after_shortcut else chrome.ChromeObservationPending
                with fixture.patches(), patch.object(chrome, "_browser_chrome", side_effect=inventory), \
                        self.assertRaisesRegex(error, "did not become ready.*address_fields=2"):
                    chrome.navigate_existing_chrome(self.url, window=fixture.identity)
                self.assertEqual(fixture.calls, [("ctrl", "l")] if after_shortcut else [])

    def test_pending_guard_does_not_accept_changed_page_focus_or_challenge(self):
        for changed in ("document", "foreground", "challenge", "editor"):
            with self.subTest(changed=changed):
                fixture = BrowserFixture()
                inspect = chrome._browser_chrome
                pending = [True]

                def inventory(window):
                    result = inspect(window)
                    if pending and fixture.calls == [("ctrl", "l")]:
                        pending.pop()
                        result["documents"] = []
                        if changed == "document":
                            fixture.document.element_info.runtime_id = (42, 7777)
                        elif changed == "editor":
                            fixture.editor.element_info.runtime_id = (42, 7777)
                        elif changed == "foreground":
                            fixture.foreground = 99
                        else:
                            fixture.document.text = "Human verification required"
                    return result

                error = BrowserChallengeBlocked if changed == "challenge" else InputDeliveryError
                with fixture.patches(), patch.object(chrome, "_browser_chrome", side_effect=inventory), \
                        self.assertRaises(error):
                    chrome.navigate_existing_chrome(self.url, window=fixture.identity)
                self.assertEqual(fixture.calls, [("ctrl", "l")])

    def test_unexpected_dialog_is_not_treated_as_readiness_delay(self):
        fixture = BrowserFixture()
        fixture.window.nodes.append(Node("Window", name="Unexpected dialog"))
        with fixture.patches(), patch.object(chrome, "wait_until") as wait, \
                self.assertRaisesRegex(InputNotDispatchedError, "unexpected Chrome dialog"):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        wait.assert_not_called()
        self.assertEqual(fixture.calls, [])

    def test_pending_destination_polls_do_not_rescan_the_old_page(self):
        fixture = BrowserFixture()
        fixture.navigate_on_enter = False
        real_observe = chrome._observe
        pending = [True, True]
        def observe(*args, **kwargs):
            if fixture.calls and fixture.calls[-1] == ("enter",):
                if pending:
                    pending.pop()
                else:
                    fixture.document.value = fixture.editor.value
            return real_observe(*args, **kwargs)
        with fixture.patches(), patch.object(chrome, "_observe", side_effect=observe), \
                patch.object(chrome, "_document_state", wraps=chrome._document_state) as scan:
            result = chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertTrue(result["verified"])
        self.assertEqual(pending, [])
        self.assertEqual(scan.call_count, 8)  # No two extra scans of the old URL.
        self.assertEqual(fixture.calls.count(("enter",)), 1)

    def test_completion_poll_stops_on_challenge_url_before_destination(self):
        fixture = BrowserFixture()
        def change(action):
            if action == ("enter",):
                fixture.document.value = "https://www.google.com/sorry/index"
        fixture.after_action = change
        with fixture.patches(), self.assertRaises(BrowserChallengeBlocked):
            chrome.navigate_existing_chrome(self.url, window=fixture.identity)
        self.assertEqual(fixture.calls.count(("enter",)), 1)


    def test_forbidden_navigation_is_rejected_before_window_or_input(self):
        for url in ("about:blank", "javascript:alert(1)", "https://user:pass@example.com/", "https://example.com/\n"):
            with self.subTest(url=url):
                fixture = BrowserFixture()
                with fixture.patches(), self.assertRaises(ValueError):
                    chrome.navigate_existing_chrome(url, window=fixture.identity)
                fixture.identity_probe.assert_not_called()
                self.assertEqual(fixture.calls, [])

    def test_focus_reuses_current_tab_and_checks_challenge_before_focusing(self):
        fixture = BrowserFixture()
        with fixture.patches():
            result = chrome.focus_existing_chrome(fixture.identity)
            self.assertTrue(result["focused"])
            self.assertEqual(result["url"], fixture.document.value)
            fixture.focus.assert_called_once_with(fixture.window)
            fixture.focus.reset_mock()
            fixture.document.text = "Human verification required"
            with self.assertRaises(BrowserChallengeBlocked):
                chrome.focus_existing_chrome(fixture.identity)
            fixture.focus.assert_not_called()
        self.assertEqual(fixture.calls, [])

    def test_no_existing_chrome_never_dispatches_or_launches(self):
        fixture = BrowserFixture()
        with fixture.patches(), patch.object(chrome, "find_chrome_window", return_value=None), \
                self.assertRaisesRegex(InputNotDispatchedError, "No existing Chrome"):
            chrome.navigate_existing_chrome(self.url)
        fixture.identity_probe.assert_not_called()
        self.assertEqual(fixture.calls, [])


class ExistingChromeInspectionTests(unittest.TestCase):
    def test_link_ordinals_follow_visible_reading_order_not_tree_depth(self):
        first = Node("Hyperlink", name="First result", value="https://first.example/", rect=(20, 100, 200, 120))
        second = Node("Hyperlink", name="Second result", value="https://second.example/", rect=(20, 200, 200, 220))
        hidden = Node("Hyperlink", name="Hidden", value="https://hidden.example/", rect=(0, 0, 200, 20))
        hidden.visible = False
        document = Node("Document", children=[second, Node("Group", children=[first]), hidden])
        state = chrome._document_state(document, include_links=True)
        self.assertEqual(state["links"], [{"text": "First result", "href": "https://first.example/"},
                                        {"text": "Second result", "href": "https://second.example/"}])

    def test_truncated_text_nodes_and_link_inventory_fail_closed(self):
        for limit in ("text", "nodes", "links"):
            with self.subTest(limit=limit):
                document = Node("Document", text="visible text", children=[
                    Node("Hyperlink", value="https://one.example/"), Node("Hyperlink", value="https://two.example/")])
                attr = {"text": "_MAX_TEXT", "nodes": "_MAX_NODES", "links": "_MAX_LINKS"}[limit]
                with patch.object(chrome, attr, 1), self.assertRaisesRegex(InputNotDispatchedError, "truncated"):
                    chrome._document_state(document, include_links=True)

    def test_page_toolbar_edit_cannot_be_mistaken_for_address_field(self):
        page_editor = Node("Edit")
        document = Node("Document", children=[Node("ToolBar", children=[page_editor])])
        omnibox = Node("Edit")
        result = chrome._browser_chrome(Node("Window", children=[Node("ToolBar", children=[omnibox]), document]))
        self.assertEqual(result["editors"], [omnibox])

    def test_offscreen_layout_container_does_not_hide_visible_document(self):
        document = Node("Document")
        layout = Node("Pane", children=[document])
        layout.visible = False
        result = chrome._browser_chrome(Node("Window", children=[layout]))
        self.assertEqual(result["documents"], [document])

    def test_hidden_page_document_never_exposes_page_edit_as_omnibox(self):
        document = Node("Document", children=[Node("ToolBar", children=[Node("Edit")])])
        document.visible = False
        result = chrome._browser_chrome(Node("Window", children=[document]))
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["editors"], [])

    def test_document_replacement_during_observation_is_rejected(self):
        fixture = BrowserFixture()
        def changed(document, **_kwargs):
            document.value = "https://replacement.example/"
            return {"evidence": [], "links": []}
        with fixture.patches(), patch.object(chrome, "_document_state", side_effect=changed), \
                self.assertRaisesRegex(InputNotDispatchedError, "changed during observation"):
            chrome.read_existing_chrome(fixture.identity)

    def test_address_scheme_elision_is_supported_without_accepting_query_drafts(self):
        self.assertTrue(chrome._address_matches_document("example.com", "https://example.com/"))
        self.assertTrue(chrome._address_matches_document("example.com/path?q=old", "https://example.com/path?q=old"))
        self.assertTrue(chrome._address_matches_document("example.com/path?q=old", "https://www.example.com/path?q=old"))
        self.assertTrue(chrome._address_matches_document("example.com", "https://www.example.com/"))
        self.assertFalse(chrome._address_matches_document("example.com/path?q=new", "https://example.com/path?q=old"))
        self.assertFalse(chrome._address_matches_document("", "https://example.com/"))

    def test_window_selection_prefers_foreground_otherwise_preserves_z_order(self):
        rows = [{"hwnd": 1, "pid": 101}, {"hwnd": 2, "pid": 102}]
        for foreground, expected in ((2, 2), (99, 1)):
            with self.subTest(foreground=foreground), patch.object(chrome.os, "name", "nt"), \
                    patch("core.app_tools._visible_windows", return_value=list(rows)), \
                    patch("core.semantic_ui_tools._foreground_hwnd", return_value=foreground), \
                    patch.object(chrome, "_window_class", return_value="Chrome_WidgetWin_1"), \
                    patch("psutil.Process", return_value=SimpleNamespace(name=lambda: "chrome.exe")), \
                    patch.object(chrome, "_identity", side_effect=lambda hwnd: {"hwnd": hwnd}), \
                    patch.object(chrome, "_window"), patch.object(chrome, "_assert_window"), \
                    patch.object(chrome, "_browser_chrome", return_value={"editors": [object()]}):
                self.assertEqual(chrome.find_chrome_window(), {"hwnd": expected})

    def test_non_chrome_windows_do_not_require_process_inspection(self):
        with patch.object(chrome.os, "name", "nt"), \
                patch("core.app_tools._visible_windows", return_value=[{"hwnd": 1, "pid": 1}]), \
                patch("core.semantic_ui_tools._foreground_hwnd", return_value=1), \
                patch.object(chrome, "_window_class", return_value="SystemWindow"), \
                patch("psutil.Process", side_effect=psutil.AccessDenied) as process:
            self.assertIsNone(chrome.find_chrome_window())
        process.assert_not_called()

    def test_unreadable_chromium_process_does_not_report_browser_absent(self):
        with patch.object(chrome.os, "name", "nt"), \
                patch("core.app_tools._visible_windows", return_value=[{"hwnd": 1, "pid": 1}]), \
                patch("core.semantic_ui_tools._foreground_hwnd", return_value=1), \
                patch.object(chrome, "_window_class", return_value="Chrome_WidgetWin_1"), \
                patch("psutil.Process", side_effect=psutil.AccessDenied), \
                self.assertRaisesRegex(InputNotDispatchedError, "refusing to launch another Chrome"):
            chrome.find_chrome_window()


if __name__ == "__main__":
    unittest.main()
