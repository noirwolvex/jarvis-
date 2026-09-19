from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from core.browser_semantic import BrowserChallengeBlocked, run_browser_operation
from core.chrome_cdp import _ChromeRuntime, chrome_is_connected


class BrowserRuntimeTests(unittest.TestCase):
    def test_legacy_click_rejects_ambiguous_targets_without_dispatch(self):
        runtime = _ChromeRuntime.__new__(_ChromeRuntime)
        runtime._active_cancel = None
        runtime._page = MagicMock()
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
        with self.assertRaises(BrowserChallengeBlocked):
            run_browser_operation(page, "semantic_action", {"action": "click", "target": {"selector": "button"}}, lambda: None)
        page.locator.assert_not_called()

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
        locator.count.return_value = 1
        locator.is_visible.return_value = locator.is_enabled.return_value = True
        locator.click.side_effect = TimeoutError("delivery uncertain")
        with self.assertRaisesRegex(TimeoutError, "uncertain"):
            run_browser_operation(page, "semantic_action", {"action": "click", "target": {"selector": "button"}}, lambda: None)
        locator.click.assert_called_once()


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

    def test_changed_dom_invalidates_observed_node_and_never_clicks(self):
        self.page.set_content('<button onclick="window.clicks=(window.clicks||0)+1">Send</button>')
        first = self.run_op("semantic_snapshot")
        node = next(n for n in first["nodes"] if n["role"] == "button")
        self.page.locator("button").evaluate("el => el.textContent = 'Delete'")
        with self.assertRaisesRegex(Exception, "STALE_UI"):
            self.run_op("semantic_action", action="click", target={"node_id": node["node_id"]}, expected_version=first["version"])
        self.assertIsNone(self.page.evaluate("window.clicks"))

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
