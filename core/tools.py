from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .desktop_input import paste_text
from .permissions import PermissionEngine, Risk

_BROWSER = None
_PAGE = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: Risk
    input_schema: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self, permissions: PermissionEngine | None = None) -> None:
        self.permissions = permissions or PermissionEngine()
        self._tools: dict[str, ToolSpec] = {}
        self._register_builtin_tools()

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
            for spec in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict[str, Any], approved: bool = False) -> str:
        spec = self._tools.get(name)
        if not spec:
            return f"ERROR: Unknown tool: {name}"
        ok, reason = self.permissions.check(name, spec.risk, approved)
        if not ok:
            return f"PERMISSION_DENIED: {reason}"
        try:
            return spec.handler(**arguments)
        except Exception as exc:
            return f"ERROR executing {name}: {type(exc).__name__}: {exc}"

    def _register_builtin_tools(self) -> None:
        self.register(ToolSpec(
            "run_powershell",
            "Run a non-interactive PowerShell command. Use only when needed to accomplish the user's request.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            _run_powershell,
        ))
        self.register(ToolSpec(
            "open_application",
            "Open a Windows application or executable by command/name.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            _open_application,
        ))
        self.register(ToolSpec(
            "focus_window",
            "Find a visible Windows window by title and bring it to the foreground before another desktop action.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
            _focus_window,
        ))
        self.register(ToolSpec(
            "active_window",
            "Read the title of the currently focused Windows window.",
            Risk.LOW,
            {"type": "object", "properties": {}, "additionalProperties": False},
            _active_window,
        ))
        self.register(ToolSpec(
            "desktop_hotkey",
            "Press a Windows keyboard shortcut in the currently focused application, such as ctrl+l, ctrl+s, alt+tab, or ctrl+shift+esc.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"shortcut": {"type": "string"}}, "required": ["shortcut"]},
            _desktop_hotkey,
        ))
        self.register(ToolSpec(
            "desktop_press",
            "Press a single keyboard key in the currently focused Windows application, such as enter, esc, tab, backspace, up, down, left, or right.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
            _desktop_press,
        ))
        self.register(ToolSpec(
            "open_url",
            "Open an HTTP(S) URL in the user's default browser.",
            Risk.LOW,
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            _open_url,
        ))
        self.register(ToolSpec(
            "read_file",
            "Read a UTF-8 text file inside the configured JARVIS workspace.",
            Risk.LOW,
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            _read_file,
        ))
        self.register(ToolSpec(
            "write_file",
            "Write or replace a UTF-8 text file inside the configured JARVIS workspace.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            _write_file,
        ))
        self.register(ToolSpec(
            "list_directory",
            "List files and folders in a directory inside the configured workspace.",
            Risk.SAFE,
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            _list_directory,
        ))
        self.register(ToolSpec(
            "take_screenshot",
            "Capture the primary monitor and save a timestamped screenshot for visual automation.",
            Risk.LOW,
            {"type": "object", "properties": {}, "additionalProperties": False},
            _take_screenshot,
        ))
        self.register(ToolSpec(
            "browser_navigate",
            "Launch or reuse a visible Chromium browser and navigate to an HTTP(S) URL.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            _browser_navigate,
        ))
        self.register(ToolSpec(
            "browser_read_page",
            "Read the current browser page title and visible text.",
            Risk.LOW,
            {"type": "object", "properties": {}, "additionalProperties": False},
            _browser_read_page,
        ))
        self.register(ToolSpec(
            "browser_click",
            "Click an element on the current browser page using a CSS selector or visible text.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]},
            _browser_click,
        ))
        self.register(ToolSpec(
            "browser_type",
            "Type text into an element on the current browser page using a CSS selector.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]},
            _browser_type,
        ))
        self.register(ToolSpec(
            "desktop_click",
            "Click the Windows desktop at absolute screen coordinates.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]},
            _desktop_click,
        ))
        self.register(ToolSpec(
            "desktop_type",
            "Reliably paste arbitrary text into the currently focused Windows application.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            _desktop_type,
        ))


def _run_powershell(command: str) -> str:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return f"exit_code={completed.returncode}\n{output[-12000:]}"


def _open_application(command: str) -> str:
    process = subprocess.Popen(command, shell=True)
    return f"Started application: {command} (pid={process.pid})"


