from __future__ import annotations

import json
from typing import Any

from .desktop_input import InputDeliveryError
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_DIRECT_CHAT_TYPES = {"ListItem", "TreeItem", "DataItem", "Button"}
_FAST_CHAT_TYPES = ("ListItem", "TreeItem", "DataItem", "Edit")
_WEBVIEW_CHAT_TYPES = ("ListItem", "TreeItem", "DataItem", "Edit", "Text", "Custom", "Group", "Pane")
_ROW_CONTAINER_TYPES = _DIRECT_CHAT_TYPES | {"Custom", "Group", "Pane"}
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
    rects: list[list[int]] = []
    for control in rows:
        rect = rect_of(control)
        if len(rect) != 4:
            continue
        center = _row_key(rect)
        duplicate = False
        for previous in rects:
            previous_center = _row_key(previous)
            vertical_overlap = max(0, min(rect[3], previous[3]) - max(rect[1], previous[1]))
            min_height = max(1, min(rect[3] - rect[1], previous[3] - previous[1]))
            if abs(center - previous_center) <= 14.0 or vertical_overlap / min_height >= 0.72:
                duplicate = True
                break
        if duplicate:
            continue
        rects.append(rect)
        result.append(control)
    return result


def _row_geometry(
    rect: list[int],
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
    width: int,
    height: int,
) -> bool:
    if len(rect) != 4:
        return False
    x1, y1, x2, y2 = [int(value) for value in rect]
    if x2 <= x1 or y2 <= y1:
        return False
    row_width = x2 - x1
    row_height = y2 - y1
    center_x = (x1 + x2) // 2
    left_pane_right = left + int(width * 0.52)
    content_top = top + max(68, int(height * 0.075))
    if center_x >= left_pane_right or y1 < content_top or y2 > bottom:
        return False
    return row_width >= max(110, int(width * 0.14)) and 30 <= row_height <= 180


def _chat_row_from_control(
    control: Any,
    *,
    source_name: str,
    control_name,
    control_type,
    rect_of,
    left: int,
    top: int,
    right: int,
    bottom: int,
    width: int,
    height: int,
) -> Any | None:
    """Resolve a named WhatsApp descendant to its clickable visual row.

    Recent WhatsApp builds often expose chat text as Text controls inside Custom/Group
    containers instead of exposing the row itself as ListItem. The Rust click only needs
    a stable, semantically-derived rectangle, so activation-pattern support is not required
    for candidate discovery.
    """
    node = control
    seen: set[int] = set()
    for _ in range(5):
        marker = id(node)
        if marker in seen:
            break
        seen.add(marker)

        ctype = control_type(node)
        name = control_name(node).strip()
        effective_name = (name or source_name).casefold()
        rect = rect_of(node)
        if (
            ctype in _ROW_CONTAINER_TYPES
            and effective_name
            and effective_name not in _EXCLUDED_NAMES
            and _row_geometry(
                rect,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                width=width,
                height=height,
            )
        ):
            return node

        try:
            parent = node.parent()
        except Exception:
            break
        if parent is None:
            break
        node = parent
    return None


def _chat_candidates(win: Any, position: int, *, controls: list[Any] | None = None) -> list[Any]:
    from .semantic_ui_tools import _control_name, _control_type, _descendants, _rect

    window_rect = win.rectangle()
    left = int(window_rect.left)
    top = int(window_rect.top)
    right = int(window_rect.right)
    bottom = int(window_rect.bottom)
    width = max(1, right - left)
    height = max(1, bottom - top)

    preferred: list[Any] = []
    fallback: list[Any] = []
    for control in controls if controls is not None else _descendants(win):
        try:
            if not control.is_visible() or not control.is_enabled():
                continue
        except Exception:
            continue

        source_name = _control_name(control).strip()
        if not source_name or source_name.casefold() in _EXCLUDED_NAMES:
            continue

        row = _chat_row_from_control(
            control,
            source_name=source_name,
            control_name=_control_name,
            control_type=_control_type,
            rect_of=_rect,
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            width=width,
            height=height,
        )
        if row is None:
            continue

        row_type = _control_type(row)
        if row_type in _DIRECT_CHAT_TYPES and _supports_activation(row):
            preferred.append(row)
        else:
            # Custom/Group rows are common in the WebView-based WhatsApp UI. They remain
            # safe deterministic candidates because their rectangle came from a named,
            # visible UIA descendant and is clicked through the guarded Rust input daemon.
            fallback.append(row)

    sorter = lambda control: (_rect(control)[1], _rect(control)[0], -(_rect(control)[2] - _rect(control)[0]))
    preferred = _dedupe_rows(sorted(preferred, key=sorter), _rect)
    fallback = _dedupe_rows(sorted(fallback, key=sorter), _rect)

    if len(preferred) >= position:
        return preferred

    combined = sorted([*preferred, *fallback], key=sorter)
    return _dedupe_rows(combined, _rect)


