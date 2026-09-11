from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class PlanStep:
    id: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    result: str = ""


@dataclass
class ToolTrace:
    name: str
    arguments: dict[str, Any]
    result: str
    success: bool
    duration_ms: float
    turn: int


@dataclass
class VerificationRecord:
    claim: str
    verified: bool
    evidence: str = ""
    created_at: float = field(default_factory=time.time)


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
    plan: list[PlanStep] = field(default_factory=list)
    verifications: list[VerificationRecord] = field(default_factory=list)
    final_result: str = ""
    finished_at: float | None = None

    @property
    def elapsed_ms(self) -> float:
        end = self.finished_at or time.time()
        return max(0.0, (end - self.started_at) * 1000.0)


class TaskOrchestrator:
    """Execution state, planning, bounded parallel reads, recovery and verification."""

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

    def set_plan(self, steps: list[PlanStep | dict[str, Any] | str]) -> list[PlanStep]:
        if not self.current:
            return []
        plan: list[PlanStep] = []
        for index, raw in enumerate(steps, start=1):
            if isinstance(raw, PlanStep):
                plan.append(raw)
            elif isinstance(raw, str):
                plan.append(PlanStep(id=f"step-{index}", description=raw))
            else:
                plan.append(PlanStep(
                    id=str(raw.get("id") or f"step-{index}"),
                    description=str(raw.get("description") or raw.get("goal") or "Unnamed step"),
                    depends_on=list(raw.get("depends_on") or []),
                ))
        self.current.plan = plan
        return plan

    def update_step(self, step_id: str, status: str, result: str = "") -> None:
        if not self.current:
            return
        for step in self.current.plan:
            if step.id == step_id:
                step.status = status
                step.result = result[:12000]
                break

    def ready_steps(self) -> list[PlanStep]:
        if not self.current:
            return []
        completed = {step.id for step in self.current.plan if step.status == "completed"}
        return [
            step for step in self.current.plan
            if step.status == "pending" and all(dep in completed for dep in step.depends_on)
        ]

    def run_independent_reads(
        self,
        jobs: list[Callable[[], Any]],
        max_workers: int = 4,
    ) -> list[Any]:
        """Run independent read-only jobs concurrently; callers must guarantee read-only semantics."""
        if not jobs:
            return []
        results: list[Any] = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(jobs))), thread_name_prefix="jarvis-read") as pool:
            futures = {pool.submit(job): index for index, job in enumerate(jobs)}
            for future in as_completed(futures):
                index = futures[future]
                results[index] = future.result()
        return results

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

    def verify(self, claim: str, verified: bool, evidence: str = "") -> bool:
        if not self.current:
            return False
        self.current.verifications.append(
            VerificationRecord(claim=claim, verified=verified, evidence=evidence[:12000])
        )
        return verified

    def all_required_verifications_passed(self) -> bool:
        if not self.current:
            return False
        return bool(self.current.verifications) and all(item.verified for item in self.current.verifications)

    def recovery_hint(self, result: str, tool_name: str) -> str:
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
            "plan_steps": len(self.current.plan),
            "plan_completed": sum(step.status == "completed" for step in self.current.plan),
            "verifications": len(self.current.verifications),
            "verified": self.all_required_verifications_passed() if self.current.verifications else False,
            "elapsed_ms": round(self.current.elapsed_ms, 2),
        }

    def _persist(self, task: TaskRun) -> None:
        path = self.trace_dir / f"{task.task_id}.json"
        path.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")


def build_execution_context(task: TaskRun | None) -> str:
    if not task:
        return ""
    plan = "; ".join(f"{step.id}:{step.status}" for step in task.plan) or "none"
    verification = "; ".join(
        f"{item.claim}={'yes' if item.verified else 'no'}" for item in task.verifications[-8:]
    ) or "none"
    return (
        "\n\nExecution state: "
        f"task_id={task.task_id}; turn={task.current_turn}; "
        f"tools_used={task.tools_used}; failures={task.failures}; recoveries={task.recoveries}; "
        f"plan={plan}; verifications={verification}."
    )
