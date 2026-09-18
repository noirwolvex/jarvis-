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
_TRAILING_TYPE = re.compile(
    r"\s+(?:(?:and\s+then|then|and)\s+)(?:write|type)(?:\s+text)?\s+(?P<text>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NEW_TAB_SIGNAL = re.compile(r"\b(?:new|another)\s+(?:browser\s+)?tab\b", re.IGNORECASE)
_EXTRA_ACTION = re.compile(
    r"(?:\b(?:and(?:\s+then)?|then)\s+|[;→]|->)\s*"
    r"(?:open|navigate|go|send|write|type|play|pause|select|switch|join|save|delete|search|click|press|close|read|find|download|upload)\b",
    re.IGNORECASE,
)
_EXPLICIT_SEQUENCE = re.compile(
    r"\s*(?:;|(?:,\s*)?(?:and\s+then|then))\s*",
    re.IGNORECASE,
)
_MIXED_GOOGLE_SEARCH = re.compile(
    r"^(?:(?:open|use)\s+(?:(?:a|another)\s+)?(?:(?:new)\s+)?(?:(?:browser)\s+)?(?:(?:tab)\s+)?"
    r"(?:(?:in|on|with)\s+)?(?:google|chrome|google\s+chrome)(?:\s+(?:app|application))?\s+(?:and\s+)?)?"
    r"(?:google\s+)?search(?:\s+for)?\s+(?P<query>.+)$",
    re.IGNORECASE,
)
_MIXED_APP_CHAT = re.compile(
    r"^open\s+(?:the\s+)?(?:whatsapp)(?:\s+(?:app|application))?\s+and\s+"
    r"(?:press|click|open|select|choose|tap)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)"
    r"\s+(?:chat|conversation)$",
    re.IGNORECASE,
)
_MIXED_CHAT_ONLY = re.compile(
    r"^(?:press|click|open|select|choose|tap)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)"
    r"\s+(?:chat|conversation)$",
    re.IGNORECASE,
)
_MIXED_APP_ONLY = re.compile(
    r"^open\s+(?:the\s+)?(?P<app>[A-Za-z0-9][A-Za-z0-9 .+_-]{0,79}?)(?:\s+(?:app|application))?$",
    re.IGNORECASE,
)
_MIXED_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


_APP_ALIASES = {
    "المفكرة": "Notepad",
    "نوت باد": "Notepad",
    "الحاسبة": "Calculator",
    "الآلة الحاسبة": "Calculator",
    "ديسكورد": "Discord",
    "الديسكورد": "Discord",
    "واتساب": "WhatsApp",
    "واتس اب": "WhatsApp",
    "كروم": "Chrome",
    "جوجل كروم": "Chrome",
    "قوقل كروم": "Chrome",
    "فيجوال ستوديو كود": "Visual Studio Code",
}


def _simple_semantic_steps(text: str) -> list[FastStep] | None:
    """Strict whole-clause grammar. Unknown work always goes to the model intact."""
    playback = {
        "شغل الفيديو الحالي": "play",
        "شغّل الفيديو الحالي": "play",
        "استكمل الفيديو الحالي": "play",
        "اوقف الفيديو مؤقتا": "pause",
        "أوقف الفيديو مؤقتا": "pause",
        "وقف الفيديو الحالي": "pause",
        "play the current youtube video": "play",
        "pause the current youtube video": "pause",
    }
    steps = []
    for index, clause in enumerate(re.split(r"\s+(?:ثم|وبعدين|بعدين)\s+", text), 1):
        if not clause or index > 32:
            return None
        action = playback.get(clause.casefold())
        tool, arguments = "", {}
        if action:
            tool, arguments = "youtube_playback", {"action": action}
        elif match := re.fullmatch(r"(?:افتح|إفتح)\s+(?:تطبيق\s+|برنامج\s+)?(.+)", clause):
            app = match.group(1).strip()
            # Arabic aliases and simple literal English app names only. Trailing
            # Arabic action prose cannot be misinterpreted as part of an app name.
            if app in _APP_ALIASES:
                app = _APP_ALIASES[app]
            elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+_-]{0,79}", app) or re.search(
                r"\b(and|then|send|type|open|play|search)\b", app, re.I
            ):
                return None
            tool, arguments = "launch_installed_app", {"query": app, "timeout_seconds": 12}
        elif match := re.fullmatch(r'(?:ابحث|إبحث)\s+في\s+(?:جوجل|قوقل|غوغل)\s+عن\s+["«](.+)["»]', clause):
            query = match.group(1)
            if len(query) > 500 or any(char in query for char in '"«»\0'):
                return None
            tool, arguments = "google_search", {"query": query, "new_tab": False}
        elif match := re.fullmatch(r'(?:شغل|شغّل|play)\s+["«](.+)["»]\s+(?:على يوتيوب|on youtube)', clause, re.I):
            query = match.group(1)
            if len(query) > 500 or any(char in query for char in '"«»\0'):
                return None
            tool, arguments = "youtube_search_open", {"query": query, "new_tab": False, "play": True}
        else:
            return None
        steps.append(FastStep(f"fast-{index}", clause, tool, arguments))
    return steps


