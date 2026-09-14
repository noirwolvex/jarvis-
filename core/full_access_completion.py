from __future__ import annotations

from typing import Any

_READ_ONLY_INTENT_SIGNALS = (
    "screen_observe",
    "look at",
    "look on",
    "observe",
    "describe",
    "what is on",
    "what's on",
    "what is visible",
    "what's visible",
    "inspect the screen",
    "current screen",
    "foreground app",
    "foreground application",
    "انظر",
    "شوف",
    "شاهد",
    "راقب",
    "صف",
    "وصف",
    "افحص الشاشة",
    "الشاشة الحالية",
    "التطبيق الحالي",
)

_MUTATION_SIGNALS = (
    " click ",
    " press ",
    " type ",
    " write ",
    " enter ",
    " open ",
    " close ",
    " launch ",
    " start ",
    " navigate ",
    " search ",
    " drag ",
    " scroll ",
    " move ",
    " save ",
    " delete ",
    " edit ",
    " change ",
    " اضغط ",
    " اكتب ",
    " افتح ",
    " اغلق ",
    " أغلق ",
    " ابحث ",
    " احفظ ",
    " حذف ",
    " احذف ",
    " عدل ",
    " غيّر ",
    " غير ",
)

_NEGATED_MUTATION_PHRASES = (
    "without clicking anything",
    "without clicking",
    "without typing",
    "without pressing anything",
    "without pressing",
    "do not click",
    "don't click",
    "do not type",
    "don't type",
    "do not press",
    "don't press",
    "no clicking",
    "no typing",
    "بدون ضغط",
    "بدون النقر",
    "بدون نقر",
    "بدون كتابة",
    "لا تضغط",
    "لا تكتب",
)

_READ_ONLY_EVIDENCE_TOOLS = {
    "screen_observe",
    "take_screenshot",
    "browser_read_page",
    "browser_links",
    "browser_page_state",
    "browser_check_challenge",
    "chrome_tabs",
    "chrome_current_tab",
    "list_windows",
    "inspect_window",
    "dialog_inspect",
    "find_installed_app",
    "task_status",
}


def _normalized_goal(goal: str) -> str:
    text = " " + str(goal or "").casefold() + " "
    for phrase in _NEGATED_MUTATION_PHRASES:
        text = text.replace(phrase, " ")
    return " ".join(text.split())


def is_read_only_observation_goal(goal: str) -> bool:
    original = str(goal or "").casefold()
    if not any(signal in original for signal in _READ_ONLY_INTENT_SIGNALS):
        return False
    normalized = " " + _normalized_goal(goal) + " "
    return not any(signal in normalized for signal in _MUTATION_SIGNALS)


def read_only_observation_verified(goal: str, current: Any) -> bool:
    """Return true only when a read-only observation goal has successful evidence and no mutating tool trace."""
    if not is_read_only_observation_goal(goal) or current is None:
        return False
    traces = list(getattr(current, "traces", []) or [])
    if not traces:
        return False

    evidence_seen = False
    for trace in traces:
        name = str(getattr(trace, "name", ""))
        success = bool(getattr(trace, "success", False))
        result = str(getattr(trace, "result", ""))
        if not success:
            return False
        if name.startswith("task_"):
            continue
        if name not in _READ_ONLY_EVIDENCE_TOOLS:
            return False
        if name == "screen_observe" and result.startswith("VERIFIED:"):
            evidence_seen = True
        elif name in {"take_screenshot", "browser_read_page", "browser_links", "browser_page_state", "chrome_tabs", "chrome_current_tab", "list_windows", "inspect_window", "dialog_inspect", "find_installed_app"}:
            evidence_seen = True
    return evidence_seen
