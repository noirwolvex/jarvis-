from __future__ import annotations

import json
from typing import Any

from .desktop_input import InputDeliveryError
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_CHAT_TYPES = {"ListItem", "TreeItem", "Button"}
_EXCLUDED_NAMES = {
    "chats",
    "status",
    "calls",
    "communities",
    "settings",
    "menu",
    "new chat",
    "search",
    "archived",
    "profile",
}


def _supports_activation(control: Any) -> bool:
    for attribute in ("iface_selection_item", "iface_invoke"):
        try:
            getattr(control, attribute)
            return True
        except Exception as exc:
            if isinstance(exc, AttributeError) or type(exc).__name__ == "NoPatternInterfaceError":
                continue
            continue
    return False


def _row_key(rect: list[int]) -> float:
    return (float(rect[1]) + float(rect[3])) / 2.0


def _dedupe_rows(rows: list[Any], rect_of) -> list[Any]:
    result: list[Any] = []
    centers: list[float] = []
    for control in rows:
        rect = rect_of(control)
        center = _row_key(rect)
        if any(abs(center - previous) <= 7.0 for previous in centers):
            continue
        centers.append(center)
        result.append(control)
    return result


def _chat_candidates(win: Any, position: int) -> list[Any]:
    from .semantic_ui_tools import _control_name, _control_type, _descendants, _rect

    window_rect = win.rectangle()
    left = int(window_rect.left)
    top = int(window_rect.top)
    right = int(window_rect.right)
    bottom = int(window_rect.bottom)
    width = max(1, right - left)
    height = max(1, bottom - top)
    left_pane_right = left + int(width * 0.48)
    content_top = top + max(72, int(height * 0.09))

    preferred: list[Any] = []
    fallback: list[Any] = []
    for control in _descendants(win):
        try:
            if not control.is_visible() or not control.is_enabled():
                continue
        except Exception:
            continue

        control_type = _control_type(control)
        if control_type not in _CHAT_TYPES:
            continue
        name = _control_name(control).strip()
        if not name or name.casefold() in _EXCLUDED_NAMES:
            continue
        rect = _rect(control)
        if len(rect) != 4:
            continue
        x1, y1, x2, y2 = rect
        row_width = x2 - x1
        row_height = y2 - y1
        center_x = (x1 + x2) // 2
        if x2 <= x1 or y2 <= y1:
            continue
        if center_x >= left_pane_right or y1 < content_top or y2 > bottom:
            continue
        if row_width < max(110, int(width * 0.16)) or not 30 <= row_height <= 150:
            continue
        if not _supports_activation(control):
            continue
        if control_type in {"ListItem", "TreeItem"}:
            preferred.append(control)
        else:
            fallback.append(control)

    sorter = lambda control: (_rect(control)[1], _rect(control)[0])
    preferred.sort(key=sorter)
    fallback.sort(key=sorter)
    preferred = _dedupe_rows(preferred, _rect)
    if len(preferred) >= position:
        return preferred

    combined = sorted([*preferred, *fallback], key=sorter)
    return _dedupe_rows(combined, _rect)


def _right_pane_signature(win: Any) -> tuple[tuple[str, str, tuple[int, int, int, int]], ...]:
    from .semantic_ui_tools import _control_name, _control_type, _descendants, _rect

    window_rect = win.rectangle()
    left = int(window_rect.left)
    right = int(window_rect.right)
    split = left + int(max(1, right - left) * 0.48)
    rows: list[tuple[str, str, tuple[int, int, int, int]]] = []
    for control in _descendants(win):
        try:
            if not control.is_visible():
                continue
        except Exception:
            continue
        rect = _rect(control)
        if len(rect) != 4 or (rect[0] + rect[2]) // 2 < split:
            continue
        name = _control_name(control)
        control_type = _control_type(control)
        if not name and control_type not in {"Edit", "Document"}:
            continue
        rows.append((control_type[:80], name[:180], tuple(int(v) for v in rect)))
        if len(rows) >= 120:
            break
    return tuple(rows)


