from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .agent import AgentEvent
from .orchestrator import PlanStep, tool_succeeded


@dataclass(frozen=True)
class FastStep:
    id: str
    description: str
    tool: str
    arguments: dict[str, Any]


_GOOGLE_FIRST = re.compile(
    r"^\s*(?:(?:open|create)\s+(?:a\s+)?new\s+(?:browser\s+)?tab\s+(?:in|on|with)\s+google\s+(?:and\s+)?)?"
    r"(?:google\s+)?search(?:\s+for)?\s+(?P<query>.+?)\s+"
    r"(?:and\s+)?(?:press|click|open)\s+(?:on\s+)?(?:the\s+)?first\s+(?:link|result)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_GOOGLE_SEARCH = re.compile(
    r"^\s*(?:(?:open|create)\s+(?:a\s+)?new\s+(?:browser\s+)?tab\s+(?:in|on|with)\s+google\s+(?:and\s+)?)?"
    r"(?:google\s+)?search(?:\s+for)?\s+(?P<query>.+?)(?P<rest>\s+(?:(?:and\s+then|then)\s+open\b.*)|\s*)$",
    re.IGNORECASE | re.DOTALL,
)
_APP_PREFIX = re.compile(r"^\s*(?:(?:and\s+then|then|and)\s+)?open\s+", re.IGNORECASE)
_NEXT_APP = re.compile(r"\s+(?:and\s+then|then|and)\s+open\s+", re.IGNORECASE)
_TRAILING_APP_WORD = re.compile(r"\s+(?:app|application)\s*$", re.IGNORECASE)
_NEW_TAB_SIGNAL = re.compile(r"\b(?:new|another)\s+(?:browser\s+)?tab\b", re.IGNORECASE)
_EXTRA_ACTION = re.compile(r"(?:\b(?:and(?:\s+then)?|then)\s+|[;→]|->)\s*(?:open|navigate|go|send|write|type|play|pause|select|switch|join|save|delete|search|click|press|close|read|find|download|upload)\b", re.IGNORECASE)


def _enabled() -> bool:
    return os.getenv("JARVIS_FAST_EXECUTION", "true").strip().casefold() not in {"0", "false", "off", "no"}


def _clean_app_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip(" ,.;"))
    text = _TRAILING_APP_WORD.sub("", text).strip()
    if not text or len(text) > 120 or "\0" in text:
        raise ValueError("Fast app step contains an invalid application name")
    return text


def _parse_app_chain(rest: str, start_index: int) -> list[FastStep] | None:
    remaining = str(rest or "").strip()
    if not remaining:
        return []

    match = _APP_PREFIX.match(remaining)
    if not match:
        return None
    remaining = remaining[match.end():]

    pieces = _NEXT_APP.split(remaining)
    if not pieces or any(not piece.strip() for piece in pieces):
        return None

    steps: list[FastStep] = []
    for offset, piece in enumerate(pieces):
        # A fast app clause must be only an app name plus optional app/application.
        # Reject prose-like clauses so unknown work is handed back to the intelligent agent.
        if re.search(r"\b(?:search|click|press|type|write|send|close|login|log\s+in|download|upload|navigate|join|play|pause|and|then)\b|[;→]|->", piece, re.IGNORECASE):
            return None
        try:
            app = _clean_app_name(piece)
        except ValueError:
            return None
        index = start_index + offset
        steps.append(
            FastStep(
                id=f"fast-{index}",
                description=f"Open and verify {app}",
                tool="launch_installed_app",
                arguments={"query": app, "timeout_seconds": 12},
            )
        )
    return steps if len(steps) <= 32 else None


def compile_fast_mission(goal: str) -> list[FastStep] | None:
    """Compile only fully understood low-ambiguity missions; return None for intelligent fallback."""
    if not _enabled():
        return None
    text = re.sub(r"\s+", " ", str(goal or "")).strip()
    if not text or len(text) > 8000:
        return None

    first = _GOOGLE_FIRST.match(text)
    if first:
        query = first.group("query").strip(" ,.;")
        if not query or len(query) > 500 or _EXTRA_ACTION.search(query):
            return None
        new_tab = bool(_NEW_TAB_SIGNAL.search(text[: first.start("query")]))
        steps = [
            FastStep(
                id="fast-1",
                description=f"Search Google for {query} and open the first real result",
                tool="browser_google_search_first_result",
                arguments={"query": query, "new_tab": new_tab, "preserve_search_tab": False},
            )
        ]
        apps = _parse_app_chain(first.group("rest"), 2)
        if apps is None:
            return None
        return [*steps, *apps]

    search = _GOOGLE_SEARCH.match(text)
    if search:
        query = search.group("query").strip(" ,.;")
        if not query or len(query) > 500 or _EXTRA_ACTION.search(query):
            return None
        new_tab = bool(_NEW_TAB_SIGNAL.search(text[: search.start("query")]))
        steps = [
            FastStep(
                id="fast-1",
                description=f"Search Google for {query}",
                tool="google_search",
                arguments={"query": query, "new_tab": new_tab},
            )
        ]
        apps = _parse_app_chain(search.group("rest"), 2)
        if apps is None:
            return None
        return [*steps, *apps]

    apps = _parse_app_chain(text, 1)
    if apps:
        return apps
    return None


def execute_fast_mission(agent: Any, goal: str, emit: Callable[[AgentEvent], None] | None = None) -> str | None:
    """Execute a compiled mission without model round-trips while preserving normal policy and traces."""
    steps = compile_fast_mission(goal)
    if not steps:
        return None

    agent.orchestrator.begin(goal)
    from .full_access_agent import _chrome_tab_rows
    agent._mission_initial_tab_count = len(_chrome_tab_rows())
    agent.orchestrator.current.metrics["fast_compiled_steps"] = len(steps)
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
    emit and emit(AgentEvent("status", f"Fast execution: {len(steps)} verified step(s) compiled; model round-trips skipped."))

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
        mutation = mutation and not str(result).startswith(("PERMISSION_DENIED", "ERROR: Observe the last"))
        agent.orchestrator.record_tool(step.tool, dict(step.arguments), result, duration_ms, 1, mutation=mutation)
        emit and emit(AgentEvent("tool_result", result, step.tool))

        if str(result).startswith("BROWSER_ACTION_BLOCKED:"):
            message = (
                "Human verification is required in the current browser tab. JARVIS paused the fast mission "
                "without attempting to bypass the checkpoint. Complete it manually, then continue."
            )
            agent.orchestrator.update_step(step.id, "failed", message)
            agent.orchestrator.finish("waiting_user", message)
            agent.memory.add("assistant", message)
            return message

        if not tool_succeeded(result):
            agent.orchestrator.update_step(step.id, "failed", str(result))
            message = f"Fast execution stopped at '{step.description}': {result}"
            agent.orchestrator.finish("incomplete", message)
            agent.memory.add("assistant", message)
            return message

        if mutation:
            if not str(result).startswith("VERIFIED:"):
                agent.orchestrator.update_step(step.id, "failed", str(result))
                message = f"Fast execution refused an unverified mutation at '{step.description}'."
                agent.orchestrator.finish("incomplete", message)
                agent.memory.add("assistant", message)
                return message
            agent.orchestrator.verify(f"{step.id}: {step.description}", True, str(result))

        agent.orchestrator.update_step(step.id, "completed", str(result))
        completed.append(step.description)

    message = "Completed and verified: " + " → ".join(completed)
    agent.orchestrator.finish("completed", message)
    agent.memory.add("assistant", message)
    emit and emit(AgentEvent("status", message))
    return message
