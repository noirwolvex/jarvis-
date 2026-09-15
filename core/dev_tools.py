from __future__ import annotations

import subprocess
import os
import shutil
from pathlib import Path

from .permissions import Risk
from .tools import ToolSpec


def _git_argument(value: str) -> str:
    value = value.strip()
    if not value or value.startswith("-") or "\0" in value:
        raise ValueError("Git target must be a nonempty name, not an option")
    return value


def _workspace() -> Path:
    import os
    return Path(os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()


def _workspace_path(path: str | None = None) -> Path:
    workspace = _workspace()
    raw = Path(path or ".").expanduser()
    resolved = (workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise PermissionError(f"Path is outside JARVIS_WORKSPACE: {resolved}")
    return resolved


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 30) -> str:
    root = _workspace_path(cwd)
    from .process_control import run_command
    result = run_command(["git", *args], cwd=root, timeout=timeout)
    return result + f"\ncwd={root}"


def git_status(path: str = ".") -> str:
    return _run_git(["status", "--short", "--branch"], path)


def git_diff(path: str = ".") -> str:
    return _run_git(["diff", "--no-ext-diff", "--", "."], path)


def git_log(path: str = ".", count: int = 10) -> str:
    count = max(1, min(int(count), 50))
    return _run_git(["log", f"-{count}", "--oneline", "--decorate"], path)


def git_add(path: str = ".", files: list[str] | None = None) -> str:
    targets = files or ["."]
    return _run_git(["add", "--", *targets], path)


def git_commit(path: str = ".", message: str = "JARVIS update") -> str:
    if not message.strip():
        raise ValueError("Commit message cannot be empty")
    return _run_git(["commit", "-m", message.strip()], path)


def git_push(path: str = ".", remote: str = "origin", branch: str = "") -> str:
    args = ["push", _git_argument(remote)]
    if branch.strip():
        args.append(_git_argument(branch))
    return _run_git(args, path, timeout=60)


def git_pull(path: str = ".", remote: str = "origin", branch: str = "") -> str:
    args = ["pull", _git_argument(remote)]
    if branch.strip():
        args.append(_git_argument(branch))
    return _run_git(args, path, timeout=60)


def git_branch(path: str = ".", name: str = "") -> str:
    if name.strip():
        return _run_git(["switch", "-c", _git_argument(name)], path)
    return _run_git(["branch", "--show-current"], path)


def git_checkout(path: str = ".", target: str = "") -> str:
    if not target.strip():
        raise ValueError("Checkout target is required")
    return _run_git(["switch", _git_argument(target)], path)


def git_remote(path: str = ".") -> str:
    return _run_git(["remote", "-v"], path)


def project_snapshot(path: str = ".") -> str:
    root = _workspace_path(path)
    if not root.exists():
        raise FileNotFoundError(root)
    entries = []
    ignored = {".git", ".jarvis", ".venv", "venv", "node_modules", "__pycache__", "target"}
    for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if item.name in ignored:
            continue
        entries.append({"name": item.name, "type": "directory" if item.is_dir() else "file"})
        if len(entries) >= 200:
            break
    return str({"root": str(root), "entries": entries})


def vscode_open(path: str = ".") -> str:
    target = _workspace_path(path)
    executable = shutil.which("code")
    if not executable:
        raise FileNotFoundError("VS Code command is not on PATH")
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        native = Path(executable).parent.parent / "Code.exe"
        if not native.is_file():
            raise FileNotFoundError("VS Code's native executable could not be resolved")
        executable = str(native)
    process = subprocess.Popen([executable, "--reuse-window", str(target)], shell=False)
    return f"Requested VS Code for {target} (launcher pid={process.pid}); inspect its window to verify"


def register_dev_tools(registry) -> None:
    registry.register(ToolSpec(
        "git_status", "Read git branch and working-tree status without changing files.", Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}, git_status))
    registry.register(ToolSpec(
        "git_diff", "Read the current git diff without changing files.", Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}, git_diff))
    registry.register(ToolSpec(
        "git_log", "Read recent git commits without changing files.", Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "count": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}, "additionalProperties": False}, git_log))
    registry.register(ToolSpec(
        "git_remote", "Read configured git remotes without changing files.", Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}, git_remote))
    registry.register(ToolSpec(
        "project_snapshot", "Inspect top-level project structure while excluding build/cache folders.", Risk.LOW,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}, project_snapshot))
    registry.register(ToolSpec(
        "git_add", "Stage explicitly named project files, or the project root when files is omitted.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "files": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}, git_add))
    registry.register(ToolSpec(
        "git_commit", "Create a git commit with the provided message after changes have been staged.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}, git_commit))
    registry.register(ToolSpec(
        "git_push", "Push the current branch to a git remote.", Risk.HIGH,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "remote": {"type": "string", "default": "origin"}, "branch": {"type": "string"}}, "additionalProperties": False}, git_push))
    registry.register(ToolSpec(
        "git_pull", "Pull changes from a git remote into the current branch.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "remote": {"type": "string", "default": "origin"}, "branch": {"type": "string"}}, "additionalProperties": False}, git_pull))
    registry.register(ToolSpec(
        "git_branch", "Create a new branch when name is supplied, otherwise report the current branch.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "name": {"type": "string"}}, "additionalProperties": False}, git_branch))
    registry.register(ToolSpec(
        "git_checkout", "Switch to an existing local git branch.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}, "target": {"type": "string"}}, "required": ["target"], "additionalProperties": False}, git_checkout))
    registry.register(ToolSpec(
        "vscode_open", "Open a workspace or file in Visual Studio Code using the installed code command.", Risk.MEDIUM,
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}, vscode_open))
