from __future__ import annotations

from .execution_telemetry import input_not_dispatched

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .agent import AgentEvent
from .orchestrator import PlanStep, tool_succeeded


@dataclass(frozen=True)
class FastStep:
    id: str
    description: str
    tool: str
    arguments: dict[str, Any]


_GOOGLE_PREFIX = (
    r"(?:please\s+)?(?:(?:(?:open|create)\s+(?:(?:a\s+)?new|another)\s+(?:browser\s+)?tab"
    r"(?:\s+(?:in|on|with)\s+(?:google\s+chrome|google|chrome))?"
    r"|(?:open|use)\s+(?:google\s+chrome|google|chrome)(?:\s+(?:app|application))?)\s+(?:and\s+)?)?"
)
_GOOGLE_QUERY = (
    r'''(?P<query>(?:"[^"]+"|'[^']+')(?:\s+(?:in|on|using|with)\s+(?:google\s+chrome|google|chrome))?|[^"'].*?)'''
)
_GOOGLE_FIRST = re.compile(
    r"^\s*" + _GOOGLE_PREFIX +
    r"(?:google\s+)?search(?:\s+for)?\s+" + _GOOGLE_QUERY + r"\s+"
    r"(?:and\s+)?(?:press|click|open)\s+(?:on\s+)?(?:the\s+)?first\s+(?:link|result)\b(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_GOOGLE_SEARCH = re.compile(
    r"^\s*" + _GOOGLE_PREFIX +
    r"(?:google\s+)?search(?:\s+for)?\s+" + _GOOGLE_QUERY + r"(?P<rest>\s+(?:(?:and\s+then|then)\s+open\b.*)|\s*)$",
    re.IGNORECASE | re.DOTALL,
)
_APP_PREFIX = re.compile(r"^\s*(?:(?:and\s+then|then|and)\s+)?(?:please\s+)?(?:open|launch|start)\s+", re.IGNORECASE)
_NEXT_APP = re.compile(r"\s+(?:and\s+then|then|and)\s+(?:please\s+)?(?:open|launch|start)\s+", re.IGNORECASE)
_TRAILING_APP_WORD = re.compile(r"\s+(?:app|application)\s*$", re.IGNORECASE)
_TRAILING_TYPE = re.compile(
    r"\s+(?:(?:and\s+then|then|and)\s+)(?:write|type)(?:\s+text)?\s+(?P<text>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TRAILING_TYPE_AFTER = re.compile(
    r"\s+(?:(?:and\s+then|then|and|(?:and\s+)?after(?:\s+that)?)\s+)"
    r"(?:write|type)(?:\s+text)?\s+(?P<text>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_NEW_TAB_SIGNAL = re.compile(r"\b(?:new|another)\s+(?:browser\s+)?tab\b", re.IGNORECASE)
_EXTRA_ACTION = re.compile(
    r"(?:\b(?:and(?:\s+then)?|then|after(?:\s+that)?)\s+|[;→]|->)\s*"
    r"(?:please\s+)?(?:open|launch|start|navigate|go|send|write|type|play|pause|select|switch|join|save|delete|search|click|press|close|read|find|download|upload|purchase|buy|remove|rename|move|copy|create|run|execute)\b",
    re.IGNORECASE,
)
_EXPLICIT_SEQUENCE = re.compile(
    r"\s*(?:;|(?:,\s*)?\b(?:and\s+then|then|(?:and\s+)?after(?:\s+that)?(?=\s+(?:please\s+)?(?:open|launch|start|press|click|select|write|type|search)\b))\b)\s*",
    re.IGNORECASE,
)
_MIXED_GOOGLE_SEARCH = re.compile(
    r"^" + _GOOGLE_PREFIX +
    r"(?:google\s+)?search(?:\s+for)?\s+(?P<query>.+)$",
    re.IGNORECASE,
)
_MIXED_APP_CHAT = re.compile(
    r"^open\s+(?:the\s+)?(?P<app>whatsapp|discord)(?:\s+(?:app|application))?\s+(?:and\s+then|and|then)\s+"
    r"(?:press|click|open|select|choose|tap)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)"
    r"\s+(?:chat|conversation)$",
    re.IGNORECASE,
)
_DISCORD_ORDINAL_WRITE_SEND = re.compile(
    r"^\s*open\s+(?:the\s+)?discord(?:\s+(?:app|application))?\s+(?:and\s+then|and|then)\s+"
    r"(?:press|click|open|select|choose|tap)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)"
    r"\s+(?:chat|conversation)\s+(?:(?:and\s+then|then|and|after(?:\s+that)?)\s+)"
    r"(?:write|type)(?:\s+text)?\s+(?P<text>.+?)\s+"
    r"(?:(?:and\s+then|then|and)\s+)?send\s+(?:it|that|the\s+message)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_MIXED_CHAT_ONLY = re.compile(
    r"^(?:press|click|open|select|choose|tap)(?:\s+on)?\s+(?:the\s+)?"
    r"(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th)?)"
    r"\s+(?:chat|conversation)$",
    re.IGNORECASE,
)
_MIXED_APP_ONLY = re.compile(
    r"^(?:please\s+)?(?:open|launch|start)\s+(?:the\s+)?(?P<app>[A-Za-z0-9][A-Za-z0-9 .+_-]{0,79}?)(?:\s+(?:app|application))?$",
    re.IGNORECASE,
)
_APP_NAME_PROSE = re.compile(
    r"\b(?:and|then|or|but|if|unless|when|before|after|while|with|without|to|in|on|for|"
    r"please|not|don't|open|launch|start|search|click|press|type|write|send|close|login|"
    r"download|upload|navigate|join|play|pause|save|delete|read|find)\b",
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


def _literal_action(text: str, index: int = 1) -> FastStep | None:
    """Compile complete literal requests only; never infer a target or text boundary."""
    typing = re.fullmatch(
        r'''(?:please\s+)?(?:type|write|اكتب|أكتب)\s+(?:text\s+)?(?P<literal>"[^"]+"|'[^']+')'''
        r'''(?:\s+(?:in|في)\s+(?P<window>"[^"]+"|'[^']+'))?''', text, re.I)
    if typing:
        value = typing.group("literal")[1:-1]
        title = (typing.group("window") or "")[1:-1]
        if len(value) > 4096 or any(char in value for char in "\r\n\0"):
            return None  # Line breaks can submit in chat; use context-aware planning.
        if len(title) > 120 or any(char in title for char in "\r\n\0"):
            return None
        return FastStep(f"fast-{index}", "Type and verify the exact unsent text",
                        "interaction_type", {"text": value, "submit": False,
                                             **({"title": title} if title else {})})

    navigation = re.fullmatch(
        r'''(?:please\s+)?(?:open|go\s+to|navigate\s+to|افتح)\s+(?P<url>https?://[^\s<>"']+)'''
        r"(?:\s+(?P<new_tab>in\s+(?:a\s+)?new\s+tab|في\s+تبويب\s+جديد))?", text, re.I)
    if navigation:
        url = navigation.group("url")
        try:
            parsed = urlparse(url)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.port == 0 or len(url) > 4096 or any(ord(char) < 32 for char in url)):
                return None
        except ValueError:
            return None
        return FastStep(f"fast-{index}", "Open and verify the requested website",
                        "chrome_new_tab" if navigation.group("new_tab") else "browser_navigate",
                        {"url": url})
    return None


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
    match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", normalized)
    if not match:
        raise ValueError("Unsupported chat ordinal")
    position = int(match.group(1))
    if not 1 <= position <= 20:
        raise ValueError("Chat ordinal must be between 1 and 20")
    return position


def _google_query(value: str) -> str | None:
    """Separate an explicit engine suffix from query data without eating later work."""
    raw = str(value).strip(" ,.;")
    raw = re.sub(r"\s+(?:in|on|using|with)\s+(?:google\s+chrome|google|chrome)$", "", raw, flags=re.I)
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}
    if quoted:
        query = raw[1:-1]
        # A closing quote followed by another clause is not a single literal.
        if raw[0] in query:
            return None
    else:
        query = raw
        if (query[:1] in {'"', "'"} or query[-1:] in {'"', "'"}
                or _EXTRA_ACTION.search(query)
                or re.search(r"\b(?:and(?:\s+then)?|then|after(?:\s+that)?)$", query, re.I)):
            return None
    if not query.strip() or len(query) > 500 or any(char in query for char in "\r\n\0"):
        return None
    return query


