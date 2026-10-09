from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.browser_semantic import BrowserChallengeBlocked, run_browser_operation
from core.chrome_cdp import _ChromeRuntime, chrome_is_connected
from core.desktop_input import InputNotDispatchedError


class BrowserRuntimeTests(unittest.TestCase):
    def test_missing_node_version_returns_pre_input_telemetry_through_interaction_tool(self):
        from core.browser_semantic import register_browser_semantic_tools
        from core.execution_telemetry import input_not_dispatched
        from core.tools import ToolRegistry
        from core.universal_interaction import register_universal_interaction_tools

        registry = ToolRegistry()
        register_browser_semantic_tools(registry)
        register_universal_interaction_tools(registry)
        page = MagicMock()
        def operation(name, **args):
            return run_browser_operation(page, name, args, lambda: None)
        with patch("core.chrome_cdp.chrome_is_connected", return_value=True), \
                patch("core.chrome_cdp.chrome_page_operation", side_effect=operation):
            result = registry.execute("interaction_click", {
                "surface": "browser", "browser_target": {"node_id": "n1"},
            }, approved=True)
        self.assertIn("expected_version", result)
        self.assertTrue(input_not_dispatched(result))
        page.evaluate_handle.assert_not_called()
        page.locator.assert_not_called()

    def test_read_only_action_preflight_failures_are_not_dispatched(self):
        cases = (
            ("missing version", {"target": {"node_id": "n1"}}, "expected_version"),
            ("unsupported action", {"action": "delete"}, "Unsupported semantic action"),
            ("invalid value", {"value": "\0"}, "without NUL"),
            ("disabled", {}, "visible and enabled"),
            ("ambiguous", {}, "2 matches"),
            ("missing frame", {"frame_selector": "iframe"}, "missing or ambiguous"),
            ("unreadable append", {"action": "append", "value": "draft"}, "appendable text"),
        )
        for case, extra, error in cases:
            with self.subTest(case=case):
                page = MagicMock()
                page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
                target = MagicMock()
                target.is_visible.return_value = True
                target.is_enabled.return_value = case != "disabled"
                target.evaluate.return_value = None
                page.locator.return_value.element_handles.return_value = [target, MagicMock()] if case == "ambiguous" else [target]
                page.locator.return_value.count.return_value = 0
                args = {"action": "click", "target": {"selector": "button"}, **extra}
                with self.assertRaisesRegex(InputNotDispatchedError, error):
                    run_browser_operation(page, "semantic_action", args, lambda: None)
                for action in ("click", "fill", "press", "focus", "select_option", "check", "uncheck"):
                    getattr(target, action).assert_not_called()

    def test_action_invocation_failures_remain_uncertain_for_every_mutation(self):
        for action, method in (("click", "click"), ("fill", "fill"), ("append", "fill"),
                               ("press", "press"), ("focus", "focus"), ("select", "select_option"),
                               ("check", "check"), ("uncheck", "uncheck")):
            with self.subTest(action=action):
                page = MagicMock()
                page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
                target = MagicMock()
                page.locator.return_value.element_handles.return_value = [target]
                target.is_visible.return_value = target.is_enabled.return_value = True
                target.evaluate.return_value = "existing draft"
                getattr(target, method).side_effect = TimeoutError("delivery uncertain")
                with self.assertRaisesRegex(TimeoutError, "delivery uncertain") as raised:
                    run_browser_operation(page, "semantic_action", {
                        "action": action, "target": {"selector": "button"}, "value": "new",
                    }, lambda: None)
                self.assertNotIsInstance(raised.exception, InputNotDispatchedError)
                getattr(target, method).assert_called_once()
                target.dispose.assert_called_once()

    def test_legacy_click_rejects_ambiguous_targets_without_dispatch(self):
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._active_cancel = None
        runtime._page = MagicMock()
        runtime._page.is_closed.return_value = False
        target = runtime._page.get_by_text.return_value
        target.count.return_value = 2
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            runtime._cmd_page("click", selector="Send")
        target.click.assert_not_called()
        target.first.click.assert_not_called()

    def test_abandoned_queued_command_is_cancelled(self):
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._ready = threading.Event()
        runtime._ready.set()
        runtime._startup_error = None
        runtime._commands = queue.Queue()
        checks = iter([None, RuntimeError("stopped")])
        def check():
            value = next(checks)
            if value:
                raise value
        with patch("core.process_control.check_cancelled", side_effect=check):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                runtime.call("page", operation="click", selector="#send")
        command, args, reply, cancelled, context = runtime._commands.get_nowait()
        self.assertTrue(cancelled.is_set())
        runtime._active_cancel = cancelled
        with self.assertRaisesRegex(RuntimeError, "abandoned"):
            runtime._check_cancelled()

    def test_challenge_inspection_failure_prevents_mutation(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("disconnected")
        target = MagicMock()
        page.locator.return_value.element_handles.return_value = [target]
        with self.assertRaises(BrowserChallengeBlocked):
            run_browser_operation(page, "semantic_action", {"action": "click", "target": {"selector": "button"}}, lambda: None)
        target.click.assert_not_called()
        target.dispose.assert_called_once()

    def test_connectivity_probe_does_not_swallow_cancellation(self):
        with patch("core.process_control.check_cancelled", side_effect=RuntimeError("Emergency stop")):
            with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
                chrome_is_connected()

    def test_expired_queued_command_is_marked_abandoned(self):
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._ready = threading.Event()
        runtime._ready.set()
        runtime._startup_error = None
        runtime._commands = queue.Queue()
        with patch("core.chrome_cdp.time.monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                runtime.call("page", operation="click", selector="#send")
        self.assertTrue(runtime._commands.get_nowait()[3].is_set())

    def test_uncertain_click_is_never_replayed(self):
        page = MagicMock()
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        locator = page.locator.return_value
        target = MagicMock()
        locator.element_handles.return_value = [target]
        target.is_visible.return_value = target.is_enabled.return_value = True
        target.click.side_effect = TimeoutError("delivery uncertain")
        with self.assertRaisesRegex(TimeoutError, "uncertain"):
            run_browser_operation(page, "semantic_action", {"action": "click", "target": {"selector": "button"}}, lambda: None)
        target.click.assert_called_once()
        target.dispose.assert_called_once()
        locator.click.assert_not_called()

    def test_wait_cancellation_during_successful_probe_is_not_reported_verified(self):
        page = MagicMock()
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        locator = page.locator.return_value
        locator.count.return_value = 1
        locator.is_visible.return_value = True
        cancelled = threading.Event()
        locator.is_visible.side_effect = lambda: cancelled.set() or True
        def check():
            if cancelled.is_set():
                raise RuntimeError("stopped during observation")
        with self.assertRaisesRegex(RuntimeError, "stopped during observation"):
            run_browser_operation(page, "wait_state", {"target": {"selector": "button"}}, check)

    def test_stop_during_semantic_preflight_prevents_dispatch_and_disposes_handle(self):
        page = MagicMock()
        target = MagicMock()
        page.locator.return_value.element_handles.return_value = [target]
        target.is_visible.return_value = target.is_enabled.return_value = True
        cancelled = threading.Event()
        inspections = 0
        def inspect(_script):
            nonlocal inspections
            inspections += 1
            if inspections == 1:
                cancelled.set()
            return {"inspection_available": True, "challenge_detected": False}
        page.evaluate.side_effect = inspect
        def check():
            if cancelled.is_set():
                raise RuntimeError("stopped during preflight")
        with self.assertRaisesRegex(RuntimeError, "stopped during preflight"):
            run_browser_operation(page, "semantic_action", {"action": "click", "target": {"selector": "button"}}, check)
        target.click.assert_not_called()
        target.dispose.assert_called_once()

    def test_snapshot_public_tool_dispatches_exactly_one_runtime_command(self):
        from core.browser_semantic import browser_semantic_snapshot
        with patch("core.chrome_cdp.chrome_page_operation", return_value={"title": "Fixture", "tab": {}, "tabs": []}) as operation, \
                patch("core.chrome_cdp.chrome_current_tab") as current, patch("core.chrome_cdp.chrome_tabs") as tabs:
            browser_semantic_snapshot()
        operation.assert_called_once_with("semantic_snapshot", force=False, max_nodes=160, frame_selector="")
        current.assert_not_called()
        tabs.assert_not_called()

    def test_legacy_wait_preserves_sixty_second_bound_without_real_sleep(self):
        from core.advanced_tools import browser_wait
        page = MagicMock()
        page.evaluate.return_value = {"inspection_available": True, "challenge_detected": False}
        page.locator.return_value.count.side_effect = [0, 1]
        page.locator.return_value.is_visible.return_value = True
        with patch("core.tools._PAGE", page), patch("core.browser_semantic.time.monotonic", side_effect=[0, 31, 31]), \
                patch("core.browser_semantic.time.sleep") as sleep:
            result = browser_wait("button", 60000)
        self.assertTrue(result.startswith("VERIFIED:"))
        sleep.assert_called_once_with(0.05)


class BrowserSemanticFixtureTests(unittest.TestCase):
    """Isolated headless HTML fixtures; never attaches to a user's Chrome/CDP."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Playwright is not installed")
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Headless Chromium fixture unavailable: {type(exc).__name__}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route("**/*", lambda route: route.fulfill(body="<html><body></body></html>", content_type="text/html"))
        self.page = self.context.new_page()
        self.page.goto("http://127.0.0.1:1/fixture")

    def tearDown(self):
        self.context.close()

    def run_op(self, operation, **args):
        return run_browser_operation(self.page, operation, args, lambda: None)

    def test_snapshot_contains_hierarchy_focus_and_cache_without_field_values(self):
        self.page.set_content('<main><h1>Inbox</h1><label>Message<input value="private-draft"></label><button>Send</button></main>')
        self.page.locator("input").focus()
        first = self.run_op("semantic_snapshot")
        second = self.run_op("semantic_snapshot")
        self.assertTrue(second["cached"])
        self.assertEqual(first["version"], second["version"])
        self.assertNotIn("private-draft", str(first))
        textbox = next(n for n in first["nodes"] if n["role"] == "textbox")
        self.assertEqual(textbox["name"], "Message")
        self.assertEqual(first["focused_node"], textbox["node_id"])
        self.assertIsNotNone(textbox["parent"])

    def test_snapshot_combines_guard_and_scene_in_one_cdp_evaluation(self):
        self.page.set_content('<button>Open</button>')
        with patch.object(self.page, "evaluate", wraps=self.page.evaluate) as evaluate:
            snapshot = self.run_op("semantic_snapshot")
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(snapshot["nodes"][0]["name"], "Open")
        self.page.set_content('<h1>Human verification</h1><button>Open</button>')
        with self.assertRaises(BrowserChallengeBlocked):
            self.run_op("semantic_snapshot")
        self.assertFalse(self.page.evaluate("!!window.__jarvisSemanticV1.cache"))

    def test_implicit_browser_typing_uses_focused_editor_and_exact_readback(self):
        from core.browser_semantic import register_browser_semantic_tools
        from core.tools import ToolRegistry
        from core.universal_interaction import interaction_type

        registry = ToolRegistry()
        register_browser_semantic_tools(registry)
        self.page.set_content('<input aria-label="Other" value="untouched"><input aria-label="Draft">')
        self.page.get_by_role("textbox", name="Draft").focus()
        with patch("core.universal_interaction._browser_active", return_value=True), \
                patch("core.chrome_cdp.chrome_page_operation", side_effect=lambda operation, **args: self.run_op(operation, **args)):
            result = interaction_type("Hello مرحبا", registry=registry)
        self.assertTrue(result.startswith("VERIFIED:"), result)
        self.assertEqual(self.page.get_by_role("textbox", name="Draft").input_value(), "Hello مرحبا")
        self.assertEqual(self.page.get_by_role("textbox", name="Other").input_value(), "untouched")

    def test_implicit_browser_typing_never_guesses_editor_or_uses_stale_focus(self):
        from core.browser_semantic import browser_semantic_snapshot, register_browser_semantic_tools
        from core.tools import ToolRegistry
        from core.universal_interaction import interaction_type

        registry = ToolRegistry()
        register_browser_semantic_tools(registry)
        self.page.set_content('<input aria-label="Draft"><button>Other</button>')
        with patch("core.universal_interaction._browser_active", return_value=True), \
                patch("core.chrome_cdp.chrome_page_operation", side_effect=lambda operation, **args: self.run_op(operation, **args)):
            with self.assertRaisesRegex(InputNotDispatchedError, "focused browser editor"):
                interaction_type("do not type", registry=registry)
            self.page.locator("input").focus()
            def changed_focus(**kwargs):
                snapshot = browser_semantic_snapshot(**kwargs)
                self.page.locator("button").focus()
                return snapshot
            with patch("core.browser_semantic.browser_semantic_snapshot", side_effect=changed_focus):
                with self.assertRaisesRegex(InputNotDispatchedError, "STALE_UI"):
                    interaction_type("do not type", registry=registry)
        self.assertEqual(self.page.locator("input").input_value(), "")

    def test_snapshot_reuses_single_geometry_read_for_each_candidate(self):
        self.page.set_content('<main><button>Open</button><input aria-label="Draft"></main>')
        self.page.evaluate("""() => {
            const original = Element.prototype.getBoundingClientRect;
            window.geometryReads = 0;
            Element.prototype.getBoundingClientRect = function() {
                window.geometryReads++; return original.call(this);
            };
        }""")
        snapshot = self.run_op("semantic_snapshot", force=True)
        self.assertEqual(len(snapshot["nodes"]), 3)
        self.assertEqual(self.page.evaluate("window.geometryReads"), 3)

    def test_snapshot_avoids_style_queries_for_offscreen_controls(self):
        self.page.set_content('<button>Visible</button><button style="position:absolute;top:5000px">Offscreen</button>')
        self.page.evaluate("""() => {
            const original = window.getComputedStyle;
            window.styleReads = 0;
            window.getComputedStyle = function(...args) {
                window.styleReads++; return original.apply(this, args);
            };
        }""")
        snapshot = self.run_op("semantic_snapshot", force=True)
        self.assertEqual([node["name"] for node in snapshot["nodes"]], ["Visible"])
        self.assertEqual(self.page.evaluate("window.styleReads"), 1)

    def test_snapshot_recognizes_custom_controls_and_keeps_observed_identity(self):
        self.page.set_content('<div tabindex="0" aria-label="Custom menu" onclick="window.opened=true">Menu</div>'
                              '<details><summary>Options</summary></details>'
                              '<button><img alt="Open files" width="20" height="20"></button>'
                              '<div contenteditable="true" aria-label="Draft">private-text</div>')
        snapshot = self.run_op("semantic_snapshot")
        nodes = {node["name"]: node for node in snapshot["nodes"]}
        self.assertEqual(set(nodes), {"Custom menu", "Options", "Open files", "Draft"})
        self.assertTrue(nodes["Custom menu"]["interactive"])
        self.assertFalse(nodes["Custom menu"]["role_resolvable"])
        self.assertFalse(nodes["Draft"]["role_resolvable"])
        self.assertNotIn("private-text", str(snapshot))
        for node in nodes.values():
            if node["role_resolvable"]:
                self.assertEqual(self.page.get_by_role(node["role"], name=node["name"], exact=True).count(), 1)
        self.run_op("semantic_action", action="click", expected_version=snapshot["version"],
                    target={"node_id": nodes["Custom menu"]["node_id"]})
        self.assertTrue(self.page.evaluate("window.opened"))
        with self.assertRaisesRegex(Exception, "STALE_UI"):
            self.run_op("semantic_action", action="fill", value="new", expected_version=snapshot["version"],
                        target={"node_id": nodes["Draft"]["node_id"]})

    def test_shadow_control_label_uses_its_own_root_and_typing_is_verified(self):
        self.page.set_content('<span id="label">Wrong outer label</span><div id="host"></div>')
        self.page.evaluate("""() => {
            document.querySelector('#host').attachShadow({mode:'open'}).innerHTML =
                '<span id="label">Rich draft</span><div contenteditable="true" aria-labelledby="label"></div>';
        }""")
        snapshot = self.run_op("semantic_snapshot")
        node = next(n for n in snapshot["nodes"] if n["role"] == "textbox")
        self.assertEqual(node["name"], "Rich draft")
        result = self.run_op("semantic_action", action="fill", value="Hello مرحبا", expected_version=snapshot["version"],
                             target={"node_id": node["node_id"]})
        self.assertTrue(result["verified"])

    def test_accessible_names_follow_label_precedence_and_omit_hidden_descendants(self):
        self.page.set_content('<span id="label" hidden>Referenced label</span>'
                              '<button id="referenced" aria-labelledby="label" aria-label="Wrong label">Wrong text</button>'
                              '<button id="title" title="Tooltip">Visible name</button>'
                              '<button id="hidden">Open<span aria-hidden="true"> decorations</span>'
                              '<span hidden> secret</span><span style="display:none"> hidden</span></button>'
                              '<button id="image">Open <img alt="files" width="20" height="20"></button>'
                              '<button id="missing" aria-labelledby="absent-one absent-two" aria-label="Fallback">Wrong</button>')
        snapshot = self.run_op("semantic_snapshot")
        nodes = {node["id"]: node for node in snapshot["nodes"]}
        expected = {"referenced": "Referenced label", "title": "Visible name", "hidden": "Open", "image": "Open files", "missing": "Fallback"}
        for identity, name in expected.items():
            with self.subTest(control=identity):
                node = nodes[identity]
                self.assertEqual(node["name"], name)
                self.assertTrue(node["role_resolvable"])
                self.assertEqual(self.page.get_by_role(node["role"], name=node["name"], exact=True).get_attribute("id"), identity)

    def test_long_display_label_keeps_exact_observed_target_for_click(self):
        from core.interaction_scene import _browser_nodes

        full_name = "A long control label " * 12
        self.page.set_content(f'<button aria-label="{full_name}" onclick="window.clicks=(window.clicks||0)+1">Open</button>')
        snapshot = self.run_op("semantic_snapshot")
        row = snapshot["nodes"][0]
        self.assertEqual(len(row["name"]), 180)
        self.assertFalse(row["role_resolvable"])
        target = _browser_nodes(snapshot)[0]["target"]
        self.assertEqual(target["browser_target"], {"node_id": row["node_id"]})
        self.assertEqual(target["expected_version"], snapshot["version"])
        self.run_op("semantic_action", action="click", target=target["browser_target"],
                    expected_version=target["expected_version"])
        self.assertEqual(self.page.evaluate("window.clicks"), 1)

    def test_native_progress_and_lower_level_headings_are_recognized(self):
        from core.computer_perception import _state_signals
        from core.interaction_scene import _browser_nodes

        self.page.set_content('<progress aria-label="Document loading" max="100" value="10"></progress>'
                              '<h4>Details</h4><h5>Advanced</h5><h6>Notes</h6>')
        snapshot = self.run_op("semantic_snapshot")
        self.assertEqual([(node["role"], node["name"]) for node in snapshot["nodes"]],
                         [("progressbar", "Document loading"), ("heading", "Details"),
                          ("heading", "Advanced"), ("heading", "Notes")])
        signals = _state_signals({"nodes": _browser_nodes(snapshot)})
        self.assertEqual([item["name"] for item in signals["loading_indicators"]], ["Document loading"])

    def test_snapshot_respects_composed_ancestors_and_disabled_fieldset(self):
        self.page.set_content('<main id="main"><div id="hidden-host" aria-hidden="true"></div>'
                              '<div id="inert-host" inert></div><div id="visible-host"></div>'
                              '<fieldset disabled><legend><button id="legend">Legend exception</button></legend>'
                              '<button id="disabled">Disabled action</button></fieldset>'
                              '<div aria-disabled="true"><button id="aria-disabled">Unavailable action</button></div></main>')
        self.page.evaluate("""() => {
            for (const id of ['hidden-host','inert-host','visible-host'])
                document.getElementById(id).attachShadow({mode:'open'}).innerHTML = `<button id="${id}-button">${id}</button>`;
        }""")
        snapshot = self.run_op("semantic_snapshot")
        nodes = {node["id"]: node for node in snapshot["nodes"]}
        self.assertNotIn("hidden-host-button", nodes)
        self.assertNotIn("inert-host-button", nodes)
        self.assertEqual(nodes["visible-host-button"]["parent"], nodes["main"]["node_id"])
        self.assertTrue(nodes["disabled"]["disabled"])
        self.assertTrue(nodes["aria-disabled"]["disabled"])
        self.assertFalse(nodes["legend"]["disabled"])

    def test_snapshot_does_not_advertise_implicit_roles_for_non_text_inputs(self):
        self.page.set_content('<input type="password" aria-label="Password">'
                              '<input type="date" aria-label="Date">'
                              '<input type="file" aria-label="Attachment">'
                              '<input type="color" aria-label="Colour">'
                              '<input type="range" aria-label="Volume">')
        snapshot = self.run_op("semantic_snapshot")
        nodes = {node["name"]: node for node in snapshot["nodes"]}
        for name in ("Password", "Date", "Attachment", "Colour"):
            with self.subTest(control=name):
                self.assertFalse(nodes[name]["role_resolvable"])
        self.assertEqual(nodes["Password"]["input_kind"], "text")
        self.assertEqual(nodes["Date"]["input_kind"], "text")
        self.assertEqual(nodes["Attachment"]["input_kind"], "")
        self.assertEqual(nodes["Colour"]["input_kind"], "")
        self.assertEqual(nodes["Volume"]["input_kind"], "")

    def test_snapshot_reports_uninspected_visual_regions_even_when_node_budget_omits_them(self):
        self.page.set_content('<button>Keep</button><canvas width="100" height="50"></canvas>'
                              '<iframe title="Embedded form" srcdoc="<body><button>Inside</button></body>"></iframe>')
        snapshot = self.run_op("semantic_snapshot", max_nodes=1)
        self.assertEqual([node["name"] for node in snapshot["nodes"]], ["Keep"])
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(set(snapshot["coverage_gaps"]), {"uninspected_embedded_content", "visual_only_regions"})
        full = self.run_op("semantic_snapshot")
        canvas = next(node for node in full["nodes"] if node["tag"] == "canvas")
        self.assertFalse(canvas["role_resolvable"])
        frame = self.run_op("semantic_snapshot", frame_selector="iframe")
        self.assertEqual(frame["coverage_gaps"], [])
        self.assertEqual(frame["nodes"][0]["name"], "Inside")
        self.page.locator("canvas, iframe").evaluate_all("els => els.forEach(el => el.hidden=true)")
        self.assertEqual(self.run_op("semantic_snapshot")["coverage_gaps"], [])

    def test_changed_dom_invalidates_observed_node_and_never_clicks(self):
        self.page.set_content('<button onclick="window.clicks=(window.clicks||0)+1">Send</button>')
        first = self.run_op("semantic_snapshot")
        node = next(n for n in first["nodes"] if n["role"] == "button")
        self.page.locator("button").evaluate("el => el.textContent = 'Delete'")
        with self.assertRaisesRegex(InputNotDispatchedError, "STALE_UI"):
            self.run_op("semantic_action", action="click", target={"node_id": node["node_id"]}, expected_version=first["version"])
        self.assertIsNone(self.page.evaluate("window.clicks"))

    def test_typing_rejects_dropdown_before_any_input_then_selects_exact_option(self):
        self.page.set_content('<select aria-label="Status"><option>All</option><option>READY</option></select>')
        self.page.evaluate("() => { document.querySelector('select').onchange=()=>window.changes=(window.changes||0)+1; }")
        snapshot = self.run_op("semantic_snapshot")
        self.assertEqual(snapshot["nodes"][0]["input_kind"], "select")
        with self.assertRaisesRegex(InputNotDispatchedError, "not a writable text editor"):
            self.run_op("semantic_action", action="fill", value="READY", target={"role": "combobox", "name": "Status"})
        self.assertIsNone(self.page.evaluate("window.changes"))
        self.assertEqual(self.page.locator("select").input_value(), "All")
        selected = self.run_op("semantic_action", action="select", value="READY", target={"role": "combobox", "name": "Status"})
        self.assertTrue(selected["verified"])
        self.assertEqual(self.page.evaluate("window.changes"), 1)

    def test_one_scene_allows_multiple_labeled_inputs_without_stale_snapshot_retry(self):
        from core.interaction_scene import _browser_nodes
        self.page.set_content('<input aria-label="Project"><textarea aria-label="Draft"></textarea>')
        nodes = _browser_nodes(self.run_op("semantic_snapshot"))
        targets = {node["name"]: node["target"] for node in nodes}
        for name, value in (("Project", "Atlas"), ("Draft", "Handoff — مرحبا")):
            action = targets[name]
            self.assertNotIn("expected_version", action)
            result = self.run_op("semantic_action", action="fill", target=action["browser_target"], value=value)
            self.assertTrue(result["verified"])
        self.assertEqual(self.page.get_by_role("textbox", name="Draft").input_value(), "Handoff — مرحبا")

    def test_select_all_verifies_editor_selection_without_exposing_private_draft(self):
        for markup in ('<textarea aria-label="Draft">private draft مرحبا</textarea>',
                       '<input aria-label="Draft" value="private draft مرحبا">',
                       '<div role="textbox" contenteditable="true" aria-label="Draft"><b>private</b> draft مرحبا</div>',
                       '<textarea aria-label="Draft"></textarea>'):
            with self.subTest(markup=markup):
                self.page.set_content(markup)
                result = self.run_op("semantic_action", action="press", value="Control+A",
                                     target={"role": "textbox", "name": "Draft"})
                self.assertTrue(result["verified"])
                self.assertEqual(result["verification"], "editor_selection_all")
                self.assertNotIn("private", str(result))
                self.assertFalse(result["requires_result_verification"])

    def test_select_all_cannot_verify_prevented_selection_changed_text_or_stolen_focus(self):
        for listener in ("e.preventDefault()", "e.target.value='changed'",
                         "e.preventDefault();document.querySelector('#other').focus()",
                         "e.preventDefault();e.target.remove()"):
            with self.subTest(listener=listener):
                self.page.set_content('<textarea aria-label="Draft">original text</textarea><input id="other">')
                self.page.locator('textarea').evaluate("(el, body) => el.addEventListener('keydown', new Function('e', body))", listener)
                with self.assertRaises(RuntimeError) as caught:
                    self.run_op("semantic_action", action="press", value="Control+A",
                                target={"role": "textbox", "name": "Draft"})
                self.assertNotIsInstance(caught.exception, InputNotDispatchedError)

    def test_enter_and_non_editor_select_all_still_require_outcome_review(self):
        self.page.set_content('<textarea aria-label="Draft">original</textarea><button>Open</button>')
        for value, target in (("Enter", {"role": "textbox", "name": "Draft"}),
                              ("Control+A", {"role": "button", "name": "Open"})):
            with self.subTest(value=value, target=target):
                result = self.run_op("semantic_action", action="press", value=value, target=target)
                self.assertFalse(result["verified"])
                self.assertTrue(result["requires_result_verification"])

    def test_missing_node_version_can_be_corrected_without_replaying_an_action(self):
        self.page.set_content('<button onclick="window.clicks=(window.clicks||0)+1">Open</button>')
        snapshot = self.run_op("semantic_snapshot")
        node = next(n for n in snapshot["nodes"] if n["role"] == "button")
        with self.assertRaisesRegex(InputNotDispatchedError, "expected_version"):
            self.run_op("semantic_action", action="click", target={"node_id": node["node_id"]})
        self.assertIsNone(self.page.evaluate("window.clicks"))
        result = self.run_op("semantic_action", action="click", target={"node_id": node["node_id"]},
                             expected_version=snapshot["version"])
        self.assertTrue(result["executed"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.page.evaluate("window.clicks"), 1)

    def test_refresh_preserves_node_identity_and_node_focus_uses_element_api(self):
        self.page.set_content('<label>Message<input></label>')
        first = self.run_op("semantic_snapshot")
        second = self.run_op("semantic_snapshot", force=True)
        original = next(n for n in first["nodes"] if n["role"] == "textbox")
        refreshed = next(n for n in second["nodes"] if n["role"] == "textbox")
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(original["node_id"], refreshed["node_id"])
        result = self.run_op("semantic_action", action="focus", target={"node_id": original["node_id"]}, expected_version=first["version"])
        self.assertTrue(result["verified"])

    def test_node_fill_uses_fresh_version_and_readback(self):
        self.page.set_content('<label>Message<input></label>')
        snapshot = self.run_op("semantic_snapshot")
        textbox = next(n for n in snapshot["nodes"] if n["role"] == "textbox")
        result = self.run_op("semantic_action", action="fill", target={"node_id": textbox["node_id"]}, expected_version=snapshot["version"], value="typed once")
        self.assertTrue(result["verified"])
        self.assertEqual(self.page.locator("input").input_value(), "typed once")

    def test_snapshot_preserves_focused_editor_when_control_budget_is_small(self):
        self.page.set_content('<main><button>One</button><button>Two</button>'
                              '<input aria-label="Current draft"></main>')
        self.page.locator("input").focus()
        snapshot = self.run_op("semantic_snapshot", max_nodes=1)
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(snapshot["nodes"][0]["name"], "Current draft")
        self.assertEqual(snapshot["focused_node"], snapshot["nodes"][0]["node_id"])

    def test_snapshot_native_roles_resolve_back_to_same_controls(self):
        self.page.set_content('<input type="search" aria-label="Search">'
                              '<input type="number" aria-label="Quantity">'
                              '<input type="button" value="Apply">'
                              '<select multiple aria-label="Choices"><option>One</option></select>')
        snapshot = self.run_op("semantic_snapshot")
        self.assertEqual({node["role"] for node in snapshot["nodes"]},
                         {"searchbox", "spinbutton", "button", "listbox"})
        for node in snapshot["nodes"]:
            self.assertEqual(self.page.get_by_role(node["role"], name=node["name"], exact=True).count(), 1)

    def test_text_checkpoint_reads_editor_values_exactly_without_exposing_them(self):
        value = " private draft مرحبا "
        self.page.set_content('<input aria-label="Draft"><textarea aria-label="Notes"></textarea>'
                              '<div role="textbox" contenteditable="true" style="white-space:pre-wrap" aria-label="Rich draft"></div>')
        for name in ("Draft", "Notes", "Rich draft"):
            with self.subTest(name=name):
                self.page.get_by_role("textbox", name=name, exact=True).fill(value)
                result = self.run_op("wait_state", state="text", text=value,
                                     target={"role": "textbox", "name": name}, timeout_ms=0)
                self.assertTrue(result["matched"])
                self.assertEqual(result["polls"], 1)
                self.assertNotIn(value, str(result))
                with self.assertRaises(TimeoutError):
                    self.run_op("wait_state", state="text", text=value.strip(),
                                target={"role": "textbox", "name": name}, timeout_ms=0)

    def test_focus_checkpoint_observes_without_changing_focus(self):
        self.page.set_content('<input aria-label="First"><input aria-label="Second">')
        self.page.get_by_role("textbox", name="First").focus()
        result = self.run_op("wait_state", state="focused", timeout_ms=0,
                             target={"role": "textbox", "name": "First"})
        self.assertTrue(result["matched"])
        with self.assertRaises(TimeoutError):
            self.run_op("wait_state", state="focused", timeout_ms=0,
                        target={"role": "textbox", "name": "Second"})
        self.assertTrue(self.page.get_by_role("textbox", name="First").evaluate("el => el.matches(':focus')"))

    def test_semantic_scroll_verifies_actual_viewport_motion(self):
        self.page.set_viewport_size({"width": 800, "height": 600})
        self.page.set_content('<div style="height:3000px">Tall page</div>')
        result = self.run_op("semantic_scroll", delta_y=400, delta_x=0)
        self.assertTrue(result["verified"])
        self.assertGreater(result["after"]["y"], result["before"]["y"])
        self.assertEqual(result["delta_y"], 400)

    def test_append_preserves_existing_text_and_verifies_without_exposing_value(self):
        self.page.set_content('<label>Draft<input value="private-prefix"></label>')
        result = self.run_op("semantic_action", action="append", target={"role": "textbox", "name": "Draft"}, value=" + new")
        self.assertTrue(result["verified"])
        self.assertEqual(self.page.locator("input").input_value(), "private-prefix + new")
        self.assertNotIn("private-prefix", str(result))

    def test_focus_change_invalidates_snapshot_node(self):
        self.page.set_content('<input aria-label="Draft"><button>Send</button>')
        first = self.run_op("semantic_snapshot")
        button = next(n for n in first["nodes"] if n["role"] == "button")
        self.page.locator("input").focus()
        with self.assertRaisesRegex(Exception, "STALE_UI"):
            self.run_op("semantic_action", action="click", target={"node_id": button["node_id"]}, expected_version=first["version"])

    def test_open_shadow_dom_mutations_invalidate_node_and_contenteditable_values_are_omitted(self):
        self.page.set_content('<main><div role="group"><div contenteditable="" aria-label="Draft">private-draft</div></div><div id="host"></div></main>')
        self.page.evaluate("document.querySelector('#host').attachShadow({mode:'open'}).innerHTML='<button>Send</button>'")
        first = self.run_op("semantic_snapshot")
        self.assertNotIn("private-draft", str(first))
        button = next(n for n in first["nodes"] if n["role"] == "button")
        self.page.locator("button").evaluate("el => el.textContent = 'Delete'")
        with self.assertRaisesRegex(Exception, "STALE_UI"):
            self.run_op("semantic_action", action="click", target={"node_id": button["node_id"]}, expected_version=first["version"])

    def test_exact_fill_verifies_locally_and_duplicate_buttons_reject(self):
        self.page.set_content('<label>Message<input></label><button>Send</button><button>Send</button>')
        result = self.run_op("semantic_action", action="fill", target={"role": "textbox", "name": "Message"}, value="hello")
        self.assertTrue(result["verified"])
        self.assertEqual(self.page.locator("input").input_value(), "hello")
        with self.assertRaisesRegex(RuntimeError, "2 matches"):
            self.run_op("semantic_action", action="click", target={"role": "button", "name": "Send"})

    def test_action_scans_challenge_once_before_input_and_once_after(self):
        from core.browser_semantic import require_clear_page
        self.page.set_content('<label>Draft<input></label><button onclick="window.clicked=true">Open</button>')
        for action, target, value in (
            ("fill", {"role": "textbox", "name": "Draft"}, "hello"),
            ("click", {"role": "button", "name": "Open"}, ""),
        ):
            with self.subTest(action=action):
                with patch("core.browser_semantic.require_clear_page", wraps=require_clear_page) as guard:
                    self.run_op("semantic_action", action=action, target=target, value=value)
                self.assertEqual(guard.call_count, 2)
        self.assertEqual(self.page.locator("input").input_value(), "hello")
        self.assertTrue(self.page.evaluate("window.clicked"))

    def test_iframe_action_retains_parent_and_frame_challenge_checks(self):
        from core.browser_semantic import require_clear_page
        self.page.set_content('<iframe srcdoc="<body><input aria-label=Draft></body>"></iframe>')
        frame = self.page.frames[1]
        with patch("core.browser_semantic.require_clear_page", wraps=require_clear_page) as guard:
            result = self.run_op("semantic_action", action="fill", value="hello", frame_selector="iframe",
                                 target={"role": "textbox", "name": "Draft"})
        self.assertTrue(result["verified"])
        self.assertEqual([call.args[0] for call in guard.call_args_list], [self.page, frame, self.page, frame])
        self.assertEqual(frame.locator("input").input_value(), "hello")
        for location in (self.page, frame):
            with self.subTest(challenge_in="parent" if location is self.page else "iframe"):
                location.evaluate("document.body.insertAdjacentHTML('beforeend', '<h1>Human verification</h1>')")
                with self.assertRaises(BrowserChallengeBlocked):
                    self.run_op("semantic_action", action="fill", value="must not type", frame_selector="iframe",
                                target={"role": "textbox", "name": "Draft"})
                self.assertEqual(frame.locator("input").input_value(), "hello")
                location.locator("h1").evaluate("el => el.remove()")

    def test_replaced_button_never_receives_input_from_previously_resolved_target(self):
        from core.browser_semantic import require_clear_page
        self.page.set_content('<button onclick="window.clicks=(window.clicks||0)+1">Send</button>')
        inspections = 0
        def inspect(page):
            nonlocal inspections
            inspections += 1
            result = require_clear_page(page)
            if inspections == 1:
                page.evaluate("document.querySelector('button').outerHTML='<button onclick=\"window.clicks=99\">Send</button>'")
            return result
        with patch("core.browser_semantic.require_clear_page", side_effect=inspect):
            with self.assertRaisesRegex(RuntimeError, "visible and enabled"):
                self.run_op("semantic_action", action="click", target={"role": "button", "name": "Send"})
        self.assertIsNone(self.page.evaluate("window.clicks"))

    def test_replaced_input_does_not_supply_successful_readback(self):
        self.page.set_content('<input aria-label="Draft" oninput="this.outerHTML=\'<input aria-label=&quot;Draft&quot; value=&quot;hello&quot;>\'">')
        with self.assertRaisesRegex(RuntimeError, "readback did not match") as raised:
            self.run_op("semantic_action", action="fill", target={"role": "textbox", "name": "Draft"}, value="hello")
        self.assertNotIsInstance(raised.exception, InputNotDispatchedError)
        self.assertEqual(self.page.locator("input").input_value(), "hello")

    def test_contenteditable_fill_uses_exact_connected_target_readback(self):
        self.page.set_content('<div contenteditable="true" role="textbox" aria-label="Draft"></div>')
        result = self.run_op("semantic_action", action="fill", target={"role": "textbox", "name": "Draft"}, value="hello مرحبا 😀")
        self.assertTrue(result["verified"])
        self.assertEqual(self.page.locator("div").inner_text(), "hello مرحبا 😀")

    def test_iframe_wait_stops_when_parent_develops_human_challenge(self):
        self.page.set_content('<iframe srcdoc="<body><button hidden>Ready</button></body>"></iframe>')
        def load(_seconds):
            self.page.evaluate("document.body.insertAdjacentHTML('beforeend', '<h1>Human verification</h1>')")
            self.page.frames[1].locator("button").evaluate("el => el.hidden=false")
        with patch("core.browser_semantic.time.sleep", side_effect=load):
            with self.assertRaises(BrowserChallengeBlocked):
                self.run_op("wait_state", target={"role": "button", "name": "Ready"}, frame_selector="iframe", timeout_ms=1000)

    def test_ready_wait_only_inspects_challenge_once_and_legacy_wait_is_verified(self):
        from core.advanced_tools import browser_wait
        from core.browser_semantic import require_clear_page
        self.page.set_content('<button>Ready</button>')
        with patch("core.browser_semantic.require_clear_page", wraps=require_clear_page) as guard, \
                patch("core.tools._PAGE", self.page):
            self.assertTrue(browser_wait("button", 0).startswith("VERIFIED:"))
        self.assertEqual(guard.call_count, 1)
        self.page.set_content('<button>Ready</button><button>Ready</button>')
        with patch("core.tools._PAGE", self.page):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                browser_wait("button", 0)

    def test_state_wait_returns_without_sleep_when_ready_and_recovers_loading(self):
        self.page.set_content('<button>Ready</button>')
        with patch("core.browser_semantic.time.sleep") as sleep:
            result = self.run_op("wait_state", target={"role": "button", "name": "Ready"}, timeout_ms=1000)
        self.assertEqual(result["polls"], 1)
        sleep.assert_not_called()
        self.page.evaluate("setTimeout(() => document.querySelector('button').textContent = 'Loaded', 40)")
        result = self.run_op("wait_state", target={"role": "button", "name": "Loaded"}, timeout_ms=1000)
        self.assertTrue(result["matched"])

    def test_wait_stops_promptly_without_action_when_cancelled(self):
        checks = 0
        def check():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise RuntimeError("Emergency stop is active")
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "Emergency"):
            run_browser_operation(self.page, "wait_state", {"target": {"selector": "#missing"}, "timeout_ms": 30000}, check)
        self.assertLess(time.monotonic() - start, 1)

    def test_snapshot_bound_and_iframe_context(self):
        self.page.set_content('<main>' + ''.join(f'<button>B{i}</button>' for i in range(40)) + '</main><iframe srcdoc="<body><button>Inside</button></body>"></iframe>')
        bounded = self.run_op("semantic_snapshot", max_nodes=5)
        self.assertEqual(len(bounded["nodes"]), 5)
        self.assertTrue(bounded["truncated"])
        frame = self.run_op("semantic_snapshot", frame_selector="iframe")
        self.assertTrue(any(n["name"] == "Inside" for n in frame["nodes"]))

    def test_playback_requires_advancing_content_and_preserves_selected_video(self):
        self.page.goto("http://127.0.0.1:1/watch?v=chosen")
        self.page.set_content('<div id="movie_player"><video></video></div>')
        self.page.evaluate("""() => {
            document.querySelector('#movie_player').getVideoData = () => ({video_id:'chosen'});
            const v = document.querySelector('video'); let paused=true, t=10;
            Object.defineProperties(v,{paused:{get:()=>paused},currentTime:{get:()=>t},readyState:{get:()=>4}});
            v.play = () => { window.plays=(window.plays||0)+1; paused=false; t+=1; return Promise.resolve(); };
            v.pause = () => { paused=true; };
        }""")
        played = self.run_op("youtube_playback", action="play", expected_video_id="chosen", timeout_ms=1000)
        self.assertTrue(played["verified"])
        self.assertEqual(self.page.evaluate("window.plays"), 1)
        paused = self.run_op("youtube_playback", action="pause", expected_video_id="chosen", timeout_ms=1000)
        self.assertTrue(paused["paused"])
        with self.assertRaisesRegex(RuntimeError, "changed"):
            self.run_op("youtube_playback", action="play", expected_video_id="different", timeout_ms=100)
        self.assertEqual(self.page.evaluate("window.plays"), 1)

    def test_post_action_challenge_blocks_further_progress(self):
        self.page.set_content('<button onclick="document.body.innerHTML=\'Human verification\'">Continue</button>')
        with self.assertRaises(BrowserChallengeBlocked):
            self.run_op("semantic_action", action="click", target={"role": "button", "name": "Continue"})

    def test_play_rejection_is_reported_without_waiting_or_retrying(self):
        self.page.goto("http://127.0.0.1:1/watch?v=chosen")
        self.page.set_content('<div id="movie_player"><video></video></div>')
        self.page.evaluate("""() => {
          document.querySelector('#movie_player').getVideoData = () => ({video_id:'chosen'});
          document.querySelector('video').play = () => { window.plays=(window.plays||0)+1; return Promise.reject(new Error('Autoplay denied')); };
        }""")
        start = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "Autoplay denied"):
            self.run_op("youtube_playback", action="play", expected_video_id="chosen", timeout_ms=5000)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(self.page.evaluate("window.plays"), 1)


if __name__ == "__main__":
    unittest.main()
