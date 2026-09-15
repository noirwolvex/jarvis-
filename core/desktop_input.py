from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import time


class InputDeliveryError(RuntimeError):
    """Some input may have reached the application; retrying could duplicate it."""


# INPUT's union must include MOUSEINPUT even for keyboard events. A keyboard-only
# union gives SendInput the wrong cbSize on 64-bit Windows (32 instead of 40).
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


def _uia_type_and_verify(title: str, text: str) -> tuple[bool, str]:
    """Use Windows UI Automation to set an editable document value and verify it."""
    if not title or not text:
        return False, ""
    attempted = False
    try:
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(title_re=re.escape(title))
        window.wait("visible", timeout=5)
        window.set_focus()

        # Modern Notepad exposes its document as an Edit control through UIA.
        edits = window.descendants(control_type="Edit")
        if not edits:
            return False, "No UIA Edit control found"

        target = next((edit for edit in edits if edit.is_visible() and edit.is_enabled()), edits[0])
        target.set_focus()
        attempted = True
        target.set_edit_text(text)
        time.sleep(0.25)

        try:
            actual = target.get_value()
        except Exception:
            actual = target.window_text()

        if actual == text:
            return True, f"Verified text in UIA editor ({len(text)} characters)"
        raise InputDeliveryError("UIA text did not match after writing; inspect before retrying")
    except Exception as exc:
        if attempted:
            raise InputDeliveryError("UIA write outcome is uncertain; automatic fallback refused") from exc
        return False, f"UIA unavailable: {type(exc).__name__}: {exc}"


def _send_unicode_text(text: str) -> None:
    """Send Unicode text directly through the Windows input pipeline."""
    if not text:
        return
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("Direct Unicode input is supported on Windows only")

    user32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    inputs: list[_INPUT] = []

    def append_unit(unit: int) -> None:
        inputs.append(_INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0))))
        inputs.append(_INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))

    for char in text.replace("\r\n", "\n"):
        codepoint = ord(char)
        if codepoint == 0x0A:
            append_unit(0x0D)
        elif codepoint <= 0xFFFF:
            append_unit(codepoint)
        else:
            codepoint -= 0x10000
            append_unit(0xD800 + (codepoint >> 10))
            append_unit(0xDC00 + (codepoint & 0x3FF))

    sent = user32.SendInput(len(inputs), (_INPUT * len(inputs))(*inputs), ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        raise InputDeliveryError(f"SendInput accepted {sent}/{len(inputs)} events; inspect before retrying")


def _clipboard_paste(text: str) -> None:
    import pyperclip
    import pyautogui

    pyperclip.copy(text)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(max(0.1, min(1.5, len(text) / 500)))


def paste_text(text: str, window_title: str | None = None, verify: bool = False) -> str:
    """Enter arbitrary Unicode text and optionally verify it in the target window."""
    if not text:
        return "Entered 0 characters"
    if len(text) > 4096 or "\0" in text:
        raise ValueError("Desktop text must contain at most 4096 characters and no NUL")

    if window_title:
        ok, detail = _uia_type_and_verify(window_title, text)
        if ok:
            return f"VERIFIED: {detail}"

    direct_error = None
    try:
        _send_unicode_text(text)
        time.sleep(max(0.08, min(1.5, len(text) / 600)))
        method = "Windows Unicode input"
    except InputDeliveryError:
        raise
    except Exception as exc:
        direct_error = f"{type(exc).__name__}: {exc}"
        _clipboard_paste(text)
        method = "clipboard fallback"

    if verify and window_title:
        # Verification must never set text again or refocus a different control.
        from pywinauto import Desktop
        window = Desktop(backend="uia").window(title_re=re.escape(window_title))
        edits = [edit for edit in window.descendants(control_type="Edit") if edit.is_visible() and edit.is_enabled()]
        if any(edit.get_value() == text for edit in edits):
            return f"VERIFIED: editor text matched after {method}"
        raise InputDeliveryError("Editor text did not match; inspect before retrying")
    else:
        failure = "verification not requested"

    if direct_error:
        return f"Entered {len(text)} characters via {method}; verification: {failure}"
    return f"Entered {len(text)} characters via {method}; verification: {failure}"
