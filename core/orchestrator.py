from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolTrace:
    name: str
    arguments: dict[str, Any]
    result: str
    success: bool
    duration_ms: float
    turn: int


@dataclass
class TaskRun:
    task_id: str
    goal: str
    started_at: float
    status: str = "running"
    current_turn: int = 0
    tools_used: int = 0
    failures: int = 0
    recoveries: int = 0
    traces: list[ToolTrace] = field(default_factory=list)
    final_result: str = ""
    finished_at: float | None = None

    @property
    def elapsed_ms(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, (end - self.started_at) * 1000.0)


class TaskOrchestrator:
    """Small, dependency-free execution layer for reliable agent runs.

    It does not replace the model. It adds durable task state, failure-aware
    recovery hints, and machine-readable traces around the existing tool loop.
    """

    def __init__(self, trace_dir: str | None = None) -> None:
        workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
        self.trace_dir = Path(trace_dir or (workspace / ".jarvis" / "traces")).resolve()
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.current: TaskRun | None = None

    def begin(self, goal: str) -> TaskRun:
        self.current = TaskRun(
            task_id=f"task-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            goal=goal,
            started_at=time.time(),
        )
        return self.current

    def start_turn(self, turn: int) -> None:
        if self.current:
            self.current.current_turn = turn

    def record_tool(self, name: str, arguments: dict[str, Any], result: str, duration_ms: float, turn: int) -> None:
        if not self.current:
            return
        success = not result.startswith("ERROR") and not result.startswith("PERMISSION_DENIED")
        self.current.traces.append(
            ToolTrace(
                name=name,
                arguments=arguments,
                result=result[:12000],
                success=success,
                duration_ms=round(duration_ms, 2),
                turn=turn,
            )
        )
        self.current.tools_used += 1
        if not success:
            self.current.failures += 1

    def recovery_hint(self, result: str, tool_name: str) -> str:
        """Return a compact recovery instruction after an unsuccessful action."""
        if not self.current or not (result.startswith("ERROR") or result.startswith("PERMISSION_DENIED")):
            return ""
        self.current.recoveries += 1
        low = result.lower()
        hints: list[str] = []
        if "foreground" in low or "focus" in low:
            hints.append("Re-check the active window and focus it before retrying.")
        if "dialog" in low or "save" in low or "open" in low:
            hints.append("Inspect the foreground dialog/window before repeating the action.")
        if "timeout" in low:
            hints.append("Use a shorter inspection path or wait for UI state before retrying.")
        if "permission" in low:
            hints.append("Do not bypass the permission system; request approval or choose a permitted path.")
        if not hints:
            hints.append(f"Diagnose the failure from the result of {tool_name} and use a safer alternate tool path.")
        return "Recovery guidance: " + " ".join(hints)

    def finish(self, status: str, result: str) -> None:
        if not self.current:
            return
        self.current.status = status
        self.current.final_result = result[:20000]
        self.current.finished_at = time.time()
        self._persist(self.current)

    def summary(self) -> dict[str, Any]:
        if not self.current:
            return {"status": "idle"}
        return {
            "task_id": self.current.task_id,
            "goal": self.current.goal,
            "status": self.current.status,
            "turn": self.current.current_turn,
            "tools_used": self.current.tools_used,
            "failures": self.current.failures,
            "recoveries": self.current.recoveries,
            "elapsed_ms": round(self.current.elapsed_ms, 2),
        }

    def _persist(self, task: TaskRun) -> None:
        path = self.trace_dir / f"{task.task_id}.json"
        path.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")


def build_execution_context(task: TaskRun | None) -> str:
    if not task:
        return ""
    return (
        "\n\nExecution state: "
        f"task_id={task.task_id}; turn={task.current_turn}; "
        f"tools_used={task.tools_used}; failures={task.failures}; recoveries={task.recoveries}."
    )
