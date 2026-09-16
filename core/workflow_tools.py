"""Ordered deterministic execution through the existing Full Access dispatcher.

The model compiles known arguments once. This executor never infers selectors or
replays an uncertain mutation; durable step journals survive model context trimming.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any

from .orchestrator import tool_succeeded
from .permissions import Risk
from .tools import ToolSpec


_CHECKPOINTS = {"ui_wait_state", "browser_wait_state", "browser_wait"}
_EXCLUDED = {"screen_observe", "take_screenshot", "browser_screenshot", "wait"}


def _report(workflow: dict, prefix: str = "") -> str:
    return prefix + json.dumps({
        "workflow_id": workflow["id"], "status": workflow["status"],
        "steps": [{"id": step["id"], "status": step["status"], "result": step.get("result", "")[:1200]}
                  for step in workflow["steps"]],
        "remaining": [step["id"] for step in workflow["steps"] if step["status"] != "completed"],
        "instruction": "Continue only remaining steps. Never replay an uncertain action. Inspect and review its actual outcome first.",
    }, ensure_ascii=False)


class WorkflowExecutor:
    def __init__(self, agent):
        self.agent = agent

    def _save(self) -> None:
        self.agent.orchestrator._persist(self.agent.orchestrator.current)

    def _validate_call(self, call: dict, checkpoint: bool = False) -> None:
        name, args = call["tool"], call["arguments"]
        spec = self.agent.tools._tools.get(name)
        if (not spec or name.startswith(("task_", "workflow_", "desktop_")) or name in _EXCLUDED
                or checkpoint and (name not in _CHECKPOINTS or spec.risk > Risk.LOW)):
            raise ValueError(f"Tool {name} is not allowed in this workflow position")
        self.agent.tools._validators[name].validate(args)
        allowed, reason = self.agent.tools.permissions.check(name, spec.risk, self.agent.approval(name, args))
        if not allowed:
            raise PermissionError(reason)

    def _dispatch(self, call: dict, emit=None) -> str:
        agent = self.agent
        name, args = call["tool"], call["arguments"]
        from .agent import AgentEvent
        emit and emit(AgentEvent("tool", f"Workflow action: {name}", name))
        start = time.perf_counter()
        result = agent._execute_tool(name, copy.deepcopy(args), approved=agent.approval(name, args))
        mutation = agent._is_mutation(name) and not result.startswith(("PERMISSION_DENIED", "ERROR: Observe the last"))
        agent.orchestrator.record_tool(name, args, result, (time.perf_counter() - start) * 1000,
                                       agent.orchestrator.current.current_turn, mutation=mutation)
        if mutation and result.startswith("VERIFIED:"):
            agent.orchestrator.verify(f"{name} reported its postcondition", True, result)
        emit and emit(AgentEvent("tool_result", result, name))
        return result

    def execute(self, workflow_id: str, steps: list[dict[str, Any]]) -> str:
        agent, current = self.agent, self.agent.orchestrator.current
        if current is None:
            raise ValueError("No active mission")
        # Validate the complete program before any side effect. No partial execution
        # when a later action has invalid arguments, unavailable tools or denied policy.
        ids = [step["id"] for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Workflow step IDs must be unique")
        for step in steps:
            self._validate_call(step)
            if step.get("checkpoint"):
                self._validate_call(step["checkpoint"], checkpoint=True)
        workflow = next((item for item in current.workflows if item["id"] == workflow_id), None)
        program = copy.deepcopy(steps)
        if workflow:
            if workflow["program"] != program:
                raise ValueError("A workflow ID binds an immutable ordered program; use its original steps to resume")
        else:
            if len(current.workflows) >= 20 or sum(len(item["steps"]) for item in current.workflows) + len(steps) > 100:
                raise ValueError("Mission workflow budget exceeded (20 workflows / 100 steps)")
            if len(json.dumps([item["program"] for item in current.workflows] + [program], ensure_ascii=False).encode("utf-8")) > 262144:
                raise ValueError("Mission workflow programs exceed the 256 KiB memory budget")
            workflow = {"id": workflow_id, "program": program, "status": "running",
                        "steps": [{"id": step["id"], "status": "pending"} for step in steps]}
            current.workflows.append(workflow)
            self._save()
        current.metrics["workflow_batches"] = current.metrics.get("workflow_batches", 0) + 1
        emit = getattr(agent, "_active_emit", None)
        for call, record in zip(program, workflow["steps"]):
            if record["status"] == "completed":
                continue
            if agent._is_stopped():
                workflow["status"] = "blocked"
                self._save()
                return _report(workflow, "CANCELLED: ")
            if record["status"] in {"running", "uncertain"}:
                workflow["status"] = "uncertain"
                self._save()
                return _report(workflow, "ERROR: Uncertain workflow action requires live review. ")
            # A checkpoint can be polled again without repeating the action it checks.
            if record["status"] != "checkpoint_pending":
                if agent.orchestrator.needs_action_review():
                    workflow["status"] = "blocked"
                    self._save()
                    return _report(workflow, "ERROR: Observe the last action before continuing. ")
                record["status"] = "running"
                record["trace_index"] = len(current.traces) - 1
                self._save()
                result = self._dispatch(call, emit)
                record["result"] = result[:4000]
                record["trace_index"] = len(current.traces) - 1
                if not tool_succeeded(result):
                    # Even a handler exception may follow a delivered send/click.
                    # Only explicit policy rejection or a pure read is safe to retry.
                    record["status"] = "blocked" if result.startswith("PERMISSION_DENIED") or not agent._is_mutation(call["tool"]) else "uncertain"
                    workflow["status"] = "blocked"
                    self._save()
                    prefix = "BROWSER_ACTION_BLOCKED: " if result.startswith("BROWSER_ACTION_BLOCKED:") else "CANCELLED: " if result.startswith("CANCELLED") else "ERROR: "
                    return _report(workflow, prefix)
                if call.get("checkpoint"):
                    record["status"] = "checkpoint_pending"
                elif agent._is_mutation(call["tool"]) and not result.startswith("VERIFIED:"):
                    record["status"] = "uncertain"
                    workflow["status"] = "uncertain"
                    self._save()
                    return _report(workflow, "ERROR: Delivered action has no verified outcome; inspect and review. ")
                else:
                    record["status"] = "completed"
                self._save()
            if record["status"] == "checkpoint_pending":
                result = self._dispatch(call["checkpoint"], emit)
                record["result"] = result[:4000]
                if not result.startswith("VERIFIED:"):
                    workflow["status"] = "blocked"
                    self._save()
                    prefix = "BROWSER_ACTION_BLOCKED: " if result.startswith("BROWSER_ACTION_BLOCKED:") else "CANCELLED: " if result.startswith("CANCELLED") else "ERROR: Checkpoint pending. "
                    return _report(workflow, prefix)
                agent.orchestrator.verify(f"Workflow {workflow_id}/{call['id']} checkpoint", True, result)
                record["status"] = "completed"
                self._save()
        workflow["status"] = "completed"
        self._save()
        return _report(workflow, "VERIFIED: ")

    def status(self, workflow_id: str = "") -> str:
        current = self.agent.orchestrator.current
        workflows = current.workflows if current else []
        selected = [item for item in workflows if not workflow_id or item["id"] == workflow_id]
        if workflow_id and not selected:
            raise ValueError("Unknown workflow")
        return json.dumps(selected, ensure_ascii=False)

    def review(self, workflow_id: str, step_id: str, claim: str) -> str:
        """Accept an existing live task verification; never dispatch or replay here."""
        current = self.agent.orchestrator.current
        workflow = next((item for item in current.workflows if item["id"] == workflow_id), None) if current else None
        if not workflow:
            raise ValueError("Unknown workflow")
        record = next((item for item in workflow["steps"] if item["id"] == step_id), None)
        if not record or record["status"] not in {"uncertain", "checkpoint_pending", "running"}:
            raise ValueError("Only an attempted uncertain step can be reviewed")
        verification = next((item for item in reversed(current.verifications) if item.claim == claim), None)
        if (not verification or not verification.verified or not verification.evidence.strip()
                or verification.evidence_trace_index <= record.get("trace_index", len(current.traces))
                or self.agent.orchestrator.needs_action_review()):
            raise ValueError("Review requires task_verify with fresh successful evidence after the uncertain action")
        record.update(status="completed", result=verification.evidence[:4000])
        workflow["status"] = "completed" if all(item["status"] == "completed" for item in workflow["steps"]) else "running"
        self._save()
        return _report(workflow)


def register_workflow_tools(agent) -> None:
    executor = WorkflowExecutor(agent)
    call = {"type": "object", "properties": {"tool": {"type": "string", "maxLength": 80},
            "arguments": {"type": "object"}}, "required": ["tool", "arguments"], "additionalProperties": False}
    step = copy.deepcopy(call)
    step["properties"].update(id={"type": "string", "minLength": 1, "maxLength": 80},
                               description={"type": "string", "minLength": 1, "maxLength": 500}, checkpoint=call)
    step["required"] += ["id", "description"]
    agent.tools.register(ToolSpec("workflow_execute",
        "Compile all known mission clauses into 1-32 ordered semantic/native tool steps, then execute in one call. "
        "Every step uses normal policy and is durably journaled. Add a read-only ui_wait_state/browser_wait_state checkpoint "
        "when a tool reports delivery only. Completed steps are never replayed when resuming the same immutable workflow_id. "
        "On failure inspect the live state; uncertain sends/writes require task_verify then workflow_review before continuation. "
        "Use ui_batch for trivial UI actions sharing a meaningful checkpoint. Raw coordinates and recursive/task tools are excluded.",
        Risk.MEDIUM, {"type": "object", "properties": {"workflow_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
        "steps": {"type": "array", "minItems": 1, "maxItems": 32, "items": step}},
        "required": ["workflow_id", "steps"], "additionalProperties": False}, executor.execute))
    agent.tools.register(ToolSpec("workflow_review", "Resolve an attempted uncertain workflow step using an existing task_verify claim backed by fresh observation. Does not execute any action. Resume workflow_execute afterward with the original program.",
        Risk.SAFE, {"type": "object", "properties": {key: {"type": "string", "minLength": 1, "maxLength": 200}
        for key in ("workflow_id", "step_id", "claim")}, "required": ["workflow_id", "step_id", "claim"], "additionalProperties": False}, executor.review))
    agent.tools.register(ToolSpec("workflow_status", "Read the original ordered workflow program and durable step states after context trimming. Omit workflow_id to list current mission workflows. Does not execute anything.",
        Risk.SAFE, {"type": "object", "properties": {"workflow_id": {"type": "string", "maxLength": 80}}, "additionalProperties": False}, executor.status))
