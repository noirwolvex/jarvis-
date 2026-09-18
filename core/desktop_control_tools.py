from __future__ import annotations

import json
import os
import time
from typing import Literal

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_MOUSE_BUTTONS = {"left", "right", "middle"}
_HELD_MOUSE_BUTTONS: set[str] = set()
_HELD_KEYS: set[str] = set()


def _windows_only() -> None:
    if os.name != "nt":
        raise RuntimeError("Desktop control is supported on Windows only")


def _button(value: str) -> Literal["left", "right", "middle"]:
    normalized = str(value or "left").strip().lower()
    if normalized not in _MOUSE_BUTTONS:
        raise ValueError("Mouse button must be left, right, or middle")
    return normalized  # type: ignore[return-value]


def _key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized or len(normalized) > 32 or "\0" in normalized:
        raise ValueError("Keyboard key is invalid")
    return normalized


def desktop_cursor() -> str:
    _windows_only()
    import pyautogui

    point = pyautogui.position()
    size = pyautogui.size()
    return json.dumps(
        {"x": int(point.x), "y": int(point.y), "screen_width": int(size.width), "screen_height": int(size.height)},
        ensure_ascii=False,
    )


def desktop_click_button(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    _windows_only()
    import pyautogui

    chosen = _button(button)
    count = max(1, min(int(clicks), 3))
    pyautogui.click(x=int(x), y=int(y), clicks=count, interval=0.06, button=chosen, _pause=False)
    return f"Clicked {chosen} at ({int(x)}, {int(y)}) x{count}"


def move_pointer(x: int, y: int, duration: float = 0.08, *, expected_foreground: int | None = None) -> None:
    """Smooth bounded movement with cancellation/foreground checks between samples."""
    _windows_only()
    import pyautogui
    from .desktop_observation import foreground_identity
    from .process_control import check_cancelled
    check_cancelled()
    hwnd = foreground_identity() if expected_foreground is None else expected_foreground
    if not hwnd:
        raise RuntimeError("Foreground window unavailable for pointer movement")
    if not 0 <= duration <= 2:
        raise ValueError("Pointer movement duration must be between 0 and 2 seconds")
    origin = pyautogui.position()
    start = time.monotonic()
    while True:
        check_cancelled()
        if foreground_identity() != hwnd:
            raise RuntimeError("Foreground changed during pointer movement")
        progress = 1.0 if duration == 0 else min(1.0, (time.monotonic() - start) / duration)
        eased = progress * progress * (3 - 2 * progress)
        pyautogui.moveTo(round(origin.x + (x - origin.x) * eased), round(origin.y + (y - origin.y) * eased), _pause=False)
        if progress >= 1:
            return
        time.sleep(min(0.016, max(0, duration - (time.monotonic() - start))))


def desktop_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.25,
    button: str = "left",
) -> str:
    _windows_only()
    import pyautogui

    chosen = _button(button)
    seconds = max(0.05, min(float(duration), 2.0))
    from .desktop_observation import foreground_identity
    hwnd = foreground_identity()
    move_pointer(int(start_x), int(start_y), 0.05, expected_foreground=hwnd)
    try:
        if foreground_identity() != hwnd:
            raise RuntimeError("Foreground changed before drag")
        desktop_mouse_down(chosen)
        move_pointer(int(end_x), int(end_y), seconds, expected_foreground=hwnd)
    finally:
        try:
            desktop_mouse_up(chosen)
        except Exception:
            release_held_inputs()
            raise
    point = pyautogui.position()
    if int(point.x) != int(end_x) or int(point.y) != int(end_y):
        raise RuntimeError(f"Pointer did not reach drag destination; current=({point.x},{point.y})")
    return f"DELIVERED: dragged {chosen} from ({int(start_x)}, {int(start_y)}) to ({int(end_x)}, {int(end_y)}); pointer destination verified, application outcome requires fresh observation"


def desktop_mouse_down(button: str = "left") -> str:
    _windows_only()
    import pyautogui

    chosen = _button(button)
    from .process_control import check_cancelled
    check_cancelled()
    _HELD_MOUSE_BUTTONS.add(chosen)
    pyautogui.mouseDown(button=chosen, _pause=False)
    return f"Mouse {chosen} button down"


