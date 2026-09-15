from __future__ import annotations

import ctypes
import json
import os
import re
import time
from typing import Any

from .desktop_input import InputDeliveryError, paste_text
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_ACTIONABLE_TYPES = {
    "Button",
    "Edit",
    "Document",
    "Hyperlink",
    "ListItem",
    "MenuItem",
    "TabItem",
    "TreeItem",
    "CheckBox",
    "RadioButton",
    "ComboBox",
}
_EDIT_TYPES = {"Edit", "Document"}


def _windows_only() -> None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise RuntimeError("Semantic UI automation is supported on Windows only")


def _foreground_hwnd() -> int:
    _windows_only()
    hwnd = int(ctypes.windll.user32.GetForegroundWindow())
    if not hwnd:
        raise RuntimeError("No foreground Windows window is available")
    return hwnd


def _window(title: str = ""):
    _windows_only()
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    cleaned = str(title or "").strip()
    if cleaned:
        win = desktop.window(title_re=f".*{re.escape(cleaned)}.*")
        win.wait("visible", timeout=3)
        return win
    return desktop.window(handle=_foreground_hwnd())


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _control_name(control: Any) -> str:
    try:
        return _text(control.element_info.name)
    except Exception:
        try:
            return _text(control.window_text())
        except Exception:
            return ""


def _control_type(control: Any) -> str:
    try:
        return _text(control.element_info.control_type)
    except Exception:
        return ""


def _automation_id(control: Any) -> str:
    try:
        return _text(control.element_info.automation_id)
    except Exception:
        return ""


def _rect(control: Any) -> list[int]:
    try:
        r = control.rectangle()
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return [0, 0, 0, 0]


def _meta(control: Any, index: int | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": _control_name(control),
        "type": _control_type(control),
        "automation_id": _automation_id(control),
        "rect": _rect(control),
    }
    if index is not None:
        row["index"] = int(index)
    try:
        row["enabled"] = bool(control.is_enabled())
    except Exception:
        row["enabled"] = True
    try:
        row["visible"] = bool(control.is_visible())
    except Exception:
        row["visible"] = True
    try:
        row["selected"] = bool(control.is_selected())
    except Exception:
        pass
    return row


def _descendants(win: Any) -> list[Any]:
    try:
        return list(win.descendants())
    except Exception as exc:
        raise RuntimeError(f"UI Automation inspection failed: {type(exc).__name__}: {exc}") from exc


def _score(control: Any, target: str, control_type: str = "") -> float:
    name = _control_name(control).casefold()
    automation_id = _automation_id(control).casefold()
    ctype = _control_type(control).casefold()
    wanted = _text(target).casefold()
    wanted_type = _text(control_type).casefold()

    if wanted_type and wanted_type != ctype:
        return -1.0
    if not wanted:
        return 1.0
    if name == wanted or automation_id == wanted:
        return 100.0
    if name.startswith(wanted) or automation_id.startswith(wanted):
        return 92.0
    if wanted in name or wanted in automation_id:
        return 84.0

    tokens = [token for token in re.split(r"[^\w]+", wanted) if len(token) >= 2]
    if tokens:
        haystack = f"{name} {automation_id}"
        matched = sum(1 for token in tokens if token in haystack)
        if matched:
            return 55.0 + 25.0 * (matched / len(tokens))
    return -1.0


def _candidate_controls(win: Any, target: str = "", control_type: str = "") -> list[tuple[float, int, Any]]:
    rows: list[tuple[float, int, Any]] = []
    for index, control in enumerate(_descendants(win)[:700]):
        try:
            if not control.is_visible() or not control.is_enabled():
                continue
        except Exception:
            pass
        score = _score(control, target, control_type)
        if score < 0:
            continue
        rows.append((score, index, control))
    rows.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return rows


