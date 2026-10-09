"""Compact automatic bookkeeping replies without changing durable tool evidence."""
from __future__ import annotations

import json


def _step(value):
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if item not in ("", [], {})}
    if result.get("action") == result.get("description"):
        result.pop("action", None)
    return result


def compact_bookkeeping_result(name: str, result: str) -> str:
    """Explicit status/recall and all action/observation/error replies stay intact.

    Automatic plan updates used to resend the full user goal, telemetry and
    recovery history after each step. Their source of truth remains the journal;
    the next model decision needs the graph and pending workflow state.
    """
    if name not in {"task_plan", "task_update_step"}:
        return result
    try:
        payload = json.loads(result)
    except (ValueError, TypeError):
        return result
    if name == "task_plan" and isinstance(payload, list):
        compact = [_step(item) for item in payload]
    elif name == "task_update_step" and isinstance(payload, dict) and "task_id" in payload:
        compact = {key: value for key, value in payload.items() if key not in {
            "goal", "engine_visibility", "metrics", "recovery_history", "execution_priority", "elapsed_ms",
        }}
        if isinstance(compact.get("task_graph"), list):
            compact["task_graph"] = [_step(item) for item in compact["task_graph"]]
        compact["details"] = "Full telemetry and recovery history remain available through task_status."
    else:
        return result
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
