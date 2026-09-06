from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import re
import subprocess
import time
from pathlib import Path


def _visible_windows() -> dict[int, str]:
    if os.name != "nt":
        return {}
    user32 = ctypes.windll.user32
    windows: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title:
                    windows[int(hwnd)] = title
        return True

    user32.EnumWindows(callback, 0)
    return windows


def _activate(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.45)
    if user32.GetForegroundWindow() != hwnd:
        import pyautogui
        pyautogui.keyDown("alt")
        pyautogui.keyUp("alt")
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.45)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("Could not focus the Notepad window")


def _is_notepad(command: str) -> bool:
    value = command.strip().lower().replace("/", "\\")
    return bool(re.fullmatch(r'notepad(?:\.exe)?(?:\s+.*)?', value))


def open_notepad_with_text(text: str, workspace: str | None = None) -> str:
    root = Path(workspace or os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    runtime = root / ".jarvis" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)

    path = runtime / f"jarvis-notepad-{int(time.time() * 1000)}.txt"
    path.write_text(text, encoding="utf-8", newline="")

    before = _visible_windows()
    process = subprocess.Popen(["notepad.exe", str(path)], shell=False)
    deadline = time.time() + 15
    hwnd: int | None = None

    while time.time() < deadline:
        current = _visible_windows()
        new_windows = [(h, title) for h, title in current.items() if h not in before]
        exact = [h for h, title in new_windows if path.stem.lower() in title.lower()]
        if exact:
            hwnd = exact[0]
            break
        # Notepad may reuse an existing process but still expose a new document window.
        existing = [h for h, title in current.items() if path.stem.lower() in title.lower()]
        if existing:
            hwnd = existing[0]
            break
        time.sleep(0.25)

    if hwnd is None:
        raise RuntimeError(f"Notepad launched but its document window was not detected (pid={process.pid})")

    _activate(hwnd)

    # The displayed document is sourced from the UTF-8 file we just created.
    # Verify the source exactly so the agent never reports a successful mutation
    # based solely on a synthetic key/paste event.
    actual = path.read_text(encoding="utf-8")
    if actual != text:
        raise RuntimeError("Notepad source verification failed")

    title = _visible_windows().get(hwnd, "")
    return f"VERIFIED: Notepad opened document '{path.name}' with the requested text ({len(text)} characters); window='{title}'"


def open_application_and_type(command: str, text: str, fallback) -> str:
    if os.name == "nt" and _is_notepad(command):
        return open_notepad_with_text(text)
    return fallback(command, text)
