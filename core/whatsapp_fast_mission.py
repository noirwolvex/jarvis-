from __future__ import annotations

import re
import time
from typing import Any, Callable

from .agent import AgentEvent
from .fast_mission import FastStep, _split_trailing_type, compile_fast_mission
from .orchestrator import PlanStep, tool_succeeded

_CHAT_ACTION = re.compile(
    r"\s+(?:(?:and\s+then|then|and)\s+)"
    r"(?:press|click|open)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)\s+chat\s*$",
    re.IGNORECASE,
)
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}


def _ordinal(value: str) -> int:
    normalized = str(value).strip().casefold()
    if normalized in _ORDINALS:
        return _ORDINALS[normalized]
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", normalized)
    if not match:
        raise ValueError("Unsupported chat ordinal")
    position = int(match.group(1))
    if not 1 <= position <= 20:
        raise ValueError("Chat ordinal must be between 1 and 20")
    return position


def compile_whatsapp_ordinal_mission(goal: str) -> list[FastStep] | None:
    """Compile app-chain -> Nth WhatsApp chat -> optional Rust type as one verified mission."""
    text = re.sub(r"\s+", " ", str(goal or "")).strip()
    if not text or len(text) > 8000 or "\0" in text:
        return None

    try:
        base_text, trailing_type = _split_trailing_type(text)
    except ValueError:
        return None

    match = _CHAT_ACTION.search(base_text)
    if not match:
        return None
    app_text = base_text[: match.start()].strip()
    if not app_text:
        return None
    try:
        position = _ordinal(match.group("ordinal"))
    except ValueError:
        return None

    app_steps = compile_fast_mission(app_text)
    if not app_steps or any(step.tool != "launch_installed_app" for step in app_steps):
        return None
    last_query = str(app_steps[-1].arguments.get("query", "")).replace(" ", "").casefold()
    if last_query != "whatsapp":
        return None

    steps = list(app_steps)
    steps.append(
        FastStep(
            id=f"fast-{len(steps) + 1}",
            description=f"Select and verify WhatsApp chat position {position} through Rust-native mouse input",
            tool="whatsapp_select_chat_native",
            arguments={"position": position},
        )
    )
    if trailing_type is not None:
        if len(steps) >= 32:
            return None
        steps.append(
            FastStep(
                id=f"fast-{len(steps) + 1}",
                description="Type the requested text into the selected WhatsApp chat through Rust",
                tool="ui_type_native",
                arguments={"text": trailing_type},
            )
        )
    return steps if len(steps) <= 32 else None


def execute_whatsapp_ordinal_mission(
    agent: Any,
    goal: str,
    emit: Callable[[AgentEvent], None] | None = None,
) -> str | None:
    steps = compile_whatsapp_ordinal_mission(goal)
    if not steps:
        return None

    agent.orchestrator.begin(goal)
    from .full_access_agent import _chrome_tab_rows

    agent._mission_initial_tab_count = len(_chrome_tab_rows())
    agent.orchestrator.current.metrics["fast_compiled_steps"] = len(steps)
    agent.orchestrator.current.metrics["whatsapp_ordinal_fast_path"] = True
    agent.workspace_context.save_snapshot()
    agent.messages.append({"role": "user", "content": goal})
    agent.memory.add("user", goal)
    agent.orchestrator.set_plan(
        [
            PlanStep(
                id=step.id,
                description=step.description,
                depends_on=[] if index == 0 else [steps[index - 1].id],
            )
            for index, step in enumerate(steps)
        ]
    )
    agent.orchestrator.start_turn(1)
    emit and emit(
        AgentEvent(
            "status",
            f"WhatsApp fast execution: {len(steps)} verified step(s) compiled; model round-trips skipped.",
        )
    )

    completed: list[str] = []
    for step in steps:
        if agent._is_stopped():
            result = "CANCELLED: Emergency stop is active"
            agent.orchestrator.finish("cancelled", result)
            agent.memory.add("assistant", result)
            return result

        agent.orchestrator.update_step(step.id, "running")
        emit and emit(AgentEvent("tool", f"Fast step: {step.description}", step.tool))
        started = time.perf_counter()
        approved = agent.approval(step.tool, step.arguments)
        result = agent._execute_tool(step.tool, dict(step.arguments), approved=approved)
        duration_ms = (time.perf_counter() - started) * 1000.0
        mutation = agent._is_mutation(step.tool)
        mutation = mutation and not str(result).startswith(
            ("PERMISSION_DENIED", "ERROR: Observe the last")
        )
        agent.orchestrator.record_tool(
            step.tool,
            dict(step.arguments),
            result,
            duration_ms,
            1,
            mutation=mutation,
        )
        emit and emit(AgentEvent("tool_result", result, step.tool))

        if str(result).startswith("BROWSER_ACTION_BLOCKED:"):
            message = (
                "Human verification is required in the foreground browser. JARVIS stopped without using desktop input as a bypass."
            )
            agent.orchestrator.update_step(step.id, "failed", message)
            agent.orchestrator.finish("waiting_user", message)
            agent.memory.add("assistant", message)
            return message

        if not tool_succeeded(result):
            agent.orchestrator.update_step(step.id, "failed", str(result))
            message = f"WhatsApp fast execution stopped at '{step.description}': {result}"
            agent.orchestrator.finish("incomplete", message)
            agent.memory.add("assistant", message)
            return message

        if mutation:
            if not str(result).startswith("VERIFIED:"):
                agent.orchestrator.update_step(step.id, "failed", str(result))
                message = f"WhatsApp fast execution refused an unverified mutation at '{step.description}'."
                agent.orchestrator.finish("incomplete", message)
                agent.memory.add("assistant", message)
                return message
            agent.orchestrator.verify(
                f"{step.id}: {step.description}", True, str(result)
            )

        agent.orchestrator.update_step(step.id, "completed", str(result))
        completed.append(step.description)

    message = "Completed and verified: " + " → ".join(completed)
    agent.orchestrator.finish("completed", message)
    agent.memory.add("assistant", message)
    emit and emit(AgentEvent("status", message))
    return message
