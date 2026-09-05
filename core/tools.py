from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .permissions import PermissionEngine, Risk


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
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
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
        except Exception as exc:  # defensive boundary around OS tools
            return f"ERROR executing {name}: {type(exc).__name__}: {exc}"

    def _register_builtin_tools(self) -> None:
        self.register(ToolSpec(
            "run_powershell",
            "Run a non-interactive PowerShell command. Use only when the command is needed to accomplish the user's request.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            _run_powershell,
        ))
        self.register(ToolSpec(
            "open_application",
            "Open a Windows application or executable by command/name.",
            Risk.LOW,
            {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
            _open_application,
        ))
        self.register(ToolSpec(
            "open_url",
            "Open a URL in the user's default browser.",
            Risk.LOW,
            {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            _open_url,
        ))
        self.register(ToolSpec(
            "read_file",
            "Read a UTF-8 text file. Paths should normally be inside the configured workspace.",
            Risk.LOW,
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            _read_file,
        ))
        self.register(ToolSpec(
            "write_file",
            "Write or replace a UTF-8 text file. Parent directories are created automatically.",
            Risk.MEDIUM,
            {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            _write_file,
        ))
        self.register(ToolSpec(
            "list_directory",
            "List files and folders in a directory.",
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
    subprocess.Popen(command, shell=True)
    return f"Started application: {command}"


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
    target = _safe_path(path)
    return target.read_text(encoding="utf-8")[:20000]


def _write_file(path: str, content: str) -> str:
    target = _safe_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {target}"


def _list_directory(path: str) -> str:
    target = _safe_path(path)
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:500]:
        entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
    return json.dumps({"path": str(target), "entries": entries}, ensure_ascii=False)


def _take_screenshot() -> str:
    from PIL import ImageGrab
    out_dir = Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve() / ".jarvis" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    path = out_dir / f"screen-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    image = ImageGrab.grab()
    image.save(path)
    return f"Screenshot saved to {path}"