def _find_control(win: Any, target: str = "", control_type: str = "", editable: bool = False):
    candidates = _candidate_controls(win, target, control_type)
    if editable:
        candidates = [row for row in candidates if _control_type(row[2]) in _EDIT_TYPES]
        if not target and candidates:
            # Chat/message composers are normally named and placed near the bottom of the app.
            def edit_score(item: tuple[float, int, Any]) -> tuple[float, int]:
                control = item[2]
                name = _control_name(control).casefold()
                bonus = 0.0
                for signal, weight in (("message", 80.0), ("chat", 55.0), ("reply", 45.0), ("type", 25.0)):
                    if signal in name:
                        bonus = max(bonus, weight)
                rect = _rect(control)
                bottom = rect[3] if len(rect) == 4 else 0
                return (bonus + item[0], bottom)

            candidates.sort(key=edit_score, reverse=True)
    if not candidates:
        qualifier = f" type={control_type!r}" if control_type else ""
        raise RuntimeError(f"No visible enabled UI control matches target={target!r}{qualifier}")

    best_score = candidates[0][0]
    equally_good = [row for row in candidates if abs(row[0] - best_score) < 0.001]
    if target and len(equally_good) > 1 and best_score < 100.0:
        # Prefer a deterministic best match by screen position only after exact matching failed.
        equally_good.sort(key=lambda row: (_rect(row[2])[1], _rect(row[2])[0]))
        return equally_good[0][2]
    return candidates[0][2]


def _focus_window(win: Any) -> int:
    try:
        win.set_focus()
    except Exception:
        try:
            win.restore()
            win.set_focus()
        except Exception as exc:
            raise RuntimeError(f"Could not focus target window: {type(exc).__name__}: {exc}") from exc
    time.sleep(0.08)
    try:
        hwnd = int(win.handle)
    except Exception:
        hwnd = _foreground_hwnd()
    if _foreground_hwnd() != hwnd:
        raise RuntimeError("Target window did not become foreground")
    return hwnd


def _invoke(control: Any) -> str:
    # Prefer semantic patterns that do not move the physical mouse.
    try:
        control.invoke()
        return "invoke"
    except Exception:
        pass
    try:
        control.select()
        return "select"
    except Exception:
        pass
    try:
        control.click()
        return "click"
    except Exception:
        pass
    try:
        control.click_input()
        return "click_input"
    except Exception as exc:
        raise RuntimeError(f"UI control could not be activated: {type(exc).__name__}: {exc}") from exc


def ui_inspect(title: str = "", query: str = "", actionable_only: bool = True, max_controls: int = 120) -> str:
    """Return a compact UIA snapshot optimized for fast agent decisions."""
    win = _window(title)
    hwnd = _focus_window(win)
    wanted = _text(query).casefold()
    limit = max(1, min(int(max_controls), 250))
    controls: list[dict[str, Any]] = []
    for index, control in enumerate(_descendants(win)[:700]):
        row = _meta(control, index)
        if actionable_only and row.get("type") not in _ACTIONABLE_TYPES:
            continue
        if wanted:
            haystack = f"{row.get('name', '')} {row.get('automation_id', '')} {row.get('type', '')}".casefold()
            if wanted not in haystack:
                continue
        if row.get("visible") is False:
            continue
        controls.append(row)
        if len(controls) >= limit:
            break
    try:
        window_title = _text(win.window_text())
    except Exception:
        window_title = _text(title)
    return json.dumps({"hwnd": hwnd, "title": window_title, "controls": controls}, ensure_ascii=False)


def ui_focus(target: str = "", title: str = "", control_type: str = "") -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    if not _text(target):
        return f"VERIFIED: focused window hwnd={hwnd}"
    control = _find_control(win, target, control_type)
    control.set_focus()
    time.sleep(0.05)
    return "VERIFIED: " + json.dumps({"action": "ui_focus", "window_hwnd": hwnd, "control": _meta(control)}, ensure_ascii=False)


def ui_activate(target: str, title: str = "", control_type: str = "") -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    control = _find_control(win, target, control_type)
    before = _meta(control)
    method = _invoke(control)
    time.sleep(0.12)
    if _foreground_hwnd() != hwnd:
        # Some controls intentionally open another foreground window; report this instead of
        # pretending the original app remained active.
        foreground = _foreground_hwnd()
    else:
        foreground = hwnd
    return "VERIFIED: " + json.dumps(
        {"action": "ui_activate", "method": method, "window_hwnd": hwnd, "foreground_hwnd": foreground, "control": before},
        ensure_ascii=False,
    )


def _control_value(control: Any) -> str | None:
    for getter in ("get_value", "window_text"):
        try:
            value = getattr(control, getter)()
            if value is not None:
                return str(value)
        except Exception:
            continue
    return None


