"""Conservative evidence-count guard for explicitly ordered multi-action goals.

This is a lower-bound structural check, not a claim that action names alone
prove the user-requested outcomes. Unrecognized prose stays with the planner.
"""
from __future__ import annotations

import re
from typing import Any, Callable


_ACTIONS = (
    r"open|launch|start|click|press|select|choose|type|write|fill|"
    r"send|submit|search|navigate|go|save|scroll|close|switch|drag"
)
_START = re.compile(rf"^(?:please\s+)?(?:{_ACTIONS})\b", re.I)
_CHAIN = re.compile(
    rf"\b(?:and\s+then|then|after(?:\s+that)?|and)\s+"
    rf"(?=(?:please\s+)?(?:{_ACTIONS})\b)",
    re.I,
)
_COMBINED_TOOLS = {
    "open_known_folder": 2,  # Open Explorer and navigate to a named folder.
    "browser_google_search_first_result": 2,  # Search, then open the first result.
    "discord_send_message": 2,  # Compose and send in the selected conversation.
}


def _mask_quoted_text(goal: str) -> str:
    result = list(goal)
    quote = ""
    for i, char in enumerate(goal):
        if quote:
            result[i] = " "
            if char == quote and (i == 0 or goal[i - 1] != "\\"):
                quote = ""
        elif char in ('"', "'") and (i == 0 or not goal[i - 1].isalnum()):
            quote = char
            result[i] = " "
    return "".join(result) if not quote else ""


def explicit_action_count(goal: str) -> int:
    """Count only overt imperative verb clauses; ambiguous prose opts out."""
    if not isinstance(goal, str) or len(goal) > 8000:
        return 0
    masked = _mask_quoted_text(goal.strip())
    if not masked or not _START.match(masked):
        return 0
    amount = 1 + len(list(_CHAIN.finditer(masked)))
    return amount if 2 <= amount <= 32 else 0


def verified_action_count(current: Any, is_mutation: Callable[[str], bool]) -> int:
    """Count independently evidenced action dispatches, never read-only tools."""
    if current is None:
        return 0
    verifications = getattr(current, "verifications", []) or []
    total = 0
    for index, trace in enumerate(getattr(current, "traces", []) or []):
        name = str(getattr(trace, "name", ""))
        if (not getattr(trace, "success", False) or not is_mutation(name)
                or name.startswith(("task_", "workflow_"))):
            continue
        direct = str(getattr(trace, "result", "")).startswith("VERIFIED:")
        independently_verified = any(
            item.verified and str(item.evidence).strip()
            and item.evidence_trace_index >= index
            for item in verifications
        )
        if not direct and not independently_verified:
            continue
        weight = _COMBINED_TOOLS.get(name, 1)
        if name == "google_search" and bool(getattr(trace, "arguments", {}).get("new_tab")):
            weight = 2
        total += weight

    # A completed top-level plan step can represent one independently verified
    # outcome even when no single tool returns a VERIFIED prefix. Do not count
    # pending/failed/skipped steps.
    completed_nodes = sum(
        item.status == "completed" for item in getattr(current, "plan", []) or []
    )
    return max(total, completed_nodes)
