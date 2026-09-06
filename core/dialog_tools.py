from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import time
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
GW_ENABLEDPOPUP = 6


def _user32():
    if os.name != "nt":
        raise RuntimeError("Windows dialog automation is supported on Windows only")
    return ctypes.windll.user32


def _window_text(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def _class_name(hwnd: int) -> str:
    user32 = _user32()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _children(hwnd: int) -> list[int]:
    user32 = _user32()
    result: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(child: int, _lparam: int) -> bool:
        result.append(int(child))
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return result


def _foreground() -> int:
    hwnd = int(_user32().GetForegroundWindow())
    if not hwnd:
        raise RuntimeError("No foreground window")
    return hwnd


def _rect(hwnd: int) -> list[int]:
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    r = RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(r)):
        return [0, 0, 0, 0]
    return [int(r.left), int(r.top), int(r.right), int(r.bottom)]


def _set_text(hwnd: int, text: str) -> None:
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        raise RuntimeError("Dialog control no longer exists")
    if not user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(text)):
        current = _window_text(hwnd)
        if current != text:
            raise RuntimeError(f"Could not set dialog field; current value={current!r}")
    time.sleep(0.15)


def _click(hwnd: int) -> None:
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        raise RuntimeError("Dialog control no longer exists")
    user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
    time.sleep(0.25)


def _find_control(dialog: int, target: str, control_type: str | None = None) -> tuple[int, dict[str, Any]]:
    needle = target.strip().lower()
    controls: list[tuple[int, dict[str, Any]]] = []
    for hwnd in _children(dialog):
        title = _window_text(hwnd)
        cls = _class_name(hwnd)
        row = {"hwnd": hwnd, "text": title, "class": cls, "rect": _rect(hwnd)}
        if control_type and control_type.lower() not in cls.lower():
            continue
        if needle and needle not in title.lower() and needle not in cls.lower():
            continue
        controls.append((hwnd, row))
    if not controls:
        raise RuntimeError(f"No dialog control matches target={target!r}, type={control_type!r}")
    return controls[0]


def dialog_inspect() -> str:
    dialog = _foreground()
    title = _window_text(dialog)
    cls = _class_name(dialog)
    controls: list[dict[str, Any]] = []
    user32 = _user32()
    for hwnd in _children(dialog):
        try:
            controls.append({
                "hwnd": hwnd,
                "text": _window_text(hwnd),
                "class": _class_name(hwnd),
                "enabled": bool(user32.IsWindowEnabled(hwnd)),
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "rect": _rect(hwnd),
            })
        except Exception:
            continue
    return json.dumps({"dialog_hwnd": dialog, "title": title, "class": cls, "controls": controls[:400]}, ensure_ascii=False)


def dialog_set_field(target: str, text: str) -> str:
    dialog = _foreground()
    hwnd, info = _find_control(dialog, target, None)
    _set_text(hwnd, text)
    verified = _window_text(hwnd) == text
    if not verified:
        raise RuntimeError(f"Field did not verify after update: {info}")
    return f"VERIFIED: set dialog control '{info['text'] or info['class']}' to {len(text)} characters"


def dialog_click_button(target: str = "") -> str:
    dialog = _foreground()
    if target.strip():
        hwnd, info = _find_control(dialog, target, "Button")
    else:
        raise RuntimeError("A button target is required")
    _click(hwnd)
    time.sleep(0.3)
    return f"Clicked dialog button '{info['text'] or target}'"


def dialog_save_file(path: str) -> str:
    dialog = _foreground()
    dialog_title = _window_text(dialog)
    filename = Path(path).expanduser()
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    resolved = (workspace / filename).resolve() if not filename.is_absolute() else filename.resolve()
    if workspace not in resolved.parents and resolved != workspace:
        raise PermissionError(f"Path is outside JARVIS_WORKSPACE: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)

    edits = []
    for hwnd in _children(dialog):
        if "edit" in _class_name(hwnd).lower() and _user32().IsWindowVisible(hwnd) and _user32().IsWindowEnabled(hwnd):
            edits.append(hwnd)
    if not edits:
        raise RuntimeError(f"No editable filename field found in dialog '{dialog_title}'")

    target_edit = edits[-1]
    _set_text(target_edit, str(resolved))
    if _window_text(target_edit) != str(resolved):
        raise RuntimeError("Filename field did not accept the requested path")

    save_buttons: list[tuple[int, str]] = []
    for hwnd in _children(dialog):
        cls = _class_name(hwnd).lower()
        text = _window_text(hwnd)
        if "button" in cls and _user32().IsWindowVisible(hwnd) and _user32().IsWindowEnabled(hwnd):
            low = text.lower()
            if low in {"save", "ok", "&save", "&ok"} or "save" in low:
                save_buttons.append((hwnd, text))
    if not save_buttons:
        raise RuntimeError(f"No Save button found in dialog '{dialog_title}'")
    _click(save_buttons[0][0])

    deadline = time.time() + 5
    while time.time() < deadline:
        if not _user32().IsWindow(dialog):
            break
        time.sleep(0.1)

    exists = resolved.exists()
    return f"VERIFIED: save requested for {resolved}; exists={exists}; dialog_closed={not bool(_user32().IsWindow(dialog))}"


def register_dialog_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "dialog_inspect",
        "Fast Win32 inspection of the foreground Windows dialog, including child controls, classes, enabled/visible state, and rectangles. Use for Save/Open/confirmation dialogs before clicking.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        dialog_inspect,
    ))
    registry.register(ToolSpec(
        "dialog_set_field",
        "Set the text of a visible foreground dialog control by partial title or class name, then verify the value.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}}, "required": ["target", "text"]},
        dialog_set_field,
    ))
    registry.register(ToolSpec(
        "dialog_click_button",
        "Click a visible Button control in the foreground Windows dialog by partial title.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        dialog_click_button,
    ))
    registry.register(ToolSpec(
        "dialog_save_file",
        "Complete a standard Windows Save As dialog by setting the filename path and activating Save, then verify the dialog closed and the file exists.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        dialog_save_file,
    ))
