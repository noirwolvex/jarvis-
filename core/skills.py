from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

BUILTIN_SKILLS: dict[str, str] = {
    "desktop-operator": "Use Windows tools systematically: inspect windows, focus the correct target, act, wait for UI transitions, and verify the requested outcome.",
    "browser-operator": "Use browser navigation, page reading, link discovery, semantic clicks/types, waits, and verification instead of blind coordinates.",
    "developer": "For software tasks, inspect repository state first, make focused changes, run checks, and verify the resulting state before reporting completion.",
    "researcher": "Break research into subquestions, gather evidence, compare sources, and keep citations or source references with important conclusions.",
}


def _workspace() -> Path:
    return Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()


def _skill_dirs() -> list[Path]:
    root = _workspace() / ".jarvis" / "skills"
    if not root.exists():
        return []
    return [p for p in root.iterdir() if p.is_dir() and p.name[:1] != "."]


def list_skills() -> str:
    skills: list[dict[str, Any]] = []
    for name, description in BUILTIN_SKILLS.items():
        skills.append({"name": name, "source": "builtin", "description": description})
    for directory in _skill_dirs():
        path = directory / "SKILL.md"
        description = "Workspace skill"
        if path.exists():
            first = next((line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()), "")
            if first.startswith("#"):
                description = first.lstrip("# ").strip()[:300] or description
        skills.append({"name": directory.name, "source": "workspace", "description": description})
    return json.dumps(skills, ensure_ascii=False)


def load_skill(name: str) -> str:
    key = name.strip().lower()
    if key in BUILTIN_SKILLS:
        return json.dumps({"name": key, "source": "builtin", "instructions": BUILTIN_SKILLS[key]}, ensure_ascii=False)
    root = _workspace() / ".jarvis" / "skills"
    target = (root / name).resolve()
    if root not in target.parents or not target.is_dir():
        raise PermissionError(f"Skill is outside the workspace skill directory: {name}")
    path = target / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.dumps({"name": target.name, "source": "workspace", "instructions": path.read_text(encoding="utf-8")[:30000]}, ensure_ascii=False)


def register_skill_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "list_skills",
        "List JARVIS built-in and workspace skills. Use this to discover specialized procedures before complex tasks.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        list_skills,
    ))
    registry.register(ToolSpec(
        "load_skill",
        "Load the instructions for one JARVIS skill. Prefer this before specialized or complex work when a relevant skill exists.",
        Risk.LOW,
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        load_skill,
    ))
