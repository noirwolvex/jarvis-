"""A provider's explicit short per-minute cooldown is different from exhausted billing."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.agent import JarvisAgent


def quota_error(delay="0s", quota="InputTokensPerModelPerMinute-FreeTier", value="250000"):
    error = RuntimeError("provider rate limit")
    error.status_code = 429
    error.body = [{"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": quota, "quotaValue": value}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
    ]}}]
    return error


class MinuteQuotaRecoveryTests(TestCase):
    def agent(self, responses):
        agent = object.__new__(JarvisAgent)
        agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=responses))))
        agent._fallback_client = None
        agent.orchestrator = SimpleNamespace(current=SimpleNamespace(metrics={}))
        agent._active_emit = Mock()
        return agent

    def test_provider_metadata_bounds_retry_and_rejects_daily_or_missing_quota(self):
        self.assertEqual(JarvisAgent._minute_quota_retry_delay(quota_error("30s")), 31)
        for error in (quota_error("31s"), quota_error("nans"), quota_error("-1s"),
                      quota_error(quota="RequestsPerDay"), quota_error(value="0"), quota_error(value="-2"),
                      quota_error(value="nan"), quota_error(value="unknown"), RuntimeError("429 Retry in 1s")):
            with self.subTest(error=error):
                self.assertIsNone(JarvisAgent._minute_quota_retry_delay(error))

    def test_long_minute_reset_is_exposed_without_exceeding_request_deadline(self):
        agent = self.agent([quota_error("59s"), object()])
        agent.model, agent.provider, agent.base_url = "fixture", "fixture", "fixture"
        with self.assertRaisesRegex(RuntimeError, "AI_PROVIDER_RATE_LIMITED"), patch("core.agent.time.sleep") as sleep:
            agent._chat_completion(messages=[])
        sleep.assert_not_called()
        self.assertEqual(agent._provider_pause_retry_seconds, 60)
        agent._chat_completion(messages=[])
        self.assertIsNone(agent._provider_pause_retry_seconds)

    def test_short_cooldown_retries_same_planning_request_once(self):
        response = object()
        agent = self.agent([quota_error(), response])
        kwargs = {"messages": [{"role": "user", "content": "fixture mission"}]}
        with patch("core.agent.time.monotonic", side_effect=[0, 1]):
            self.assertIs(agent._primary_completion(kwargs, lambda: None), response)
        calls = agent.client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(agent.orchestrator.current.metrics["provider_cooldown_ms"], 1000)
        self.assertEqual(agent.orchestrator.current.metrics["provider_retries"], 1)
        agent._active_emit.assert_called_once()

    def test_second_quota_error_is_not_retried(self):
        agent = self.agent([quota_error(), quota_error(), object()])
        with patch("core.agent.time.monotonic", side_effect=[0, 1]), self.assertRaises(RuntimeError):
            agent._primary_completion({}, lambda: None)
        self.assertEqual(agent.client.chat.completions.create.call_count, 2)

    def test_cancellation_during_cooldown_prevents_another_request(self):
        agent = self.agent([quota_error(), object()])
        cancelled = False
        def cancel(_):
            nonlocal cancelled
            cancelled = True
        def check():
            if cancelled:
                raise RuntimeError("CANCELLED")
        with patch("core.agent.time.monotonic", return_value=0), patch("core.agent.time.sleep", side_effect=cancel), \
                self.assertRaisesRegex(RuntimeError, "CANCELLED"):
            agent._primary_completion({}, check)
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)

    def test_configured_fallback_takes_priority_over_waiting(self):
        agent = self.agent([quota_error()])
        agent._fallback_client = object()
        with self.assertRaises(RuntimeError), patch("core.agent.time.sleep") as sleep:
            agent._primary_completion({}, lambda: None)
        sleep.assert_not_called()
        self.assertEqual(agent.client.chat.completions.create.call_count, 1)
