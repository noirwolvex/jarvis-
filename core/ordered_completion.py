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


# Only these action classes have sufficiently stable tool-to-intent meanings to
# enforce execution order. Unknown application adapters use count-only defense.
_KNOWN_TOOL_STAGES = {
    "open_known_folder": ("open", "navigate"),
    "browser_google_search_first_result": ("search", "activate"),
    "discord_send_message": ("type", "send"),
    "launch_installed_app": ("open",),
    "open_application": ("open",),
    "chrome_new_tab": ("open",),
    "browser_navigate": ("navigate",),
    "open_url": ("navigate",),
    "discord_select_chat": ("activate",),
    "whatsapp_select_chat_native": ("activate",),
    "interaction_click": ("activate",),
    "ui_activate": ("activate",),
    "desktop_click_button": ("activate",),
    "desktop_click": ("activate",),
    "desktop_double_click": ("activate",),
    "browser_click": ("activate",),
    "browser_semantic_click": ("activate",),
    "ui_type": ("type",),
    "ui_type_native": ("type",),
    "interaction_type": ("type",),
    "desktop_type": ("type",),
    "browser_fill": ("type",),
    "browser_type": ("type",),
    "desktop_press": ("activate",),
    "desktop_hotkey": ("activate",),
    "ui_hotkey": ("activate",),
    "interaction_hotkey": ("activate",),
    "google_search": ("search",),
    "youtube_search_open": ("search",),
    "interaction_scroll": ("scroll",),
    "desktop_scroll": ("scroll",),
}
_STAGE_ALIASES = {
    "launch": "open", "start": "open",
    "press": "activate", "click": "activate",
    "select": "activate", "choose": "activate",
    "write": "type", "fill": "type",
    "go": "navigate",
}


def _action_stages(goal: str) -> list[str]:
    text = _mask_quoted_text(goal.strip())
    if not _START.match(text):
        return []
    verb = re.compile(rf"(?:{_ACTIONS})\b", re.I)
    first = verb.search(text)
    if first is None:
        return []
    result = [_STAGE_ALIASES.get(first.group().lower(), first.group().lower())]
    for separator in _CHAIN.finditer(text):
        next_verb = verb.search(text, separator.end(), separator.end() + 48)
        if next_verb is None:
            return []
        name = next_verb.group().lower()
        result.append(_STAGE_ALIASES.get(name, name))
    return result if 2 <= len(result) <= 32 else []


def verified_ordered_stage_count(
    goal: str, current: Any, is_mutation: Callable[[str], bool]
) -> int | None:
    """Return a matched ordered prefix when tool semantics are known.

    None means use the structural lower-bound check instead. This never uses
    read-only snapshots as evidence for an action. No action is replayed.
    """
    expected = _action_stages(goal)
    if not expected or not set(expected) <= {"open", "activate", "type", "navigate", "search"}:
        return None
    if current is None:
        return 0
    verified = getattr(current, "verifications", []) or []
    observed: list[str] = []
    for index, trace in enumerate(getattr(current, "traces", []) or []):
        name = str(getattr(trace, "name", ""))
        if not getattr(trace, "success", False) or not is_mutation(name):
            continue
        stages = _KNOWN_TOOL_STAGES.get(name)
        if stages is None:
            return None  # Do not misclassify a custom/native adapter.
        if not (str(trace.result).startswith("VERIFIED:") or any(
            item.verified and str(item.evidence).strip()
            and item.evidence_trace_index >= index for item in verified
        )):
            continue
        if name == "google_search" and trace.arguments.get("new_tab"):
            stages = ("open", "search")
        observed.extend(stages)
    matched = 0
    for stage in observed:
        if matched >= len(expected):
            break
        want = expected[matched]
        # Semantic click/navigation controls can both open destinations, but
        # typing cannot satisfy an expected click or the reverse.
        if stage == want or (want in {"open", "navigate"} and stage == "activate"):
            matched += 1
    return matched
