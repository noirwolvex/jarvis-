"""Validated, serializable requests and evidence for the desktop engines."""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class CancellationToken:
    """Thread-safe cancellation owned by a caller, never supplied in model JSON."""
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


CHECK_TOOLS = {"interaction_wait", "ui_wait_state", "browser_wait_state"}


def json_copy(value: Any) -> Any:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ValueError("Action data exceeds 64 KiB")
    return json.loads(encoded)


@dataclass(frozen=True)
class Check:
    tool: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if self.tool not in CHECK_TOOLS or not isinstance(self.arguments, dict):
            raise ValueError("Verification must use an allowlisted read-only state check")
        object.__setattr__(self, "arguments", json_copy(self.arguments))


@dataclass(frozen=True)
class RetryPolicy:
    read_attempts: int = 2
    backoff_seconds: float = 0.05

    def __post_init__(self) -> None:
        if type(self.read_attempts) is not int or not 1 <= self.read_attempts <= 5:
            raise ValueError("Read attempts must be 1-5")
        if isinstance(self.backoff_seconds, bool) or not math.isfinite(self.backoff_seconds) or not 0 <= self.backoff_seconds <= 2:
            raise ValueError("Backoff must be finite and between 0 and 2 seconds")


@dataclass(frozen=True)
class Action:
    action_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    timeout: float = 15.0
    preconditions: tuple[Check, ...] = ()
    expected_outcome: Check | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.action_id):
            raise ValueError("Action ID must contain 1-80 letters, digits, underscores or hyphens")
        if not isinstance(self.action_type, str) or not self.action_type:
            raise ValueError("Action type is required")
        if not isinstance(self.parameters, dict) or not isinstance(self.target, dict):
            raise ValueError("Parameters and target must be objects")
        if self.parameters.keys() & self.target.keys():
            raise ValueError("Target fields cannot be overridden by parameters")
        if isinstance(self.timeout, bool) or not math.isfinite(self.timeout) or not 0.05 <= self.timeout <= 120:
            raise ValueError("Action timeout must be finite and between 0.05 and 120 seconds")
        if len(self.preconditions) > 8 or any(not isinstance(item, Check) for item in self.preconditions):
            raise ValueError("Preconditions must contain at most eight typed checks")
        if self.expected_outcome is not None and not isinstance(self.expected_outcome, Check):
            raise ValueError("Expected outcome must be a typed check")
        if not isinstance(self.retry_policy, RetryPolicy):
            raise ValueError("Retry policy must be typed")
        if (len(self.dependencies) > 32
                or any(not isinstance(dep, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", dep)
                       or dep == self.action_id for dep in self.dependencies)
                or len(set(self.dependencies)) != len(self.dependencies)):
            raise ValueError("Dependencies must be unique and bounded")
        object.__setattr__(self, "parameters", json_copy(self.parameters))
        object.__setattr__(self, "target", json_copy(self.target))

    @property
    def arguments(self) -> dict[str, Any]:
        return json_copy({**self.target, **self.parameters})


@dataclass
class Result:
    action_id: str
    action_type: str
    status: Status
    started_at: str
    completed_at: str = ""
    duration_ms: float = 0
    target: dict[str, Any] = field(default_factory=dict)
    observed_state: str = ""
    verification_result: bool = False
    retry_count: int = 0
    error_code: str = ""
    error_message: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
