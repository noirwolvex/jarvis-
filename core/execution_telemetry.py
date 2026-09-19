"""Per-dispatch engine evidence, isolated across nested and concurrent tool calls."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import time
from typing import Any


@dataclass
class ExecutionSpan:
    started: float = field(default_factory=time.perf_counter)
    operations: list[dict[str, str]] = field(default_factory=list)


_SPAN: ContextVar[ExecutionSpan | None] = ContextVar("jarvis_execution_span", default=None)


class ToolResult(str):
    def __new__(cls, value: str, execution: dict[str, Any]):
        result = super().__new__(cls, value)
        result.execution = execution
        return result

    def __getnewargs__(self):
        return str(self), self.execution


def record_backend(engine: str, phase: str = "execute", detail: str = "") -> None:
    """Call at the actual adapter boundary, never infer a route from the tool name."""
    span = _SPAN.get()
    if span is None:
        return
    row = {"engine": str(engine)[:64], "phase": str(phase)[:32], "detail": str(detail)[:160]}
    if row not in span.operations and len(span.operations) < 32:
        span.operations.append(row)


def begin_execution():
    span = ExecutionSpan()
    return span, _SPAN.set(span)


def finish_execution(span: ExecutionSpan, token, result: str, *, dispatched: bool) -> ToolResult:
    _SPAN.reset(token)
    # Prefer the mutation mechanism over supporting reads (e.g. Rust input + UIA readback).
    engines = list(dict.fromkeys(row["engine"] for row in span.operations if row["phase"] == "execute"))
    if not engines:
        engines = list(dict.fromkeys(row["engine"] for row in span.operations))
    backend = "+".join(engines) if engines else ("unreported" if dispatched else "not_dispatched")
    execution = {"backend": backend, "operations": [dict(row) for row in span.operations],
                 "duration_ms": round((time.perf_counter() - span.started) * 1000, 2)}
    parent = _SPAN.get()
    if parent is not None:
        for row in span.operations:
            record_backend(**row)
    return ToolResult(str(result), execution)
