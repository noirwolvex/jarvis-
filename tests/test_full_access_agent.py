from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.full_access_agent import FullAccessJarvisAgent
from core.orchestrator import TaskOrchestrator
from core.permissions import Risk
from core.tools import ToolRegistry, ToolSpec


class _Memory:
    def add(self, _kind: str, _content: str) -> None:
        return None

    def context(self, _text: str, recent_limit: int = 8, search_limit: int = 6) -> str:
        return ""


class _Workspace:
    def save_snapshot(self) -> None:
        return None

    def context_text(self) -> str:
        return ""


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
    def test_captcha_blocks_browser_action_and_pauses_without_recovery(self) -> None:
        browser_type_called = {"value": False}
        registry = ToolRegistry()
        registry.register(ToolSpec(
            "browser_check_challenge",
            "test challenge guard",
            Risk.SAFE,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda: json.dumps({
                "challenge_detected": True,
                "action": "STOP_AND_REQUEST_USER",
                "url": "https://www.google.com/sorry/",
                "title": "Google",
            }),
        ))

        def browser_type(selector: str, text: str) -> str:
            browser_type_called["value"] = True
            return f"typed {text} into {selector}"

        registry.register(ToolSpec(
            "browser_type",
            "test browser type",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {"selector": {"type": "string"}, "text": {"type": "string"}},
                "required": ["selector", "text"],
            },
            browser_type,
        ))

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"AI_API_KEY": "test-key", "TABITOKEN_API_KEY": ""},
            clear=False,
        ):
            agent = FullAccessJarvisAgent(tools=registry, approval=lambda _name, _args: True, memory=_Memory())
            agent.workspace_context = _Workspace()
            agent.orchestrator = TaskOrchestrator(trace_dir=tmp)
            completions = _Completions()
            agent.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

            result = agent.run("search Google for cat")

            self.assertFalse(browser_type_called["value"])
            self.assertEqual(completions.calls, 1)
            self.assertIn("Human verification is required", result)
            self.assertIsNotNone(agent.orchestrator.current)
            self.assertEqual(agent.orchestrator.current.status, "waiting_user")
            self.assertIn("left the page open", agent.orchestrator.current.final_result)


if __name__ == "__main__":
    unittest.main()