def _mixed_ordinal(value: str) -> int:
    normalized = str(value).strip().casefold()
    if normalized in _MIXED_ORDINALS:
        return _MIXED_ORDINALS[normalized]
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)", normalized)
    if not match:
        raise ValueError("Unsupported chat ordinal")
    position = int(match.group(1))
    if not 1 <= position <= 20:
        raise ValueError("Chat ordinal must be between 1 and 20")
    return position


def _compile_explicit_sequence(text: str) -> list[FastStep] | None:
    """Compile explicit English then-chains without model calls.

    This grammar is deliberately narrow: app launches, WhatsApp ordinal selection and
    Google search. Any unknown or side-effectful clause falls back to the intelligent
    planner intact rather than being guessed.
    """
    if not re.search(r"\b(?:and\s+then|then)\b|;", text, re.IGNORECASE):
        return None
    clauses = [part.strip(" ,.;") for part in _EXPLICIT_SEQUENCE.split(text) if part.strip(" ,.;")]
    if not 2 <= len(clauses) <= 32:
        return None

    steps: list[FastStep] = []
    last_app = ""
    for clause in clauses:
        google = _MIXED_GOOGLE_SEARCH.fullmatch(clause)
        if google:
            query = google.group("query").strip(" ,.;")
            if (
                not query
                or len(query) > 500
                or "\0" in query
                or _EXTRA_ACTION.search(query)
            ):
                return None
            new_tab = bool(_NEW_TAB_SIGNAL.search(clause))
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Search Google for {query}",
                tool="google_search",
                arguments={"query": query, "new_tab": new_tab},
            ))
            last_app = "browser"
            continue

        app_chat = _MIXED_APP_CHAT.fullmatch(clause)
        if app_chat:
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description="Open and verify WhatsApp",
                tool="launch_installed_app",
                arguments={"query": "WhatsApp", "timeout_seconds": 12},
            ))
            try:
                position = _mixed_ordinal(app_chat.group("ordinal"))
            except ValueError:
                return None
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Select and verify WhatsApp chat position {position}",
                tool="whatsapp_select_chat_native",
                arguments={"position": position},
            ))
            last_app = "whatsapp"
            continue

        chat = _MIXED_CHAT_ONLY.fullmatch(clause)
        if chat:
            if last_app != "whatsapp":
                return None
            try:
                position = _mixed_ordinal(chat.group("ordinal"))
            except ValueError:
                return None
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Select and verify WhatsApp chat position {position}",
                tool="whatsapp_select_chat_native",
                arguments={"position": position},
            ))
            continue

        app_match = _MIXED_APP_ONLY.fullmatch(clause)
        if app_match:
            app = app_match.group("app").strip()
            # Browser launches are routed through guarded CDP operations. Bare browser
            # clauses need model/browser routing unless they include a deterministic search.
            if app.replace(" ", "").casefold() in {"google", "chrome", "googlechrome"}:
                return None
            try:
                app = _clean_app_name(app)
            except ValueError:
                return None
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Open and verify {app}",
                tool="launch_installed_app",
                arguments={"query": app, "timeout_seconds": 12},
            ))
            last_app = app.replace(" ", "").casefold()
            continue

        return None

    return steps if steps and len(steps) <= 32 else None


def _enabled() -> bool:
    return os.getenv("JARVIS_FAST_EXECUTION", "true").strip().casefold() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _clean_app_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip(" ,.;"))
    text = _TRAILING_APP_WORD.sub("", text).strip()
    if not text or len(text) > 120 or "\0" in text:
        raise ValueError("Fast app step contains an invalid application name")
    return text