def _compile_explicit_sequence(text: str) -> list[FastStep] | None:
    """Compile explicit English then-chains without model calls.

    This grammar is deliberately narrow: app launches, WhatsApp/Discord ordinal selection and
    Google search. Any unknown or side-effectful clause falls back to the intelligent
    planner intact rather than being guessed.
    """
    if not _EXPLICIT_SEQUENCE.search(text):
        return None
    # Preserve quoted drafts/search terms; sequence words inside literal text are
    # data, never additional mission steps.
    quoted = set()
    quote = ""
    for index, char in enumerate(text):
        if quote:
            quoted.add(index)
            if char == quote:
                quote = ""
        elif char in {'"', "'"} and (index == 0 or text[index - 1].isspace()):
            quote = char
            quoted.add(index)
    if quote:
        return None
    boundaries = [match for match in _EXPLICIT_SEQUENCE.finditer(text)
                  if not any(index in quoted for index in range(match.start(), match.end()))]
    if not boundaries:
        return None
    def clean_clause(value: str) -> str:
        stripped = value.strip()
        # URL path/query punctuation is data. Do not change the destination when
        # navigation is part of a longer sequence.
        if re.match(r"(?:please\s+)?(?:open|go\s+to|navigate\s+to|افتح)\s+https?://", stripped, re.I):
            return stripped
        return stripped.strip(" ,.;")

    clauses, start = [], 0
    for match in boundaries:
        clauses.append(clean_clause(text[start:match.start()]))
        start = match.end()
    clauses.append(clean_clause(text[start:]))
    if any(not clause for clause in clauses):
        return None
    if not 2 <= len(clauses) <= 32:
        return None

    steps: list[FastStep] = []
    last_app = ""
    for clause in clauses:
        literal = _literal_action(clause, len(steps) + 1)
        if literal:
            if literal.tool == "interaction_type" and last_app and last_app != "browser":
                literal.arguments.setdefault("title", {"whatsapp": "WhatsApp", "discord": "Discord"}.get(last_app, last_app))
                literal.arguments["surface"] = "desktop"
            steps.append(literal)
            if literal.tool in {"browser_navigate", "chrome_new_tab"}:
                last_app = "browser"
            elif literal.arguments.get("title"):
                last_app = literal.arguments["title"].casefold()
            continue
        google = _MIXED_GOOGLE_SEARCH.fullmatch(clause)
        if google:
            query = _google_query(google.group("query"))
            if query is None:
                return None
            new_tab = bool(_NEW_TAB_SIGNAL.search(clause[:google.start("query")]))
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Search Google for {query}",
                tool="google_search",
                arguments={"query": query, "new_tab": new_tab},
            ))
            last_app = "browser"
            continue

        try:
            chat_clause, draft = _split_trailing_type(clause, allow_after=True)
        except ValueError:
            return None
        app_chat = _MIXED_APP_CHAT.fullmatch(chat_clause)
        if app_chat:
            app = app_chat.group("app").casefold()
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Open and verify {app.title()}",
                tool="launch_installed_app",
                arguments={"query": "WhatsApp" if app == "whatsapp" else "Discord", "timeout_seconds": 12},
            ))
            try:
                position = _mixed_ordinal(app_chat.group("ordinal"))
            except ValueError:
                return None
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Select and verify {app.title()} chat position {position}",
                tool="whatsapp_select_chat_native" if app == "whatsapp" else "discord_select_chat",
                arguments={"position": position},
            ))
            last_app = app
            if draft is not None:
                steps.append(FastStep(
                    id=f"fast-{len(steps) + 1}",
                    description=f"Write and verify the unsent draft in {app.title()}",
                    tool="interaction_type",
                    arguments={"text": draft, "title": "WhatsApp" if app == "whatsapp" else "Discord", "submit": False},
                ))
            continue

        chat = _MIXED_CHAT_ONLY.fullmatch(clause)
        if chat:
            if last_app not in {"whatsapp", "discord"}:
                return None
            try:
                position = _mixed_ordinal(chat.group("ordinal"))
            except ValueError:
                return None
            steps.append(FastStep(
                id=f"fast-{len(steps) + 1}",
                description=f"Select and verify {last_app.title()} chat position {position}",
                tool="whatsapp_select_chat_native" if last_app == "whatsapp" else "discord_select_chat",
                arguments={"position": position},
            ))
            continue

        app_match = _MIXED_APP_ONLY.fullmatch(clause)
        if app_match:
            if _EXTRA_ACTION.search(clause):
                return None
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
            last_app = app.casefold()
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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .+_-]{0,79}", text) or _APP_NAME_PROSE.search(text):
        raise ValueError("Fast app step contains an invalid application name")
    return text


