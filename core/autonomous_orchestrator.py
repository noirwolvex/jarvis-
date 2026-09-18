from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
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


@dataclass
class RecoveryRecord:
    step_id: str
    failure: str
    recovery_action: str
    selected_backend: str = ""
    outcome: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass
class AutonomousTaskRun(TaskRun):
    recovery_history: list[RecoveryRecord] = field(default_factory=list)


class ExecutionRouter:
    """Classify the real execution path and expose the preferred deterministic fallback order.

    This class does not blindly switch methods. It describes the route selected by the
    runtime and the legal preference order. Recovery still has to re-observe state and
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


class AutonomousTaskOrchestrator(TaskOrchestrator):
    """TaskOrchestrator with execution contracts, backend visibility and recovery history."""

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
                "skipped": "COMPLETED",
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
        base = self.current.traces[-1]
        self.current.traces[-1] = ExecutionToolTrace(
            name=base.name,
            arguments=base.arguments,
            result=base.result,
            success=base.success,
            duration_ms=base.duration_ms,
            turn=base.turn,
            execution_backend=route.execution_backend,
            resolution_backend=route.resolution_backend,
        )

        running = [step for step in self.current.plan if step.status == "running"]
        if running:
            step = running[-1]
            if isinstance(step, ExecutionPlanStep):
                step.execution_backend = route.execution_backend
                step.resolution_backend = route.resolution_backend
                if not step.fallback_strategy:
                    step.fallback_strategy = list(route.fallback_chain)
                if mutation and tool_succeeded(result):
                    step.phase = "DELIVERED"
        self._persist(self.current)

    def verify(self, claim: str, verified: bool, evidence: str = "") -> bool:
        ok = super().verify(claim, verified, evidence)
        if not self.current:
            return ok
        for step in self.current.plan:
            if not isinstance(step, ExecutionPlanStep):
                continue
            if step.id not in claim:
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
        route = ExecutionRouter.classify(tool_name)
        selected = route.fallback_chain[0] if route.fallback_chain else ""
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
                selected_backend=selected,
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

    def live_task_graph(self) -> list[dict[str, Any]]:
        if not self.current:
            return []
        graph: list[dict[str, Any]] = []
        for item in self.current.plan:
            if isinstance(item, ExecutionPlanStep):
                graph.append({
                    "id": item.id,
                    "action": item.action or item.description,
                    "description": item.description,
                    "dependencies": list(item.depends_on),
                    "required_state": list(item.required_state),
                    "execution_method": item.execution_method,
                    "execution_backend": item.execution_backend,
                    "resolution_backend": item.resolution_backend,
                    "expected_result": item.expected_result,
                    "verification_method": item.verification_method,
                    "verification_result": item.verification_result,
                    "fallback_strategy": list(item.fallback_strategy),
                    "retry_policy": dict(item.retry_policy),
                    "status": item.phase,
                    "result": item.result,
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
            })
        data.update({
            "task_graph": self.live_task_graph(),
            "engine_visibility": traces,
            "execution_priority": list(EXECUTION_PRIORITY),
            "recovery_history": [
                asdict(item) if isinstance(item, RecoveryRecord) else dict(item)
                for item in getattr(self.current, "recovery_history", [])
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
