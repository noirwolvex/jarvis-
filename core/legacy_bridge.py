from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .tools import _activate_hwnd, _window_title


@dataclass(frozen=True)
class ApplicationSpec:
    name: str
    command: list[str]
    process_names: tuple[str, ...]


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("The legacy application bridge is available on Windows only")


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _discord_spec() -> ApplicationSpec:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable; Discord installation cannot be resolved")
    root = Path(local) / "Discord"
    updater = root / "Update.exe"
    if updater.is_file():
        return ApplicationSpec("discord", [str(updater), "--processStart", "Discord.exe"], ("discord.exe",))
    candidates = sorted(root.glob("app-*/Discord.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return ApplicationSpec("discord", [str(candidates[0])], ("discord.exe",))
    raise FileNotFoundError("Discord installation was not found under LOCALAPPDATA\\Discord")


def _notepad_spec() -> ApplicationSpec:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = _first_existing(system_root / "System32" / "notepad.exe")
    resolved = str(executable) if executable else shutil.which("notepad.exe")
    if not resolved:
        raise FileNotFoundError("Notepad executable was not found")
    return ApplicationSpec("notepad", [resolved], ("notepad.exe",))


def _chrome_spec() -> ApplicationSpec:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    executable = _first_existing(
        local / "Google" / "Chrome" / "Application" / "chrome.exe",
        program_files / "Google" / "Chrome" / "Application" / "chrome.exe",
        program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
    )
    resolved = str(executable) if executable else shutil.which("chrome.exe")
    if not resolved:
        raise FileNotFoundError("Google Chrome executable was not found")
    return ApplicationSpec("chrome", [resolved], ("chrome.exe",))


def _vscode_spec() -> ApplicationSpec:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    executable = _first_existing(
        local / "Programs" / "Microsoft VS Code" / "Code.exe",
        program_files / "Microsoft VS Code" / "Code.exe",
    )
    if not executable:
        raise FileNotFoundError("Visual Studio Code executable was not found")
    return ApplicationSpec("vscode", [str(executable)], ("code.exe",))


def _application_spec(app: str) -> ApplicationSpec:
    _require_windows()
    normalized = str(app).strip().lower()
    factories = {
        "discord": _discord_spec,
        "notepad": _notepad_spec,
        "chrome": _chrome_spec,
        "vscode": _vscode_spec,
    }
    factory = factories.get(normalized)
    if not factory:
        raise PermissionError(f"Application is not allowlisted for the legacy bridge: {normalized or '<empty>'}")
    return factory()


def _matching_processes(process_names: tuple[str, ...]) -> list[tuple[int, str]]:
    import psutil

    expected = {name.lower() for name in process_names}
    matches: list[tuple[int, str]] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = str(process.info.get("name") or "").lower()
            if name in expected:
                matches.append((int(process.info["pid"]), name))
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
    return matches


def _window_for_processes(process_names: tuple[str, ...]) -> tuple[int, int] | None:
    process_ids = {pid for pid, _ in _matching_processes(process_names)}
    if not process_ids:
        return None

    user32 = ctypes.windll.user32
    visible: list[tuple[int, int]] = []
    hidden: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd: int, _lparam: int) -> bool:
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in process_ids:
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        pair = (int(hwnd), int(pid.value))
        if user32.IsWindowVisible(hwnd):
            visible.append(pair)
        else:
            hidden.append(pair)
        return True

    user32.EnumWindows(callback, 0)
    return visible[0] if visible else hidden[0] if hidden else None


def _verified_window(spec: ApplicationSpec) -> tuple[int, int, str] | None:
    match = _window_for_processes(spec.process_names)
    if not match:
        return None
    hwnd, owner_pid = match
    try:
        _activate_hwnd(hwnd)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(hwnd):
        return None
    title = _window_title(hwnd)
    if not title:
        return None
    live_pids = {pid for pid, _ in _matching_processes(spec.process_names)}
    if owner_pid not in live_pids:
        return None
    return hwnd, owner_pid, title


def launch_application(app: str) -> dict[str, object]:
    spec = _application_spec(app)

    existing = _verified_window(spec)
    launcher_pid: int | None = None
    if existing is None:
        process = subprocess.Popen(spec.command, shell=False)
        launcher_pid = process.pid
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            existing = _verified_window(spec)
            if existing:
                break
            time.sleep(0.25)

    if existing is None:
        raise RuntimeError(
            f"{spec.name} launch was requested but no visible window owned by {', '.join(spec.process_names)} was verified"
        )

    hwnd, owner_pid, title = existing
    focused = False
    try:
        _activate_hwnd(hwnd)
        focused = True
    except Exception:
        focused = False

    return {
        "ok": True,
        "action": "launch_application",
        "app": spec.name,
        "launcher_pid": launcher_pid,
        "process_id": owner_pid,
        "process_name": spec.process_names[0],
        "window_title": title,
        "window_handle": hwnd,
        "visible_window_verified": True,
        "process_identity_verified": True,
        "focused": focused,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 2 or args[0] != "launch":
            raise ValueError("usage: python -m core.legacy_bridge launch <allowlisted-app>")
        result = launch_application(args[1])
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
