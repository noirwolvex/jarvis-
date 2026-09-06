from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
from pathlib import Path

WM_GETTEXT = 0x000D
EM_GETTEXTLENGTH = 0x000E
EM_GETTEXT = 0x000D


def _user32():
    if os.name != "nt":
        raise RuntimeError("Notepad automation is supported on Windows only")
    return ctypes.windll.user32


def _foreground_hwnd() -> int:
    hwnd = int(_user32().GetForegroundWindow())
    if not hwnd:
        raise RuntimeError("No foreground window")
    return hwnd


def _window_title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value


def _children(hwnd: int) -> list[int]:
    result: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(child: int, _lparam: int) -> bool:
        result.append(int(child))
        return True

    _user32().EnumChildWindows(hwnd, callback, 0)
    return result


def _read_edit(hwnd: int) -> str:
    user32 = _user32()
    length = int(user32.SendMessageW(hwnd, EM_GETTEXTLENGTH, 0, 0))
    if length < 0 or length > 2_000_000:
        raise RuntimeError("Unsupported editor text length")
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, EM_GETTEXT, length + 1, ctypes.cast(buf, ctypes.c_void_p))
    return buf.value


def _find_text_editor(hwnd: int) -> int | None:
    for child in reversed(_children(hwnd)):
        cls = _class_name(child).lower()
        if "richedit" in cls or "notepadtextbox" in cls:
            return child
    return None


def _safe_target(path: str) -> Path:
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    raw = Path(path).expanduser()
    resolved = (workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if workspace not in resolved.parents and resolved != workspace:
        raise PermissionError(f"Path is outside JARVIS_WORKSPACE: {resolved}")
    return resolved


def notepad_save_as(path: str) -> str:
    hwnd = _foreground_hwnd()
    title = _window_title(hwnd)
    if "notepad" not in title.lower():
        raise RuntimeError(f"Foreground window is not Notepad: {title}")

    editor = _find_text_editor(hwnd)
    if editor is None:
        raise RuntimeError("Could not locate the Notepad text editor")
    content = _read_edit(editor)
    target = _safe_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="")
    if target.read_text(encoding="utf-8") != content:
        raise RuntimeError(f"Save verification failed for {target}")
    return f"VERIFIED: saved current Notepad text to {target} ({len(content)} characters); source_window='{title}'"