def desktop_mouse_up(button: str = "left") -> str:
    _windows_only()
    import pyautogui

    chosen = _button(button)
    pyautogui.mouseUp(button=chosen, _pause=False)
    _HELD_MOUSE_BUTTONS.discard(chosen)
    return f"Mouse {chosen} button up"


def desktop_key_down(key: str) -> str:
    _windows_only()
    import pyautogui

    value = _key(key)
    from .process_control import check_cancelled
    check_cancelled()
    if value not in pyautogui.KEYBOARD_KEYS:
        raise ValueError("Unsupported keyboard key")
    _HELD_KEYS.add(value)
    # Per-call pause suppression preserves PyAutoGUI's fail-safe check and avoids
    # four global 100ms pauses for an ordinary two-key shortcut.
    pyautogui.keyDown(value, _pause=False)
    return f"Key down: {value}"


def desktop_key_up(key: str) -> str:
    _windows_only()
    import pyautogui

    value = _key(key)
    pyautogui.keyUp(value, _pause=False)
    _HELD_KEYS.discard(value)
    return f"Key up: {value}"


def release_held_inputs() -> None:
    """Best-effort fail-safe so an interrupted mission never leaves synthetic input held down."""
    if os.name != "nt" or (not _HELD_MOUSE_BUTTONS and not _HELD_KEYS):
        _HELD_MOUSE_BUTTONS.clear()
        _HELD_KEYS.clear()
        return
    try:
        import pyautogui
        previous_failsafe, previous_pause = pyautogui.FAILSAFE, pyautogui.PAUSE
        # Releasing synthetic inputs must work even when the pointer is in a fail-safe corner.
        pyautogui.FAILSAFE, pyautogui.PAUSE = False, 0
        for key in list(_HELD_KEYS):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        for button in list(_HELD_MOUSE_BUTTONS):
            try:
                pyautogui.mouseUp(button=button)
            except Exception:
                pass
    finally:
        if "previous_failsafe" in locals():
            pyautogui.FAILSAFE, pyautogui.PAUSE = previous_failsafe, previous_pause
        _HELD_KEYS.clear()
        _HELD_MOUSE_BUTTONS.clear()


def register_desktop_control_tools(registry: ToolRegistry) -> None:
    coordinate = {"type": "integer", "minimum": -32768, "maximum": 32767}
    registry.register(ToolSpec(
        "desktop_cursor",
        "Read the current mouse cursor position and primary virtual screen dimensions.",
        Risk.LOW,
        {"type": "object", "properties": {}, "additionalProperties": False},
        desktop_cursor,
    ))
    registry.register(ToolSpec(
        "desktop_click_button",
        "Click a precise desktop coordinate with left, right, or middle mouse button. Supports up to three clicks.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "x": coordinate,
                "y": coordinate,
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        desktop_click_button,
    ))
    registry.register(ToolSpec(
        "desktop_drag",
        "Drag from one desktop coordinate to another with a bounded duration and mouse button, then verify the pointer reached the destination.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "start_x": coordinate,
                "start_y": coordinate,
                "end_x": coordinate,
                "end_y": coordinate,
                "duration": {"type": "number", "minimum": 0.05, "maximum": 2.0},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
            "additionalProperties": False,
        },
        desktop_drag,
    ))
    for name, description, handler in (
        ("desktop_mouse_down", "Hold a mouse button down for a controlled multi-step gesture. Any unreleased synthetic button is automatically released when the mission ends.", desktop_mouse_down),
        ("desktop_mouse_up", "Release a mouse button previously held down.", desktop_mouse_up),
    ):
        registry.register(ToolSpec(
            name,
            description,
            Risk.MEDIUM,
            {"type": "object", "properties": {"button": {"type": "string", "enum": ["left", "right", "middle"]}}, "additionalProperties": False},
            handler,
        ))
    for name, description, handler in (
        ("desktop_key_down", "Hold a keyboard key down for a controlled multi-step shortcut or gesture. Any unreleased synthetic key is automatically released when the mission ends.", desktop_key_down),
        ("desktop_key_up", "Release a keyboard key previously held down.", desktop_key_up),
    ):
        registry.register(ToolSpec(
            name,
            description,
            Risk.MEDIUM,
            {"type": "object", "properties": {"key": {"type": "string", "minLength": 1, "maxLength": 32}}, "required": ["key"], "additionalProperties": False},
            handler,
        ))
