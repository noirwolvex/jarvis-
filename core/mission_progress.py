"""Bounded, redacted task graph snapshots for the local Control Center stream."""
from __future__ import annotations

from .memory import redact_secrets


def compact_task_graph(nodes: list[dict]) -> list[dict]:
    result = []
    for node in nodes[:100]:
        row = {key: redact_secrets(str(node.get(key, "")))[:limit] for key, limit in {
            "id": 128, "action": 1000, "description": 1000, "status": 32,
            "execution_backend": 512, "resolution_backend": 512,
            "verification_result": 1000, "result": 2000,
        }.items()}
        row["dependencies"] = [str(item)[:128] for item in node.get("dependencies", [])[:100]]
        result.append(row)
    return result
