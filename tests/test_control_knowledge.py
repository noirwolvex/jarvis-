from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.control_knowledge import (
    _catalog, ControlCatalogUnavailable, control_context, guide_reference, register_control_knowledge, select_guides,
)
from core.fast_execution_agent import FastExecutionFullAccessAgent
from core.full_access_agent import FullAccessJarvisAgent
from core.skills import load_skill, register_skill_tools
from core.tools import ToolRegistry


def control_registry():
    # Registration only: no app, browser, model client, or native input is opened.
    import importlib
    registry = ToolRegistry()
    for module, function in (
        ("app_tools", "register_app_tools"), ("advanced_tools", "register_advanced_tools"),
        ("browser_semantic", "register_browser_semantic_tools"),
        ("browser_fast_tools", "register_browser_fast_tools"),
        ("browser_tab_tools", "register_browser_tab_tools"),
        ("chrome_cdp", "register_chrome_cdp_tools"),
        ("chrome_session_tools", "register_chrome_session_tools"),
        ("desktop_control_tools", "register_desktop_control_tools"),
        ("native_ui_input", "register_native_ui_input_tools"),
        ("semantic_ui_tools", "register_semantic_ui_tools"),
        ("universal_interaction", "register_universal_interaction_tools"),
        ("interaction_scene", "register_interaction_scene_tools"),
        ("computer_perception", "register_perception_tools"),
        ("vision_tools", "register_vision_tools"),
        ("wincom_tools", "register_wincom_tools"),
        ("filesystem_tools", "register_filesystem_tools"),
        ("discord_tools", "register_discord_tools"),
        ("whatsapp_native", "register_whatsapp_native_tools"),
        ("youtube_fast_tools", "register_youtube_fast_tools"),
    ):
        getattr(importlib.import_module("core." + module), function)(registry)
    from core.task_tools import register_task_tools
    from core.workflow_tools import register_workflow_tools
    register_task_tools(registry, Mock())
    register_workflow_tools(SimpleNamespace(tools=registry))
    register_skill_tools(registry)
    register_control_knowledge(registry)
    return registry


class ControlKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = control_registry()

    def test_catalog_tools_and_source_references_exist(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(len(_catalog()), 17)
        for row in _catalog():
            with self.subTest(guide=row["id"]):
                self.assertTrue(set(row["required_tools"]) <= self.registry._tools.keys(),
                                set(row["required_tools"]) - self.registry._tools.keys())
                for reference in row["references"]:
                    path = (root / reference).resolve()
                    self.assertTrue(path.is_relative_to(root) and path.is_file(), reference)

    def test_retrieval_returns_detached_data_and_live_argument_schemas(self):
        guide = _catalog()[0]
        result = json.loads(guide_reference(self.registry, guide["id"], include_schemas=True))
        self.assertEqual(result["kind"], "control_reference")
        self.assertEqual(result["guides"][0]["id"], guide["id"])
        for name in guide["required_tools"]:
            self.assertEqual(result["tool_arguments"][name], self.registry._tools[name].input_schema)
        selected = select_guides(guide["id"], self.registry._tools)
        selected[0]["procedure"].clear()
        self.assertTrue(select_guides(guide["id"], self.registry._tools)[0]["procedure"])

    def test_unavailable_backends_and_surface_filter_out_inapplicable_guides(self):
        for row in _catalog():
            with self.subTest(guide=row["id"]):
                tools = set(row["required_tools"])
                tools.remove(row["required_tools"][0])
                self.assertNotIn(row["id"], {item["id"] for item in select_guides(row["id"], tools)})
        desktop = select_guides("type click scroll app window", self.registry._tools, surface="desktop", limit=6)
        self.assertTrue(desktop)
        self.assertNotIn("browser", {row["surface"] for row in desktop})

    def test_matching_is_bounded_multilingual_and_does_not_echo_query(self):
        for query in ("type text in browser", "اكتب نص في المتصفح"):
            with self.subTest(query=query):
                self.assertTrue(select_guides(query, self.registry._tools))
        query = "type click scroll browser window app retry workflow draft form " + "PRIVATE_UNTRUSTED_INSTRUCTION"
        result = control_context(query, self.registry._tools)
        self.assertLessEqual(len(result), 5200)
        self.assertNotIn("PRIVATE_UNTRUSTED_INSTRUCTION", result)
        self.assertIn("not a live observation", result)
        self.assertLessEqual(result.count("\n["), 3)
        self.assertEqual(control_context("unrelated-topic-xyz", self.registry._tools), "")

    def test_empty_query_lists_topics_and_invalid_arguments_never_execute_tools(self):
        result = json.loads(self.registry.execute("control_guide", {}))
        self.assertTrue(result["catalog"])
        self.assertEqual(result["guides"], [])
        for args in ({"limit": 0}, {"limit": True}, {"surface": "unknown"}, {"query": "x" * 8001}, {"execute": True}):
            with self.subTest(arguments=list(args)):
                self.assertTrue(self.registry.execute("control_guide", args).startswith("ERROR"))

    def test_context_selects_the_requested_surface_and_specialized_app(self):
        for query, expected, excluded in (
            ("type text in browser", "browser_text", "desktop_text"),
            ("اكتب نص في المتصفح", "browser_text", "desktop_text"),
            ("open Notepad", "app_launch_window", "browser_tabs"),
            ("type hello in WhatsApp", "whatsapp_conversations", "browser_text"),
            ("select the first chat in Discord", "discord_conversations", "browser_text"),
            ("search YouTube and play a song", "youtube_media", "desktop_text"),
        ):
            with self.subTest(query=query):
                context = control_context(query, self.registry._tools)
                self.assertIn("[" + expected + "]", context)
                self.assertNotIn("[" + excluded + "]", context)
        mixed = control_context("open WhatsApp then search Google", self.registry._tools)
        self.assertIn("[whatsapp_conversations]", mixed)
        self.assertIn("[browser_search]", mixed)

    def test_manual_guides_and_skills_follow_the_current_agent_tool_profile(self):
        registry = control_registry()
        agent = FastExecutionFullAccessAgent.__new__(FastExecutionFullAccessAgent)
        agent.tools = registry
        current = SimpleNamespace(goal="use Google to inspect this website")
        agent.orchestrator = SimpleNamespace(current=current)
        register_control_knowledge(registry, agent._control_reference_tools)
        register_skill_tools(registry, agent._control_reference_tools)
        available = agent._control_reference_tools()
        self.assertNotIn("wincom_inspect", available)
        self.assertNotIn("run_powershell", available)
        for name, args in (
            ("control_guide", {}),
            ("control_guide", {"query": "office_native_api", "include_schemas": True}),
            ("control_guide", {"query": "workspace_files_terminal", "include_schemas": True}),
            ("load_skill", {"name": "control-mechanisms"}),
        ):
            with self.subTest(name=name, args=args):
                result = registry.execute(name, args, approved=True)
                self.assertNotIn("office_native_api", result)
                self.assertNotIn("workspace_files_terminal", result)
        # Retrieval follows the new goal, rather than retaining a stale profile.
        current.goal = "write a code file with powershell"
        result = json.loads(registry.execute("control_guide", {
            "query": "workspace_files_terminal", "include_schemas": True,
        }))
        self.assertEqual(result["guides"][0]["id"], "workspace_files_terminal")
        self.assertIn("run_powershell", result["tool_arguments"])

    def test_builtin_control_skills_expand_but_other_skills_keep_original_shape(self):
        for name in ("desktop-operator", "browser-operator", "control-mechanisms"):
            data = json.loads(load_skill(name))
            self.assertTrue(data["control_reference"]["guides"])
            self.assertEqual(data["control_reference"]["kind"], "control_reference")
        self.assertNotIn("control_reference", json.loads(load_skill("researcher")))

    def test_prompt_uses_only_exposed_tools_and_adds_guidance_without_tool_calls(self):
        agent = FastExecutionFullAccessAgent.__new__(FastExecutionFullAccessAgent)
        agent.tools = self.registry
        agent.orchestrator = SimpleNamespace(current=SimpleNamespace(traces=[]))
        with patch.object(FullAccessJarvisAgent, "_system_prompt", return_value="BASE"), \
                patch.object(agent, "_execute_tool") as execute:
            prompt = agent._system_prompt("type text in browser")
        self.assertIn("Relevant local control procedures", prompt)
        self.assertLess(len(prompt), 7600)
        execute.assert_not_called()
        for goal in ("open WhatsApp", "search Google for cats", "edit code file"):
            names = {item["function"]["name"] for item in agent._tool_schemas_for_goal(goal)}
            self.assertTrue({"control_guide", "load_skill", "list_skills"} <= names)

    def test_recent_failure_routes_reference_without_copying_error_payload(self):
        row = _catalog()[0]
        selected = select_guides("unrelated-topic-xyz", self.registry._tools, recent_tools=row["required_tools"])
        self.assertIn(row["id"], {guide["id"] for guide in selected})

    def test_packaged_catalog_is_loaded_once_without_workspace_or_network_reads(self):
        _catalog.cache_clear()
        self.addCleanup(_catalog.cache_clear)
        original = Path.read_bytes
        paths = []
        def read(path):
            paths.append(path)
            return original(path)
        with patch.object(Path, "read_bytes", read):
            for _ in range(3):
                control_context("type", self.registry._tools)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].name, "control_guides.json")

    def test_invalid_or_missing_optional_catalog_cannot_crash_prompt_or_inject_content(self):
        valid = json.loads(Path(__file__).resolve().parents[1].joinpath(
            'core/resources/control_guides.json').read_text(encoding='utf-8'))
        invalid_id = json.loads(json.dumps(valid))
        invalid_id['guides'][0]['id'] = 'PRIVATE_UNTRUSTED_INVALID_ID'
        duplicate = json.loads(json.dumps(valid))
        duplicate['guides'].append(duplicate['guides'][0])
        for value in (b'{PRIVATE_UNTRUSTED_CORRUPT_JSON', b'[]', json.dumps(invalid_id).encode(),
                      json.dumps(duplicate).encode(), b'x' * 100001,
                      FileNotFoundError('PRIVATE_UNTRUSTED_PATH')):
            with self.subTest(case=type(value).__name__):
                _catalog.cache_clear()
                self.addCleanup(_catalog.cache_clear)
                with patch.object(Path, 'read_bytes', **(
                    {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
                )):
                    with self.assertRaises(ControlCatalogUnavailable):
                        _catalog()
                    context = control_context('open Explorer and press Downloads', self.registry._tools)
                    self.assertIn('procedures are unavailable', context)
                    self.assertIn('result verification', context)
                    self.assertNotIn('PRIVATE_UNTRUSTED', context)
                    reference = json.loads(guide_reference(self.registry, 'desktop_text'))
                    self.assertEqual(reference['status'], 'unavailable')
                    self.assertEqual(reference['guides'], [])
                    skill = json.loads(load_skill('desktop-operator'))
                    self.assertEqual(skill['control_reference']['status'], 'unavailable')

    def test_reference_outage_retains_core_prompt_and_reports_once_per_mission(self):
        agent = FastExecutionFullAccessAgent.__new__(FastExecutionFullAccessAgent)
        agent.tools = self.registry
        current = SimpleNamespace(traces=[], metrics={})
        agent.orchestrator = SimpleNamespace(current=current)
        agent._active_emit = Mock()
        with patch('core.control_knowledge._catalog', side_effect=ControlCatalogUnavailable('fixture')), \
                patch.object(FullAccessJarvisAgent, '_system_prompt', return_value='CORE_PERMISSION_RULES'), \
                patch.object(agent, '_execute_tool') as execute:
            for goal in ('OPEN FILE EXPLORE AND PRESS THE DOWNLOADS',
                         'OPEN NEW TAB AND SEARCH FOR A CAT', 'SEARCH FOR A CAT IN GOOGLE'):
                prompt = agent._system_prompt(goal)
                self.assertIn('CORE_PERMISSION_RULES', prompt)
                self.assertIn('Local control procedures are unavailable', prompt)
                self.assertIn('never blindly replay', prompt)
        execute.assert_not_called()
        agent._active_emit.assert_called_once()
        self.assertEqual(current.metrics['control_guidance_unavailable'], 1)


if __name__ == "__main__":
    unittest.main()