def _right_pane_signature(win: Any, *, controls: list[Any] | None = None,
                          control_types: tuple[str, ...] = ()) -> tuple[tuple[str, str, tuple[int, int, int, int]], ...]:
    from .semantic_ui_tools import _control_name, _control_type, _descendants, _rect

    window_rect = win.rectangle()
    left = int(window_rect.left)
    right = int(window_rect.right)
    split = left + int(max(1, right - left) * 0.48)
    rows: list[tuple[str, str, tuple[int, int, int, int]]] = []
    for control in controls if controls is not None else _descendants(win, control_types=control_types, visible_only=True):
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


def _auto_mode_activate(control: Any, hwnd: int) -> str:
    from .semantic_ui_tools import _guard_foreground, _invoke

    try:
        return _invoke(control, hwnd)
    except RuntimeError as exc:
        if "no supported semantic activation pattern" not in str(exc).casefold():
            raise
        # Compatibility only for explicit auto mode. The target rectangle was resolved
        # semantically from UIA, so this is not a blind coordinate fallback.
        _guard_foreground(hwnd)
        try:
            control.click_input()
        except Exception as click_exc:
            raise InputDeliveryError(
                "UIA-resolved WhatsApp click outcome is uncertain; inspect before retrying"
            ) from click_exc
        return "uia_resolved_click_input"


