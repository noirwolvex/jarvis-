from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class WorkspaceContext:
    """Discovers and persists lightweight context about the active JARVIS workspace."""

    def __init__(self, workspace: str | None = None) -> None:
        self.root = Path(workspace or os.getenv("JARVIS_WORKSPACE", ".")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / ".jarvis" / "workspace.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def snapshot(self, max_entries: int = 80) -> dict[str, Any]:
        entries: list[dict[str, str]] = []
        try:
            for item in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
                if item.name == ".jarvis":
                    continue
                entries.append({
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                })
                if len(entries) >= max_entries:
                    break
        except OSError:
            pass
        state = self._load()
        return {
            "root": str(self.root),
            "entries": entries,
            "project": state.get("project", {}),
        }

    def set_project(self, name: str, kind: str = "unknown", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._load()
        payload["project"] = {
            "name": name.strip(),
            "kind": kind.strip() or "unknown",
            "metadata": metadata or {},
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload["project"]

    def context_text(self) -> str:
        snapshot = self.snapshot()
        project = snapshot.get("project") or {}
        lines = [f"Workspace root: {snapshot['root']}"]
        if project.get("name"):
            lines.append(f"Active project: {project['name']} ({project.get('kind', 'unknown')})")
        if snapshot["entries"]:
            names = ", ".join(item["name"] for item in snapshot["entries"][:40])
            lines.append(f"Workspace entries: {names}")
        return "\n".join(lines)

    def save_snapshot(self) -> dict[str, Any]:
        payload = self._load()
        payload["last_snapshot"] = self.snapshot()
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload["last_snapshot"]
