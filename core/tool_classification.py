"""Shared distinctions between procedural references and live tool evidence."""
from __future__ import annotations


KNOWLEDGE_TOOLS = frozenset({"control_guide", "list_skills", "load_skill"})


def is_reference_tool(name: str) -> bool:
    """Reference text describes procedures; it never observes or executes them."""
    return name in KNOWLEDGE_TOOLS