def whatsapp_select_chat_native(position: int, title: str = "WhatsApp") -> str:
    """Select a visible WhatsApp chat by ordinal using a Rust-native click and verify the UI changed."""
    ordinal = int(position)
    if not 1 <= ordinal <= 20:
        raise ValueError("WhatsApp chat position must be between 1 and 20")

    from .process_control import check_cancelled
    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode
    from .semantic_ui_tools import (
        _SNAPSHOTS,
        _descendants,
        _focus_window,
        _guard_foreground,
        _guard_native_target,
        _meta,
        _target_binding,
        _validate_target,
        _window,
    )
    from .ui_state import wait_until

    check_cancelled()
    win = _window(title)
    hwnd = _focus_window(win)
    _guard_foreground(hwnd)

    candidates: list[Any] = []
    controls: list[Any] = []
    observation_types: tuple[str, ...] = _FAST_CHAT_TYPES

    def chats_ready() -> bool:
        nonlocal candidates, controls, observation_types
        check_cancelled()
        _guard_foreground(hwnd)
        observation_types = _FAST_CHAT_TYPES
        controls = _descendants(win, require_complete=True, control_types=observation_types, visible_only=True)
        candidates = _chat_candidates(win, ordinal, controls=controls)
        if len(candidates) < ordinal:
            # Modern WhatsApp WebView builds often expose the label as Text inside
            # Custom/Group/Pane rows. Query those types natively before considering
            # a broad tree walk; this avoids materializing unrelated chat-history UI.
            observation_types = _WEBVIEW_CHAT_TYPES
            controls = _descendants(win, require_complete=True,
                                    control_types=observation_types, visible_only=True)
            candidates = _chat_candidates(win, ordinal, controls=controls)
        if len(candidates) < ordinal:
            # Last-resort compatibility for an unknown provider shape. Keep the old
            # full-tree path, but only after both bounded native UIA queries fail.
            observation_types = ()
            controls = _descendants(win, require_complete=True)
            candidates = _chat_candidates(win, ordinal, controls=controls)
        return len(candidates) >= ordinal

    try:
        wait_until(
            chats_ready,
            timeout=6.0,
            description=f"WhatsApp visible chat row {ordinal}",
        )
    except TimeoutError as exc:
        raise RuntimeError(
            f"WhatsApp opened but only {len(candidates)} visible chat row(s) became semantically resolvable; "
            f"cannot select position {ordinal}. Inspect the current WhatsApp UI before retrying."
        ) from exc

    control = candidates[ordinal - 1]
    before = _meta(control)

    # If WhatsApp already opened with the requested row selected, the requested postcondition
    # already holds. Avoid a redundant click and report that state explicitly.
    if before.get("selected") is True:
        return "VERIFIED: " + json.dumps(
            {
                "action": "whatsapp_select_chat_native",
                "window_hwnd": hwnd,
                "position": ordinal,
                "control": before,
                "click": None,
                "method": "already_selected",
                "rust_executed": False,
                "simulation": None,
                "evidence": {
                    "already_selected": True,
                    "selected": True,
                    "focused": bool(before.get("focused")),
                    "view_changed": False,
                },
            },
            ensure_ascii=False,
        )

    # Reuse the same observation for candidate discovery and the before-state.
    # Do not enumerate the entire chat history a second time before clicking.
    binding = _target_binding(win, control)
    rect = binding["control"]["rect"]
    if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise InputDeliveryError("WhatsApp chat row geometry is unavailable; no input delivered")
    x = (int(rect[0]) + int(rect[2])) // 2
    y = (int(rect[1]) + int(rect[3])) // 2
    before_signature = _right_pane_signature(win, controls=controls)
    client, status = _preflight()
    _validate_target(win, control, binding)
    method = "semantic_ui"
    rust_result: dict[str, Any] | None = None
    if client is None:
        if native_engine_mode() != "auto":
            raise RustEngineUnavailable(
                "Strict Rust mode requires the native daemon before WhatsApp chat selection"
            )
        method = _auto_mode_activate(control, hwnd)
    else:
        _guard_foreground(hwnd)
        _guard_native_target(hwnd, status)
        try:
            rust_result = client.click(x, y, status,
                                       before_dispatch=lambda: _validate_target(win, control, binding))
        except RustEngineUnavailable:
            if native_engine_mode() == "auto":
                _validate_target(win, control, binding)
                method = _auto_mode_activate(control, hwnd)
            else:
                raise
        else:
            if rust_result.get("executed") is not True or rust_result.get("simulation") is not False:
                raise InputDeliveryError(
                    "Rust daemon did not confirm native mouse execution; inspect before retrying"
                )
            method = "rust_native_click"

    _SNAPSHOTS.invalidate(hwnd)
    evidence: dict[str, Any] = {
        "already_selected": False,
        "selected": False,
        "focused": False,
        "view_changed": False,
    }

    def selection_verified() -> bool:
        _guard_foreground(hwnd)
        after = _meta(control)
        evidence["selected"] = after.get("selected") is True
        evidence["focused"] = after.get("focused") is True
        if evidence["selected"]:
            return True
        # Focus alone can mean only that the row received keyboard focus. When
        # selection is unavailable, independently inspect the conversation view.
        evidence["view_changed"] = _right_pane_signature(win, control_types=observation_types) != before_signature
        return bool(evidence["view_changed"])

    try:
        wait_until(
            selection_verified,
            timeout=4.0,
            description="WhatsApp ordinal chat selection evidence",
        )
    except TimeoutError as exc:
        raise InputDeliveryError(
            "WhatsApp chat activation was dispatched but no independent selection/conversation-view evidence appeared; "
            "inspect the current WhatsApp state before retrying and do not replay the click blindly."
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
            "Select the Nth visible WhatsApp chat from the left chat list. Waits for the chat list to become ready, resolves classic ListItem rows plus modern Custom/Group UIA rows from named descendants, dispatches the click through the Rust native-input daemon in strict Rust mode, and independently verifies selection or conversation-view change before returning VERIFIED. Keyboard focus alone does not prove chat selection.",
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