def ui_type(
    text: str,
    target: str = "",
    title: str = "",
    submit: bool = False,
    replace: bool = False,
) -> str:
    if len(str(text)) > 4096 or "\0" in str(text):
        raise ValueError("Semantic UI text must contain at most 4096 characters and no NUL")
    win = _window(title)
    hwnd = _focus_window(win)
    control = _find_control(win, target, editable=True)
    control.set_focus()
    time.sleep(0.04)

    wrote_with = "unicode_input"
    if replace:
        replaced = False
        try:
            control.set_edit_text(str(text))
            replaced = True
            wrote_with = "uia_set_edit_text"
        except Exception:
            pass
        if not replaced:
            try:
                control.iface_value.SetValue(str(text))
                replaced = True
                wrote_with = "uia_value_pattern"
            except Exception:
                pass
        if not replaced:
            # Controlled semantic fallback: focus is already bound to the resolved edit control.
            try:
                from pywinauto.keyboard import send_keys
                send_keys("^a{BACKSPACE}")
            except Exception:
                pass
            paste_text(str(text))
    else:
        paste_text(str(text))

    time.sleep(0.06)
    if not submit:
        value = _control_value(control)
        if value is not None and str(text) not in value and replace:
            raise InputDeliveryError("Semantic editor value did not verify after replacement; inspect before retrying")
        return "VERIFIED: " + json.dumps(
            {"action": "ui_type", "window_hwnd": hwnd, "control": _meta(control), "characters": len(str(text)), "method": wrote_with, "submitted": False},
            ensure_ascii=False,
        )

    try:
        from pywinauto.keyboard import send_keys
        send_keys("{ENTER}")
    except Exception as exc:
        raise InputDeliveryError("Text was entered but Enter delivery failed; do not retry blindly") from exc
    time.sleep(0.25)

    # Sending can be verified either because the composer clears or because the sent text is
    # now exposed by UI Automation in the conversation surface.
    value_after = _control_value(control)
    cleared = value_after is not None and not _text(value_after)
    echoed = False
    wanted = _text(text)
    if wanted:
        for candidate in _descendants(win)[:700]:
            if _control_name(candidate) == wanted:
                echoed = True
                break
    if not cleared and not echoed:
        raise InputDeliveryError(
            "Text may have been submitted, but the resulting UI state could not be verified; do not resend automatically"
        )
    return "VERIFIED: " + json.dumps(
        {
            "action": "ui_type",
            "window_hwnd": hwnd,
            "control": _meta(control),
            "characters": len(str(text)),
            "method": wrote_with,
            "submitted": True,
            "composer_cleared": cleared,
            "message_visible": echoed,
        },
        ensure_ascii=False,
    )


def _hotkey_tokens(keys: list[str]) -> list[str]:
    allowed = {
        "ctrl", "control", "alt", "shift", "win", "windows", "enter", "tab", "esc", "escape",
        "space", "backspace", "delete", "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    }
    result: list[str] = []
    for key in keys:
        value = str(key or "").strip().casefold()
        if len(value) == 1 and value.isprintable():
            result.append(value)
            continue
        if value not in allowed and not re.fullmatch(r"f(?:[1-9]|1[0-2])", value):
            raise ValueError(f"Unsupported semantic hotkey key: {key}")
        result.append(value)
    if not result:
        raise ValueError("At least one hotkey key is required")
    return result


def ui_hotkey(keys: list[str], title: str = "") -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    normalized = _hotkey_tokens(keys)
    import pyautogui

    aliases = {"control": "ctrl", "windows": "win", "escape": "esc"}
    pyautogui.hotkey(*[aliases.get(value, value) for value in normalized])
    time.sleep(0.08)
    return "VERIFIED: " + json.dumps({"action": "ui_hotkey", "window_hwnd": hwnd, "keys": normalized}, ensure_ascii=False)


def ui_batch(actions: list[dict[str, Any]], title: str = "") -> str:
    """Execute a compact semantic UI sequence without model round-trips between known steps."""
    if not isinstance(actions, list) or not actions or len(actions) > 20:
        raise ValueError("ui_batch requires 1-20 actions")
    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"ui_batch action {index} must be an object")
        op = str(action.get("op") or "").strip().casefold()
        target = str(action.get("target") or "")
        action_title = str(action.get("title") or title or "")
        control_type = str(action.get("control_type") or "")

        if op == "activate":
            raw = ui_activate(target=target, title=action_title, control_type=control_type)
        elif op == "focus":
            raw = ui_focus(target=target, title=action_title, control_type=control_type)
        elif op == "type":
            raw = ui_type(
                text=str(action.get("text") or ""),
                target=target,
                title=action_title,
                submit=bool(action.get("submit", False)),
                replace=bool(action.get("replace", False)),
            )
        elif op == "hotkey":
            keys = action.get("keys")
            if not isinstance(keys, list):
                raise ValueError("ui_batch hotkey action requires keys[]")
            raw = ui_hotkey([str(key) for key in keys], title=action_title)
        elif op == "wait":
            seconds = max(0.0, min(float(action.get("seconds", 0.15)), 5.0))
            time.sleep(seconds)
            raw = f"VERIFIED: waited {seconds:.2f}s"
        elif op == "assert_visible":
            win = _window(action_title)
            _focus_window(win)
            control = _find_control(win, target, control_type)
            raw = "VERIFIED: " + json.dumps({"action": "assert_visible", "control": _meta(control)}, ensure_ascii=False)
        else:
            raise ValueError(f"Unsupported ui_batch operation: {op}")

        if not str(raw).startswith("VERIFIED:"):
            raise RuntimeError(f"Semantic UI batch action {index} did not verify: {raw}")
        results.append({"index": index, "op": op, "result": str(raw)[:2000]})
    return "VERIFIED: " + json.dumps({"action": "ui_batch", "steps": results}, ensure_ascii=False)


