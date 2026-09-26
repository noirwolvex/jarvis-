from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core import interaction_scene as scene
from core.tools import ToolRegistry


class InteractionSceneTests(unittest.TestCase):
    def setUp(self) -> None:
        scene._SCENES.reset()
        self.registry = ToolRegistry()

    def _browser_snapshot(self, name: str = "Send", *, version: str = "epoch:1") -> str:
        return json.dumps({
            "surface": "browser",
            "snapshot": {
                "version": version,
                "url": "https://example.test/",
                "title": "Example",
                "cached": False,
                "truncated": False,
                "nodes": [
                    {
                        "node_id": "n1",
                        "parent": None,
                        "role": "button",
                        "name": name,
                        "disabled": False,
                        "focused": False,
                        "selected": None,
                        "bounds": {"x": 10, "y": 20, "width": 80, "height": 30},
                    }
                ],
            },
        })

    def _desktop_snapshot(self) -> str:
        return json.dumps({
            "surface": "desktop",
            "snapshot": {
                "generation": 7,
                "hwnd": 42,
                "title": "App",
                "cached": False,
                "truncated": False,
                "is_foreground": True,
                "controls": [
                    {
                        "name": "First",
                        "type": "ListItem",
                        "automation_id": "",
                        "runtime_id": [1, 10],
                        "rect": [10, 100, 200, 130],
                        "enabled": True,
                        "visible": True,
                        "selected": False,
                        "focused": False,
                        "parent_ref": "container-0",
                        "ancestors": ["container-0", "window"],
                    },
                    {
                        "name": "Second",
                        "type": "ListItem",
                        "automation_id": "",
                        "runtime_id": [1, 20],
                        "rect": [10, 140, 200, 170],
                        "enabled": True,
                        "visible": True,
                        "selected": False,
                        "focused": False,
                        "parent_ref": "container-0",
                        "ancestors": ["container-0", "window"],
                    },
                ],
            },
        })

    def test_first_scene_is_full_then_unchanged_scene_is_small_delta(self) -> None:
        with patch.object(scene, "interaction_inspect", side_effect=[
            self._browser_snapshot(),
            self._browser_snapshot(),
        ]):
            first = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser"
            )[len("VERIFIED: "):])
            second = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser"
            )[len("VERIFIED: "):])

        self.assertEqual(first["mode"], "full")
        self.assertEqual(len(first["nodes"]), 1)
        self.assertEqual(second["mode"], "delta")
        self.assertEqual(second["delta"]["added"], [])
        self.assertEqual(second["delta"]["changed"], [])
        self.assertEqual(second["delta"]["removed"], [])
        self.assertEqual(second["delta"]["unchanged_count"], 1)

    def test_scene_delta_reports_changed_browser_target_without_full_tree(self) -> None:
        with patch.object(scene, "interaction_inspect", side_effect=[
            self._browser_snapshot("Send", version="epoch:1"),
            self._browser_snapshot("Submit", version="epoch:2"),
        ]):
            scene.interaction_scene(registry=self.registry, surface="browser")
            changed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser", mode="delta"
            )[len("VERIFIED: "):])

        self.assertEqual(changed["mode"], "delta")
        self.assertEqual([row["name"] for row in changed["delta"]["changed"]], ["Submit"])
        self.assertNotIn("nodes", changed)

    def test_exact_browser_resolution_prefers_stable_role_name_action_target(self) -> None:
        with patch.object(scene, "interaction_inspect", return_value=self._browser_snapshot()):
            result = json.loads(scene.interaction_resolve(
                registry=self.registry,
                query="Send",
                role="button",
                surface="browser",
            )[len("VERIFIED: "):])

        self.assertEqual(result["match"], "exact")
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["action_target"], {
            "surface": "browser",
            "browser_target": {"role": "button", "name": "Send"},
        })

    def test_desktop_ordinal_resolution_returns_live_reresolving_selector(self) -> None:
        with patch.object(scene, "interaction_inspect", return_value=self._desktop_snapshot()):
            result = json.loads(scene.interaction_resolve(
                registry=self.registry,
                role="ListItem",
                ordinal=2,
                surface="desktop",
            )[len("VERIFIED: "):])

        self.assertEqual(result["node"]["name"], "Second")
        self.assertEqual(result["action_target"]["surface"], "desktop")
        self.assertEqual(result["action_target"]["control_type"], "ListItem")
        self.assertEqual(result["action_target"]["selector"], {"ordinal": 2})

    def test_ambiguous_semantic_target_fails_closed(self) -> None:
        duplicate = json.loads(self._browser_snapshot())
        duplicate["snapshot"]["nodes"].append({
            **duplicate["snapshot"]["nodes"][0],
            "node_id": "n2",
            "bounds": {"x": 10, "y": 60, "width": 80, "height": 30},
        })
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(duplicate)):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                scene.interaction_resolve(
                    registry=self.registry,
                    query="Send",
                    role="button",
                    surface="browser",
                )


if __name__ == "__main__":
    unittest.main()
