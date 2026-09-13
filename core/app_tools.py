from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import time
from difflib import SequenceMatcher
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_START_APPS_SCRIPT = r'''
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$q = $env:JARVIS_APP_QUERY
Get-StartApps |
    Where-Object { $_.Name -like ('*' + $q + '*') } |
    Select-Object -First 25 Name, AppID |
    ConvertTo-Json -Compress
'''


def _clean_query(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text or len(text) > 160 or "\0" in text:
        raise ValueError("Application name must contain 1-160 safe characters")
    text = re.sub(r"\s+(app|application)$", "", text, flags=re.IGNORECASE).strip()
    return text or str(value).strip()


def _decode_start_apps(stdout: str) -> list[dict[str, str]]:
    raw = stdout.strip()
    if not raw:
        return []
    payload: Any = json.loads(raw)
    rows = payload if isinstance(payload, list) else [payload]
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        app_id = str(row.get("AppID") or "").strip()
        if name and app_id:
            result.append({"name": name, "app_id": app_id})
    return result


def _start_apps(query: str) -> list[dict[str, str]]:
    if os.name != "nt":
        raise RuntimeError("Installed-app discovery is supported on Windows only")
    cleaned = _clean_query(query)
    env = dict(os.environ)
    env["JARVIS_APP_QUERY"] = cleaned
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _START_APPS_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=12,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Get-StartApps failed").strip()
        raise RuntimeError(detail[-2000:])
    return _decode_start_apps(completed.stdout)


def _score(query: str, candidate: str) -> float:
    q = _clean_query(query).casefold()
    c = candidate.casefold().strip()
    if c == q:
        return 100.0
    if c.startswith(q):
        return 90.0
    if q in c:
        return 80.0
    return SequenceMatcher(None, q, c).ratio() * 60.0


def _resolve(query: str) -> dict[str, str]:
    rows = _start_apps(query)
    if not rows:
        raise FileNotFoundError(f"No installed Start application matched: {query}")
    ranked = sorted(rows, key=lambda row: _score(query, row["name"]), reverse=True)
    best = ranked[0]
    if _score(query, best["name"]) < 35.0:
        raise FileNotFoundError(f"No confident installed application match for: {query}")
    return best


def find_installed_app(query: str) -> str:
    rows = _start_apps(query)
    ranked = sorted(rows, key=lambda row: _score(query, row["name"]), reverse=True)
    return json.dumps(ranked[:10], ensure_ascii=False)


def _visible_windows() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    result: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append({"hwnd": int(hwnd), "title": title, "pid": int(pid.value)})
        return True

    user32.EnumWindows(callback, 0)
    return result


def _window_score(query: str, resolved_name: str, title: str) -> float:
    title_fold = title.casefold()
    values = [_clean_query(query).casefold(), resolved_name.casefold()]
    tokens = []
    for value in values:
        if value and value in title_fold:
            return 100.0
        tokens.extend(token for token in re.split(r"[^\w]+", value) if len(token) >= 3)
    hits = sum(token in title_fold for token in set(tokens))
    return float(hits * 20)


def _focus(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return int(user32.GetForegroundWindow()) == int(hwnd)


def launch_installed_app(query: str, timeout_seconds: float = 15.0) -> str:
    if os.name != "nt":
        raise RuntimeError("Installed-app launch is supported on Windows only")
    app = _resolve(query)
    before = {row["hwnd"] for row in _visible_windows()}
    target = f"shell:AppsFolder\\{app['app_id']}"
    subprocess.Popen(["explorer.exe", target], shell=False)

    deadline = time.time() + max(2.0, min(float(timeout_seconds), 20.0))
    best: dict[str, Any] | None = None
    while time.time() < deadline:
        windows = _visible_windows()
        ranked = sorted(
            windows,
            key=lambda row: (
                _window_score(query, app["name"], row["title"]),
                1 if row["hwnd"] not in before else 0,
            ),
            reverse=True,
        )
        if ranked and _window_score(query, app["name"], ranked[0]["title"]) >= 20.0:
            best = ranked[0]
            break
        time.sleep(0.35)

    if best is None:
        raise RuntimeError(
            f"Launched installed app '{app['name']}' but no matching visible window was verified within the timeout"
        )

    focused = _focus(int(best["hwnd"]))
    process_name = "unknown"
    try:
        import psutil
        process_name = psutil.Process(int(best["pid"])).name()
    except Exception:
        pass

    payload = {
        "query": _clean_query(query),
        "resolved_name": app["name"],
        "app_id": app["app_id"],
        "window_title": best["title"],
        "window_handle": best["hwnd"],
        "process_id": best["pid"],
        "process_name": process_name,
        "focused": focused,
        "visible_window_verified": True,
    }
    return "VERIFIED: " + json.dumps(payload, ensure_ascii=False)


def register_app_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "find_installed_app",
        "Find installed Windows applications by friendly name through the Start Apps catalog. Use this instead of PowerShell for application discovery.",
        Risk.LOW,
        {
            "type": "object",
            "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 160}},
            "required": ["query"],
            "additionalProperties": False,
        },
        find_installed_app,
    ))
    registry.register(ToolSpec(
        "launch_installed_app",
        "Resolve and launch an installed Windows application by friendly name, including Microsoft Store/Start-menu apps, then verify a matching visible window. Prefer this over open_application or run_powershell for requests like open WhatsApp, Spotify, Telegram, or another installed app. After success, record task verification using the returned VERIFIED evidence.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 160},
                "timeout_seconds": {"type": "number", "minimum": 2, "maximum": 20},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        launch_installed_app,
    ))
