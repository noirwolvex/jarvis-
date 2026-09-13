from __future__ import annotations

import json
import sys
from typing import Any

from .full_access_bridge import build_full_access_agent, run_agent_mission

PROTOCOL = 1
_AGENT = None


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = build_full_access_agent()
    return _AGENT


def _handle(raw: str) -> dict[str, Any]:
    try:
        message: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"type": "result", "id": "", "ok": False, "error": f"Invalid JSON: {exc}"}

    if not isinstance(message, dict):
        return {"type": "result", "id": "", "ok": False, "error": "Worker message must be an object"}

    request_id = str(message.get("id") or "")
    if not request_id or len(request_id) > 128:
        return {"type": "result", "id": request_id[:128], "ok": False, "error": "Worker request id is invalid"}
    if message.get("protocol") != PROTOCOL:
        return {"type": "result", "id": request_id, "ok": False, "error": "Unsupported worker protocol"}

    action = str(message.get("action") or "")
    if action == "ping":
        return {"type": "result", "id": request_id, "ok": True, "payload": {"ready": True, "protocol": PROTOCOL, "agent_loaded": _AGENT is not None}}
    if action != "run":
        return {"type": "result", "id": request_id, "ok": False, "error": f"Unsupported worker action: {action}"}

    title = str(message.get("title") or "").strip()
    if not title or len(title) > 8000 or "\0" in title:
        return {"type": "result", "id": request_id, "ok": False, "error": "Mission must contain 1-8000 safe characters"}

    try:
        payload = run_agent_mission(_agent(), title)
        return {"type": "result", "id": request_id, "ok": True, "payload": payload}
    except Exception as exc:
        return {"type": "result", "id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    _write({"type": "ready", "protocol": PROTOCOL})
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        _write(_handle(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
