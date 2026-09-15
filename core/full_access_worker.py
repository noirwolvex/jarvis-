from __future__ import annotations

import json
import sys
import threading
import os
import time
from typing import Any

from .full_access_bridge import build_full_access_agent, run_agent_mission

PROTOCOL = 1
_AGENT = None
_OUTPUT_LOCK = threading.Lock()
_STOPPED = threading.Event()


def _write(payload: dict[str, Any]) -> None:
    with _OUTPUT_LOCK:
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
        if _STOPPED.is_set():
            raise RuntimeError("Worker emergency stop is latched")
        def progress(event):
            from .memory import redact_secrets
            _write({"type": "progress", "id": request_id, "kind": event.kind,
                    "message": redact_secrets(str(event.message))[:2000]})
            if event.kind == "tool_result" and event.tool == "screen_observe":
                from .vision_tools import _payload_from_result, vision_followup_message
                frame = _payload_from_result(event.message)
                visual = vision_followup_message(event.message)
                if frame and visual:
                    frame.pop("path", None)
                    _write({"type": "observation", "id": request_id, "frame": frame,
                            "preview": visual["content"][1]["image_url"]["url"]})
        payload = run_agent_mission(_agent(), title, emit=progress, cancel_event=_STOPPED, allow_shell=message.get("allow_shell") is True)
        return {"type": "result", "id": request_id, "ok": True, "payload": payload}
    except Exception as exc:
        return {"type": "result", "id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    active: threading.Thread | None = None

    def stop() -> None:
        _STOPPED.set()
        if _AGENT is not None:
            _AGENT.request_stop()
        # Stop both execution backends. The Rust daemon has an independent emergency
        # latch, while Python-held synthetic keys/buttons and child processes are also
        # released locally. All stop operations are best-effort and idempotent.
        from .rust_engine import rust_engine_emergency_stop_best_effort
        rust_engine_emergency_stop_best_effort()
        from .desktop_control_tools import release_held_inputs
        release_held_inputs()
        from .process_control import stop_processes
        stop_processes()
        _write({"type": "stopped"})

    def hotkey_watch() -> None:
        if os.name != "nt":
            return
        import ctypes
        while True:
            # Ctrl+Alt+Escape works even when the control-center window has no focus.
            if all(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x1B)):
                stop()
                return
            time.sleep(0.02)

    threading.Thread(target=hotkey_watch, daemon=True, name="jarvis-emergency-hotkey").start()
    _write({"type": "ready", "protocol": PROTOCOL})
    for line in iter(lambda: sys.stdin.readline(65537), ""):
        if len(line) > 65536:
            stop()
            return 2
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            _write(_handle(raw))
            continue
        if isinstance(message, dict) and message.get("protocol") == PROTOCOL and message.get("action") == "stop":
            stop()
            return 0
        if active and active.is_alive():
            _write({"type": "result", "id": message.get("id", "") if isinstance(message, dict) else "", "ok": False, "error": "Worker is busy"})
            continue
        active = threading.Thread(target=lambda value=raw: _write(_handle(value)), daemon=True)
        active.start()
    stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
