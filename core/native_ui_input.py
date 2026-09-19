from __future__ import annotations

import json
from typing import Any

from .desktop_input import InputDeliveryError
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec
from .semantic_target import SELECTOR_SCHEMA
from .execution_telemetry import record_backend


def ui_type_native(text: str, target: str = "", title: str = "", selector: dict[str, Any] | None = None) -> str:
    """Type through the Rust daemon while UIA resolves/focuses and verifies the editor."""
    value = str(text)
    if not value or len(value) > 4096 or "\0" in value:
        raise ValueError("Native semantic text must contain 1-4096 safe characters")

    from .process_control import check_cancelled
    from .semantic_ui_tools import (
        _SNAPSHOTS,
        _control_value,
        _find_control,
        _focus_control,
        _focus_window,
        _guard_foreground,
        _guard_native_target,
        _has_focus,
        _target_binding,
        _validate_target,
        _meta,
        _window,
        ui_type,
    )
    from .ui_state import wait_until
    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode

    check_cancelled()
    win = _window(title)
    hwnd = _focus_window(win)
    _guard_foreground(hwnd)
    control = _find_control(win, target, editable=True, selector=selector)
    _focus_control(control, hwnd)
    binding = _target_binding(win, control)
    before_value = _control_value(control)
    if before_value is None:
        raise InputDeliveryError(
            "Editor value cannot be read semantically; Rust input was not dispatched"
        )
    expected = before_value + value

    client, status = _preflight()
    if client is None:
        # Compatibility is allowed for auto or explicitly selected Python mode. The normal
        # JARVIS X launcher uses strict rust mode and must fail closed if the daemon/config
        # is unavailable instead of silently typing through Python.
        if native_engine_mode() in {"auto", "python"}:
            return ui_type(text=value, target=target, title=title, submit=False, replace=False, selector=selector)
        raise RustEngineUnavailable(
            "Strict Rust mode requires the native daemon before semantic keyboard input"
        )

    _validate_target(win, control, binding)
    _guard_native_target(hwnd, status)
    if not _has_focus(control) or _control_value(control) != before_value:
        raise InputDeliveryError("Editor focus or draft changed during Rust preflight; no input delivered")
    if before_value or any(char in value for char in "\r\n"):
        raise InputDeliveryError("Native typing requires an empty single-line composer; use ui_type with a writable Value pattern for drafts or multiline text")
    _SNAPSHOTS.invalidate(hwnd)
    try:
        result = client.type_text(value, status)
    except RustEngineUnavailable:
        if native_engine_mode() == "auto":
            return ui_type(text=value, target=target, title=title, submit=False, replace=False, selector=selector)
        raise

    if result.get("executed") is not True or result.get("simulation") is not False:
        raise InputDeliveryError(
            "Rust daemon did not confirm native keyboard execution; inspect before retrying"
        )

    def value_matches() -> bool:
        _guard_foreground(hwnd)
        record_backend("windows_uia", phase="verify", detail="Exact editor readback after native input")
        return _control_value(control) == expected

    try:
        wait_until(value_matches, description="exact editor value after Rust native input")
    except TimeoutError as exc:
        raise InputDeliveryError(
            "Rust keyboard input was dispatched but editor readback did not match exactly; inspect before retrying"
        ) from exc

    return "VERIFIED: " + json.dumps(
        {
            "action": "ui_type_native",
            "window_hwnd": hwnd,
            "control": _meta(control),
            "characters": len(value),
            "method": "rust_native_input",
            "submitted": False,
            "rust_executed": True,
            "simulation": False,
        },
        ensure_ascii=False,
    )


def register_native_ui_input_tools(registry: ToolRegistry) -> None:
    def guarded(**kwargs: Any) -> str:
        from .semantic_ui_tools import BrowserBoundaryError

        try:
            return ui_type_native(**kwargs)
        except BrowserBoundaryError as exc:
            return str(exc)

    registry.register(
        ToolSpec(
            "ui_type_native",
            "Resolve and focus one exact Windows UI Automation editor, type through the Rust native-input daemon, then verify the exact editor value. Omitted target requires a unique editor/composer. Strict Rust mode fails closed and never falls back to Python.",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "target": {"type": "string"},
                    "title": {"type": "string"},
                    "selector": SELECTOR_SCHEMA,
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            guarded,
        )
    )

    from .whatsapp_native import register_whatsapp_native_tools

    register_whatsapp_native_tools(registry)
