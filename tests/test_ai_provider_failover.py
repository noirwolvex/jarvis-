from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.agent import JarvisAgent
from core.tools import ToolRegistry


class _DeniedError(RuntimeError):
    status_code = 403


class AIProviderFailoverTests(unittest.TestCase):
    def _agent(self, openai_factory):
        memory = Mock()
        memory.context.return_value = ""
        with patch("core.agent.OpenAI", side_effect=openai_factory):
            return JarvisAgent(tools=ToolRegistry(), memory=memory)

    @patch.dict(
        os.environ,
        {
            "TABITOKEN_API_KEY": "primary-secret",
            "AI_PROVIDER": "primary",
            "AI_BASE_URL": "https://primary.example/v1",
            "AI_MODEL": "primary-model",
            "AI_FALLBACK_PROVIDER": "backup",
            "AI_FALLBACK_API_KEY": "fallback-secret",
            "AI_FALLBACK_BASE_URL": "https://backup.example/v1",
            "AI_FALLBACK_MODEL": "backup-model",
        },
        clear=False,
    )
    def test_403_switches_once_to_fallback_and_continues(self) -> None:
        primary = Mock()
        fallback = Mock()
        primary.chat.completions.create.side_effect = _DeniedError(
            "403 PERMISSION_DENIED: Your project has been denied access."
        )
        response = SimpleNamespace(choices=[])
        fallback.chat.completions.create.return_value = response

        agent = self._agent([primary, fallback])
        try:
            result = agent._chat_completion(messages=[], tools=[], tool_choice="auto")
            self.assertIs(result, response)
            self.assertEqual(agent.provider, "backup")
            self.assertEqual(agent.model, "backup-model")
            self.assertIn("switched", agent.pop_provider_failover_notice())
            self.assertEqual(agent.pop_provider_failover_notice(), "")

            agent._chat_completion(messages=[], tools=[], tool_choice="auto")
            self.assertEqual(primary.chat.completions.create.call_count, 1)
            self.assertEqual(fallback.chat.completions.create.call_count, 2)
        finally:
            agent.close()

    @patch.dict(
        os.environ,
        {
            "TABITOKEN_API_KEY": "primary-secret",
            "AI_PROVIDER": "primary",
            "AI_BASE_URL": "https://primary.example/v1",
            "AI_MODEL": "primary-model",
            "AI_FALLBACK_PROVIDER": "",
            "AI_FALLBACK_API_KEY": "",
            "AI_FALLBACK_BASE_URL": "",
            "AI_FALLBACK_MODEL": "",
        },
        clear=False,
    )
    def test_403_without_fallback_explains_provider_without_exposing_key(self) -> None:
        primary = Mock()
        primary.chat.completions.create.side_effect = _DeniedError(
            "403 PERMISSION_DENIED: Your project has been denied access."
        )

        agent = self._agent([primary])
        try:
            with self.assertRaisesRegex(RuntimeError, "AI_PROVIDER_ACCESS_DENIED") as caught:
                agent._chat_completion(messages=[], tools=[], tool_choice="auto")
            message = str(caught.exception)
            self.assertIn("provider=primary", message)
            self.assertIn("primary-model", message)
            self.assertNotIn("primary-secret", message)
        finally:
            agent.close()

    @patch.dict(
        os.environ,
        {
            "TABITOKEN_API_KEY": "primary-secret",
            "AI_PROVIDER": "primary",
            "AI_BASE_URL": "https://primary.example/v1",
            "AI_MODEL": "primary-model",
            "AI_FALLBACK_PROVIDER": "backup",
            "AI_FALLBACK_API_KEY": "fallback-secret",
            "AI_FALLBACK_BASE_URL": "",
            "AI_FALLBACK_MODEL": "backup-model",
        },
        clear=False,
    )
    def test_partial_fallback_configuration_fails_before_mission(self) -> None:
        primary = Mock()
        with patch("core.agent.OpenAI", return_value=primary):
            with self.assertRaisesRegex(RuntimeError, "Fallback AI configuration is incomplete"):
                JarvisAgent(tools=ToolRegistry(), memory=Mock())


if __name__ == "__main__":
    unittest.main()
