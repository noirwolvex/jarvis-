from __future__ import annotations

import json
from typing import Any

from .orchestrator import PlanStep, TaskOrchestrator
from .permissions import Risk
from .tools import ToolSpec


def register_task_tools(registry, orchestrator: TaskOrchestrator) -> None:
    def task_plan(steps: list[dict[str, Any]] | list[str]) -> str:
        plan = orchestrator.set_plan(steps)
        return json.dumps([{
            "id": step.id,
            "description": step.description,
            "depends_on": step.depends_on,
            "status": step.status,
        } for step in plan], ensure_ascii=False)

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
                if trace.success and not trace.name.startswith("task_")
            ]
            if not evidence:
                raise ValueError(
                    "Cannot mark a step completed before a successful non-task tool result provides execution or observation evidence."
                )
            verified_evidence = next(
                (trace for trace in evidence if str(trace.result).startswith("VERIFIED:")),
                None,
            )

        orchestrator.update_step(step_id, normalized, result)
        if normalized == "completed" and verified_evidence is not None:
            orchestrator.verify(
                f"Plan step {step_id} completed with tool-verified evidence",
                True,
                str(verified_evidence.result),
            )
        return json.dumps(orchestrator.summary(), ensure_ascii=False)

    def task_verify(claim: str, verified: bool, evidence: str = "") -> str:
        ok = orchestrator.verify(claim, bool(verified), evidence)
        return json.dumps({"verified": ok, "claim": claim, "evidence": evidence[:12000]}, ensure_ascii=False)

    def task_status() -> str:
        return json.dumps(orchestrator.summary(), ensure_ascii=False)

    registry.register(ToolSpec(
        "task_plan",
        "Create or replace the ordered execution plan for the current task. Use this before complex multi-step work.",
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
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                            }, "required": ["description"]},
                        ]
                    },
                    "minItems": 1,
                    "maxItems": 20,
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
        task_plan,
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
        "Return the current task plan, progress, failures, recoveries, and verification summary.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        task_status,
    ))
