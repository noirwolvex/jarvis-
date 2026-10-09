from __future__ import annotations

import json
import sys
import threading
import os
import time
from typing import Any

from .full_access_bridge import build_full_access_agent, run_agent_mission

PROTOCOL = 1

def _configure_utf8_stdio() -> None:
    """Force the JSON-lines worker protocol to UTF-8 on Windows and all pipe hosts."""
    for stream_name, errors in (("stdin", "strict"), ("stdout", "strict"), ("stderr", "backslashreplace")):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors=errors, newline="\n")
            except (TypeError, ValueError, OSError):
                try:
                    reconfigure(encoding="utf-8", errors=errors)
                except (TypeError, ValueError, OSError):
                    pass


_AGENT = None
_OUTPUT_LOCK = threading.Lock()
_STOPPED = threading.Event()
_ACTIVE_CONTROL = None
_ACTIVE_REQUEST = ""


def _write(payload: dict[str, Any]) -> None:
    with _OUTPUT_LOCK:
        # Keep the wire protocol ASCII-safe even if a host launches Python with a
        # legacy Windows pipe encoding such as cp1252. JSON decoding restores the
        # original Unicode text on the receiver.
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = build_full_access_agent()
    return _AGENT


def _native_engine_payload() -> dict[str, Any]:
    try:
        from .rust_engine import native_engine_status

        value = json.loads(native_engine_status())
        return value if isinstance(value, dict) else {"backend": "unknown", "error": "invalid native engine status"}
    except Exception as exc:
        return {"backend": "unavailable", "rust_input_ready": False, "error": f"{type(exc).__name__}: {exc}"}


def _native_input_probe(message: dict[str, Any]) -> dict[str, Any]:
    """Supervised local qualification path. Disabled unless the launcher opts in explicitly."""
    if os.environ.get("JARVIS_RUST_LIVE_PROBE", "").strip() != "1":
        raise RuntimeError("Native input probe is disabled")

    from .rust_engine import _preflight, native_engine_mode

    if native_engine_mode() != "rust":
        raise RuntimeError("Native input probe requires JARVIS_NATIVE_ENGINE=rust")
    client, status = _preflight()
    if client is None or status is None:
        raise RuntimeError("Rust daemon is not ready for strict native input")

    probe = message.get("probe")
    if not isinstance(probe, dict):
        raise ValueError("Native input probe payload must be an object")
    expected = probe.get("expected_window")
    if not isinstance(expected, dict) or any(
        type(expected.get(key)) is not int or expected[key] <= 0
        for key in ("hwnd", "process_id")
    ):
        raise ValueError("Native input probe requires an exact qualification window binding")
    foreground = status.get("foreground")
    if not isinstance(foreground, dict) or any(
        foreground.get(key) != expected[key] for key in ("hwnd", "process_id")
    ):
        actual = {key: foreground.get(key) for key in ("hwnd", "process_id")} if isinstance(foreground, dict) else None
        raise RuntimeError(
            f"Qualification window lost foreground; no test input dispatched "
            f"(expected={expected}, actual={actual})"
        )
    kind = str(probe.get("kind") or "")
    if kind == "capture":
        display_id = probe.get("display_id")
        if type(display_id) is not int or display_id < 0:
            raise ValueError("Native capture probe requires a display id")
        return {"backend": "rust", "result": client.capture(display_id)}
    if kind == "click":
        x, y = probe.get("x"), probe.get("y")
        if not isinstance(x, int) or isinstance(x, bool) or not isinstance(y, int) or isinstance(y, bool):
            raise ValueError("Native click probe requires integer x/y")
        result = client.click(x, y, status)
    elif kind == "type_text":
        text = probe.get("text")
        if not isinstance(text, str):
            raise ValueError("Native keyboard probe requires text")
        result = client.type_text(text, status)
    elif kind == "press_key":
        key = probe.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Native key probe requires a key")
        result = client.press_key(key, status)
    elif kind == "hotkey":
        keys = probe.get("keys")
        if (
            not isinstance(keys, list)
            or not keys
            or len(keys) > 8
            or any(not isinstance(key, str) or not key.strip() for key in keys)
        ):
            raise ValueError("Native hotkey probe requires 1-8 keys")
        result = client.hotkey(keys, status)
    else:
        raise ValueError("Native probe kind must be capture, click, type_text, press_key, or hotkey")

    if result.get("executed") is not True or result.get("simulation") is not False:
        raise RuntimeError("Rust daemon did not confirm native execution")
    return {"backend": "rust", "result": result}


