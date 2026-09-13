from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

agent_stub = types.ModuleType("core.agent")

class _AgentEvent:
    def __init__(self, kind: str, message: str, tool: str | None = None) -> None:
        self.kind = kind
        self.message = message
        self.tool = tool

class _BaseAgent:
    def _system_prompt(self, user_text: str = "") -> str:
        return "base"

agent_stub.AgentEvent = _AgentEvent
agent_stub.JarvisAgent = _BaseAgent
agent_stub._tool_schemas = lambda tools: []
sys.modules["core.agent"] = agent_stub

from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator


class _Memory:
    def add(self, _kind: str, _content: str) -> None:
        return None


class _Workspace:
    def save_snapshot(self) -> None:
        return None


class _Message:
    content = None

    def __init__(self) -> None:
        function = SimpleNamespace(
            name="browser_type",
            arguments=json.dumps({"selector": "textarea", "text": "cat"}),
        )
        self.tool_calls = [SimpleNamespace(id="call-1", function=function)]

    def model_dump(self, exclude_none: bool = True):
        return {"role": "assistant", "tool_calls": [{"id": "call-1"}]}


class _Completions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=_Message())])


class FullAccessAgentTests(unittest.TestCase):
    def test_captcha_block_pauses_without_running_browser_action_or_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = FullAccessJarvisAgent()
            agent.orchestrator = TaskOrchestrator(trace_dir=tmp)
            agent.workspace_context = _Workspace()
            agent.memory = _Memory()
            agent.messages = []
            agent.max_turns = 4
            agent.tools = SimpleNamespace()
            agent.approval = lambda _name, _args: True
            completions = _Completions()
            agent.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
            executed: list[str] = []

            agent._system_prompt = lambda _text="": "test"
            agent._browser_action_guard = lambda name: (
                "BROWSER_ACTION_BLOCKED: A human-verification/anti-bot challenge is present."
                if name == "browser_type" else None
            )
            agent._execute_tool = lambda name, _arguments, approved=False: executed.append(name) or "unexpected"

            result = agent.run("search Google for cat")

            self.assertEqual(completions.calls, 1)
            self.assertEqual(executed, [])
            self.assertIn("Human verification is required", result)
            self.assertIsNotNone(agent.orchestrator.current)
            self.assertEqual(agent.orchestrator.current.status, "waiting_user")
            self.assertIn("left the page open", agent.orchestrator.current.final_result)


if __name__ == "__main__":
    unittest.main()