def register_semantic_ui_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "ui_inspect",
        "Fast compact Windows UI Automation snapshot of the foreground or named application. Returns actionable controls with semantic names/types/automation IDs/rectangles. Prefer this over screenshots for labeled desktop interfaces.",
        Risk.LOW,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "query": {"type": "string"},
                "actionable_only": {"type": "boolean"},
                "max_controls": {"type": "integer", "minimum": 1, "maximum": 250},
            },
            "additionalProperties": False,
        },
        ui_inspect,
    ))
    registry.register(ToolSpec(
        "ui_focus",
        "Focus a named Windows application/control through UI Automation without guessing screen coordinates.",
        Risk.LOW,
        {
            "type": "object",
            "properties": {"target": {"type": "string"}, "title": {"type": "string"}, "control_type": {"type": "string"}},
            "additionalProperties": False,
        },
        ui_focus,
    ))
    registry.register(ToolSpec(
        "ui_activate",
        "Activate a visible enabled Windows control by semantic name/automation ID and optional type. Uses UIA Invoke/Select first and physical clicking only as a final fallback. Prefer this over coordinate clicks.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"target": {"type": "string", "minLength": 1}, "title": {"type": "string"}, "control_type": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        ui_activate,
    ))
    registry.register(ToolSpec(
        "ui_type",
        "Focus a semantic editable Windows control and enter text quickly. target may be omitted to choose the most likely visible message/chat editor. submit=true presses Enter and verifies that the composer cleared or the submitted text became visible; uncertain sends fail closed to prevent duplicates.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 4096},
                "target": {"type": "string"},
                "title": {"type": "string"},
                "submit": {"type": "boolean"},
                "replace": {"type": "boolean"},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        ui_type,
    ))
    registry.register(ToolSpec(
        "ui_hotkey",
        "Focus a named Windows application and send a short keyboard shortcut. Use semantic app targeting instead of alt-tab or blind keyboard input.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}, "title": {"type": "string"}},
            "required": ["keys"],
            "additionalProperties": False,
        },
        ui_hotkey,
    ))
    registry.register(ToolSpec(
        "ui_batch",
        "Execute 1-20 already-known semantic Windows UI actions in order inside one verified tool call, avoiding model round-trips between clicks/focus/text/hotkeys. Supported ops: activate, focus, type, hotkey, wait, assert_visible.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["activate", "focus", "type", "hotkey", "wait", "assert_visible"]},
                            "target": {"type": "string"},
                            "title": {"type": "string"},
                            "control_type": {"type": "string"},
                            "text": {"type": "string", "maxLength": 4096},
                            "submit": {"type": "boolean"},
                            "replace": {"type": "boolean"},
                            "keys": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                            "seconds": {"type": "number", "minimum": 0, "maximum": 5},
                        },
                        "required": ["op"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["actions"],
            "additionalProperties": False,
        },
        ui_batch,
    ))
