from __future__ import annotations

from typing import Any
import json

from .tool_classification import is_reference_tool


def native_browser_progress(current: Any) -> dict[str, Any] | None:
    """Use verified mission history and the exact live window, never another CDP browser."""
    from .browser_fast_tools import _is_google_search_url
    from .chrome_existing_window import read_existing_chrome
    from .process_control import check_cancelled

    evidence = []
    for trace in getattr(current, "traces", []) or []:
        if not getattr(trace, "success", False):
            continue
        if getattr(trace, "name", "") not in {"google_search", "browser_google_search_first_result", "chrome_new_tab", "browser_navigate", "open_url"}:
            continue
        result = str(getattr(trace, "result", ""))
        if not result.startswith("VERIFIED:"):
            continue
        try:
            item = json.loads(result[len("VERIFIED:"):])
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict) and item.get("session_type") == "existing-window" and item.get("verified") is True:
            evidence.append(item)
    if not evidence:
        return None
    window = evidence[-1].get("window")
    initial = evidence[0].get("initial_tab_count")
    initial = initial if type(initial) is int and initial >= 0 else 0
    unknown = {"tab_count": 0, "google_search_count": 0, "initial_tab_count": initial}
    if not isinstance(window, dict) or any(item.get("window") != window for item in evidence):
        return {**unknown, "error": "Browser mission changed windows; inspect its recorded destinations"}
    try:
        state = read_existing_chrome(window)
        if state.get("challenge_detected"):
            return {**unknown, "error": "Human verification requires the user; do not repeat browser actions"}
        count = state.get("tab_count")
        if type(count) is not int or count < 1:
            raise ValueError("Live tab count unavailable")
    except Exception as exc:
        check_cancelled()
        return {**unknown, "error": "Cannot verify the mission's original Chrome window: " + str(exc)}
    queries = set()
    for item in evidence:
        query = str(item.get("query") or "").strip()
        url = str(item.get("search_url") or item.get("url") or "")
        if query and _is_google_search_url(url, query):
            queries.add(query.casefold())
    return {"tab_count": count, "google_search_count": len(queries), "initial_tab_count": initial}

_READ_ONLY_INTENT_SIGNALS = (
    "screen_observe",
    "computer_observe",
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
    "computer_observe",
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
    "ui_inspect",
    "ui_wait_state",
    "browser_semantic_snapshot",
    "browser_wait_state",
    "workflow_status",
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
        if is_reference_tool(name):
            continue
        if not success:
            return False
        if name.startswith("task_") or name == "workflow_status":
            continue
        if name not in _READ_ONLY_EVIDENCE_TOOLS:
            return False
        if name in {"screen_observe", "computer_observe"} and result.startswith("VERIFIED:"):
            evidence_seen = True
        elif name in {"take_screenshot", "browser_read_page", "browser_links", "browser_page_state", "chrome_tabs", "chrome_current_tab", "list_windows", "inspect_window", "dialog_inspect", "find_installed_app", "ui_inspect", "ui_wait_state", "browser_semantic_snapshot", "browser_wait_state"}:
            evidence_seen = True
    return evidence_seen
