"""Perception boundaries with in-memory tools; never capture or control a device."""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.agent import AgentEvent
from core.computer_perception import register_perception_tools
from core.desktop_observation import DesktopObservationGate
from core.fast_execution_agent import FastExecutionFullAccessAgent
from core.full_access_agent import FullAccessJarvisAgent
from core.full_access_completion import read_only_observation_verified
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.task_tools import register_task_tools
from core.tools import ToolRegistry, ToolSpec
from core.vision_tools import vision_followup_message


class PerceptionIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.agent = agent = object.__new__(FullAccessJarvisAgent)
        agent._stop = threading.Event()
        agent._device_action_active = threading.Event()
        agent._desktop_observation = DesktopObservationGate()
        agent._tool_executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(agent._tool_executor.shutdown, wait=True)
        agent.tools = ToolRegistry()
        agent.tools.permissions.set_access_mode("full")
        agent.orchestrator = TaskOrchestrator(str(self.root / "traces"))
        agent.orchestrator.begin("Observe the interface")
        agent._browser_action_guard = lambda *_: None
        self.metadata = {
            "observation_type": "computer", "visual_included": False,
            "scene_bound": False, "stable": False,
            "semantic": {"nodes": [{"id": "composer", "value": "hello"}]},
        }
        self.observation = Mock(return_value="VERIFIED: " + json.dumps(self.metadata))
        self.click = Mock(return_value="DELIVERED: fixture click")
        agent.tools.register(ToolSpec("computer_observe", "fixture observation", Risk.LOW,
                                      {"type": "object"}, self.observation))
        agent.tools.register(ToolSpec("desktop_click_button", "fixture click", Risk.MEDIUM,
                                      {"type": "object"}, self.click))
        register_task_tools(agent.tools, agent.orchestrator)

    def visual_frame(self):
        target = self.root / ".jarvis" / "vision" / "fixture.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"owned fixture image")
        return {**self.metadata, "path": str(target), "visual_included": True,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "stable": True, "foreground_hwnd": 42,
                "virtual_origin_x": 0, "virtual_origin_y": 0,
                "source_width": 100, "source_height": 100}

    def test_semantic_only_read_invalidates_old_coordinates_but_supports_verification(self):
        agent = self.agent
        agent._desktop_observation.observe(self.visual_frame())
        agent.orchestrator.record_tool("ui_type", {}, "DELIVERED: fixture typing", 1, 1, mutation=True)
        result = agent._execute_tool("computer_observe", {}, approved=True)
        agent.orchestrator.record_tool("computer_observe", {}, result, 1, 1)
        self.assertIsNone(agent._desktop_observation.frame)
        self.assertIsNone(vision_followup_message(result))
        verified = agent._execute_tool("task_verify", {"claim": "typed hello", "verified": True,
                                                        "evidence": "fresh composer value is hello"}, approved=True)
        self.assertTrue(json.loads(verified)["verified"])
        denied = agent._execute_tool("desktop_click_button", {"x": 10, "y": 20}, approved=True)
        self.assertIn("Fresh screen_observe required", denied)
        self.click.assert_not_called()

    def test_combined_visual_result_binds_coordinates_and_blocks_background_capture(self):
        frame = self.visual_frame()
        observed_busy = []
        def observe():
            observed_busy.append(self.agent._device_action_active.is_set())
            return "VERIFIED: " + json.dumps(frame)
        self.observation.side_effect = observe
        self.agent._execute_tool("computer_observe", {}, approved=True)
        self.assertEqual(observed_busy, [True])
        self.assertFalse(self.agent._device_action_active.is_set())
        with patch("core.desktop_observation.foreground_identity", return_value=42):
            result = self.agent._execute_tool("desktop_click_button", {"x": 10, "y": 20}, approved=True)
        self.assertTrue(result.startswith("DELIVERED:"), result)
        self.click.assert_called_once()
        self.assertIsNone(self.agent._desktop_observation.frame)

    def test_all_goal_profiles_offer_combined_observation(self):
        register_perception_tools(self.agent.tools)
        for goal in ("Inspect browser page", "Observe WhatsApp", "Inspect repository code"):
            with self.subTest(goal=goal):
                agent = object.__new__(FastExecutionFullAccessAgent)
                agent.tools = self.agent.tools
                names = {row["function"]["name"] for row in agent._tool_schemas_for_goal(goal)}
                self.assertIn("computer_observe", names)

    def test_read_only_completion_accepts_semantics_without_pretending_a_mutation_completed(self):
        self.agent.orchestrator.record_tool("computer_observe", {}, self.observation(), 1, 1)
        current = self.agent.orchestrator.current
        self.assertTrue(read_only_observation_verified("computer_observe the current interface", current))
        self.assertFalse(read_only_observation_verified("Observe the interface and click Send", current))

    def test_worker_preview_only_contains_a_valid_visual_observation(self):
        from core import full_access_worker as worker
        frame = self.visual_frame()
        for data, expected in ((self.metadata, 0), (frame, 1), ({**frame, "sha256": "wrong"}, 0)):
            with self.subTest(visual=data.get("sha256")):
                def mission(*args, **kwargs):
                    kwargs["emit"](AgentEvent("tool_result", "VERIFIED: " + json.dumps(data), "computer_observe"))
                    return {"ok": True}
                with patch.object(worker, "_AGENT", object()), \
                     patch.object(worker, "_STOPPED", threading.Event()), \
                     patch.object(worker, "run_agent_mission", side_effect=mission), \
                     patch.object(worker, "_write") as write, \
                     patch("core.vision_tools._workspace", return_value=self.root):
                    result = worker._handle(json.dumps({"protocol": 1, "id": "fixture", "action": "run", "title": "Observe fixture"}))
                self.assertTrue(result["ok"], result)
                previews = [call.args[0] for call in write.call_args_list if call.args[0]["type"] == "observation"]
                self.assertEqual(len(previews), expected)
                if previews:
                    self.assertNotIn("path", previews[0]["frame"])
                    self.assertTrue(previews[0]["preview"].startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
