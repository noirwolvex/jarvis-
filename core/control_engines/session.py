"""Typed desktop session with bounded observation recovery and no mutation replay."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextvars import copy_context
from datetime import datetime, timezone

from .engines import KeyboardEngine, MouseEngine, SmartUIEngine, VerificationEngine, VisionEngine, Provider
from .models import Action, CancellationToken, Check, Result, Status
from .scheduler import ActionCancelled, ActionTimedOut, action_context, check_context

log = logging.getLogger("jarvis.control")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failure(text: str) -> bool:
    return text.startswith(("ERROR", "PERMISSION_DENIED", "CANCELLED", "BROWSER_ACTION_BLOCKED"))


class ControlSession:
    """One action at a time. Timed-out providers retain the lane until they exit.

    Deadlines are cooperative inside native calls. A hung COM provider cannot be
    safely killed in a Python thread; no second action is accepted while it drains.
    """
    def __init__(self, provider: Provider, *, on_event=None):
        self.provider = provider
        self.engines = [MouseEngine(provider), KeyboardEngine(provider), SmartUIEngine(provider),
                        VisionEngine(provider), VerificationEngine(provider)]
        self.verifier = self.engines[-1]
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-control")
        self._guard = threading.Lock()
        self._pending = None
        self._active_call = False
        self._closed = False
        self._cancel = None
        self._records: dict[str, tuple[str, Result]] = {}
        self.on_event = on_event

    def _engine(self, action: Action):
        for engine in self.engines:
            if action.action_type in engine.operations:
                return engine
        raise ValueError(f"Unsupported typed desktop action: {action.action_type}")

    def validate(self, action: Action) -> None:
        self._engine(action).call(action)
        for check in (*action.preconditions, *((action.expected_outcome,) if action.expected_outcome else ())):
            self.provider.validate(check.tool, check.arguments)

    @staticmethod
    def _fingerprint(action: Action) -> str:
        return json.dumps(action, default=lambda obj: obj.__dict__, sort_keys=True, allow_nan=False)

    def _event(self, result: Result) -> None:
        # Deliberately exclude text, targets, screenshots and tool arguments from logs.
        log.info("action=%s operation=%s status=%s duration_ms=%.2f retries=%d error=%s",
                 result.action_id, result.action_type, result.status.value, result.duration_ms,
                 result.retry_count, result.error_code)
        if self.on_event:
            try:
                self.on_event(copy.deepcopy(result.as_dict()))
            except Exception:
                log.warning("Control event sink failed", exc_info=False)

    def execute(self, action: Action, cancellation: CancellationToken | None = None) -> Result:
        """Validate and execute an immutable action ID; existing IDs never replay input."""
        action = copy.deepcopy(action)
        self.validate(action)
        fingerprint = self._fingerprint(action)
        token = cancellation or CancellationToken()
        began = time.monotonic()
        result = Result(action.action_id, action.action_type, Status.PENDING, _now(), target=action.target)
        with self._guard:
            if self._closed:
                raise RuntimeError("Control session is closed")
            if self._active_call or self._pending is not None and not self._pending.done():
                raise RuntimeError("Control session is busy; prior action has not released the input lane")
            if action.action_id in self._records:
                previous, outcome = self._records[action.action_id]
                if previous != fingerprint:
                    raise ValueError("Action ID is bound to different input; no action dispatched")
                return copy.deepcopy(outcome)
            if len(self._records) >= 500:
                raise ValueError("Session action budget exceeded; start a new mission session")
            for dependency in action.dependencies:
                if dependency not in self._records or self._records[dependency][1].status != Status.SUCCEEDED:
                    raise ValueError(f"Dependency is not verified: {dependency}")
            self._cancel = token
            self._active_call = True
            self._pending = self._pool.submit(copy_context().run, self._run, action, token,
                                              began + action.timeout, copy.deepcopy(result))
            future = self._pending
        self._event(result)
        try:
            while True:
                try:
                    check_context()
                except (ActionCancelled, ActionTimedOut) as exc:
                    token.cancel()
                    result.status = Status.CANCELLED if isinstance(exc, ActionCancelled) else Status.TIMED_OUT
                    result.error_code, result.error_message = result.status.value, str(exc)
                    break
                remaining = began + action.timeout - time.monotonic()
                if token.cancelled or remaining <= 0:
                    token.cancel()
                    result.status = Status.CANCELLED if remaining > 0 else Status.TIMED_OUT
                    result.error_code = result.status.value
                    result.error_message = "Action interrupted; outcome may be uncertain. Inspect before another action."
                    break
                try:
                    result = future.result(timeout=min(0.025, remaining))
                    break
                except FutureTimeout:
                    continue
        finally:
            result.completed_at = _now()
            result.duration_ms = round((time.monotonic() - began) * 1000, 2)
            with self._guard:
                self._records[action.action_id] = (fingerprint, copy.deepcopy(result))
                self._active_call = False
            self._event(result)
        return result

    def _read(self, condition: Check, action: Action, result: Result) -> str:
        for attempt in range(action.retry_policy.read_attempts):
            check_context()
            text = self.verifier.check(condition)
            self._evidence(result, condition.tool, text, "verify")
            check_context()
            if text.startswith("VERIFIED:"):
                return text
            if text.startswith(("PERMISSION_DENIED", "CANCELLED", "BROWSER_ACTION_BLOCKED")):
                return text
            if attempt + 1 < action.retry_policy.read_attempts:
                result.retry_count += 1
                until = time.monotonic() + action.retry_policy.backoff_seconds * (attempt + 1)
                while time.monotonic() < until:
                    check_context()
                    time.sleep(min(0.01, max(0, until - time.monotonic())))
        return text

    @staticmethod
    def _evidence(result: Result, tool: str, text: str, phase: str) -> None:
        result.evidence.append({"tool": tool, "phase": phase, "result": str(text)[:4000],
                                "execution": getattr(text, "execution", {})})
        result.observed_state = str(text)[:4000]

    def _run(self, action: Action, token: CancellationToken, deadline: float, result: Result) -> Result:
        try:
            with action_context(deadline, lambda: token.cancelled):
                result.status = Status.RUNNING
                self._event(result)
                for condition in action.preconditions:
                    text = self._read(condition, action, result)
                    if not text.startswith("VERIFIED:"):
                        result.status = Status.FAILED
                        result.error_code = "PRECONDITION_FAILED"
                        result.error_message = "Precondition was not verified; no mutation dispatched"
                        return result
                engine = self._engine(action)
                text = engine.execute(action)
                self._evidence(result, engine.operations[action.action_type], text, "execute")
                check_context()
                if text.startswith("PERMISSION_DENIED"):
                    result.status = Status.NEEDS_CONFIRMATION
                    result.error_code = "POLICY_DENIED"
                elif _failure(text):
                    result.status = Status.CANCELLED if text.startswith("CANCELLED") else Status.FAILED
                    result.error_code = "BACKEND_FAILED"
                elif action.expected_outcome:
                    text = self._read(action.expected_outcome, action, result)
                    result.verification_result = text.startswith("VERIFIED:")
                    result.status = Status.SUCCEEDED if result.verification_result else Status.VERIFICATION_FAILED
                    if not result.verification_result:
                        result.error_code = "POSTCONDITION_FAILED"
                elif action.action_type == "verify" and not text.startswith("VERIFIED:"):
                    result.status = Status.VERIFICATION_FAILED
                    result.error_code = "POSTCONDITION_FAILED"
                elif not str(text).strip():
                    result.status = Status.FAILED
                    result.error_code = "EMPTY_BACKEND_RESULT"
                elif text.startswith("VERIFIED:") or action.action_type in engine.reads:
                    result.status = Status.SUCCEEDED
                    result.verification_result = True
                else:
                    result.status = Status.DELIVERED
                    result.error_code = "OUTCOME_UNVERIFIED"
                if result.error_code:
                    result.error_message = str(text)[:500]
                if (action.expected_outcome or action.action_type == "verify") and result.status in {
                        Status.SUCCEEDED, Status.VERIFICATION_FAILED}:
                    record = getattr(self.provider, "record_verification", None)
                    if record:
                        record(action.action_id, text, verified=result.verification_result)
        except (ActionCancelled, ActionTimedOut) as exc:
            result.status = Status.CANCELLED if isinstance(exc, ActionCancelled) else Status.TIMED_OUT
            result.error_code, result.error_message = result.status.value, str(exc)
        except Exception as exc:
            result.status = Status.FAILED
            result.error_code, result.error_message = type(exc).__name__, str(exc)[:500]
        finally:
            try:
                self.provider.cleanup()
            except Exception as exc:
                result.status = Status.FAILED
                result.error_code, result.error_message = "CLEANUP_FAILED", str(exc)[:500]
        return result

    def run(self, actions: list[Action], cancellation: CancellationToken | None = None) -> dict:
        """Preflight the entire ordered task. Stop without silently skipping work."""
        actions = copy.deepcopy(actions)
        if not 1 <= len(actions) <= 100 or len({a.action_id for a in actions}) != len(actions):
            raise ValueError("Task requires 1-100 unique actions")
        with self._guard:
            records = copy.deepcopy(self._records)
        seen = set(records)
        for action in actions:
            self.validate(action)
            if action.action_id in records and records[action.action_id][0] != self._fingerprint(action):
                raise ValueError("Action ID is bound to different input; no task actions dispatched")
            if any(dep not in seen for dep in action.dependencies):
                raise ValueError("Dependencies must precede the action")
            seen.add(action.action_id)
        results = []
        for action in actions:
            result = self.execute(action, cancellation)
            results.append(result.as_dict())
            if result.status != Status.SUCCEEDED:
                break
        return {"completed": len(results) == len(actions) and all(r["status"] == "SUCCEEDED" for r in results),
                "actions": results, "remaining": [a.action_id for a in actions[len(results):]]}

    def close(self) -> None:
        with self._guard:
            self._closed = True
            if self._cancel:
                self._cancel.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
