from __future__ import annotations

import ctypes
import ctypes.wintypes
import time


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

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.wintypes.WORD),
            ("wScan", ctypes.wintypes.WORD),
            ("dwFlags", ctypes.wintypes.DWORD),
            ("time", ctypes.wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.wintypes.DWORD), ("u", INPUTUNION)]

    inputs: list[INPUT] = []

    def append_unit(unit: int) -> None:
        inputs.append(INPUT(INPUT_KEYBOARD, INPUTUNION(KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, None))))
        inputs.append(INPUT(INPUT_KEYBOARD, INPUTUNION(KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None))))

    for char in text:
        codepoint = ord(char)
        if codepoint == 0x0A:
            append_unit(0x0D)
            append_unit(0x0A)
        elif codepoint <= 0xFFFF:
            append_unit(codepoint)
        else:
            codepoint -= 0x10000
            append_unit(0xD800 + (codepoint >> 10))
            append_unit(0xDC00 + (codepoint & 0x3FF))

    sent = user32.SendInput(len(inputs), (INPUT * len(inputs))(*inputs), ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ctypes.WinError()


def paste_text(text: str) -> str:
    """Enter arbitrary Unicode text into the currently focused Windows application.

    Direct Win32 Unicode input is preferred because it does not depend on the
    clipboard or application-specific paste handling. Clipboard paste remains
    as a fallback for applications that reject injected Unicode keystrokes.
    """
    if not text:
        return "Pasted 0 characters"

    try:
        _send_unicode_text(text)
        time.sleep(max(0.08, min(1.5, len(text) / 600)))
        return f"Entered {len(text)} characters via Windows Unicode input"
    except Exception:
        import pyperclip
        import pyautogui

        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(max(0.1, min(1.5, len(text) / 500)))
        return f"Pasted {len(text)} characters into the focused application via clipboard fallback"
