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

    def test_version_only_change_refreshes_browser_node_action_targets(self) -> None:
        first = json.loads(self._browser_snapshot(version="epoch:1"))
        first["snapshot"]["nodes"][0]["role_resolvable"] = False
        second = json.loads(json.dumps(first))
        second["snapshot"]["version"] = "epoch:2"
        with patch.object(scene, "interaction_inspect", side_effect=[
            json.dumps(first), json.dumps(second),
        ]):
            scene.interaction_scene(registry=self.registry, surface="browser")
            changed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser"
            )[len("VERIFIED: "):])
        self.assertEqual(changed["delta"]["unchanged_count"], 0)
        self.assertEqual(changed["delta"]["changed"][0]["target"]["expected_version"], "epoch:2")

    def test_unique_labeled_scene_targets_survive_unrelated_version_change(self) -> None:
        with patch.object(scene, "interaction_inspect", side_effect=[
            self._browser_snapshot(version="epoch:1"), self._browser_snapshot(version="epoch:2"),
        ]):
            first = json.loads(scene.interaction_scene(registry=self.registry, surface="browser")[10:])
            changed = json.loads(scene.interaction_scene(registry=self.registry, surface="browser")[10:])
        self.assertEqual(first["nodes"][0]["target"], {
            "surface": "browser", "browser_target": {"role": "button", "name": "Send"}})
        self.assertEqual(changed["delta"]["unchanged_count"], 1)

    def test_browser_iframe_context_survives_scene_and_exact_resolution(self) -> None:
        with patch.object(scene, "interaction_inspect", return_value=self._browser_snapshot()):
            observed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser", frame_selector="#editor-frame"
            )[len("VERIFIED: "):])
            resolved = json.loads(scene.interaction_resolve(
                registry=self.registry, surface="browser", query="Send", role="button",
                frame_selector="#editor-frame",
            )[len("VERIFIED: "):])
        self.assertEqual(observed["nodes"][0]["target"]["frame_selector"], "#editor-frame")
        self.assertEqual(resolved["action_target"]["frame_selector"], "#editor-frame")

    def test_scene_cache_remains_bounded_across_long_navigation_missions(self) -> None:
        tracker = scene.SceneTracker()
        for index in range(200):
            tracker.update(f"page:{index}", [])
        self.assertEqual(len(tracker._scenes), 128)
        self.assertNotIn("page:0", tracker._scenes)
        self.assertIn("page:199", tracker._scenes)

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
        self.assertEqual(result["action_target"]["title"], "App")
        self.assertEqual(result["action_target"]["expected_hwnd"], 42)
        self.assertEqual(result["fallback"], "")

    def test_partial_desktop_name_returns_exact_live_target_not_partial_query(self) -> None:
        with patch.object(scene, "interaction_inspect", return_value=self._desktop_snapshot()):
            result = json.loads(scene.interaction_resolve(
                registry=self.registry, query="Sec", role="ListItem", ordinal=1, surface="desktop",
            )[len("VERIFIED: "):])
        self.assertEqual(result["action_target"], {
            "surface": "desktop", "target": "Second", "control_type": "ListItem",
            "title": "App", "expected_hwnd": 42,
        })

    def test_exact_ordinal_query_preserves_original_candidate_group(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        for node in snapshot["snapshot"]["controls"]:
            node["automation_id"] = "chat-row"
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            result = json.loads(scene.interaction_resolve(
                registry=self.registry, query="chat-row", role="ListItem", ordinal=2, surface="desktop",
            )[len("VERIFIED: "):])
        self.assertEqual(result["action_target"]["target"], "chat-row")
        self.assertEqual(result["action_target"]["selector"], {"ordinal": 2})

    def test_desktop_ordinal_does_not_claim_resolution_across_containers(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        snapshot["snapshot"]["controls"][1]["parent_ref"] = "another-list"
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            with self.assertRaisesRegex(RuntimeError, "uncertain containers"):
                scene.interaction_resolve(registry=self.registry, role="ListItem", ordinal=1, surface="desktop")

    def test_last_target_is_not_guessed_from_truncated_scene(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        snapshot["snapshot"]["truncated"] = True
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                scene.interaction_resolve(registry=self.registry, role="ListItem", ordinal="last", surface="desktop")

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

    def test_search_tree_and_specialized_menu_controls_remain_actionable(self) -> None:
        roles = ["searchbox", "spinbutton", "menuitemcheckbox", "menuitemradio", "treeitem", "listbox"]
        snapshot = json.loads(self._browser_snapshot())
        template = snapshot["snapshot"]["nodes"][0]
        snapshot["snapshot"]["nodes"] = [
            {**template, "node_id": f"n{index}", "role": role, "name": role}
            for index, role in enumerate(roles)
        ]
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            observed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser",
            )[len("VERIFIED: "):])
            resolved = json.loads(scene.interaction_resolve(
                registry=self.registry, surface="browser", role="searchbox", query="searchbox",
            )[len("VERIFIED: "):])
        self.assertEqual(observed["actionable_count"], len(roles))
        self.assertEqual(resolved["action_target"]["browser_target"], {
            "role": "searchbox", "name": "searchbox",
        })

    def test_custom_browser_control_retains_version_bound_target(self) -> None:
        wrapped = json.loads(self._browser_snapshot("Open menu"))
        wrapped["snapshot"]["nodes"][0].update(role="generic", interactive=True, role_resolvable=False)
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(wrapped)):
            observed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser", scope="actionable"
            )[len("VERIFIED: "):])
            resolved = json.loads(scene.interaction_resolve(
                registry=self.registry, surface="browser", query="Open menu"
            )[len("VERIFIED: "):])
        self.assertEqual(len(observed["nodes"]), 1)
        self.assertTrue(observed["nodes"][0]["actionable"])
        self.assertEqual(resolved["action_target"]["browser_target"], {"node_id": "n1"})
        self.assertEqual(resolved["action_target"]["expected_version"], "epoch:1")

    def test_state_disambiguated_browser_target_keeps_node_and_version(self) -> None:
        for state in ("focused", "selected"):
            with self.subTest(state=state):
                snapshot = json.loads(self._browser_snapshot())
                first = snapshot["snapshot"]["nodes"][0]
                first[state] = False
                snapshot["snapshot"]["nodes"].append({**first, "node_id": "n2", state: True})
                with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
                    resolved = json.loads(scene.interaction_resolve(
                        registry=self.registry, surface="browser", query="Send", role="button",
                        **{state: True},
                    )[len("VERIFIED: "):])
                self.assertEqual(resolved["action_target"], {
                    "surface": "browser", "browser_target": {"node_id": "n2"},
                    "expected_version": "epoch:1",
                })

    def test_unique_desktop_automation_id_is_not_replaced_by_duplicate_name(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        for index, node in enumerate(snapshot["snapshot"]["controls"]):
            node.update(name="Chat", automation_id=f"chat-{index + 1}")
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            resolved = json.loads(scene.interaction_resolve(
                registry=self.registry, surface="desktop", role="ListItem", query="chat-2",
            )[len("VERIFIED: "):])
        self.assertEqual(resolved["action_target"]["target"], "chat-2")
        self.assertEqual(resolved["action_target"]["expected_hwnd"], 42)

    def test_partial_automation_id_uses_exact_id_when_name_repeats(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        for index, node in enumerate(snapshot["snapshot"]["controls"]):
            node.update(name="Chat", automation_id=f"chat-{index + 1}-entry")
        for ordinal in (None, 1):
            with self.subTest(ordinal=ordinal):
                with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
                    resolved = json.loads(scene.interaction_resolve(
                        registry=self.registry, surface="desktop", role="ListItem", query="chat-2",
                        ordinal=ordinal,
                    )[len("VERIFIED: "):])
                self.assertEqual(resolved["action_target"]["target"], "chat-2-entry")
                self.assertNotIn("selector", resolved["action_target"])
                self.assertEqual(resolved["fallback"], "")

    def test_cross_surface_role_alias_preserves_native_control_type(self) -> None:
        for native_type, requested_role in (("Edit", "textbox"), ("Hyperlink", "link"),
                                             ("TabItem", "tab"), ("RadioButton", "radio"),
                                             ("Spinner", "spinbutton")):
            with self.subTest(role=requested_role):
                snapshot = json.loads(self._desktop_snapshot())
                for node in snapshot["snapshot"]["controls"]:
                    node["type"] = native_type
                with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
                    resolved = json.loads(scene.interaction_resolve(
                        registry=self.registry, surface="desktop", role=requested_role, ordinal=2,
                    )[len("VERIFIED: "):])
                self.assertEqual(resolved["node"]["name"], "Second")
                self.assertEqual(resolved["action_target"]["control_type"], native_type)
                self.assertEqual(resolved["action_target"]["selector"], {"ordinal": 2})

    def test_document_root_is_not_resolved_as_textbox(self) -> None:
        snapshot = json.loads(self._desktop_snapshot())
        snapshot["snapshot"]["controls"] = [{
            **snapshot["snapshot"]["controls"][0], "type": "Document", "name": "Page",
        }]
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            with self.assertRaisesRegex(RuntimeError, "No semantic target matched"):
                scene.interaction_resolve(registry=self.registry, surface="desktop", role="textbox")

    def test_first_ordinal_is_not_guessed_from_focused_truncated_snapshot(self) -> None:
        snapshot = json.loads(self._browser_snapshot("Last visible button"))
        snapshot["snapshot"]["truncated"] = True
        snapshot["snapshot"]["nodes"][0]["focused"] = True
        with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
            with self.assertRaisesRegex(RuntimeError, "ordinal target from a truncated"):
                scene.interaction_resolve(
                    registry=self.registry, surface="browser", role="button", ordinal=1,
                )
            # Targeting the explicitly focused control remains safe and available.
            resolved = json.loads(scene.interaction_resolve(
                registry=self.registry, surface="browser", role="button", focused=True,
            )[len("VERIFIED: "):])
        self.assertEqual(resolved["action_target"]["browser_target"], {"node_id": "n1"})

    def test_browser_ordinal_requires_valid_distinct_geometry(self) -> None:
        for bounds in (None, {"x": 10, "y": 20, "width": 0, "height": 30},
                       {"x": 10, "y": 20, "width": 80, "height": 30}):
            with self.subTest(bounds=bounds):
                snapshot = json.loads(self._browser_snapshot())
                first = snapshot["snapshot"]["nodes"][0]
                snapshot["snapshot"]["nodes"].append({
                    **first, "node_id": "n2", "name": "Other", "bounds": bounds,
                })
                with patch.object(scene, "interaction_inspect", return_value=json.dumps(snapshot)):
                    with self.assertRaisesRegex(RuntimeError, "geometry|overlap"):
                        scene.interaction_resolve(registry=self.registry, surface="browser", role="button", ordinal=1)
                    # An exact identity does not need an ordinal position.
                    resolved = json.loads(scene.interaction_resolve(
                        registry=self.registry, surface="browser", role="button", query="Send",
                    )[10:])
                    self.assertEqual(resolved["action_target"]["browser_target"]["name"], "Send")

    def test_embedded_content_coverage_and_source_changes_survive_normalization(self) -> None:
        first = json.loads(self._browser_snapshot())
        first["snapshot"]["coverage_gaps"] = ["uninspected_embedded_content"]
        first["snapshot"]["nodes"].append({
            "node_id": "frame", "role": "iframe", "tag": "iframe", "name": "Editor",
            "frame_url": "https://example.test/editor/one",
            "bounds": {"x": 100, "y": 100, "width": 400, "height": 300},
        })
        second = json.loads(json.dumps(first))
        second["snapshot"]["nodes"][1]["frame_url"] = "https://example.test/editor/two"
        with patch.object(scene, "interaction_inspect", side_effect=[json.dumps(first), json.dumps(second)]):
            observed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser", scope="structure",
            )[10:])
            changed = json.loads(scene.interaction_scene(
                registry=self.registry, surface="browser", scope="structure",
            )[10:])
        self.assertEqual(observed["coverage_gaps"], ["uninspected_embedded_content"])
        self.assertEqual(observed["nodes"][1]["tag"], "iframe")
        self.assertEqual(changed["delta"]["changed"][0]["frame_url"], "https://example.test/editor/two")


if __name__ == "__main__":
    unittest.main()
