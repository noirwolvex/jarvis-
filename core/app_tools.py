from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec

_START_APPS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$q = $env:JARVIS_APP_QUERY
Get-StartApps |
    Where-Object { $_.Name -like ('*' + $q + '*') } |
    Select-Object -First 40 Name, AppID |
    ConvertTo-Json -Compress
"""

_SOURCE_BONUS = {
    "explicit_path": 18.0,
    "start_apps": 14.0,
    "app_paths": 12.0,
    "path": 10.0,
    "start_menu": 9.0,
    "uninstall": 7.0,
    "common_install": 5.0,
}

_BLOCKED_EXECUTABLE_STEMS = {
    "cmd",
    "powershell",
    "pwsh",
    "wscript",
    "cscript",
    "mshta",
    "rundll32",
    "regsvr32",
    "certutil",
    "msiexec",
    "python",
    "python3",
    "pythonw",
    "py",
    "node",
    "nodejs",
    "bash",
    "sh",
    "wsl",
    "wmic",
    "reg",
    "schtasks",
    "sc",
    "net",
    "netsh",
}


def _clean_query(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text or len(text) > 260 or "\0" in text:
        raise ValueError("Application name/path must contain 1-260 safe characters")
    text = re.sub(r"\s+(app|application)$", "", text, flags=re.IGNORECASE).strip()
    return text or str(value).strip()


def _score(query: str, candidate: str) -> float:
    q = _clean_query(query).casefold()
    c = str(candidate).casefold().strip()
    if not c:
        return 0.0
    if c == q:
        return 100.0
    if c.startswith(q):
        return 92.0
    if q in c:
        return 84.0

    q_tokens = [token for token in re.split(r"[^\w]+", q) if len(token) >= 2]
    c_tokens = [token for token in re.split(r"[^\w]+", c) if len(token) >= 2]
    if q_tokens and c_tokens:
        overlap = len(set(q_tokens) & set(c_tokens)) / max(1, len(set(q_tokens)))
        if overlap:
            return max(SequenceMatcher(None, q, c).ratio() * 60.0, 52.0 + overlap * 28.0)
    return SequenceMatcher(None, q, c).ratio() * 60.0


def _is_blocked_executable(path: str) -> bool:
    stem = Path(str(path).strip().strip('"')).stem.casefold()
    return stem in _BLOCKED_EXECUTABLE_STEMS


def _candidate(
    name: str,
    source: str,
    launch_type: str,
    launch_value: str,
    *,
    process_hint: str = "",
) -> dict[str, str]:
    return {
        "name": str(name).strip(),
        "source": source,
        "launch_type": launch_type,
        "launch_value": str(launch_value).strip(),
        "process_hint": str(process_hint).strip(),
    }


def _candidate_identity(row: dict[str, str]) -> tuple[str, str]:
    value = (
        os.path.normcase(os.path.normpath(row["launch_value"]))
        if row["launch_type"] != "apps_folder"
        else row["launch_value"].casefold()
    )
    return row["launch_type"], value


def _rank_candidates(query: str, candidates: list[dict[str, str]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for row in candidates:
        if not row.get("name") or not row.get("launch_value"):
            continue
        key = _candidate_identity(row)
        previous = deduped.get(key)
        if previous is None or _SOURCE_BONUS.get(row.get("source", ""), 0.0) > _SOURCE_BONUS.get(
            previous.get("source", ""), 0.0
        ):
            deduped[key] = row

    ranked: list[dict[str, Any]] = []
    for row in deduped.values():
        names = [row["name"]]
        if row["launch_type"] in {"executable", "shortcut"}:
            names.append(Path(row["launch_value"]).stem)
        best_name_score = max(_score(query, value) for value in names if value)
        total = min(120.0, best_name_score + _SOURCE_BONUS.get(row["source"], 0.0))
        ranked.append({**row, "score": round(total, 2)})

    ranked.sort(
        key=lambda row: (float(row["score"]), _SOURCE_BONUS.get(str(row["source"]), 0.0)),
        reverse=True,
    )
    return ranked


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
            result.append(_candidate(name, "start_apps", "apps_folder", app_id, process_hint=name))
    return result


def _start_apps(query: str) -> list[dict[str, str]]:
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


def _explicit_path_candidates(query: str) -> list[dict[str, str]]:
    raw = os.path.expandvars(os.path.expanduser(_clean_query(query).strip('"')))
    if not any(mark in raw for mark in ("\\", "/", ":")):
        return []
    path = Path(raw)
    if not path.is_file():
        return []
    suffix = path.suffix.casefold()
    if suffix == ".exe":
        if _is_blocked_executable(str(path)):
            raise PermissionError(f"Executable is not allowed through the application launcher: {path.name}")
        return [
            _candidate(
                path.stem,
                "explicit_path",
                "executable",
                str(path.resolve()),
                process_hint=path.stem,
            )
        ]
    if suffix == ".lnk":
        return [
            _candidate(
                path.stem,
                "explicit_path",
                "shortcut",
                str(path.resolve()),
                process_hint=path.stem,
            )
        ]
    return []


def _registry_app_path_candidates(query: str) -> list[dict[str, str]]:
    try:
        import winreg
    except ImportError:
        return []

    result: list[dict[str, str]] = []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    views = [0]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        views.extend([winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY])

    seen: set[tuple[int, str, int]] = set()
    for hive, base, view in locations:
        marker = (int(hive), base, int(view))
        if marker in seen:
            continue
        seen.add(marker)
        try:
            with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as root:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(min(count, 5000)):
                    try:
                        sub_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    friendly = Path(sub_name).stem
                    if _score(query, friendly) < 25.0:
                        continue
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            value = str(winreg.QueryValue(sub, None) or "").strip().strip('"')
                    except OSError:
                        continue
                    path = Path(os.path.expandvars(value))
                    if (
                        path.is_file()
                        and path.suffix.casefold() == ".exe"
                        and not _is_blocked_executable(str(path))
                    ):
                        result.append(
                            _candidate(
                                friendly,
                                "app_paths",
                                "executable",
                                str(path),
                                process_hint=path.stem,
                            )
                        )
        except OSError:
            continue
    return result


def _path_candidates(query: str) -> list[dict[str, str]]:
    cleaned = _clean_query(query)
    result: list[dict[str, str]] = []
    requested = [cleaned, f"{cleaned}.exe"]
    for value in requested:
        resolved = shutil.which(value)
        if (
            resolved
            and Path(resolved).suffix.casefold() == ".exe"
            and not _is_blocked_executable(resolved)
        ):
            result.append(
                _candidate(
                    Path(resolved).stem,
                    "path",
                    "executable",
                    resolved,
                    process_hint=Path(resolved).stem,
                )
            )

    inspected = 0
    for raw_dir in os.getenv("PATH", "").split(os.pathsep):
        directory = Path(raw_dir.strip('"'))
        if not directory.is_dir():
            continue
        try:
            for child in directory.iterdir():
                inspected += 1
                if inspected > 6000:
                    return result
                if not child.is_file() or child.suffix.casefold() != ".exe":
                    continue
                if _score(cleaned, child.stem) < 45.0 or _is_blocked_executable(str(child)):
                    continue
                result.append(
                    _candidate(
                        child.stem,
                        "path",
                        "executable",
                        str(child),
                        process_hint=child.stem,
                    )
                )
        except OSError:
            continue
    return result


def _start_menu_roots() -> list[Path]:
    roots: list[Path] = []
    appdata = os.getenv("APPDATA")
    programdata = os.getenv("PROGRAMDATA")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if programdata:
        roots.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return roots


def _start_menu_candidates(query: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    inspected = 0
    for root in _start_menu_roots():
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.lnk"):
                inspected += 1
                if inspected > 5000:
                    return result
                if _score(query, path.stem) < 35.0:
                    continue
                result.append(
                    _candidate(
                        path.stem,
                        "start_menu",
                        "shortcut",
                        str(path),
                        process_hint=path.stem,
                    )
                )
        except OSError:
            continue
    return result


def _parse_display_icon(value: str) -> str:
    raw = os.path.expandvars(str(value or "").strip())
    if not raw:
        return ""
    if raw.startswith('"'):
        match = re.match(r'^"([^"]+)"', raw)
        if match:
            return match.group(1)
    if "," in raw:
        raw = raw.rsplit(",", 1)[0]
    return raw.strip().strip('"')


def _scan_executables(
    directory: Path,
    query: str,
    max_depth: int = 2,
    max_files: int = 1800,
) -> list[dict[str, str]]:
    if not directory.is_dir():
        return []
    result: list[dict[str, str]] = []
    base_depth = len(directory.parts)
    inspected = 0
    for root, dirs, files in os.walk(directory):
        current = Path(root)
        depth = len(current.parts) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        for filename in files:
            inspected += 1
            if inspected > max_files:
                return result
            if not filename.casefold().endswith(".exe"):
                continue
            path = current / filename
            if _score(query, path.stem) < 38.0 or _is_blocked_executable(str(path)):
                continue
            result.append(
                _candidate(
                    path.stem,
                    "common_install",
                    "executable",
                    str(path),
                    process_hint=path.stem,
                )
            )
    return result


def _registry_uninstall_candidates(query: str) -> list[dict[str, str]]:
    try:
        import winreg
    except ImportError:
        return []

    result: list[dict[str, str]] = []
    locations = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    views = [0]
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        views.extend([winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY])

    seen: set[tuple[int, str, int]] = set()
    for hive, base, view in locations:
        marker = (int(hive), base, int(view))
        if marker in seen:
            continue
        seen.add(marker)
        try:
            with winreg.OpenKey(hive, base, 0, winreg.KEY_READ | view) as root:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(min(count, 6000)):
                    try:
                        sub_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, sub_name) as sub:
                            try:
                                display_name = str(
                                    winreg.QueryValueEx(sub, "DisplayName")[0] or ""
                                ).strip()
                            except OSError:
                                continue
                            if _score(query, display_name) < 35.0:
                                continue

                            display_icon = ""
                            install_location = ""
                            try:
                                display_icon = _parse_display_icon(
                                    str(winreg.QueryValueEx(sub, "DisplayIcon")[0] or "")
                                )
                            except OSError:
                                pass
                            try:
                                install_location = str(
                                    winreg.QueryValueEx(sub, "InstallLocation")[0] or ""
                                ).strip()
                            except OSError:
                                pass
                    except OSError:
                        continue

                    icon_path = Path(display_icon)
                    if (
                        icon_path.is_file()
                        and icon_path.suffix.casefold() == ".exe"
                        and not _is_blocked_executable(str(icon_path))
                    ):
                        result.append(
                            _candidate(
                                display_name,
                                "uninstall",
                                "executable",
                                str(icon_path),
                                process_hint=icon_path.stem,
                            )
                        )
                        continue

                    install_dir = (
                        Path(os.path.expandvars(install_location.strip('"')))
                        if install_location
                        else None
                    )
                    if install_dir and install_dir.is_dir():
                        matches = _scan_executables(
                            install_dir,
                            query,
                            max_depth=2,
                            max_files=900,
                        )
                        for match in matches[:8]:
                            match["name"] = display_name
                            match["source"] = "uninstall"
                            result.append(match)
        except OSError:
            continue
    return result


def _common_install_roots() -> list[Path]:
    values = [
        os.getenv("ProgramFiles"),
        os.getenv("ProgramFiles(x86)"),
        os.getenv("LOCALAPPDATA"),
    ]
    roots: list[Path] = []
    localappdata = os.getenv("LOCALAPPDATA")
    for value in values:
        if not value:
            continue
        root = Path(value)
        if localappdata and value == localappdata:
            root = root / "Programs"
        if root.is_dir() and root not in roots:
            roots.append(root)
    return roots


def _matching_install_dirs(
    root: Path,
    query: str,
    max_depth: int = 2,
    max_dirs: int = 3000,
) -> list[Path]:
    result: list[Path] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    inspected = 0
    while queue and inspected < max_dirs:
        current, depth = queue.pop(0)
        try:
            children = [child for child in current.iterdir() if child.is_dir()]
        except OSError:
            continue
        for child in children:
            inspected += 1
            if _score(query, child.name) >= 38.0:
                result.append(child)
            if depth + 1 < max_depth:
                queue.append((child, depth + 1))
            if inspected >= max_dirs:
                break
    return result


def _common_install_candidates(query: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen_dirs: set[str] = set()
    for root in _common_install_roots():
        for directory in _matching_install_dirs(root, query):
            key = os.path.normcase(str(directory))
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            matches = _scan_executables(
                directory,
                query,
                max_depth=3,
                max_files=1400,
            )
            result.extend(matches[:12])
            if len(result) >= 50:
                return result
    return result


def _discover_candidates(query: str) -> list[dict[str, Any]]:
    if os.name != "nt":
        raise RuntimeError("Installed-app discovery is supported on Windows only")

    cleaned = _clean_query(query)
    candidates: list[dict[str, str]] = []
    candidates.extend(_explicit_path_candidates(cleaned))

    sources = (
        _start_apps,
        _registry_app_path_candidates,
        _path_candidates,
        _start_menu_candidates,
        _registry_uninstall_candidates,
        _common_install_candidates,
    )
    for source in sources:
        try:
            candidates.extend(source(cleaned))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue

    return _rank_candidates(cleaned, candidates)


def _resolve(query: str) -> dict[str, Any]:
    ranked = _discover_candidates(query)
    if not ranked:
        raise FileNotFoundError(f"No installed application matched: {query}")
    best = ranked[0]
    if float(best["score"]) < 50.0:
        raise FileNotFoundError(f"No confident installed application match for: {query}")
    if len(ranked) > 1:
        second = ranked[1]
        if (
            float(best["score"]) < 100.0
            and abs(float(best["score"]) - float(second["score"])) < 3.0
            and str(best["name"]).casefold() != str(second["name"]).casefold()
        ):
            choices = ", ".join(
                f"{row['name']} ({row['source']})" for row in ranked[:4]
            )
            raise RuntimeError(
                f"Application name is ambiguous. Top matches: {choices}"
            )
    return best


def find_installed_app(query: str) -> str:
    ranked = _discover_candidates(query)
    public = [
        {
            "name": row["name"],
            "source": row["source"],
            "launch_type": row["launch_type"],
            "score": row["score"],
        }
        for row in ranked[:15]
    ]
    return json.dumps(public, ensure_ascii=False)


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


def _processes() -> list[dict[str, Any]]:
    try:
        import psutil
    except ImportError:
        return []
    rows: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = process.info
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or ""),
                    "exe": str(info.get("exe") or ""),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return rows


def _window_score(query: str, resolved_name: str, title: str) -> float:
    title_fold = title.casefold()
    values = [_clean_query(query).casefold(), resolved_name.casefold()]
    tokens: list[str] = []
    for value in values:
        if value and value in title_fold:
            return 100.0
        tokens.extend(
            token for token in re.split(r"[^\w]+", value) if len(token) >= 3
        )
    hits = sum(token in title_fold for token in set(tokens))
    return float(hits * 20)


def _process_score(
    query: str,
    app: dict[str, Any],
    row: dict[str, Any],
) -> float:
    values = [
        str(row.get("name") or ""),
        Path(str(row.get("exe") or "")).stem,
    ]
    hints = [
        _clean_query(query),
        str(app.get("name") or ""),
        str(app.get("process_hint") or ""),
    ]
    best = 0.0
    for hint in hints:
        if not hint:
            continue
        for value in values:
            if value:
                best = max(best, _score(hint, value))
    if app.get("launch_type") == "executable":
        expected = os.path.normcase(
            os.path.abspath(str(app.get("launch_value") or ""))
        )
        actual = (
            os.path.normcase(os.path.abspath(str(row.get("exe") or "")))
            if row.get("exe")
            else ""
        )
        if expected and actual and expected == actual:
            return 120.0
    return best


def _focus(hwnd: int) -> bool:
    """Bring a verified visible window forward without paying a fixed sleep when Windows responds quickly."""
    from .process_control import check_cancelled
    from .ui_state import wait_until

    user32 = ctypes.windll.user32
    check_cancelled()
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    try:
        wait_until(
            lambda: int(user32.GetForegroundWindow()) == int(hwnd),
            timeout=0.8,
            interval=0.025,
            description="launched application foreground",
        )
        return True
    except TimeoutError:
        return False


def _launch_candidate(app: dict[str, Any]) -> int | None:
    launch_type = str(app["launch_type"])
    value = str(app["launch_value"])
    if launch_type == "apps_folder":
        process = subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{value}"],
            shell=False,
        )
        return int(process.pid)
    if launch_type == "executable":
        if _is_blocked_executable(value):
            raise PermissionError(
                f"Executable is not allowed through the application launcher: {Path(value).name}"
            )
        process = subprocess.Popen(
            [value],
            shell=False,
            cwd=str(Path(value).parent),
        )
        return int(process.pid)
    if launch_type == "shortcut":
        normalized = Path(value).resolve()
        allowed_roots = [
            root.resolve() for root in _start_menu_roots() if root.exists()
        ]
        explicit = str(app.get("source")) == "explicit_path"
        if not explicit and not any(
            root == normalized or root in normalized.parents
            for root in allowed_roots
        ):
            raise PermissionError(
                "Shortcut launch is limited to Start Menu shortcuts unless the user supplies an explicit shortcut path"
            )
        os.startfile(str(normalized))
        return None
    raise RuntimeError(f"Unsupported launch type: {launch_type}")


def launch_installed_app(
    query: str,
    timeout_seconds: float = 15.0,
) -> str:
    """Launch an installed GUI app and distinguish process-started from interaction-ready.

    A background process alone is not enough evidence for a chained desktop action. We keep
    polling for a matching visible window and foreground binding; only that state is VERIFIED.
    """
    if os.name != "nt":
        raise RuntimeError("Installed-app launch is supported on Windows only")

    from .process_control import check_cancelled
    from .ui_state import cancellable_delay

    app = _resolve(query)
    before_windows = {row["hwnd"] for row in _visible_windows()}
    before_pids = {row["pid"] for row in _processes()}
    launcher_pid = _launch_candidate(app)

    deadline = time.monotonic() + max(
        2.0,
        min(float(timeout_seconds), 25.0),
    )
    best_window: dict[str, Any] | None = None
    best_process: dict[str, Any] | None = None
    focused = False

    while time.monotonic() < deadline:
        check_cancelled()
        windows = _visible_windows()
        ranked_windows = sorted(
            windows,
            key=lambda row: (
                _window_score(
                    query,
                    str(app["name"]),
                    str(row["title"]),
                ),
                1 if row["hwnd"] not in before_windows else 0,
            ),
            reverse=True,
        )
        if (
            ranked_windows
            and _window_score(
                query,
                str(app["name"]),
                str(ranked_windows[0]["title"]),
            )
            >= 20.0
        ):
            best_window = ranked_windows[0]
            focused = _focus(int(best_window["hwnd"]))
            if focused:
                break

        processes = _processes()
        ranked_processes = sorted(
            processes,
            key=lambda row: (
                _process_score(query, app, row),
                1 if row["pid"] not in before_pids else 0,
            ),
            reverse=True,
        )
        if (
            ranked_processes
            and _process_score(query, app, ranked_processes[0]) >= 58.0
        ):
            best_process = ranked_processes[0]

        # A process can appear hundreds of milliseconds before its UI tree/window. Do not
        # race the next mission step merely because the process exists.
        cancellable_delay(0.08)

    if best_window is None and best_process is None:
        raise RuntimeError(
            f"Launched '{app['name']}' from {app['source']} but could not verify a matching window or process within the timeout"
        )

    process_id = (
        int(best_process["pid"])
        if best_process
        else int(best_window["pid"])
        if best_window
        else 0
    )
    process_name = (
        str(best_process.get("name") or "") if best_process else ""
    )
    if not process_name and process_id:
        try:
            import psutil

            process_name = psutil.Process(process_id).name()
        except Exception:
            process_name = "unknown"

    interaction_ready = bool(best_window is not None and focused)
    payload = {
        "query": _clean_query(query),
        "resolved_name": app["name"],
        "source": app["source"],
        "launch_type": app["launch_type"],
        "match_score": app["score"],
        "launcher_pid": launcher_pid,
        "window_title": best_window["title"] if best_window else "",
        "window_handle": best_window["hwnd"] if best_window else 0,
        "process_id": process_id,
        "process_name": process_name,
        "focused": focused,
        "visible_window_verified": best_window is not None,
        "process_verified": best_process is not None or process_id > 0,
        "interaction_ready": interaction_ready,
    }
    prefix = "VERIFIED: " if interaction_ready else "DELIVERED: "
    return prefix + json.dumps(payload, ensure_ascii=False)


def register_app_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "find_installed_app",
            "Find installed Windows applications by friendly name across Start Apps/Microsoft Store, Registry App Paths, PATH, Start Menu shortcuts, uninstall metadata, Program Files, and LocalAppData Programs. Use this instead of PowerShell for application discovery.",
            Risk.LOW,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 260,
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            find_installed_app,
        )
    )
    registry.register(
        ToolSpec(
            "launch_installed_app",
            "Resolve, launch, and verify a Windows application by friendly name or explicit .exe/.lnk path. Searches Start Apps/Microsoft Store, Registry App Paths, PATH, Start Menu shortcuts, Program Files, and LocalAppData Programs. Prefer this for general 'open <app>' requests instead of open_application or run_powershell. Raw shells/interpreters remain blocked through this launcher.",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 260,
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 2,
                        "maximum": 25,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            launch_installed_app,
        )
    )