def _handle(raw: str) -> dict[str, Any]:
    global _ACTIVE_CONTROL, _ACTIVE_REQUEST
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
        return {
            "type": "result",
            "id": request_id,
            "ok": True,
            "payload": {
                "ready": True,
                "protocol": PROTOCOL,
                "agent_loaded": _AGENT is not None,
                "native_engine": _native_engine_payload(),
            },
        }
    if action == "native_input_probe":
        try:
            return {"type": "result", "id": request_id, "ok": True, "payload": _native_input_probe(message)}
        except Exception as exc:
            return {"type": "result", "id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
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

            _write(
                {
                    "type": "progress",
                    "id": request_id,
                    "kind": event.kind,
                    "message": redact_secrets(str(event.message))[:2000],
                }
            )
            if event.kind == "tool_result" and event.tool in {"screen_observe", "computer_observe"}:
                from .vision_tools import _payload_from_result, vision_followup_message

                frame = _payload_from_result(event.message)
                visual = vision_followup_message(event.message)
                if frame and visual:
                    frame.pop("path", None)
                    _write(
                        {
                            "type": "observation",
                            "id": request_id,
                            "frame": frame,
                            "preview": visual["content"][1]["image_url"]["url"],
                        }
                    )

        def observation(value):
            if not _STOPPED.is_set():
                _write({"type": "observation", "id": request_id, **value})

        last_graph = None

        def task_graph(nodes):
            nonlocal last_graph
            from .mission_progress import compact_task_graph
            compact = compact_task_graph(nodes)
            if not _STOPPED.is_set() and compact != last_graph:
                _write({"type": "task_graph", "id": request_id, "nodes": compact})
                last_graph = compact

        from .mission_control import MissionControl
        agent = _agent()
        def control_update(value):
            _write({"type": "progress", "id": request_id, "kind": "mission_control", "message": json.dumps(value)})
        control = MissionControl(control_update)
        _ACTIVE_CONTROL, _ACTIVE_REQUEST = control, request_id
        payload = run_agent_mission(
            agent,
            title,
            emit=progress,
            cancel_event=_STOPPED,
            allow_shell=message.get("allow_shell") is True,
            observation_emit=observation,
            task_graph_emit=task_graph,
            mission_control=control,
        )
        return {"type": "result", "id": request_id, "ok": True, "payload": payload}
    except Exception as exc:
        return {"type": "result", "id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _ACTIVE_CONTROL, _ACTIVE_REQUEST = None, ""


def _control_message(message: dict) -> bool:
    """Handle operator commands on stdin while the model/tool thread is blocked."""
    if message.get("protocol") != PROTOCOL or message.get("action") not in {"pause", "resume", "cancel", "confirm", "reject"}:
        return False
    try:
        if _ACTIVE_CONTROL is None or message.get("id") != _ACTIVE_REQUEST:
            raise ValueError("No matching active mission")
        if set(message) - {"protocol", "action", "id", "confirmation_id"}:
            raise ValueError("Unknown control property")
        _ACTIVE_CONTROL.command(message["action"], str(message.get("confirmation_id", "")))
    except Exception as exc:
        _write({"type": "progress", "id": message.get("id", ""), "kind": "control_error", "message": str(exc)})
    return True


def main() -> int:
    _configure_utf8_stdio()
    active: threading.Thread | None = None

    def stop() -> None:
        _STOPPED.set()
        if _ACTIVE_CONTROL is not None:
            _ACTIVE_CONTROL.cancel()
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
    _write({"type": "ready", "protocol": PROTOCOL, "native_engine": _native_engine_payload()})
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
        if isinstance(message, dict) and _control_message(message):
            continue
        if active and active.is_alive():
            _write(
                {
                    "type": "result",
                    "id": message.get("id", "") if isinstance(message, dict) else "",
                    "ok": False,
                    "error": "Worker is busy",
                }
            )
            continue
        active = threading.Thread(target=lambda value=raw: _write(_handle(value)), daemon=True)
        active.start()
    stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