def whatsapp_select_chat_native(position: int, title: str = "WhatsApp") -> str:
    """Select a visible WhatsApp chat by ordinal using a Rust-native click and verify the UI changed."""
    ordinal = int(position)
    if not 1 <= ordinal <= 20:
        raise ValueError("WhatsApp chat position must be between 1 and 20")

    from .process_control import check_cancelled
    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode
    from .semantic_ui_tools import (
        _SNAPSHOTS,
        _focus_window,
        _guard_foreground,
        _invoke,
        _meta,
        _rect,
        _window,
    )
    from .ui_state import wait_until

    check_cancelled()
    win = _window(title)
    hwnd = _focus_window(win)
    _guard_foreground(hwnd)
    candidates = _chat_candidates(win, ordinal)
    if len(candidates) < ordinal:
        raise RuntimeError(
            f"WhatsApp exposes only {len(candidates)} verified visible chat row(s); cannot select position {ordinal}"
        )

    control = candidates[ordinal - 1]
    before = _meta(control)
    rect = _rect(control)
    x = (int(rect[0]) + int(rect[2])) // 2
    y = (int(rect[1]) + int(rect[3])) // 2
    before_signature = _right_pane_signature(win)

    client, status = _preflight()
    method = "semantic_ui"
    rust_result: dict[str, Any] | None = None
    if client is None:
        if native_engine_mode() != "auto":
            raise RustEngineUnavailable(
                "Strict Rust mode requires the native daemon before WhatsApp chat selection"
            )
        method = _invoke(control, hwnd)
    else:
        _guard_foreground(hwnd)
        try:
            rust_result = client.click(x, y, status)
        except RustEngineUnavailable:
            if native_engine_mode() == "auto":
                method = _invoke(control, hwnd)
            else:
                raise
        else:
            if rust_result.get("executed") is not True or rust_result.get("simulation") is not False:
                raise InputDeliveryError(
                    "Rust daemon did not confirm native mouse execution; inspect before retrying"
                )
            method = "rust_native_click"

    _SNAPSHOTS.invalidate(hwnd)
    evidence: dict[str, Any] = {"selected": False, "focused": False, "view_changed": False}

    def selection_verified() -> bool:
        _guard_foreground(hwnd)
        after = _meta(control)
        evidence["selected"] = after.get("selected") is True
        evidence["focused"] = after.get("focused") is True
        evidence["view_changed"] = _right_pane_signature(win) != before_signature
        return bool(evidence["selected"] or evidence["focused"] or evidence["view_changed"])

    try:
        wait_until(selection_verified, timeout=3.0, description="WhatsApp ordinal chat selection evidence")
    except TimeoutError as exc:
        raise InputDeliveryError(
            "WhatsApp chat activation was dispatched but no independent selection/view-change evidence appeared; inspect before retrying"
        ) from exc

    return "VERIFIED: " + json.dumps(
        {
            "action": "whatsapp_select_chat_native",
            "window_hwnd": hwnd,
            "position": ordinal,
            "control": before,
            "click": {"x": x, "y": y},
            "method": method,
            "rust_executed": bool(rust_result and rust_result.get("executed") is True),
            "simulation": rust_result.get("simulation") if rust_result else None,
            "evidence": evidence,
        },
        ensure_ascii=False,
    )


def register_whatsapp_native_tools(registry: ToolRegistry) -> None:
    def guarded(**kwargs: Any) -> str:
        from .semantic_ui_tools import BrowserBoundaryError

        try:
            return whatsapp_select_chat_native(**kwargs)
        except BrowserBoundaryError as exc:
            return str(exc)

    registry.register(
        ToolSpec(
            "whatsapp_select_chat_native",
            "Select the Nth visible WhatsApp chat from the left chat list. Resolves the row through Windows UI Automation, dispatches the click through the Rust native-input daemon in strict Rust mode, and independently verifies selection/focus or conversation-view change before returning VERIFIED.",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {
                    "position": {"type": "integer", "minimum": 1, "maximum": 20},
                    "title": {"type": "string"},
                },
                "required": ["position"],
                "additionalProperties": False,
            },
            guarded,
        )
    )
