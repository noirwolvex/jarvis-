from __future__ import annotations

import ctypes.wintypes  # Ensure ctypes.wintypes is registered for Win32 helpers.
import time


def paste_text(text: str) -> str:
    """Paste arbitrary text into the currently focused Windows application."""
    if not text:
        return "Pasted 0 characters"

    import pyperclip
    import pyautogui

    pyperclip.copy(text)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(max(0.1, min(1.5, len(text) / 500)))
    return f"Pasted {len(text)} characters into the focused application"
