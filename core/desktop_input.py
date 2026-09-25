from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import time


class InputDeliveryError(RuntimeError):
    """Some input may have reached the application; retrying could duplicate it."""


class InputNotDispatchedError(InputDeliveryError):
    """The requested text/input was rejected before delivery; no write needs review."""


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
        from .process_control import check_cancelled

        def ensure_active():
            try:
                check_cancelled()
            except Exception as exc:
                raise InputDeliveryError("UIA typing cancelled; automatic fallback refused") from exc

        ensure_active()
        window = Desktop(backend="uia").window(title_re=re.escape(title))
        window.wait("visible", timeout=5)
        ensure_active()
        window.set_focus()

        # Modern Notepad exposes its document as an Edit control through UIA.
        edits = window.descendants(control_type="Edit")
        ensure_active()
        if not edits:
            return False, "No UIA Edit control found"

        candidates = [edit for edit in edits if edit.is_visible() and edit.is_enabled()]
        if len(candidates) != 1:
            raise InputDeliveryError("Editor target is missing or ambiguous; resolve a semantic control first")
        target = candidates[0]
        ensure_active()
        target.set_focus()
        ensure_active()
        attempted = True
        target.set_edit_text(text)
        from .ui_state import wait_until
        def matches():
            try:
                return target.get_value() == text
            except Exception:
                return target.window_text() == text
        wait_until(matches, description="exact editor value")
        return True, f"Verified text in UIA editor ({len(text)} characters)"
    except InputDeliveryError:
        raise
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
    from .desktop_observation import foreground_identity
    from .process_control import check_cancelled
    try:
        check_cancelled()
        foreground = foreground_identity()
    except Exception as exc:
        raise InputDeliveryError("Native typing preflight failed; text was not sent") from exc
    if not foreground:
        raise InputDeliveryError("No foreground window; native text was not sent")

    user32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    inputs: list[_INPUT] = []

    def append_unit(unit: int) -> None:
        inputs.append(_INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0))))
        inputs.append(_INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))

    def deliver() -> None:
        if not inputs:
            return
        try:
            check_cancelled()
            if foreground_identity() != foreground:
                raise InputDeliveryError("Foreground changed during typing; inspect before continuing")
        except Exception as exc:
            raise InputDeliveryError("Native typing stopped; automatic fallback refused") from exc
        try:
            sent = user32.SendInput(len(inputs), (_INPUT * len(inputs))(*inputs), ctypes.sizeof(_INPUT))
        except Exception as exc:
            raise InputDeliveryError("Native typing outcome is uncertain; inspect before retrying") from exc
        if sent != len(inputs):
            # Release Unicode units from this batch only; never resend its text.
            releases = [item for item in inputs if item.ki.dwFlags & KEYEVENTF_KEYUP]
            try:
                user32.SendInput(len(releases), (_INPUT * len(releases))(*releases), ctypes.sizeof(_INPUT))
            except Exception:
                pass
            raise InputDeliveryError(f"SendInput accepted {sent}/{len(inputs)} events; inspect before retrying")
        inputs.clear()

    for char in text.replace("\r\n", "\n"):
        codepoint = ord(char)
        units = 1 if codepoint <= 0xFFFF else 2
        if len(inputs) + units * 2 > 128:
            deliver()
        if codepoint == 0x0A:
            append_unit(0x0D)
        elif codepoint <= 0xFFFF:
            append_unit(codepoint)
        else:
            codepoint -= 0x10000
            append_unit(0xD800 + (codepoint >> 10))
            append_unit(0xDC00 + (codepoint & 0x3FF))

    deliver()


def _clipboard_paste(text: str) -> None:
    import pyperclip
    import pyautogui
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False

    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(max(0.05, min(0.5, len(text) / 2000)))


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
