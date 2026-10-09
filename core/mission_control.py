"""Operator controls at safe execution boundaries, independent of model latency."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import Callable


def _review_arguments(value):
    from .memory import redact_secrets
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.search(r"password|passwd|secret|token|api.?key|authorization|cookie", key, re.I)
                else _review_arguments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_review_arguments(item) for item in value]
    return redact_secrets(value) if isinstance(value, str) else value


class MissionControl:
    def __init__(self, emit: Callable[[dict], None], *, confirmation_timeout: float = 300):
        self._condition = threading.Condition()
        self._publish_lock = threading.RLock()
        self._emit = emit
        self._pause_requested = False
        self._paused = False
        self._cancelled = False
        self._pending: dict | None = None
        self._decision: bool | None = None
        self._timeout = confirmation_timeout
        self._history: list[dict] = []

    def history(self) -> list[dict]:
        with self._condition:
            return [dict(item) for item in self._history]

    def _record(self, action: str) -> None:
        self._history.append({"action": action, "time": time.time(),
                              **({key: self._pending[key] for key in ("id", "tool", "fingerprint")}
                                 if self._pending else {})})

    @property
    def cancelled(self) -> bool:
        with self._condition:
            return self._cancelled

    def snapshot(self) -> dict:
        with self._condition:
            return {"paused": self._paused, "pauseRequested": self._pause_requested,
                    **({"pendingConfirmation": dict(self._pending)} if self._pending else {})}

    def _publish(self) -> None:
        # Two threads may acknowledge an operator decision and consume it at
        # once. Serialize snapshots with delivery so an old prompt cannot arrive
        # after the cleared state. Reentrant test/status sinks remain supported.
        with self._publish_lock:
            self._emit(self.snapshot())

    def command(self, action: str, confirmation_id: str = "") -> None:
        with self._condition:
            if self._cancelled:
                raise ValueError("Mission is already cancelled")
            if action == "pause":
                self._pause_requested = True
            elif action == "resume":
                self._pause_requested = False
                self._paused = False
            elif action == "cancel":
                self._cancelled = True
            elif action in {"confirm", "reject"}:
                if not self._pending or confirmation_id != self._pending["id"] or self._decision is not None:
                    raise ValueError("Confirmation is expired, already answered, or belongs to another action")
                self._decision = action == "confirm"
                if action == "reject":
                    self._cancelled = True
            else:
                raise ValueError("Unsupported mission control command")
            self._record(action)
            self._condition.notify_all()
        self._publish()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._record("emergency_stop")
            self._condition.notify_all()

    def boundary(self, stopped: Callable[[], bool]) -> bool:
        """Pause between actions, never while a drag/shortcut holds synthetic input."""
        waited = False
        while True:
            with self._condition:
                if self._cancelled or stopped():
                    raise RuntimeError("Emergency stop is active")
                if not self._pause_requested:
                    return waited
                changed = not self._paused
                self._paused = True
                if changed:
                    self._record("paused")
                waited = True
            if changed:
                from .desktop_control_tools import release_held_inputs
                release_held_inputs()
                self._publish()
            with self._condition:
                self._condition.wait(timeout=0.05)

    def confirm(self, tool: str, arguments: dict, requirement, mission_id: str,
                stopped: Callable[[], bool]) -> None:
        from .action_confirmation import action_fingerprint
        # The pending request binds this invocation, not a reusable permission for
        # the tool. The model has no tool capable of answering it.
        fingerprint = action_fingerprint(tool, arguments, mission_id=mission_id)
        # Show the complete reviewed arguments locally. Never silently truncate a
        # command/content and ask the operator to approve its unseen remainder.
        details = json.dumps(_review_arguments(arguments), ensure_ascii=False, allow_nan=False, indent=2)
        if len(details.encode("utf-16-le")) // 2 > 16000:
            raise ValueError("Action is too large to review; split it into smaller actions")
        with self._condition:
            if self._cancelled or stopped():
                raise RuntimeError("Mission is cancelled; no input dispatched")
            if self._pending:
                raise RuntimeError("A confirmation is already pending")
            self._pending = {"id": uuid.uuid4().hex, "tool": tool,
                             "summary": requirement.summary, "category": requirement.category,
                             "details": details, "reason": requirement.reason,
                             "fingerprint": fingerprint}
            self._decision = None
            self._record("confirmation_requested")
        deadline = time.monotonic() + self._timeout
        try:
            self._publish()
            while True:
                with self._condition:
                    if self._cancelled or stopped():
                        raise RuntimeError("Emergency stop is active")
                    if time.monotonic() >= deadline:
                        self._cancelled = True
                        self._record("confirmation_expired")
                        raise RuntimeError("Action confirmation expired; no input dispatched")
                    if self._decision is True:
                        if action_fingerprint(tool, arguments, mission_id=mission_id) != fingerprint:
                            self._cancelled = True
                            self._record("arguments_changed")
                            raise RuntimeError("Confirmed action arguments changed; no input dispatched")
                        self._record("approval_consumed")
                        return
                    self._condition.wait(timeout=0.05)
        finally:
            with self._condition:
                self._pending = None
                self._decision = None
            self._publish()
