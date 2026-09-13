from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .tools import _activate_hwnd, _visible_windows, _window_for_pid, _window_for_process_tree, _window_title


def _discord_command() -> list[str]:
    if os.name != "nt":
        raise RuntimeError("The legacy application bridge is available on Windows only")
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable; Discord installation cannot be resolved")
    root = Path(local) / "Discord"
    updater = root / "Update.exe"
    if updater.is_file():
        return [str(updater), "--processStart", "Discord.exe"]
    candidates = sorted(root.glob("app-*/Discord.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return [str(candidates[0])]
    raise FileNotFoundError("Discord installation was not found under LOCALAPPDATA\\Discord")


def _discord_window() -> int | None:
    try:
        import psutil

        for process in psutil.process_iter(["pid", "name"]):
            try:
                if str(process.info.get("name") or "").lower() == "discord.exe":
                    hwnd = _window_for_pid(int(process.info["pid"]))
                    if hwnd:
                        return hwnd
            except (psutil.Error, OSError, ValueError, TypeError):
                continue
    except Exception:
        pass

    for hwnd, title in _visible_windows().items():
        if "discord" in title.lower():
            return hwnd
    return None


def launch_application(app: str) -> dict[str, object]:
    normalized = str(app).strip().lower()
    if normalized != "discord":
        raise PermissionError(f"Application is not allowlisted for the legacy bridge: {normalized or '<empty>'}")

    command = _discord_command()
    process = subprocess.Popen(command, shell=False)
    deadline = time.monotonic() + 25.0
    hwnd: int | None = None
    while time.monotonic() < deadline:
        hwnd = _window_for_process_tree(process.pid) or _discord_window()
        if hwnd:
            break
        time.sleep(0.25)

    if hwnd is None:
        raise RuntimeError(f"Discord launch was requested but no visible Discord window was verified (launcher_pid={process.pid})")

    title = _window_title(hwnd)
    focused = False
    try:
        _activate_hwnd(hwnd)
        focused = True
    except Exception:
        # Visibility is the launch postcondition. Foreground activation is best effort
        # because Windows may intentionally reject focus stealing.
        focused = False

    return {
        "ok": True,
        "action": "launch_application",
        "app": "discord",
        "launcher_pid": process.pid,
        "window_title": title,
        "window_handle": hwnd,
        "visible_window_verified": True,
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
