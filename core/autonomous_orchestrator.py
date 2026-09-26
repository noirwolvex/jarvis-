from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from .orchestrator import (
    PlanStep,
    TaskOrchestrator,
    TaskRun,
    ToolTrace,
    VerificationRecord,
    tool_succeeded,
)


EXECUTION_PRIORITY = (
    "DIRECT",
    "APP_API",
    "CDP_DOM",
    "UIA",
    "RUST_NATIVE",
    "VISION",
    "COORDINATE",
)

STEP_PHASES = {
    "QUEUED",
    "RUNNING",
    "DELIVERED",
    "VERIFIED",
    "RECOVERING",
    "FAILED",
    "WAITING_USER",
    "COMPLETED",
}


@dataclass(frozen=True)
class EngineRoute:
    execution_backend: str
    resolution_backend: str = ""
    fallback_chain: tuple[str, ...] = ()


@dataclass
class ExecutionPlanStep(PlanStep):
    """A plan node with enough information to execute and recover without replanning."""

    action: str = ""
    required_state: list[str] = field(default_factory=list)
    execution_method: str = "AUTO"
    expected_result: str = ""
    verification_method: str = "INDEPENDENT"
    fallback_strategy: list[str] = field(default_factory=list)
    retry_policy: dict[str, Any] = field(default_factory=lambda: {
        "max_attempts": 2,
        "retry_only_if_safe": True,
        "backoff_ms": 0,
    })
    phase: str = "QUEUED"
    execution_backend: str = ""
    resolution_backend: str = ""
    verification_result: str = ""


@dataclass
class ExecutionToolTrace(ToolTrace):
    execution_backend: str = ""
    resolution_backend: str = ""
    operations: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RecoveryRecord:
    step_id: str
    failure: str
    recovery_action: str
    selected_backend: str = ""
    outcome: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass
class GraphRewriteRecord:
    failed_step_id: str
    inserted_step_ids: list[str]
    rewired_step_ids: list[str]
    reason: str
    created_at: float = field(default_factory=time.time)


@dataclass
class AutonomousTaskRun(TaskRun):
    recovery_history: list[RecoveryRecord] = field(default_factory=list)
    graph_rewrites: list[GraphRewriteRecord] = field(default_factory=list)


class ExecutionRouter:
    """Describe preferred routes for planning, never evidence of actual execution.

    This class does not blindly switch methods. It describes the route selected by the
    planner and the legal preference order. Recovery still has to re-observe state and
    determine whether a fallback is safe before mutating the desktop.
    """

    @staticmethod
    def _fallbacks(backend: str) -> tuple[str, ...]:
        if backend not in EXECUTION_PRIORITY:
            return EXECUTION_PRIORITY
        index = EXECUTION_PRIORITY.index(backend)
        return EXECUTION_PRIORITY[index + 1 :]

    @classmethod
    def classify(cls, tool_name: str, arguments: dict[str, Any] | None = None) -> EngineRoute:
        name = str(tool_name or "").strip().casefold()
        args = arguments or {}

        if name.startswith(("wincom_", "office_", "word_", "excel_", "powerpoint_")):
            # Native application APIs should win over GUI automation when an explicit,
            # permission-gated adapter exists. Recovery never calls arbitrary COM.
            return EngineRoute(
                "APP_API",
                "WINCOM",
                ("UIA", "RUST_NATIVE", "VISION", "COORDINATE"),
            )

        if name.endswith("_native") or name.startswith("rust_") or name in {
            "whatsapp_select_chat_native",
            "ui_type_native",
        }:
            resolution = "UIA" if name.startswith(("ui_", "whatsapp_")) else ""
            return EngineRoute("RUST_NATIVE", resolution, cls._fallbacks("RUST_NATIVE"))

        if name.startswith(("chrome_", "browser_", "youtube_")) or name in {
            "google_search",
            "open_url",
        }:
            return EngineRoute("CDP_DOM", "DOM", cls._fallbacks("CDP_DOM"))

        if name.startswith(("ui_", "dialog_", "discord_")) or name in {
            "list_windows",
            "inspect_window",
            "focus_window",
            "focus_window_advanced",
        }:
            return EngineRoute("UIA", "UIA", cls._fallbacks("UIA"))

        if name.startswith(("vision_", "screen_")) or name in {
            "take_screenshot",
            "screen_observe",
        }:
            return EngineRoute("VISION", "VISION", cls._fallbacks("VISION"))

        rust_atomic_desktop = {
            "desktop_click",
            "desktop_type",
            "desktop_click_button",
            "desktop_move",
            "desktop_scroll",
            "desktop_double_click",
            "desktop_drag",
            "desktop_press",
            "desktop_hotkey",
        }
        if name in rust_atomic_desktop:
            coordinate_tools = {
                "desktop_click",
                "desktop_click_button",
                "desktop_move",
                "desktop_double_click",
                "desktop_drag",
            }
            resolution = "SCREEN" if name in coordinate_tools else "FOREGROUND_WINDOW"
            return EngineRoute("RUST_NATIVE", resolution, cls._fallbacks("RUST_NATIVE"))

        has_coordinates = all(key in args for key in ("x", "y"))
        if has_coordinates:
            return EngineRoute("COORDINATE", "SCREEN", ())

        if name.startswith("desktop_"):
            # Stateful key/mouse hold primitives are Python-only compatibility tools and
            # are intentionally disabled when the runtime is in strict Rust mode.
            return EngineRoute("DIRECT", "FOREGROUND_WINDOW", cls._fallbacks("DIRECT"))

        return EngineRoute("DIRECT", "", cls._fallbacks("DIRECT"))


