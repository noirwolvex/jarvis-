"""Local, bounded control procedures grounded in the tools this agent exposes.

Guidance is not screen evidence, an executable macro, or an authorization grant.
Only the repository-owned catalog is loaded; UI text and workspace skills never
become trusted catalog entries. Retrieval has no network or model dependency.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_CATALOG = Path(__file__).with_name("resources") / "control_guides.json"
_SURFACES = {"any", "desktop", "browser"}
_LIST_FIELDS = ("keywords", "required_tools", "preconditions", "procedure", "verification", "recovery", "pitfalls", "references")
_CONTRACT = (
    "Procedural reference only, not a live observation or proof of execution. "
    "Resolve real targets from fresh state. Existing permissions, confirmations, "
    "challenge stops and verification gates still apply. Never execute example placeholders."
)
_UNAVAILABLE = (
    "Local control procedures are unavailable. Continue using the exposed tool schemas and core "
    "execution rules. Fresh observations, permissions and result verification are still required. "
    "Do not repeatedly retry reference lookup during this mission."
)


class ControlCatalogUnavailable(RuntimeError):
    """The optional repository reference failed validation; no entries may be used."""


@lru_cache(maxsize=1)
def _catalog() -> tuple[dict[str, Any], ...]:
    try:
        return _read_catalog()
    except (OSError, ValueError, TypeError) as exc:
        # Keep raw file content and exception text out of the model prompt. A bad
        # optional guide must neither inject instructions nor disable the engine.
        raise ControlCatalogUnavailable("Local control catalog is missing or invalid") from exc


def _read_catalog() -> tuple[dict[str, Any], ...]:
    raw = _CATALOG.read_bytes()
    if len(raw) > 100_000:
        raise ValueError("Control catalog exceeds the size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("guides"), list):
        raise ValueError("Unsupported control catalog")
    rows = payload["guides"]
    if not 1 <= len(rows) <= 32:
        raise ValueError("Invalid control guide count")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "title", "surface", "summary", *_LIST_FIELDS}:
            raise ValueError("Invalid control guide fields")
        identity = row["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", identity) or identity in seen:
            raise ValueError("Invalid or duplicate control guide id")
        seen.add(identity)
        if row["surface"] not in _SURFACES:
            raise ValueError("Invalid guide surface")
        for key in ("title", "summary"):
            if not isinstance(row[key], str) or not 1 <= len(row[key]) <= 700:
                raise ValueError("Invalid guide text")
        for key in _LIST_FIELDS:
            if (not isinstance(row[key], list) or not 1 <= len(row[key]) <= 24
                    or any(not isinstance(item, str) or not 1 <= len(item) <= 1200 for item in row[key])):
                raise ValueError("Invalid guide list")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in row["required_tools"]):
            raise ValueError("Invalid guide tool name")
    return tuple(rows)


def _matches(query: str, term: str) -> bool:
    # Word boundaries avoid selecting "tab" for "table". Arabic keywords can
    # explicitly include their common prefixed forms in the packaged catalog.
    return bool(re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", query))


def select_guides(query: str, available_tools: Iterable[str], *, surface: str = "any",
                  limit: int = 3, recent_tools: Iterable[str] = ()) -> list[dict[str, Any]]:
    if surface not in _SURFACES or type(limit) is not int or not 1 <= limit <= 6:
        raise ValueError("Choose any/browser/desktop and 1-6 guides")
    if not isinstance(query, str) or len(query) > 8000:
        raise ValueError("Guide query must be at most 8000 characters")
    available, recent = set(available_tools), set(recent_tools)
    text = query.casefold()
    ranked = []
    for index, row in enumerate(_catalog()):
        if not set(row["required_tools"]) <= available or surface != "any" and row["surface"] not in {surface, "any"}:
            continue
        score = sum(2 for word in row["keywords"] if _matches(text, word))
        score += 3 * len(recent.intersection(row["required_tools"]))
        if text == row["id"]:
            score += 100
        if score:
            ranked.append((-score, index, row))
    ranked.sort(key=lambda item: item[:2])
    return [copy.deepcopy(item[2]) for item in ranked[:limit]]


def guide_reference(registry: ToolRegistry, query: str = "", surface: str = "any",
                    limit: int = 3, include_schemas: bool = False, *,
                    available_tools: Iterable[str] | None = None) -> str:
    available = set(registry._tools).intersection(available_tools) if available_tools is not None else set(registry._tools)
    try:
        rows = select_guides(query, available, surface=surface, limit=limit)
    except ControlCatalogUnavailable:
        return json.dumps({"kind": "control_reference", "status": "unavailable",
                           "contract": _CONTRACT, "guides": [], "message": _UNAVAILABLE})
    result: dict[str, Any] = {"kind": "control_reference", "version": 1, "contract": _CONTRACT, "guides": rows}
    if not rows:
        result["catalog"] = [{"id": row["id"], "title": row["title"], "surface": row["surface"]}
                             for row in _catalog() if set(row["required_tools"]) <= available
                             and (surface == "any" or row["surface"] in {surface, "any"})]
    if include_schemas:
        names = {name for row in rows for name in row["required_tools"]}
        result["tool_arguments"] = {name: copy.deepcopy(registry._tools[name].input_schema) for name in sorted(names)}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def skill_reference(surface: str, available_tools: Iterable[str] | None = None) -> dict[str, Any]:
    if surface not in _SURFACES:
        raise ValueError("Invalid reference surface")
    available = set(available_tools) if available_tools is not None else None
    try:
        rows = _catalog()
    except ControlCatalogUnavailable:
        return {"kind": "control_reference", "status": "unavailable", "contract": _CONTRACT,
                "guides": [], "message": _UNAVAILABLE}
    return {"kind": "control_reference", "contract": _CONTRACT, "version": 1,
            "guides": [copy.deepcopy(row) for row in rows
                       if (surface == "any" or row["surface"] in {surface, "any"})
                       and (available is None or set(row["required_tools"]) <= available)]}


def _context_surface(query: str) -> str:
    """Route reference prose only; this never chooses an execution target/backend."""
    text = query.casefold()
    browser = any(_matches(text, term) for term in (
        "browser", "chrome", "google", "website", "web page", "youtube",
        "متصفح", "المتصفح", "كروم", "جوجل", "يوتيوب",
    ))
    desktop = any(_matches(text, term) for term in (
        "desktop", "whatsapp", "whats app", "discord", "notepad", "vscode", "visual studio code",
        "excel", "powerpoint", "word", "سطح المكتب", "واتساب", "واتس", "ديسكورد", "دسكورد",
    ))
    return "browser" if browser and not desktop else "desktop" if desktop and not browser else "any"


def control_context(query: str, available_tools: Iterable[str], *, recent_tools: Iterable[str] = (),
                    max_chars: int = 5200) -> str:
    """Only selected repo text enters the prompt; user/error payloads are never echoed."""
    if type(max_chars) is not int or not 800 <= max_chars <= 8000:
        raise ValueError("Invalid control context budget")
    header = "\nRelevant local control procedures (control_guide has details and current argument schemas):\n" + _CONTRACT
    recent = tuple(recent_tools)
    # A failed backend can need guidance from another surface; keep recovery and
    # mixed desktop/browser missions broad instead of filtering out their tools.
    surface = "any" if recent else _context_surface(query[:8000])
    try:
        rows = select_guides(query[:8000], available_tools, surface=surface, recent_tools=recent)
    except ControlCatalogUnavailable:
        return "\n" + _UNAVAILABLE
    parts = [header]
    for row in rows:
        block = f"\n[{row['id']}] {row['summary']}\nTools: " + ", ".join(row["required_tools"])
        for key in ("preconditions", "procedure", "verification", "recovery", "pitfalls"):
            block += "\n" + key + ": " + " ".join(row[key])
        if len("\n".join(parts)) + len(block) + 1 <= max_chars:
            parts.append(block)
    return "\n".join(parts) if len(parts) > 1 else ""


def register_control_knowledge(registry: ToolRegistry,
                               available_tools: Callable[[], Iterable[str]] | None = None) -> None:
    registry.register(ToolSpec(
        "control_guide",
        "Read local control procedures: backend choice, preconditions, exact tool arguments, verification and recovery. "
        "Use a guide id or task keywords; empty query lists available topics. No UI action or observation. "
        "Relevant guidance is automatically loaded; query only for missing details.",
        Risk.SAFE,
        {"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 8000},
            "surface": {"enum": sorted(_SURFACES)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 6},
            "include_schemas": {"type": "boolean"},
        }, "additionalProperties": False},
        lambda **kwargs: guide_reference(registry, **kwargs,
                                         available_tools=available_tools() if available_tools else None),
    ))