def _focus_window(title: str) -> str:
    if os.name != "nt":
        raise OSError("focus_window is supported on Windows only")
    if not title.strip():
        raise ValueError("Window title cannot be empty")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    matches: list[tuple[int, str]] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        window_title = buffer.value
        if title.lower() in window_title.lower():
            matches.append((hwnd, window_title))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    if not matches:
        raise RuntimeError(f"No visible window matched: {title}")

    hwnd, matched_title = matches[0]
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    if not user32.SetForegroundWindow(hwnd):
        raise RuntimeError(f"Windows refused to focus: {matched_title}")
    return f"Focused window: {matched_title}"


def _active_window() -> str:
    if os.name != "nt":
        raise OSError("active_window is supported on Windows only")

    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "No active window"
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    title = buffer.value or "Untitled window"
    return f"Active window: {title}"


def _desktop_hotkey(shortcut: str) -> str:
    import pyautogui

    parts = [part.strip().lower() for part in shortcut.replace("+", " ").split() if part.strip()]
    if not parts:
        raise ValueError("Shortcut cannot be empty")
    if len(parts) > 5:
        raise ValueError("Shortcut has too many keys")
    pyautogui.hotkey(*parts)
    return f"Pressed shortcut: {'+'.join(parts)}"


def _desktop_press(key: str) -> str:
    import pyautogui

    key = key.strip().lower()
    if not key:
        raise ValueError("Key cannot be empty")
    pyautogui.press(key)
    return f"Pressed key: {key}"


def _open_url(url: str) -> str:
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("Only http:// and https:// URLs are allowed")
    webbrowser.open(url)
    return f"Opened URL: {url}"


def _safe_path(path: str) -> Path:
    raw = Path(path).expanduser()
    workspace = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()
    resolved = (workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if workspace not in resolved.parents and resolved != workspace:
        raise PermissionError(f"Path is outside JARVIS_WORKSPACE: {resolved}")
    return resolved


def _read_file(path: str) -> str:
    return _safe_path(path).read_text(encoding="utf-8")[:20000]


def _write_file(path: str, content: str) -> str:
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {target}"


def _list_directory(path: str) -> str:
    target = _safe_path(path)
    entries = [{"name": p.name, "type": "directory" if p.is_dir() else "file"} for p in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]]
    return json.dumps({"path": str(target), "entries": entries}, ensure_ascii=False)


def _take_screenshot() -> str:
    from PIL import ImageGrab
    from datetime import datetime
    out_dir = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve() / ".jarvis" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"screen-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    ImageGrab.grab().save(path)
    return f"Screenshot saved to {path}"


def _browser_navigate(url: str) -> str:
    global _BROWSER, _PAGE
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("Only http:// and https:// URLs are allowed")
    from playwright.sync_api import sync_playwright
    if _PAGE is None:
        if _BROWSER is None:
            pw = sync_playwright().start()
            _BROWSER = pw.chromium.launch(headless=os.getenv("JARVIS_BROWSER_HEADLESS", "false").lower() == "true")
        _PAGE = _BROWSER.new_page()
    _PAGE.goto(url, wait_until="domcontentloaded", timeout=30000)
    return f"Loaded {_PAGE.title()} — {_PAGE.url}"


def _browser_read_page() -> str:
    if _PAGE is None:
        return "No browser page is open."
    return f"TITLE: {_PAGE.title()}\nURL: {_PAGE.url}\nTEXT:\n{_PAGE.locator('body').inner_text()[:20000]}"


def _browser_click(selector: str) -> str:
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    locator = _PAGE.get_by_text(selector, exact=True)
    if locator.count() == 0:
        locator = _PAGE.locator(selector)
    locator.first.click(timeout=15000)
    return f"Clicked: {selector}"


def _browser_type(selector: str, text: str) -> str:
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    _PAGE.locator(selector).first.fill(text, timeout=15000)
    return f"Typed {len(text)} characters into {selector}"


def _desktop_click(x: int, y: int) -> str:
    import pyautogui
    pyautogui.click(x=x, y=y)
    return f"Clicked desktop at ({x}, {y})"


def _desktop_type(text: str) -> str:
    return paste_text(text)
