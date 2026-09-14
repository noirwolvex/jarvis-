from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class AppDiscoveryCacheTests(unittest.TestCase):
    def test_cache_reuses_friendly_name_and_bypasses_explicit_path(self) -> None:
        import core.app_discovery_cache as cache

        original = Mock(return_value=[{"name": "Demo", "score": 100.0}])
        cache._ORIGINAL = original
        cache.clear_app_discovery_cache()
        with patch.object(cache, "_ttl_seconds", return_value=90.0):
            first = cache._cached_discover("Demo")
            first[0]["name"] = "mutated"
            second = cache._cached_discover("Demo")
            cache._cached_discover(r"C:\\Apps\\Demo.exe")
            cache._cached_discover(r"C:\\Apps\\Demo.exe")

        self.assertEqual(second[0]["name"], "Demo")
        self.assertEqual(original.call_count, 3)


class VisionHelperTests(unittest.TestCase):
    def test_bounded_size_preserves_aspect_ratio(self) -> None:
        from core.vision_tools import _bounded_size

        self.assertEqual(_bounded_size(1920, 1080, 1280), (1280, 720))
        self.assertEqual(_bounded_size(800, 600, 1280), (800, 600))

    def test_vision_followup_keeps_image_bytes_out_of_tool_result(self) -> None:
        import core.vision_tools as vision

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            target = root / ".jarvis" / "vision" / "screen.jpg"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"fake-jpeg")
            result = "VERIFIED: " + json.dumps({"path": str(target), "mime": "image/jpeg"})
            with patch.object(vision, "_workspace", return_value=root):
                message = vision.vision_followup_message(result)

        self.assertIsNotNone(message)
        content = message["content"]
        self.assertTrue(content[0]["text"].startswith(vision.VISION_MARKER))
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("ZmFrZS1qcGVn", result)


class DesktopControlRegistrationTests(unittest.TestCase):
    def test_extended_mouse_keyboard_tools_are_registered_medium_risk(self) -> None:
        from core.desktop_control_tools import register_desktop_control_tools
        from core.permissions import Risk
        from core.tools import ToolRegistry

        registry = ToolRegistry()
        register_desktop_control_tools(registry)
        for name in (
            "desktop_click_button",
            "desktop_drag",
            "desktop_mouse_down",
            "desktop_mouse_up",
            "desktop_key_down",
            "desktop_key_up",
        ):
            self.assertIn(name, registry._tools)
            self.assertEqual(registry._tools[name].risk, Risk.MEDIUM)
        self.assertEqual(registry._tools["desktop_cursor"].risk, Risk.LOW)


class GoogleFastPathTests(unittest.TestCase):
    @patch("core.browser_fast_tools.browser_check_challenge")
    @patch("core.browser_fast_tools.chrome_current_tab")
    @patch("core.browser_fast_tools.chrome_page_operation")
    @patch("core.browser_fast_tools.chrome_is_connected")
    def test_google_search_navigates_and_verifies_in_one_tool(
        self,
        connected,
        page_operation,
        current_tab,
        challenge,
    ) -> None:
        from core.browser_fast_tools import google_search

        connected.return_value = True
        current_tab.return_value = json.dumps({
            "url": "https://www.google.com/search?q=cat",
            "title": "cat - Google Search",
        })
        challenge.return_value = json.dumps({"challenge_detected": False})

        result = google_search("cat")

        page_operation.assert_called_once_with("goto", url="https://www.google.com/search?q=cat")
        self.assertTrue(result.startswith("VERIFIED: "))

    @patch("core.browser_fast_tools.browser_check_challenge")
    @patch("core.browser_fast_tools.chrome_current_tab")
    @patch("core.browser_fast_tools.chrome_new_tab")
    @patch("core.browser_fast_tools.chrome_is_connected")
    def test_google_search_new_tab_stops_on_human_verification(
        self,
        connected,
        new_tab,
        current_tab,
        challenge,
    ) -> None:
        from core.browser_fast_tools import google_search

        connected.return_value = True
        current_tab.return_value = json.dumps({"url": "https://www.google.com/sorry/index", "title": "Sorry"})
        challenge.return_value = json.dumps({"challenge_detected": True, "evidence": ["text:captcha"]})

        result = google_search("car", new_tab=True)

        new_tab.assert_called_once()
        self.assertTrue(result.startswith("BROWSER_ACTION_BLOCKED: "))


class WorkerProtocolTests(unittest.TestCase):
    def test_ping_does_not_load_agent(self) -> None:
        import core.full_access_worker as worker

        worker._AGENT = None
        result = worker._handle(json.dumps({"protocol": 1, "id": "ping-1", "action": "ping"}))
        self.assertTrue(result["ok"])
        self.assertFalse(result["payload"]["agent_loaded"])

    def test_run_reuses_agent_object(self) -> None:
        import core.full_access_worker as worker

        sentinel = object()
        worker._AGENT = sentinel
        with patch("core.full_access_worker.run_agent_mission", return_value={"ok": True}) as run:
            first = worker._handle(json.dumps({"protocol": 1, "id": "1", "action": "run", "title": "one"}))
            second = worker._handle(json.dumps({"protocol": 1, "id": "2", "action": "run", "title": "two"}))
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(run.call_count, 2)
        self.assertIs(run.call_args_list[0].args[0], sentinel)
        self.assertIs(run.call_args_list[1].args[0], sentinel)


if __name__ == "__main__":
    unittest.main()
