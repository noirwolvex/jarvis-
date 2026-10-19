"""Model-facing typed container retaining authorization on every child call."""
from __future__ import annotations

import json

from .engines import AgentProvider, MouseEngine, KeyboardEngine, SmartUIEngine, VisionEngine, VerificationEngine
from .models import Action, Check, RetryPolicy, Status
from .session import ControlSession


def register_control_engines(agent) -> None:
    from ..permissions import Risk
    from ..tools import ToolSpec
    from .vision import register_local_vision_tools
    register_local_vision_tools(agent.tools)

    def execute(action: dict) -> str:
        current = agent.orchestrator.current
        if current is None:
            raise ValueError("Typed control requires an active mission")
        session = getattr(agent, "control_session", None)
        if session is None or getattr(agent, "_control_session_mission", None) != current.task_id:
            if session is not None:
                session.close()
            session = ControlSession(AgentProvider(agent))
            agent.control_session = session
            agent._control_session_mission = current.task_id
        fields = dict(action)
        fields["preconditions"] = tuple(Check(**item) for item in fields.get("preconditions", []))
        fields["dependencies"] = tuple(fields.get("dependencies", []))
        if fields.get("expected_outcome") is not None:
            fields["expected_outcome"] = Check(**fields["expected_outcome"])
        fields["retry_policy"] = RetryPolicy(**fields.get("retry_policy", {}))
        outcome = session.execute(Action(**fields))
        if outcome.status in {Status.TIMED_OUT, Status.CANCELLED}:
            agent._stop.set()
        prefix = "VERIFIED: " if outcome.status == Status.SUCCEEDED else "CANCELLED: " if outcome.status == Status.CANCELLED else "ERROR: "
        return prefix + json.dumps(outcome.as_dict(), ensure_ascii=False)

    check = {"type": "object", "properties": {
        "tool": {"enum": ["interaction_wait", "ui_wait_state", "browser_wait_state"]},
        "arguments": {"type": "object"}}, "required": ["tool", "arguments"], "additionalProperties": False}
    operations = sorted({op for cls in (MouseEngine, KeyboardEngine, SmartUIEngine, VisionEngine, VerificationEngine)
                         for op in cls.operations})
    schema = {"type": "object", "properties": {"action": {"type": "object", "properties": {
        "action_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"},
        "action_type": {"enum": operations}, "parameters": {"type": "object"}, "target": {"type": "object"},
        "timeout": {"type": "number", "minimum": 0.05, "maximum": 120},
        "preconditions": {"type": "array", "items": check, "maxItems": 8},
        "expected_outcome": check,
        "dependencies": {"type": "array", "items": {"type": "string"}, "uniqueItems": True, "maxItems": 32},
        "retry_policy": {"type": "object", "properties": {
            "read_attempts": {"type": "integer", "minimum": 1, "maximum": 5},
            "backoff_seconds": {"type": "number", "minimum": 0, "maximum": 2}}, "additionalProperties": False},
    }, "required": ["action_id", "action_type"], "additionalProperties": False}},
        "required": ["action"], "additionalProperties": False}
    agent.tools.register(ToolSpec("workflow_control_action", "Typed five-engine desktop action with a deadline, "
        "optional read-only preconditions/postcondition, evidence and bounded read retries. Uses existing child "
        "tool argument schemas in target/parameters; never retries mutations or runs shell/code. IDs are immutable "
        "within the mission. Use workflow_execute for an ordered persistent multi-step program.", Risk.MEDIUM, schema, execute))
