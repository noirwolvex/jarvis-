"""Exercise the real SDK against an owned loopback provider, without paid API calls."""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai import BadRequestError, Timeout

from core.agent import JarvisAgent
from core.tools import ToolRegistry


class ProviderLatencyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.request_bodies = []
        self.primary_status = 429
        self.primary_statuses = []
        self.fallback_status = 200
        self.retry_after = '40'
        self.slow_primary = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, body['model']))
                owner.request_bodies.append(body)
                primary = self.path.startswith('/primary/')
                if primary and owner.slow_primary:
                    threading.Event().wait(0.2)
                status = owner.primary_status if primary else owner.fallback_status
                if primary and owner.primary_statuses:
                    status = owner.primary_statuses.pop(0)
                payload = ({'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                            'model': body['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                            'message': {'role': 'assistant', 'content': 'fixture response'}}]}
                           if status == 200 else {'error': {'message': 'fixture provider unavailable',
                                                          'type': 'fixture_error', 'code': str(status)}})
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    if owner.retry_after is not None:
                        self.send_header('Retry-After', owner.retry_after)
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Expected when the client enforces its read timeout.

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)
        base = f'http://127.0.0.1:{self.server.server_port}'
        self.enterContext(patch.dict(os.environ, {
            'TABITOKEN_API_KEY': 'fixture-key', 'AI_PROVIDER': 'fixture',
            'AI_BASE_URL': base + '/primary', 'AI_MODEL': 'primary-model',
            'AI_FALLBACK_API_KEY': 'fixture-backup-key', 'AI_FALLBACK_PROVIDER': 'fixture-backup',
            'AI_FALLBACK_BASE_URL': base + '/backup', 'AI_FALLBACK_MODEL': 'backup-model',
            'JARVIS_AI_TIMEOUT_SECONDS': '45',
            'AI_REASONING_EFFORT': '', 'AI_FALLBACK_REASONING_EFFORT': '',
        }))
        # A regression must fail immediately, rather than actually sleeping 40 seconds.
        self.enterContext(patch('openai._base_client.time.sleep', side_effect=AssertionError('Hidden SDK retry')))

    def agent(self, *, fallback=True):
        if not fallback:
            self.enterContext(patch.dict(os.environ, {
                'AI_FALLBACK_API_KEY': '', 'AI_FALLBACK_BASE_URL': '', 'AI_FALLBACK_MODEL': ''}))
        agent = JarvisAgent(tools=ToolRegistry(), memory=Mock())
        self.addCleanup(agent.close)
        self.addCleanup(agent.client.close)
        if agent._fallback_client is not None:
            self.addCleanup(agent._fallback_client.close)
        return agent

    def test_quota_retry_after_does_not_delay_or_repeat_before_fallback(self):
        agent = self.agent()
        self.assertEqual(agent.client.max_retries, 0)
        self.assertEqual(agent.client.timeout.read, 45)
        self.assertEqual(agent.client.timeout.connect, 5)
        started = time.monotonic()
        result = agent._chat_completion(messages=[{'role': 'user', 'content': 'fixture'}])
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result.choices[0].message.content, 'fixture response')
        self.assertEqual(self.requests, [('/primary/chat/completions', 'primary-model'),
                                        ('/backup/chat/completions', 'backup-model')])
        self.assertEqual(agent.model, 'backup-model')

    def test_timeout_uses_fallback_once_without_replaying_primary(self):
        agent = self.agent()
        self.slow_primary = True
        agent.client.timeout = Timeout(0.05)
        result = agent._chat_completion(messages=[])
        self.assertEqual(result.choices[0].message.content, 'fixture response')
        self.assertEqual(len(self.requests), 2)
        self.assertIn('timeout', agent.pop_provider_failover_notice())

    def test_timeout_without_fallback_has_checkpoint_error(self):
        agent = self.agent(fallback=False)
        self.slow_primary = True
        agent.client.timeout = Timeout(0.05)
        with self.assertRaisesRegex(RuntimeError, 'AI_PROVIDER_UNAVAILABLE.*checkpoint'):
            agent._chat_completion(messages=[])
        self.assertEqual(len(self.requests), 1)

    def test_server_error_uses_fallback_but_schema_error_does_not(self):
        for status in (503, 400):
            with self.subTest(status=status):
                self.requests.clear()
                self.primary_status = status
                agent = self.agent()
                if status == 400:
                    with self.assertRaises(BadRequestError):
                        agent._chat_completion(messages=[])
                    self.assertEqual(len(self.requests), 1)
                else:
                    agent._chat_completion(messages=[])
                    self.assertEqual(len(self.requests), 2)

    def test_both_providers_unavailable_stop_after_one_request_each(self):
        agent = self.agent()
        self.primary_status = self.fallback_status = 503
        with self.assertRaisesRegex(RuntimeError, 'AI_PROVIDER_FAILOVER_FAILED'):
            agent._chat_completion(messages=[])
        self.assertEqual(len(self.requests), 2)

    def test_cancelled_request_does_not_start_fallback_or_change_provider(self):
        for requests in (1, 2):
            with self.subTest(requests=requests):
                self.requests.clear()
                agent = self.agent()
                with self.assertRaisesRegex(RuntimeError, 'CANCELLED'):
                    agent._chat_completion(messages=[], _cancel_check=lambda: len(self.requests) >= requests)
                self.assertEqual(len(self.requests), requests)
                self.assertEqual(agent.model, 'primary-model')
                self.assertIsNotNone(agent._fallback_client)

    def test_invalid_timeout_rejected_before_client_creation(self):
        for value in ('nan', 'inf', '0', '181', 'bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'JARVIS_AI_TIMEOUT_SECONDS': value}), \
                    patch('core.agent.OpenAI') as client:
                with self.assertRaisesRegex(ValueError, 'JARVIS_AI_TIMEOUT_SECONDS'):
                    self.agent()
                client.assert_not_called()

    def test_transient_rejection_without_fallback_recovers_once(self):
        self.primary_statuses = [503, 200]
        self.retry_after = '0'
        agent = self.agent(fallback=False)
        agent.orchestrator = SimpleNamespace(current=SimpleNamespace(metrics={}))
        response = agent._chat_completion(messages=[{'role': 'user', 'content': 'fixture'}])
        self.assertEqual(response.choices[0].message.content, 'fixture response')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.request_bodies[0], self.request_bodies[1])
        self.assertIn('one transient-error retry', agent.pop_provider_failover_notice())
        self.assertEqual(agent.orchestrator.current.metrics['provider_retries'], 1)

    def test_persistent_transient_error_stops_after_second_request(self):
        self.primary_status = 503
        self.retry_after = '0'
        agent = self.agent(fallback=False)
        with self.assertRaisesRegex(RuntimeError, 'AI_PROVIDER_UNAVAILABLE.*checkpoint'):
            agent._chat_completion(messages=[])
        self.assertEqual(len(self.requests), 2)

    def test_no_retry_for_quota_invalid_request_or_unbounded_retry_after(self):
        for status, delay in ((429, '0'), (400, '0'), (503, '40'), (503, 'NaN'),
                              (503, '-1'), (503, 'Thu, 08 Oct 2026 18:00:00 GMT')):
            with self.subTest(status=status, delay=delay):
                self.requests.clear()
                self.primary_status, self.retry_after = status, delay
                agent = self.agent(fallback=False)
                with self.assertRaises((RuntimeError, BadRequestError)):
                    agent._chat_completion(messages=[])
                self.assertEqual(len(self.requests), 1)

    def test_cancel_during_backoff_prevents_retry(self):
        self.primary_status = 503
        self.retry_after = None
        agent = self.agent(fallback=False)
        agent.orchestrator = SimpleNamespace(current=SimpleNamespace(metrics={}))
        cancelled = threading.Event()
        with patch('core.agent.time.sleep', side_effect=lambda _: cancelled.set()) as sleep:
            with self.assertRaisesRegex(RuntimeError, 'CANCELLED'):
                agent._chat_completion(messages=[], _cancel_check=cancelled.is_set)
        sleep.assert_called_once()
        self.assertLessEqual(sleep.call_args.args[0], 0.05)
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn('provider_retries', agent.orchestrator.current.metrics)

    def test_effort_is_optional_and_bound_to_the_selected_provider(self):
        agent = self.agent()
        self.primary_status = 200
        agent._chat_completion(messages=[])
        self.assertNotIn('reasoning_effort', self.request_bodies[-1])
        agent.reasoning_effort = 'low'
        agent._chat_completion(messages=[])
        self.assertEqual(self.request_bodies[-1]['reasoning_effort'], 'low')
        self.primary_status = 503
        agent._chat_completion(messages=[])
        self.assertEqual(self.request_bodies[-2]['reasoning_effort'], 'low')
        self.assertNotIn('reasoning_effort', self.request_bodies[-1])
        self.assertEqual(agent.reasoning_effort, '')

    def test_fallback_effort_is_sent_and_retained_after_failover(self):
        with patch.dict(os.environ, {'AI_REASONING_EFFORT': 'low', 'AI_FALLBACK_REASONING_EFFORT': 'high'}):
            agent = self.agent()
        agent._chat_completion(messages=[])
        self.assertEqual(self.request_bodies[0]['reasoning_effort'], 'low')
        self.assertEqual(self.request_bodies[1]['reasoning_effort'], 'high')
        agent._chat_completion(messages=[])
        self.assertEqual(self.request_bodies[-1]['reasoning_effort'], 'high')

    def test_invalid_effort_rejected_before_client_creation(self):
        for key in ('AI_REASONING_EFFORT', 'AI_FALLBACK_REASONING_EFFORT'):
            with self.subTest(key=key), patch.dict(os.environ, {key: 'fastest'}), patch('core.agent.OpenAI') as client:
                with self.assertRaisesRegex(ValueError, key):
                    self.agent()
                client.assert_not_called()
