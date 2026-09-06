from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .permissions import Risk
from .tools import ToolSpec


def _workspace() -> Path:
    return Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()


def _run_git(args: list[str], cwd: str | None = None) -> str:
    root = Path(cwd).expanduser().resolve() if cwd else _workspace()
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return f"cwd={root}\nexit_code={completed.returncode}\n{output[-16000:]}"


def git_status(path: str = ".") -> str:
    return _run_git(["status", "--short", "--branch"], path)


def git_diff(path: str = ".") -> str:
    return _run_git(["diff", "--no-ext-diff", "--", "."], path)


def git_log(path: str = ".", count: int = 10) -> str:
    count = max(1, min(int(count), 50))
    return _run_git(["log", f"-{count}", "--oneline", "--decorate"], path)


def vscode_open(path: str = ".") -> str:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (_workspace() / target).resolve()
    subprocess.Popen(["code", "--reuse-window", str(target)], shell=False)
    return f"Opened VS Code: {target}"


def register_dev_tools(registry) -> None:
    registry.register(ToolSpec(
        "git_status",
        "Read git branch and working-tree status without changing files.",
        Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False},
        git_status,
    ))
    registry.register(ToolSpec(
        "git_diff",
        "Read the current git diff without changing files.",
        Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False},
        git_diff,
    ))
    registry.register(ToolSpec(
        "git_log",
        "Read recent git commits without changing files.",
        Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "count": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, "additionalProperties": False},
        git_log,
    ))
    registry.register(ToolSpec(
        "vscode_open",
        "Open a workspace or file in Visual Studio Code using the installed code command.",
        Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False},
        vscode_open,
    ))
