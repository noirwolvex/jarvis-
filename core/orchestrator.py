from __future__ import annotations

import json
import os
import time
import uuid
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .memory import redact_secrets


def tool_succeeded(result: str) -> bool:
    text = str(result).strip()
    if text.startswith(("ERROR", "PERMISSION_DENIED", "BROWSER_ACTION_BLOCKED", "CANCELLED")):
        return False
    match = re.search(r"(?:^|\n)exit_code=(-?\d+)", text)
    return match is None or int(match.group(1)) == 0


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
    evidence_trace_index: int = -1


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
    in_flight: dict[str, Any] | None = None
    last_mutation_index: int = -1

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
        self._persist(self.current)
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
        ids = [step.id for step in plan]
        if not plan or len(plan) > 100 or len(set(ids)) != len(ids) or any(not item for item in ids):
            raise ValueError("Plan must contain 1-100 uniquely identified steps")
        dependencies = {step.id: set(step.depends_on) for step in plan}
        if any(not deps.issubset(ids) for deps in dependencies.values()):
            raise ValueError("Plan refers to an unknown dependency")
        resolved: set[str] = set()
        while len(resolved) < len(plan):
            ready = {key for key, deps in dependencies.items() if key not in resolved and deps <= resolved}
            if not ready:
                raise ValueError("Plan contains a dependency cycle")
            resolved.update(ready)
        self.current.plan = plan
        self._persist(self.current)
        return plan

    def update_step(self, step_id: str, status: str, result: str = "") -> None:
        if not self.current:
            return
        for step in self.current.plan:
            if step.id == step_id:
                if status not in {"pending", "running", "completed", "failed", "skipped"}:
                    raise ValueError("Invalid step status")
                completed = {s.id for s in self.current.plan if s.status in {"completed", "skipped"}}
                if status in {"running", "completed"} and not set(step.depends_on) <= completed:
                    raise ValueError("Step dependencies have not completed")
                step.status = status
                step.result = result[:12000]
                self._persist(self.current)
                return
        raise ValueError(f"Unknown plan step: {step_id}")

    def ready_steps(self) -> list[PlanStep]:
        if not self.current:
            return []
        completed = {step.id for step in self.current.plan if step.status in {"completed", "skipped"}}
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

    def record_tool(self, name: str, arguments: dict[str, Any], result: str, duration_ms: float, turn: int, mutation: bool = False) -> None:
        if not self.current:
            return
        success = tool_succeeded(result)
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
        if mutation:
            self.current.last_mutation_index = len(self.current.traces) - 1
        if not success:
            self.current.failures += 1
        self.current.in_flight = None
        self._persist(self.current)

    def start_action(self, name: str, arguments: dict[str, Any]) -> None:
        """Write intent before dispatch: a crash here leaves an uncertain action, never a replay command."""
        if self.current:
            self.current.in_flight = {"name": name, "arguments": arguments, "started_at": time.time()}
            self._persist(self.current)

    def restore(self, task_id: str) -> TaskRun:
        if not re.fullmatch(r"task-[0-9]+-[a-f0-9]{8}", task_id):
            raise ValueError("Invalid task identifier")
        path = self.trace_dir / f"{task_id}.json"
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Checkpoint exceeds the size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["plan"] = [PlanStep(**step) for step in payload["plan"]]
        payload["traces"] = [ToolTrace(**trace) for trace in payload["traces"]]
        payload["verifications"] = [VerificationRecord(**item) for item in payload["verifications"]]
        restored = TaskRun(**payload)
        if restored.status == "running" or restored.in_flight:
            restored.status = "interrupted"
            for step in restored.plan:
                if step.status == "running":
                    step.status = "pending"
        self.current = restored
        return restored

    def verify(self, claim: str, verified: bool, evidence: str = "") -> bool:
        if not self.current:
            return False
        self.current.verifications.append(
            VerificationRecord(claim=claim, verified=verified, evidence=evidence[:12000], evidence_trace_index=len(self.current.traces) - 1)
        )
        self._persist(self.current)
        return verified

    def all_required_verifications_passed(self) -> bool:
        if not self.current:
            return False
        latest = {item.claim: item for item in self.current.verifications}
        return bool(latest) and all(item.verified for item in latest.values()) and not self.needs_action_review()

    def needs_action_review(self) -> bool:
        if not self.current or self.current.last_mutation_index < 0:
            return False
        reviewed = max((item.evidence_trace_index for item in self.current.verifications), default=-1)
        return reviewed < self.current.last_mutation_index

    def recovery_hint(self, result: str, tool_name: str) -> str:
        if not self.current or tool_succeeded(result):
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

    def repeated_failure(self, name: str, arguments: dict[str, Any], limit: int = 3) -> bool:
        if not self.current:
            return False
        count = 0
        for trace in reversed(self.current.traces):
            if trace.name.startswith("task_"):
                continue
            if trace.success:
                break
            if trace.name == name and trace.arguments == arguments:
                count += 1
        return count >= limit

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
        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                return redact_secrets(value)
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {key: ("[REDACTED]" if re.search(r"password|secret|token|api.?key", key, re.I) else scrub(item)) for key, item in value.items()}
            return value
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(scrub(asdict(task)), output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)


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
