from __future__ import annotations

import json
from typing import Any, Callable

from .desktop_input import InputDeliveryError, InputNotDispatchedError
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec
from .semantic_target import SELECTOR_SCHEMA
from .execution_telemetry import record_backend


def _append_caret(control: Any, before: str) -> tuple[str, str, Callable[[], None]] | None:
    """Bind a collapsed TextPattern range to an exact append position, never guess a caret."""
    try:
        pattern = control.iface_text
    except Exception as exc:
        if isinstance(exc, AttributeError) or type(exc).__name__ == "NoPatternInterfaceError":
            return None
        raise InputNotDispatchedError("Editor text range is unavailable; no input delivered") from exc
    try:
        document = pattern.DocumentRange
        if document.GetText(4097) != before:
            raise ValueError("Text and value providers disagree")
        if before in ("\n", "\r\n"):
            # There is no draft to preserve or meaningful caret offset in this
            # empty paragraph. Native Unicode inserts before its terminal break.
            # Chromium may advertise Select/SetValue but ignore both methods.
            def check_empty_paragraph() -> None:
                if pattern.DocumentRange.GetText(4097) != before:
                    raise InputNotDispatchedError("Editor paragraph changed before input; no input delivered")
            return "", before, check_empty_paragraph
        caret = document.Clone()
        caret.MoveEndpointByRange(0, document, 1)  # Start -> document End, collapsed.
        suffix = "\r\n" if before.endswith("\r\n") else "\n" if before.endswith("\n") else ""
        if suffix:
            # Chromium includes a terminal paragraph break even in an empty editor.
            # Insert before that break, preserving it and every existing character.
            caret.MoveEndpointByUnit(0, 0, -1)  # TextUnit_Character
            caret.MoveEndpointByRange(1, caret, 0)
        prefix_range = document.Clone()
        prefix_range.MoveEndpointByRange(1, caret, 0)
        prefix = prefix_range.GetText(4097)
        if prefix + suffix != before:
            raise ValueError("Append range does not match the exact editor value")
        def is_append_position() -> bool:
            current_document = pattern.DocumentRange
            if current_document.GetText(4097) != before:
                return False
            selection = pattern.GetSelection()
            if selection.Length != 1:
                return False
            selected = selection.GetElement(0)
            if (selected.CompareEndpoints(0, selected, 1) != 0 or selected.GetText(1) != ""
                    or selected.CompareEndpoints(0, current_document, 0) < 0):
                return False
            # WebView providers can report a collapsed insertion point one position
            # beyond DocumentRange.End. Compare the exact text on both sides of
            # the actual selection, rather than requiring identical range offsets.
            # This also rejects a selection or a caret inside an existing draft.
            left = current_document.Clone()
            left.MoveEndpointByRange(1, selected, 0)
            right = current_document.Clone()
            right.MoveEndpointByRange(0, selected, 1)
            return left.GetText(4097) == prefix and right.GetText(4097) == suffix

        def check_caret() -> None:
            try:
                if is_append_position():
                    return
            except Exception as exc:
                raise InputNotDispatchedError("Editor caret could not be read before input; no input delivered") from exc
            raise InputNotDispatchedError("Editor caret changed before input; no input delivered")

        if not is_append_position():
            caret.Select()
        check_caret()
        return prefix, suffix, check_caret
    except InputNotDispatchedError:
        raise
    except Exception as exc:
        raise InputNotDispatchedError("Editor append position could not be verified; no input delivered") from exc


def ui_type_native(text: str, target: str = "", title: str = "", selector: dict[str, Any] | None = None) -> str:
    """Type through the Rust daemon while UIA resolves/focuses and verifies the editor."""
    value = str(text)
    if not value or len(value) > 4096 or "\0" in value:
        raise ValueError("Native semantic text must contain 1-4096 safe characters")

    from .process_control import check_cancelled
    from .semantic_ui_tools import (
        _SNAPSHOTS,
        _control_value,
        _resolve_input_control,
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
    try:
        hwnd = _focus_window(win)
        _guard_foreground(hwnd)
        control = _resolve_input_control(win, target, editable=True, selector=selector)
        _focus_control(control, hwnd)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    binding = _target_binding(win, control)
    before_value = _control_value(control)
    if before_value is None:
        raise InputNotDispatchedError(
            "Editor value cannot be read semantically; Rust input was not dispatched"
        )
    expected = before_value + value
    expected_values = {expected}

    caret_guard = None
    if before_value and not any(char in value for char in "\r\n"):
        try:
            _validate_target(win, control, binding)
        except InputDeliveryError as exc:
            raise InputNotDispatchedError(str(exc)) from exc
        append = _append_caret(control, before_value)
        if append is not None:
            prefix, suffix, caret_guard = append
            expected = prefix + value + suffix
            expected_values = {expected}
            if before_value in ("\n", "\r\n"):
                # Some WebView providers remove the empty paragraph's synthetic
                # line break once text exists. Accept exactly either paragraph
                # representation, never trim arbitrary whitespace or draft text.
                expected_values.add(value)
    # If TextPattern cannot bind the caret, reuse the resolved control for a
    # writable Value pattern. Never repeat an attempted write after failed readback.
    if (before_value and caret_guard is None) or any(char in value for char in "\r\n"):
        return ui_type(text=value, target=target, title=title, submit=False, replace=False,
                       selector=selector, _resolved_editor=(win, control, binding))

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

    try:
        _validate_target(win, control, binding)
        _guard_native_target(hwnd, status)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    if not _has_focus(control) or _control_value(control) != before_value:
        raise InputNotDispatchedError("Editor focus or draft changed during Rust preflight; no input delivered")
    _SNAPSHOTS.invalidate(hwnd)
    # Advance only for our own cache invalidation. Any additional invalidation
    # still makes the resolved binding stale at the final dispatch guard.
    binding["generation"] += 1

    def input_guard() -> None:
        try:
            _validate_target(win, control, binding)
        except InputDeliveryError as exc:
            raise InputNotDispatchedError(str(exc)) from exc
        if not _has_focus(control) or _control_value(control) != before_value:
            raise InputNotDispatchedError("Editor focus or draft changed after Rust capture; no input delivered")
        if caret_guard is not None:
            caret_guard()
    try:
        result = client.type_text(value, status, before_dispatch=input_guard)
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
        return _control_value(control) in expected_values

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
            "Resolve and focus one exact Windows UI Automation editor and verify exact text readback. Use Rust for single-line text, binding the append caret through TextPattern for drafts and handling empty paragraph markers. Otherwise use a writable UIA Value pattern. Omitted target requires a unique editor/composer. Never submits. Native input in strict Rust mode never falls back to Python.",
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