def _split_trailing_type(text: str) -> tuple[str, str | None]:
    """Detach one final explicit write/type clause without swallowing later actions."""
    match = _TRAILING_TYPE.search(text)
    if not match:
        return text, None

    prefix = text[: match.start()].strip()
    raw = match.group("text").strip()
    if not prefix or not raw:
        raise ValueError("Fast native type clause is incomplete")

    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}
    if quoted:
        value = raw[1:-1]
    else:
        # Unquoted text may contain ordinary words, but a second executable clause must
        # fall back to the intelligent planner rather than being typed accidentally.
        if _EXTRA_ACTION.search(raw):
            raise ValueError("Fast native type clause contains another action")
        if raw[:1] in {'"', "'"} or raw[-1:] in {'"', "'"}:
            raise ValueError("Fast native type clause has unmatched quotes")
        value = raw

    if not value or len(value) > 4096 or "\0" in value:
        raise ValueError("Fast native type text must contain 1-4096 safe characters")
    return prefix, value


def _append_native_type(steps: list[FastStep], text: str | None) -> list[FastStep]:
    if text is None:
        return steps
    if not steps or len(steps) >= 32:
        raise ValueError("Fast native type requires a preceding verified step")
    return [
        *steps,
        FastStep(
            id=f"fast-{len(steps) + 1}",
            description="Type the requested text into the active verified editor through Rust",
            tool="ui_type_native",
            arguments={"text": text},
        ),
    ]


def _parse_app_chain(rest: str, start_index: int) -> list[FastStep] | None:
    remaining = str(rest or "").strip()
    if not remaining:
        return []

    match = _APP_PREFIX.match(remaining)
    if not match:
        return None
    remaining = remaining[match.end() :]

    pieces = _NEXT_APP.split(remaining)
    if not pieces or any(not piece.strip() for piece in pieces):
        return None

    steps: list[FastStep] = []
    for offset, piece in enumerate(pieces):
        # A fast app clause must be only an app name plus optional app/application.
        # Reject prose-like clauses so unknown work is handed back to the intelligent agent.
        if re.search(
            r"\b(?:search|click|press|type|write|send|close|login|log\s+in|download|upload|navigate|join|play|pause|and|then)\b|[;→]|->",
            piece,
            re.IGNORECASE,
        ):
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
    if "\0" in text:
        return None

    mixed = _compile_explicit_sequence(text)
    if mixed:
        return mixed

    try:
        base_text, trailing_type = _split_trailing_type(text)
    except ValueError:
        return None

    simple = _simple_semantic_steps(base_text)
    if simple:
        try:
            return _append_native_type(simple, trailing_type)
        except ValueError:
            return None

    first = _GOOGLE_FIRST.match(base_text)
    if first:
        query = first.group("query").strip(" ,.;")
        if not query or len(query) > 500 or _EXTRA_ACTION.search(query):
            return None
        new_tab = bool(_NEW_TAB_SIGNAL.search(base_text[: first.start("query")]))
        steps = [
            FastStep(
                id="fast-1",
                description=f"Search Google for {query} and open the first real result",
                tool="browser_google_search_first_result",
                arguments={
                    "query": query,
                    "new_tab": new_tab,
                    "preserve_search_tab": False,
                },
            )
        ]
        apps = _parse_app_chain(first.group("rest"), 2)
        if apps is None:
            return None
        try:
            return _append_native_type([*steps, *apps], trailing_type)
        except ValueError:
            return None

    search = _GOOGLE_SEARCH.match(base_text)
    if search:
        query = search.group("query").strip(" ,.;")
        if not query or len(query) > 500 or _EXTRA_ACTION.search(query):
            return None
        new_tab = bool(_NEW_TAB_SIGNAL.search(base_text[: search.start("query")]))
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
        try:
            return _append_native_type([*steps, *apps], trailing_type)
        except ValueError:
            return None

    apps = _parse_app_chain(base_text, 1)
    if apps:
        try:
            return _append_native_type(apps, trailing_type)
        except ValueError:
            return None
    return None


def execute_fast_mission(
    agent: Any, goal: str, emit: Callable[[AgentEvent], None] | None = None
) -> str | None:
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
    emit and emit(
        AgentEvent(
            "status",
            f"Fast execution: {len(steps)} verified step(s) compiled; model round-trips skipped.",
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
                message = (
                    f"Fast execution refused an unverified mutation at '{step.description}'."
                )
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