def _split_trailing_type(text: str, *, allow_after: bool = False) -> tuple[str, str | None]:
    """Detach one final explicit write/type clause without swallowing later actions."""
    match = (_TRAILING_TYPE_AFTER if allow_after else _TRAILING_TYPE).search(text)
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
            description="Enter and verify the requested text in the active editor",
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
    text = str(goal or "").strip()
    if not text or len(text) > 8000:
        return None
    if "\0" in text:
        return None

    literal = _literal_action(text)
    if literal:
        return [literal]

    # An explicit File Explorer → Downloads request is a local OS operation.
    # Match the whole goal: never drop a trailing instruction or guess a folder.
    downloads = re.fullmatch(
        r"(?:please\s+)?(?:open|launch|start)\s+(?:the\s+)?"
        r"(?:file\s+explor(?:er|e)|windows\s+explorer|explorer)"
        r"\s+(?:and(?:\s+then)?|then)\s+"
        r"(?:(?:go|navigate)\s+to|open|press|click)\s+"
        r"(?:the\s+)?downloads(?:\s+(?:folder|section))?",
        text, re.I,
    )
    if downloads:
        return [FastStep("fast-1", "Open and verify File Explorer Downloads",
                         "open_known_folder", {"folder": "Downloads", "timeout_seconds": 8})]

    discord_send = _DISCORD_ORDINAL_WRITE_SEND.fullmatch(text)
    if discord_send:
        try:
            position = _mixed_ordinal(discord_send.group("ordinal"))
        except ValueError:
            return None
        message = discord_send.group("text").strip()
        quoted = len(message) >= 2 and message[0] == message[-1] and message[0] in {'"', "'"}
        if quoted:
            message = message[1:-1]
        if (
            not message
            or len(message) > 4000
            or "\0" in message
            or _EXTRA_ACTION.search(message)
            or message[:1] in {'"', "'"}
            or message[-1:] in {'"', "'"}
        ):
            return None
        return [
            FastStep(
                "fast-1",
                "Open and verify Discord",
                "launch_installed_app",
                {"query": "Discord", "timeout_seconds": 12},
            ),
            FastStep(
                "fast-2",
                f"Select and verify Discord chat position {position}",
                "discord_select_chat",
                {"position": position},
            ),
            FastStep(
                "fast-3",
                "Write and send the requested Discord message once, then verify delivery",
                "discord_send_message",
                {"text": message},
            ),
        ]

    mixed = _compile_explicit_sequence(text)
    if mixed:
        return mixed

    try:
        base_text, trailing_type = _split_trailing_type(text, allow_after=True)
    except ValueError:
        return None

    chat = _MIXED_APP_CHAT.fullmatch(base_text)
    if chat and chat.group("app").casefold() == "discord":
        try:
            position = _mixed_ordinal(chat.group("ordinal"))
            steps = [FastStep("fast-1", "Open and verify Discord", "launch_installed_app",
                              {"query": "Discord", "timeout_seconds": 12}),
                     FastStep("fast-2", f"Select and verify Discord chat position {position}",
                              "discord_select_chat", {"position": position})]
            if trailing_type is not None:
                steps.append(FastStep("fast-3", "Enter and verify the requested Discord draft", "interaction_type",
                                      {"text": trailing_type, "title": "Discord", "surface": "desktop"}))
            return steps
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
        query = _google_query(first.group("query"))
        if query is None:
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
        query = _google_query(search.group("query"))
        if query is None:
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
    from .browser_mission_contract import required_new_tabs
    from .full_access_agent import _chrome_tab_rows

    agent._mission_initial_tab_count = len(_chrome_tab_rows()) if required_new_tabs(goal) else 0
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
    verified_discord_destination = ""
    for step in steps:
        if agent._is_stopped():
            result = "CANCELLED: Emergency stop is active"
            agent.orchestrator.finish("cancelled", result)
            agent.memory.add("assistant", result)
            return result

        agent.orchestrator.update_step(step.id, "running")
        emit and emit(AgentEvent("tool", f"Fast step: {step.description}", step.tool))
        started = time.perf_counter()
        mutation = agent._is_mutation(step.tool)
        arguments = dict(step.arguments)
        if (
            step.tool == "discord_send_message"
            and verified_discord_destination
            and not arguments.get("destination")
        ):
            arguments["destination"] = verified_discord_destination
        approved = agent.approval(step.tool, arguments)
        result = agent._execute_tool(step.tool, arguments, approved=approved)
        duration_ms = (time.perf_counter() - started) * 1000.0
        mutation = mutation and not str(result).startswith(
            ("PERMISSION_DENIED", "ERROR: Observe the last")
        ) and not input_not_dispatched(result)
        agent.orchestrator.record_tool(
            step.tool,
            arguments,
            result,
            duration_ms,
            1,
            mutation=mutation,
        )
        emit and emit(AgentEvent("tool_result", result, step.tool))

        if step.tool == "discord_select_chat" and str(result).startswith("VERIFIED: "):
            try:
                payload = json.loads(str(result)[len("VERIFIED: "):])
                destination = str(payload.get("destination") or "").strip()
                if destination:
                    verified_discord_destination = destination
            except (TypeError, ValueError, json.JSONDecodeError):
                verified_discord_destination = ""

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
