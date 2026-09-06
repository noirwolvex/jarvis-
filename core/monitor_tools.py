from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec


def _workspace() -> Path:
    return Path(os.getenv("JARVIS_WORKSPACE", ".")).resolve()


def _trace_dir() -> Path:
    return _workspace() / ".jarvis" / "traces"


def task_history(limit: int = 10) -> str:
    directory = _trace_dir()
    if not directory.exists():
        return "[]"
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("task-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, min(limit, 50))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append({
                "task_id": data.get("task_id"),
                "goal": data.get("goal"),
                "status": data.get("status"),
                "tools_used": data.get("tools_used", 0),
                "failures": data.get("failures", 0),
                "recoveries": data.get("recoveries", 0),
                "elapsed_ms": data.get("finished_at", 0) and round((data.get("finished_at", 0) - data.get("started_at", 0)) * 1000.0, 2),
            })
        except Exception:
            continue
    return json.dumps(rows, ensure_ascii=False, indent=2)


def task_trace(task_id: str) -> str:
    if "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError("Invalid task identifier")
    path = _trace_dir() / f"{task_id}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(data, ensure_ascii=False, indent=2)[:40000]


def system_snapshot() -> str:
    import psutil

    snapshot: dict[str, Any] = {
        "timestamp": time.time(),
        "platform": os.name,
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "memory": {
            "percent": psutil.virtual_memory().percent,
            "available_mb": round(psutil.virtual_memory().available / 1024 / 1024, 1),
        },
        "disk": {
            "root": str(_workspace().anchor or _workspace()),
            "percent": psutil.disk_usage(_workspace().anchor or _workspace()).percent,
        },
        "process_count": len(psutil.pids()),
    }
    if os.name == "nt":
        import ctypes
        snapshot["foreground_window"] = int(ctypes.windll.user32.GetForegroundWindow())
    return json.dumps(snapshot, ensure_ascii=False)


def register_monitor_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "task_history",
        "List recent completed or failed JARVIS task traces with IDs, goals, status, tool counts, failures, recoveries, and duration.",
        Risk.SAFE,
        {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False},
        task_history,
    ))
    registry.register(ToolSpec(
        "task_trace",
        "Read the durable trace for a specific JARVIS task ID to diagnose exactly what tools ran, their arguments, results, timings, and outcome.",
        Risk.LOW,
        {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
        task_trace,
    ))
    registry.register(ToolSpec(
        "system_snapshot",
        "Return a safe local health snapshot including CPU, memory, disk usage, process count, and foreground window handle.",
        Risk.SAFE,
        {"type": "object", "properties": {}, "additionalProperties": False},
        system_snapshot,
    ))
