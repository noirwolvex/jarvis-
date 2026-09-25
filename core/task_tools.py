from __future__ import annotations

import json
from typing import Any

from .orchestrator import PlanStep, TaskOrchestrator
from .permissions import Risk
from .tools import ToolSpec


def register_task_tools(registry, orchestrator: TaskOrchestrator) -> None:
    def task_plan(steps: list[dict[str, Any]] | list[str]) -> str:
        plan = orchestrator.set_plan(steps)
        rows = []
        for step in plan:
            row = {
                "id": step.id,
                "description": step.description,
                "depends_on": step.depends_on,
                "status": step.status,
            }
            for key in (
                "action",
                "required_state",
                "execution_method",
                "expected_result",
                "verification_method",
                "fallback_strategy",
                "retry_policy",
                "phase",
                "execution_backend",
                "resolution_backend",
                "verification_result",
            ):
                if hasattr(step, key):
                    row[key] = getattr(step, key)
            rows.append(row)
        return json.dumps(rows, ensure_ascii=False)

    def task_rewrite_recovery(
        failed_step_id: str,
        recovery_steps: list[dict[str, Any]] | list[str],
        reason: str = "",
    ) -> str:
        rewrite = getattr(orchestrator, "rewrite_failed_step", None)
        if rewrite is None:
            raise ValueError("Adaptive graph rewriting is not available for this execution profile")
        inserted = rewrite(failed_step_id, recovery_steps, reason)
        return json.dumps({
            "failed_step_id": failed_step_id,
            "inserted_step_ids": [step.id for step in inserted],
            "summary": orchestrator.summary(),
        }, ensure_ascii=False)

    def task_update_step(step_id: str, status: str, result: str = "") -> str:
        normalized = str(status).strip().lower().replace("-", "_")
        if normalized == "in_progress":
            normalized = "running"
        allowed = {"pending", "running", "completed", "failed", "skipped"}
        if normalized not in allowed:
            raise ValueError(f"Unsupported step status: {status}")

        verified_evidence = None
        if normalized == "completed" and orchestrator.current is not None:
            recent = []
            for trace in reversed(orchestrator.current.traces):
                if trace.name == "task_update_step":
                    break
                recent.append(trace)
            evidence = [
                trace for trace in recent
                if trace.success and not trace.name.startswith(("task_", "workflow_"))
            ]
            if not evidence:
                raise ValueError(
                    "Cannot mark a step completed before a successful non-task tool result provides execution or observation evidence."
                )
            verified_evidence = next(
                (trace for index, trace in reversed(list(enumerate(orchestrator.current.traces)))
                 if index >= max(orchestrator.current.last_mutation_index, len(orchestrator.current.traces) - len(recent))
                 and trace in evidence and str(trace.result).startswith("VERIFIED:")),
                None,
            )
            latest_verification = orchestrator.current.verifications[-1] if orchestrator.current.verifications else None
            independently_verified = bool(
                latest_verification and latest_verification.verified
                and latest_verification.evidence.strip()
                and latest_verification.evidence_trace_index >= max(
                    orchestrator.current.last_mutation_index, len(orchestrator.current.traces) - len(recent))
            )
            if verified_evidence is None and not independently_verified:
                raise ValueError("Cannot complete a step from delivery alone; observe the outcome and verify it first")

        orchestrator.update_step(step_id, normalized, result)
        if normalized == "completed" and verified_evidence is not None:
            orchestrator.verify(
                f"Plan step {step_id} completed with tool-verified evidence",
                True,
                str(verified_evidence.result),
            )
        return json.dumps(orchestrator.summary(), ensure_ascii=False)

    def task_verify(claim: str, verified: bool, evidence: str = "") -> str:
        current = orchestrator.current
        if current is None:
            raise ValueError("No active task")
        observed = [trace for index, trace in enumerate(current.traces)
                    if trace.success and not trace.name.startswith(("task_", "workflow_"))
                    and (index > current.last_mutation_index
                         or index == current.last_mutation_index and trace.result.startswith("VERIFIED:") and not trace.name.startswith("desktop_"))]
        if not observed:
            if current.last_mutation_index < 0:
                raise ValueError(
                    "Verification requires successful execution or observation evidence; "
                    "no non-task action has succeeded yet. Execute or observe the pending plan step before task_verify"
                )
            raise ValueError("Verification requires successful observation after the action and nonempty evidence")
        if not evidence.strip():
            raise ValueError("Verification requires successful observation after the action and nonempty evidence")
        ok = orchestrator.verify(claim, bool(verified), evidence)
        return json.dumps({"verified": ok, "claim": claim, "evidence": evidence[:12000]}, ensure_ascii=False)

    def task_status() -> str:
        return json.dumps(orchestrator.summary(), ensure_ascii=False)

    def task_recall(task_id: str = "latest") -> str:
        candidates = sorted(orchestrator.trace_dir.glob("task-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if task_id == "latest":
            candidates = [path for path in candidates if not orchestrator.current or path.stem != orchestrator.current.task_id]
            if not candidates:
                return json.dumps({"status": "no_previous_checkpoint"})
            task_id = candidates[0].stem
        previous = type(orchestrator)(str(orchestrator.trace_dir))
        restored = previous.restore(task_id)
        from dataclasses import asdict
        return json.dumps({"summary": previous.summary(), "plan": [asdict(step) for step in restored.plan],
                           "workflows": restored.workflows,
                           "uncertain_action": restored.in_flight, "recent_evidence": [asdict(trace) for trace in restored.traces[-8:]],
                           "instruction": "Re-observe current state before continuing. An uncertain action must never be automatically replayed."}, ensure_ascii=False)

    registry.register(ToolSpec(
        "task_plan",
        "Create or replace the ordered execution plan for the current task. For complex work, include execution method, required state, expected result, verification method, fallback strategy, and retry policy when known.",
        Risk.SAFE,
        {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object", "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "action": {"type": "string"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "required_state": {"type": "array", "items": {"type": "string"}},
                                "execution_method": {
                                    "type": "string",
                                    "enum": ["AUTO", "DIRECT", "APP_API", "CDP_DOM", "UIA", "RUST_NATIVE", "VISION", "COORDINATE"],
                                },
                                "expected_result": {"type": "string"},
                                "verification_method": {"type": "string"},
                                "fallback_strategy": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["DIRECT", "APP_API", "CDP_DOM", "UIA", "RUST_NATIVE", "VISION", "COORDINATE"],
                                    },
                                },
                                "retry_policy": {
                                    "type": "object",
                                    "properties": {
                                        "max_attempts": {"type": "integer", "minimum": 0, "maximum": 10},
                                        "retry_only_if_safe": {"type": "boolean"},
                                        "backoff_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
                                    },
                                    "additionalProperties": False,
                                },
                            }, "required": ["description"], "additionalProperties": False},
                        ]
                    },
                    "minItems": 1,
                    "maxItems": 100,
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
        task_plan,
    ))
    registry.register(ToolSpec(
        "task_rewrite_recovery",
        "Dynamically insert a bounded recovery subgraph after a failed/recovering plan step. The original failed node is preserved, completed work is not replayed, and direct downstream dependencies are rewired to the recovery tail. Use only after inspecting the live state and deciding on a safe alternate path.",
        Risk.SAFE,
        {
            "type": "object",
            "properties": {
                "failed_step_id": {"type": "string"},
                "reason": {"type": "string", "maxLength": 4000},
                "recovery_steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "object", "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "action": {"type": "string"},
                                "required_state": {"type": "array", "items": {"type": "string"}},
                                "execution_method": {
                                    "type": "string",
                                    "enum": ["AUTO", "DIRECT", "APP_API", "CDP_DOM", "UIA", "RUST_NATIVE", "VISION", "COORDINATE"],
                                },
                                "expected_result": {"type": "string"},
                                "verification_method": {"type": "string"},
                                "fallback_strategy": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["DIRECT", "APP_API", "CDP_DOM", "UIA", "RUST_NATIVE", "VISION", "COORDINATE"],
                                    },
                                },
                                "retry_policy": {
                                    "type": "object",
                                    "properties": {
                                        "max_attempts": {"type": "integer", "minimum": 0, "maximum": 10},
                                        "retry_only_if_safe": {"type": "boolean"},
                                        "backoff_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
                                    },
                                    "additionalProperties": False,
                                },
                            }, "required": ["description"], "additionalProperties": False},
                        ]
                    },
                },
            },
            "required": ["failed_step_id", "recovery_steps"],
            "additionalProperties": False,
        },
        task_rewrite_recovery,
    ))
    registry.register(ToolSpec(
        "task_update_step",
        "Update one execution-plan step after starting or completing it. Use running (or in_progress, which is normalized to running). A completed step must follow successful execution or observation evidence; never mark an attempted action completed before its tool succeeds. VERIFIED tool results are automatically recorded as verification evidence.",
        Risk.SAFE,
        {
            "type": "object",
            "properties": {
                "step_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "in_progress", "completed", "failed", "skipped"],
                },
                "result": {"type": "string"},
            },
            "required": ["step_id", "status"],
            "additionalProperties": False,
        },
        task_update_step,
    ))
    registry.register(ToolSpec(
        "task_verify",
        "Record explicit verification evidence for an important outcome. Use after mutations and before claiming completion.",
        Risk.SAFE,
        {"type": "object", "properties": {"claim": {"type": "string"}, "verified": {"type": "boolean"}, "evidence": {"type": "string"}}, "required": ["claim", "verified"], "additionalProperties": False},
        task_verify,
    ))
    registry.register(ToolSpec(
        "task_status",
        "Return the current live task graph, progress, execution backends, failures, recoveries, and verification summary.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        task_status,
    ))
    registry.register(ToolSpec("task_recall", "Read a previous task checkpoint when the user asks to resume or continue. Returns plan, progress, recent evidence, and uncertain actions without executing them.", Risk.SAFE,
        {"type": "object", "properties": {"task_id": {"type": "string", "default": "latest"}}, "additionalProperties": False}, task_recall))