def _atomic_checkpoint(method):
    """Commit base state and its rich metadata together, before returning to execution.

    Restricted to synchronous state updates below; never wrap tool dispatch or an
    entire workflow. Intent journaling must still reach disk before every effect.
    """
    @wraps(method)
    def update(self, *args, **kwargs):
        self._checkpoint_depth = getattr(self, "_checkpoint_depth", 0) + 1
        try:
            return method(self, *args, **kwargs)
        finally:
            self._checkpoint_depth -= 1
            if not self._checkpoint_depth:
                pending = getattr(self, "_pending_checkpoint", None)
                self._pending_checkpoint = None
                if pending is not None:
                    self._persist(pending)
    return update


class AutonomousTaskOrchestrator(TaskOrchestrator):
    """TaskOrchestrator with execution contracts, backend visibility and recovery history."""

    def _persist(self, task: TaskRun) -> None:
        if getattr(self, "_checkpoint_depth", 0):
            self._pending_checkpoint = task
            return
        super()._persist(task)
        callback = getattr(self, "on_task_graph", None)
        if callback is not None:
            # Progress is read-only and must never turn a delivered action into a retry.
            try:
                callback(self.live_task_graph())
            except Exception:
                task.metrics["progress_delivery_errors"] = task.metrics.get("progress_delivery_errors", 0) + 1

    def begin(self, goal: str) -> AutonomousTaskRun:
        self.current = AutonomousTaskRun(
            task_id=f"task-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}",
            goal=goal,
            started_at=time.time(),
        )
        self._persist(self.current)
        return self.current

    def _rich_step(self, raw: PlanStep | dict[str, Any] | str, index: int) -> ExecutionPlanStep:
        if isinstance(raw, ExecutionPlanStep):
            return raw
        if isinstance(raw, PlanStep):
            return ExecutionPlanStep(
                id=raw.id,
                description=raw.description,
                depends_on=list(raw.depends_on),
                status=raw.status,
                result=raw.result,
                action=raw.description,
            )
        if isinstance(raw, str):
            return ExecutionPlanStep(
                id=f"step-{index}",
                description=raw,
                action=raw,
            )

        retry = dict(raw.get("retry_policy") or {})
        retry.setdefault("max_attempts", 2)
        retry.setdefault("retry_only_if_safe", True)
        retry.setdefault("backoff_ms", 0)
        retry["max_attempts"] = max(0, min(int(retry["max_attempts"]), 10))
        retry["backoff_ms"] = max(0, min(int(retry["backoff_ms"]), 30000))
        retry["retry_only_if_safe"] = bool(retry["retry_only_if_safe"])

        method = str(raw.get("execution_method") or raw.get("preferred_engine") or "AUTO").upper()
        allowed_methods = {"AUTO", *EXECUTION_PRIORITY}
        if method not in allowed_methods:
            raise ValueError(f"Unsupported execution method: {method}")

        fallbacks = [str(item).upper() for item in (raw.get("fallback_strategy") or raw.get("fallback_chain") or [])]
        if any(item not in EXECUTION_PRIORITY for item in fallbacks):
            raise ValueError("Fallback strategy contains an unsupported execution backend")

        phase = str(raw.get("phase") or "QUEUED").upper()
        if phase not in STEP_PHASES:
            raise ValueError(f"Unsupported execution phase: {phase}")

        return ExecutionPlanStep(
            id=str(raw.get("id") or f"step-{index}"),
            description=str(raw.get("description") or raw.get("goal") or raw.get("action") or "Unnamed step"),
            depends_on=list(raw.get("depends_on") or raw.get("dependencies") or []),
            status=str(raw.get("status") or "pending"),
            result=str(raw.get("result") or ""),
            action=str(raw.get("action") or raw.get("description") or raw.get("goal") or ""),
            required_state=[str(item) for item in (raw.get("required_state") or [])],
            execution_method=method,
            expected_result=str(raw.get("expected_result") or ""),
            verification_method=str(raw.get("verification_method") or "INDEPENDENT").upper(),
            fallback_strategy=fallbacks,
            retry_policy=retry,
            phase=phase,
            execution_backend=str(raw.get("execution_backend") or ""),
            resolution_backend=str(raw.get("resolution_backend") or ""),
            verification_result=str(raw.get("verification_result") or ""),
        )

    @_atomic_checkpoint
    def set_plan(self, steps: list[PlanStep | dict[str, Any] | str]) -> list[ExecutionPlanStep]:
        rich = [self._rich_step(raw, index) for index, raw in enumerate(steps, start=1)]
        plan = super().set_plan(rich)
        for step in plan:
            if isinstance(step, ExecutionPlanStep):
                if step.status == "pending" and step.phase not in {"WAITING_USER", "RECOVERING"}:
                    step.phase = "QUEUED"
                elif step.status == "running" and step.phase not in {"DELIVERED", "VERIFIED", "RECOVERING"}:
                    step.phase = "RUNNING"
                elif step.status == "failed":
                    step.phase = "FAILED"
                elif step.status == "completed":
                    step.phase = "COMPLETED"
        if self.current:
            self._persist(self.current)
        return list(plan)

    @_atomic_checkpoint
    def update_step(self, step_id: str, status: str, result: str = "") -> None:
        super().update_step(step_id, status, result)
        if not self.current:
            return
        step = next((item for item in self.current.plan if item.id == step_id), None)
        if isinstance(step, ExecutionPlanStep):
            phase_by_status = {
                "pending": "QUEUED",
                "running": "RUNNING",
                "failed": "FAILED",
                "completed": "COMPLETED",
                "skipped": "FAILED",
            }
            step.phase = phase_by_status.get(status, step.phase)
            self._persist(self.current)

    def mark_waiting_user(self, step_id: str, reason: str) -> None:
        if not self.current:
            return
        step = next((item for item in self.current.plan if item.id == step_id), None)
        if not isinstance(step, ExecutionPlanStep):
            raise ValueError(f"Unknown plan step: {step_id}")
        step.phase = "WAITING_USER"
        step.result = reason[:12000]
        self._persist(self.current)

    @_atomic_checkpoint
    def record_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        result: str,
        duration_ms: float,
        turn: int,
        mutation: bool = False,
        review_required: bool | None = None,
    ) -> None:
        super().record_tool(
            name,
            arguments,
            result,
            duration_ms,
            turn,
            mutation=mutation,
            review_required=review_required,
        )
        if not self.current or not self.current.traces:
            return

        route = ExecutionRouter.classify(name, arguments)
        evidence = getattr(result, "execution", {})
        operations = evidence.get("operations", [])
        backend = evidence.get("backend", "unreported")
        resolution = "+".join(dict.fromkeys(row["engine"] for row in operations if row["phase"] == "resolve"))
        base = self.current.traces[-1]
        self.current.traces[-1] = ExecutionToolTrace(
            name=base.name,
            arguments=base.arguments,
            result=base.result,
            success=base.success,
            duration_ms=base.duration_ms,
            turn=base.turn,
            execution_backend=backend,
            resolution_backend=resolution,
            operations=operations,
        )

        running = [step for step in self.current.plan if step.status == "running"]
        if running:
            step = running[-1]
            if isinstance(step, ExecutionPlanStep):
                if mutation or not step.execution_backend:
                    step.execution_backend = backend
                    step.resolution_backend = resolution
                if not step.fallback_strategy:
                    step.fallback_strategy = list(route.fallback_chain)
                if mutation and tool_succeeded(result):
                    step.phase = "DELIVERED"
        self._persist(self.current)

    @_atomic_checkpoint
    def verify(self, claim: str, verified: bool, evidence: str = "") -> bool:
        ok = super().verify(claim, verified, evidence)
        if not self.current:
            return ok
        for step in self.current.plan:
            if not isinstance(step, ExecutionPlanStep):
                continue
            if not re.search(rf"(?<![\w-]){re.escape(step.id)}(?![\w-])", claim):
                continue
            step.verification_result = "VERIFIED" if verified else "FAILED"
            if verified:
                step.phase = "COMPLETED" if step.status == "completed" else "VERIFIED"
            else:
                step.phase = "FAILED"
            break
        self._persist(self.current)
        return ok

    def recovery_hint(self, result: str, tool_name: str) -> str:
        hint = super().recovery_hint(result, tool_name)
        if not self.current or not hint:
            return hint
        active = next(
            (step for step in reversed(self.current.plan) if step.status in {"running", "failed"}),
            None,
        )
        if isinstance(active, ExecutionPlanStep):
            active.phase = "RECOVERING"
            step_id = active.id
        else:
            step_id = ""
        if isinstance(self.current, AutonomousTaskRun):
            self.current.recovery_history.append(RecoveryRecord(
                step_id=step_id,
                failure=str(result)[:4000],
                recovery_action=hint,
                selected_backend="",  # Inspection guidance is not an executed fallback.
            ))
        self._persist(self.current)
        return hint

    def record_recovery_outcome(self, step_id: str, outcome: str, backend: str = "") -> None:
        if not isinstance(self.current, AutonomousTaskRun):
            return
        for item in reversed(self.current.recovery_history):
            if item.step_id == step_id and item.outcome == "pending":
                item.outcome = str(outcome)[:4000]
                if backend:
                    item.selected_backend = str(backend).upper()
                break
        self._persist(self.current)

    @_atomic_checkpoint
    def rewrite_failed_step(
        self,
        failed_step_id: str,
        recovery_steps: list[PlanStep | dict[str, Any] | str],
        reason: str = "",
    ) -> list[ExecutionPlanStep]:
        """Insert a bounded recovery subgraph without deleting or replaying mission steps.

        The failed node remains in the graph as historical evidence. Recovery nodes are
        inserted immediately after it, inherit only its already-satisfied dependencies,
        and downstream nodes that directly depended on the failed node are rewired to
        the recovery tail. This allows the mission to continue after a verified alternate
        path while preserving the original requested work and its failure record.
        """
        if not self.current:
            raise ValueError("No active task")
        if not recovery_steps or len(recovery_steps) > 8:
            raise ValueError("Recovery rewrite requires 1-8 bounded steps")

        try:
            failed_index = next(
                index for index, step in enumerate(self.current.plan)
                if step.id == failed_step_id
            )
        except StopIteration as exc:
            raise ValueError(f"Unknown failed plan step: {failed_step_id}") from exc

        failed = self.current.plan[failed_index]
        failed_phase = getattr(failed, "phase", "")
        if failed.status != "failed" and failed_phase != "RECOVERING":
            raise ValueError("Only a failed or recovering step can be rewritten")

        existing_ids = {step.id for step in self.current.plan}
        inserted: list[ExecutionPlanStep] = []
        previous_id = ""
        inherited_dependencies = list(failed.depends_on)

        for offset, raw in enumerate(recovery_steps, start=1):
            generated_id = f"recovery-{failed_step_id}-{offset}"
            prepared: PlanStep | dict[str, Any] | str = raw
            if isinstance(raw, str):
                prepared = {"id": generated_id, "description": raw}
            elif isinstance(raw, dict) and not raw.get("id"):
                prepared = {**raw, "id": generated_id}
            step = self._rich_step(prepared, failed_index + offset + 1)
            if step.id in existing_ids or any(item.id == step.id for item in inserted):
                raise ValueError(f"Recovery step id already exists: {step.id}")
            step.depends_on = [previous_id] if previous_id else inherited_dependencies
            step.status = "pending"
            step.result = ""
            step.phase = "QUEUED"
            step.execution_backend = ""
            step.resolution_backend = ""
            step.verification_result = ""
            inserted.append(step)
            previous_id = step.id

        tail_id = inserted[-1].id
        rewired: list[str] = []
        for step in self.current.plan:
            if step.id == failed_step_id or failed_step_id not in step.depends_on:
                continue
            next_dependencies: list[str] = []
            for dependency in step.depends_on:
                candidate = tail_id if dependency == failed_step_id else dependency
                if candidate not in next_dependencies:
                    next_dependencies.append(candidate)
            step.depends_on = next_dependencies
            rewired.append(step.id)

        self.current.plan[failed_index + 1:failed_index + 1] = inserted
        if isinstance(failed, ExecutionPlanStep):
            failed.phase = "RECOVERING"

        if isinstance(self.current, AutonomousTaskRun):
            self.current.graph_rewrites.append(GraphRewriteRecord(
                failed_step_id=failed_step_id,
                inserted_step_ids=[step.id for step in inserted],
                rewired_step_ids=rewired,
                reason=str(reason)[:4000],
            ))
        self.current.metrics["graph_rewrites"] = self.current.metrics.get("graph_rewrites", 0) + 1
        self._persist(self.current)
        return inserted

    def step_is_resolved(self, step: PlanStep) -> bool:
        """Return true when a required node completed directly or via its recovery tail."""
        if step.status == "completed":
            return True
        if not isinstance(self.current, AutonomousTaskRun):
            return False
        for rewrite in reversed(self.current.graph_rewrites):
            if rewrite.failed_step_id != step.id or not rewrite.inserted_step_ids:
                continue
            tail_id = rewrite.inserted_step_ids[-1]
            tail = next((item for item in self.current.plan if item.id == tail_id), None)
            return bool(tail and tail.status == "completed")
        return False

    def live_task_graph(self) -> list[dict[str, Any]]:
        if not self.current:
            return []
        graph: list[dict[str, Any]] = []
        for item in self.current.plan:
            if isinstance(item, ExecutionPlanStep):
                recovered = item.status != "completed" and self.step_is_resolved(item)
                recovery_tail = None
                if recovered and isinstance(self.current, AutonomousTaskRun):
                    for rewrite in reversed(self.current.graph_rewrites):
                        if rewrite.failed_step_id != item.id or not rewrite.inserted_step_ids:
                            continue
                        tail_id = rewrite.inserted_step_ids[-1]
                        recovery_tail = next((step for step in self.current.plan if step.id == tail_id), None)
                        break
                projected_result = item.result
                projected_execution_backend = item.execution_backend
                projected_resolution_backend = item.resolution_backend
                projected_verification = item.verification_result
                if recovered and recovery_tail is not None:
                    projected_result = (
                        "RECOVERED: " + (recovery_tail.result or recovery_tail.description)
                    )[:12000]
                    projected_execution_backend = getattr(recovery_tail, "execution_backend", "") or item.execution_backend
                    projected_resolution_backend = getattr(recovery_tail, "resolution_backend", "") or item.resolution_backend
                    projected_verification = getattr(recovery_tail, "verification_result", "") or "VERIFIED"
                graph.append({
                    "id": item.id,
                    "action": item.action or item.description,
                    "description": item.description,
                    "dependencies": list(item.depends_on),
                    "required_state": list(item.required_state),
                    "execution_method": item.execution_method,
                    "execution_backend": projected_execution_backend,
                    "resolution_backend": projected_resolution_backend,
                    "expected_result": item.expected_result,
                    "verification_method": item.verification_method,
                    "verification_result": projected_verification,
                    "fallback_strategy": list(item.fallback_strategy),
                    "retry_policy": dict(item.retry_policy),
                    "status": "COMPLETED" if recovered else item.phase,
                    "result": projected_result,
                    "recovered": recovered,
                    "original_status": item.phase if recovered else "",
                    "original_result": item.result if recovered else "",
                })
            else:
                graph.append({
                    "id": item.id,
                    "action": item.description,
                    "description": item.description,
                    "dependencies": list(item.depends_on),
                    "status": item.status.upper(),
                    "result": item.result,
                })
        return graph

    def summary(self) -> dict[str, Any]:
        data = super().summary()
        if not self.current:
            return data
        traces = []
        for trace in self.current.traces[-12:]:
            traces.append({
                "tool": trace.name,
                "success": trace.success,
                "duration_ms": trace.duration_ms,
                "execution_backend": getattr(trace, "execution_backend", ""),
                "resolution_backend": getattr(trace, "resolution_backend", ""),
                "operations": getattr(trace, "operations", []),
            })
        data.update({
            "task_graph": self.live_task_graph(),
            "plan_resolved": sum(self.step_is_resolved(step) for step in self.current.plan),
            "engine_visibility": traces,
            "execution_priority": list(EXECUTION_PRIORITY),
            "recovery_history": [
                asdict(item) if isinstance(item, RecoveryRecord) else dict(item)
                for item in getattr(self.current, "recovery_history", [])
            ],
            "graph_rewrites": [
                asdict(item) if isinstance(item, GraphRewriteRecord) else dict(item)
                for item in getattr(self.current, "graph_rewrites", [])
            ],
        })
        return data

    def restore(self, task_id: str) -> AutonomousTaskRun:
        if not re.fullmatch(r"task-[0-9]+-[a-f0-9]{8}", task_id):
            raise ValueError("Invalid task identifier")
        path = self.trace_dir / f"{task_id}.json"
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Checkpoint exceeds the size limit")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["plan"] = [self._rich_step(step, index) for index, step in enumerate(payload.get("plan", []), start=1)]
        payload["traces"] = [ExecutionToolTrace(**trace) for trace in payload.get("traces", [])]
        payload["verifications"] = [VerificationRecord(**item) for item in payload.get("verifications", [])]
        payload["recovery_history"] = [RecoveryRecord(**item) for item in payload.get("recovery_history", [])]
        payload["graph_rewrites"] = [GraphRewriteRecord(**item) for item in payload.get("graph_rewrites", [])]
        restored = AutonomousTaskRun(**payload)
        if restored.status == "running" or restored.in_flight:
            restored.status = "interrupted"
            for step in restored.plan:
                if step.status == "running":
                    step.status = "pending"
                    if isinstance(step, ExecutionPlanStep):
                        step.phase = "QUEUED"
        self.current = restored
        self._persist(restored)
        return restored
