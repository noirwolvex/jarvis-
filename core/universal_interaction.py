from __future__ import annotations

import json
from typing import Any

from .desktop_input import InputNotDispatchedError
from .permissions import Risk
from .semantic_target import SELECTOR_SCHEMA
from .tools import ToolRegistry, ToolSpec

_SURFACES = {"auto", "browser", "desktop"}

_BROWSER_TARGET_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "maxLength": 32},
        "selector": {"type": "string", "maxLength": 500},
        "role": {"type": "string", "maxLength": 40},
        "name": {"type": "string", "maxLength": 300},
    },
    "additionalProperties": False,
    "oneOf": [
        {"required": ["node_id"], "maxProperties": 1},
        {"required": ["selector"], "maxProperties": 1},
        {"required": ["role", "name"], "maxProperties": 2},
    ],
}


def _browser_active() -> bool:
    try:
        from .full_access_agent import _foreground_is_chrome
        return bool(_foreground_is_chrome())
    except Exception:
        return False


def _surface(
    value: str, *, title: str = "", selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None, expected_version: str = "",
    frame_selector: str = "", expected_hwnd: int | None = None,
) -> str:
    requested = str(value or "auto").strip().casefold()
    if requested not in _SURFACES:
        raise ValueError("interaction surface must be auto, browser, or desktop")
    desktop_bound = bool(title or selector is not None or expected_hwnd is not None)
    browser_bound = bool(browser_target is not None or expected_version or frame_selector)
    if desktop_bound and browser_bound:
        raise InputNotDispatchedError(
            "Mixed desktop and browser targets; use the action_target returned by interaction_resolve"
        )
    if requested == "auto":
        route = "desktop" if desktop_bound else "browser" if _browser_active() else "desktop"
    else:
        route = requested
    if route == "desktop" and browser_bound:
        raise InputNotDispatchedError(
            "A browser target cannot control the foreground desktop app. Inspect surface=desktop "
            "and use target/control_type or selector from Windows UI Automation."
        )
    if route == "browser" and desktop_bound:
        raise InputNotDispatchedError("Desktop window/selector arguments cannot be used for browser interaction")
    if requested == "browser":
        from .chrome_cdp import chrome_is_connected
        if not chrome_is_connected():
            raise RuntimeError("Managed Chrome/CDP is not connected; use desktop interaction for another application/browser")
    return route


def _desktop_window(expected_hwnd: int | None) -> None:
    if expected_hwnd is None:
        return
    from .semantic_ui_tools import _foreground_hwnd
    try:
        foreground = _foreground_hwnd()
    except Exception as exc:
        raise InputNotDispatchedError("Cannot validate the observed desktop window; inspect again") from exc
    if type(expected_hwnd) is not int or expected_hwnd <= 0 or foreground != expected_hwnd:
        raise InputNotDispatchedError("Desktop scene window is no longer foreground; inspect the intended window again")


def _require_permission(registry: ToolRegistry, name: str) -> None:
    spec = registry._tools.get(name)
    if spec is None:
        raise RuntimeError(f"Required interaction backend is not registered: {name}")
    allowed, reason = registry.permissions.check(name, spec.risk, approved=True)
    if not allowed:
        raise PermissionError(reason)


def _browser_target(target: dict[str, Any] | None, name: str, role: str) -> dict[str, Any]:
    if target is not None:
        return target
    clean_name = " ".join(str(name or "").split()).strip()
    clean_role = " ".join(str(role or "").split()).strip()
    if not clean_name or not clean_role:
        raise ValueError("Browser interaction requires browser_target or exact target + control_type/role")
    return {"role": clean_role, "name": clean_name}


def _desktop_target(target: str, selector: dict[str, Any] | None) -> None:
    if not str(target or "").strip() and selector is None:
        raise ValueError("Desktop semantic interaction requires an exact target or selector")


def interaction_inspect(
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    title: str = "",
    query: str = "",
    control_type: str = "",
    max_controls: int = 120,
    frame_selector: str = "",
    force_refresh: bool = False,
    actionable_only: bool = True,
) -> str:
    route = _surface(surface, title=title, frame_selector=frame_selector)
    if route == "browser":
        if control_type:
            raise ValueError("interaction_inspect control_type filters Windows UIA controls; use surface=desktop or omit it for a browser snapshot")
        _require_permission(registry, "browser_semantic_snapshot")
        from .browser_semantic import browser_semantic_snapshot
        raw = browser_semantic_snapshot(
            force=bool(force_refresh),
            max_nodes=max(1, min(int(max_controls), 250)),
            frame_selector=frame_selector,
        )
        return json.dumps({"surface": "browser", "snapshot": json.loads(raw)}, ensure_ascii=False)

    _require_permission(registry, "ui_inspect")
    from .semantic_ui_tools import ui_inspect
    raw = ui_inspect(
        title=title,
        query=query,
        actionable_only=bool(actionable_only),
        max_controls=max_controls,
        force_refresh=bool(force_refresh),
        control_type=control_type,
    )
    return json.dumps({"surface": "desktop", "snapshot": json.loads(raw)}, ensure_ascii=False)


def interaction_click(
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    target: str = "",
    title: str = "",
    control_type: str = "",
    selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None,
    expected_version: str = "",
    frame_selector: str = "",
    expected_hwnd: int | None = None,
) -> str:
    route = _surface(surface, title=title, selector=selector, browser_target=browser_target,
                     expected_version=expected_version, frame_selector=frame_selector,
                     expected_hwnd=expected_hwnd)
    if route == "browser":
        _require_permission(registry, "browser_semantic_action")
        from .browser_semantic import browser_semantic_action
        return browser_semantic_action(
            "click",
            _browser_target(browser_target, target, control_type),
            expected_version=expected_version,
            frame_selector=frame_selector,
        )

    _require_permission(registry, "ui_activate")
    _desktop_target(target, selector)
    _desktop_window(expected_hwnd)
    from .semantic_ui_tools import ui_activate
    return ui_activate(target=target, title=title, control_type=control_type, selector=selector)


def interaction_focus(
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    target: str = "",
    title: str = "",
    control_type: str = "",
    selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None,
    expected_version: str = "",
    frame_selector: str = "",
    expected_hwnd: int | None = None,
) -> str:
    route = _surface(surface, title=title, selector=selector, browser_target=browser_target,
                     expected_version=expected_version, frame_selector=frame_selector,
                     expected_hwnd=expected_hwnd)
    if route == "browser":
        _require_permission(registry, "browser_semantic_action")
        from .browser_semantic import browser_semantic_action
        return browser_semantic_action(
            "focus",
            _browser_target(browser_target, target, control_type),
            expected_version=expected_version,
            frame_selector=frame_selector,
        )

    _require_permission(registry, "ui_focus")
    _desktop_target(target, selector)
    _desktop_window(expected_hwnd)
    from .semantic_ui_tools import ui_focus
    return ui_focus(target=target, title=title, control_type=control_type, selector=selector)


def interaction_type(
    text: str,
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    target: str = "",
    title: str = "",
    control_type: str = "",
    selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None,
    expected_version: str = "",
    frame_selector: str = "",
    replace: bool = False,
    submit: bool = False,
    focused_fallback: bool = False,
    expected_hwnd: int | None = None,
) -> str:
    value = str(text)
    if not value or len(value) > 4096 or "\0" in value:
        raise ValueError("interaction_type text must contain 1-4096 characters without NUL")
    route = _surface(surface, title=title, selector=selector, browser_target=browser_target,
                     expected_version=expected_version, frame_selector=frame_selector,
                     expected_hwnd=expected_hwnd)

    if route == "browser":
        _require_permission(registry, "browser_semantic_action")
        from .browser_semantic import browser_semantic_action
        if browser_target is None and not target and not control_type and not expected_version:
            # A literal "type ..." command can use the currently focused editor
            # without a model turn. Never guess the first/only editor on a page.
            if submit:
                raise InputNotDispatchedError("Browser submit requires an explicit target; no input delivered")
            _require_permission(registry, "browser_semantic_snapshot")
            from .browser_semantic import browser_semantic_snapshot
            scene = json.loads(browser_semantic_snapshot(force=True, max_nodes=1, frame_selector=frame_selector))
            focused = [node for node in scene.get("nodes", [])
                       if node.get("node_id") == scene.get("focused_node") and node.get("focused") is True]
            if (scene.get("has_focus") is not True or len(focused) != 1
                    or focused[0].get("input_kind") != "text" or focused[0].get("disabled")
                    or not isinstance(scene.get("version"), str) or not scene["version"]):
                raise InputNotDispatchedError("No verified focused browser editor; focus the intended field before typing")
            browser = {"node_id": focused[0]["node_id"]}
            expected_version = scene["version"]
        else:
            browser = _browser_target(browser_target, target, control_type or "textbox")
        if submit and browser.get("node_id"):
            raise ValueError("Browser submit cannot reuse a node_id after typing invalidates its snapshot; use an exact role/name or selector target")
        action = "fill" if replace else "append"
        typed = browser_semantic_action(
            action,
            browser,
            value=value,
            expected_version=expected_version,
            frame_selector=frame_selector,
        )
        if not submit:
            return typed
        # Sending/submitting is a distinct action. Its result must be independently
        # observed by the mission before another mutation; never infer success here.
        pressed = browser_semantic_action(
            "press",
            browser,
            value="Enter",
            frame_selector=frame_selector,
        )
        return "ACTION_EXECUTED: " + json.dumps(
            {"surface": "browser", "typed": typed, "submit": pressed,
             "requires_result_verification": True},
            ensure_ascii=False,
        )

    # Desktop text follows the same deterministic priority as the rest of JARVIS:
    # direct UI Automation write/readback first, then Rust-native caret-bound typing
    # only when the exact editor exposes no writable Value pattern. Ambiguous targets,
    # focus loss, draft changes and multiline safety errors never fall through.
    _require_permission(registry, "ui_type")
    _desktop_window(expected_hwnd)
    from .semantic_ui_tools import ui_type
    try:
        return ui_type(
            text=value,
            target=target,
            title=title,
            submit=bool(submit),
            replace=bool(replace),
            selector=selector,
        )
    except InputNotDispatchedError as semantic_error:
        semantic_text = str(semantic_error)
        native_allowed = (
            not submit
            and not replace
            and "no writable Value pattern" in semantic_text
        )
        if native_allowed:
            _require_permission(registry, "ui_type_native")
            _desktop_window(expected_hwnd)
            from .native_ui_input import ui_type_native
            try:
                return ui_type_native(text=value, target=target, title=title, selector=selector)
            except InputNotDispatchedError:
                if not focused_fallback or target or selector is not None:
                    raise
        elif not focused_fallback or target or selector is not None or submit or replace:
            raise

    # Last-resort keyboard path for custom/canvas apps with no useful accessibility
    # editor. It is opt-in, non-browser only, and types into the already-focused control.
    _require_permission(registry, "desktop_type")
    _desktop_window(expected_hwnd)
    spec = registry._tools["desktop_type"]
    registry._validators["desktop_type"].validate({"text": value})
    return spec.handler(text=value)


def _browser_hotkey(keys: list[str]) -> str:
    aliases = {
        "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift",
        "win": "Meta", "windows": "Meta", "esc": "Escape", "escape": "Escape",
        "enter": "Enter", "tab": "Tab", "space": "Space", "backspace": "Backspace",
        "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft",
        "right": "ArrowRight", "home": "Home", "end": "End",
        "pageup": "PageUp", "pagedown": "PageDown",
    }
    result: list[str] = []
    for key in keys:
        value = str(key or "").strip()
        lower = value.casefold()
        if lower in aliases:
            result.append(aliases[lower])
        elif len(value) == 1 and value.isprintable():
            result.append(value.upper() if len(keys) > 1 else value)
        elif lower.startswith("f") and lower[1:].isdigit() and 1 <= int(lower[1:]) <= 12:
            result.append(lower.upper())
        else:
            raise ValueError(f"Unsupported browser hotkey key: {key}")
    if not result or len(result) > 8:
        raise ValueError("interaction_hotkey requires 1-8 keys")
    return "+".join(result)


def interaction_scroll(
    delta_y: int,
    *,
    registry: ToolRegistry,
    delta_x: int = 0,
    surface: str = "auto",
    frame_selector: str = "",
) -> str:
    route = _surface(surface, frame_selector=frame_selector)
    if route == "browser":
        _require_permission(registry, "browser_semantic_scroll")
        from .browser_semantic import browser_semantic_scroll
        return browser_semantic_scroll(delta_y=int(delta_y), delta_x=int(delta_x), frame_selector=frame_selector)

    if int(delta_x) != 0:
        raise ValueError("Desktop native scroll currently supports vertical wheel input only")
    _require_permission(registry, "desktop_scroll")
    spec = registry._tools["desktop_scroll"]
    # Browser/CSS coordinates use positive Y for downward movement; Windows wheel
    # uses positive notches for upward movement. Normalize the public contract.
    arguments = {"clicks": -int(delta_y)}
    registry._validators["desktop_scroll"].validate(arguments)
    return spec.handler(**arguments)


def interaction_wait(
    *,
    registry: ToolRegistry,
    state: str = "visible",
    surface: str = "auto",
    target: str = "",
    title: str = "",
    control_type: str = "",
    selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None,
    expected_version: str = "",
    frame_selector: str = "",
    timeout_ms: int = 1500,
    text: str = "",
    expected_hwnd: int | None = None,
) -> str:
    """Universal read-only postcondition wait; never dispatches mouse or keyboard input."""
    if expected_version:
        raise ValueError("interaction_wait requires a stable exact locator, not a snapshot expected_version")
    route = _surface(surface, title=title, selector=selector, browser_target=browser_target,
                     frame_selector=frame_selector, expected_hwnd=expected_hwnd)
    timeout = max(0, min(int(timeout_ms), 15000))
    if route == "browser":
        _require_permission(registry, "browser_wait_state")
        if state == "selected":
            raise ValueError("selected-state interaction_wait is supported for desktop UIA controls only")
        from .browser_semantic import browser_wait_state
        return browser_wait_state(
            _browser_target(browser_target, target, control_type),
            state=state,
            timeout_ms=timeout,
            text=text,
            frame_selector=frame_selector,
        )

    if state not in {"visible", "focused", "selected", "enabled", "hidden", "text"}:
        raise ValueError("Unsupported desktop interaction_wait state")
    _require_permission(registry, "ui_wait_state")
    _desktop_target(target, selector)
    _desktop_window(expected_hwnd)
    from .semantic_ui_tools import ui_wait_state
    return ui_wait_state(
        target=target,
        title=title,
        control_type=control_type,
        state=state,
        timeout_ms=timeout,
        selector=selector,
        **({"text": text} if state == "text" else {}),
    )


def interaction_hotkey(
    keys: list[str],
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    title: str = "",
    target: str = "",
    control_type: str = "",
    selector: dict[str, Any] | None = None,
    browser_target: dict[str, Any] | None = None,
    expected_version: str = "",
    frame_selector: str = "",
    expected_hwnd: int | None = None,
) -> str:
    route = _surface(surface, title=title, selector=selector, browser_target=browser_target,
                     expected_version=expected_version, frame_selector=frame_selector,
                     expected_hwnd=expected_hwnd)
    if route == "browser":
        _require_permission(registry, "browser_semantic_action")
        from .browser_semantic import browser_semantic_action
        return browser_semantic_action(
            "press",
            _browser_target(browser_target, target, control_type or "textbox"),
            value=_browser_hotkey(keys),
            expected_version=expected_version,
            frame_selector=frame_selector,
        )

    _require_permission(registry, "ui_hotkey")
    _desktop_window(expected_hwnd)
    if target or selector is not None:
        _require_permission(registry, "ui_focus")
        from .semantic_ui_tools import ui_focus
        ui_focus(target=target, title=title, control_type=control_type, selector=selector)
        _desktop_window(expected_hwnd)
    from .semantic_ui_tools import ui_hotkey
    return ui_hotkey(keys=keys, title=title)


def register_universal_interaction_tools(registry: ToolRegistry) -> None:
    common = {
        "surface": {"enum": ["auto", "browser", "desktop"]},
        "target": {"type": "string", "minLength": 1, "maxLength": 500},
        "title": {"type": "string", "maxLength": 500},
        "control_type": {"type": "string", "maxLength": 80},
        "selector": SELECTOR_SCHEMA,
        "browser_target": _BROWSER_TARGET_SCHEMA,
        "expected_version": {"type": "string", "maxLength": 100},
        "expected_hwnd": {"type": "integer", "minimum": 1},
        "frame_selector": {"type": "string", "maxLength": 500},
    }
    registry.register(ToolSpec(
        "interaction_inspect",
        "Universal read-only interaction snapshot. Auto-routes foreground managed Chrome to bounded DOM/CDP controls; otherwise reads Windows UI Automation. Desktop control_type filters at the provider (Edit, Button, TreeItem, etc.) to avoid scanning unrelated controls. Use this first for an unfamiliar app/site.",
        Risk.LOW,
        {"type": "object", "properties": {
            "surface": common["surface"], "title": common["title"], "query": {"type": "string", "maxLength": 500},
            "control_type": common["control_type"],
            "max_controls": {"type": "integer", "minimum": 1, "maximum": 250},
            "frame_selector": common["frame_selector"], "force_refresh": {"type": "boolean"},
            "actionable_only": {"type": "boolean"},
        }, "additionalProperties": False},
        lambda **kwargs: interaction_inspect(registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_click",
        "Universal exact semantic click/activation. Reuse interaction_resolve.action_target. Desktop: surface=desktop, target=exact label, control_type=UIA type (e.g. ListItem), title=window. Browser: browser_target must be node_id alone, selector alone, or role+name. Never use browser_target for a desktop app. For unlabeled/canvas targets, use screen_observe + guarded desktop coordinates instead.",
        Risk.MEDIUM,
        {"type": "object", "properties": common, "anyOf": [{"required": ["target"]}, {"required": ["selector"]}, {"required": ["browser_target"]}], "additionalProperties": False},
        lambda **kwargs: interaction_click(registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_focus",
        "Universal exact semantic focus. Uses DOM focus for managed sites or Windows UIA focus for desktop controls.",
        Risk.MEDIUM,
        {"type": "object", "properties": common, "anyOf": [{"required": ["target"]}, {"required": ["selector"]}, {"required": ["browser_target"]}], "additionalProperties": False},
        lambda **kwargs: interaction_focus(registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_type",
        "Universal verified text entry. Uses DOM fill/append in managed Chrome; an omitted browser target can use a freshly verified focused editor for unsent typing only. On desktop it prefers exact Windows UI Automation ValuePattern write/readback, falls back to Rust-native caret-bound typing only when that exact editor has no writable Value pattern, and only with focused_fallback=true may use guarded focused Rust input for custom controls with no editable UIA node. Typing does not submit unless submit=true.",
        Risk.MEDIUM,
        {"type": "object", "properties": {
            **common,
            "text": {"type": "string", "minLength": 1, "maxLength": 4096},
            "replace": {"type": "boolean"}, "submit": {"type": "boolean"},
            "focused_fallback": {"type": "boolean"},
        }, "required": ["text"], "additionalProperties": False},
        lambda text, **kwargs: interaction_type(text, registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_wait",
        "Universal read-only postcondition checkpoint. Auto-routes managed Chrome to bounded DOM polling and desktop apps to fresh UIA state polling. selected is desktop-only and proves control selection, not navigation. Use inside workflow_execute after delivered semantic clicks/hotkeys with the actual expected result so execution can continue without another model turn.",
        Risk.LOW,
        {"type": "object", "properties": {
            **common,
            "state": {"enum": ["visible", "hidden", "enabled", "text", "focused", "selected"]},
            "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 15000},
            "text": {"type": "string", "maxLength": 4096},
        }, "anyOf": [{"required": ["target"]}, {"required": ["selector"]}, {"required": ["browser_target"]}], "additionalProperties": False},
        lambda **kwargs: interaction_wait(registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_scroll",
        "Universal scroll where positive delta_y means down and negative means up. Managed Chrome scrolls the DOM viewport with challenge guards; desktop apps use guarded native wheel input. For nested/canvas regions that need pointer placement, use screen_observe first.",
        Risk.MEDIUM,
        {"type": "object", "properties": {
            "delta_y": {"type": "integer", "minimum": -1000, "maximum": 1000},
            "delta_x": {"type": "integer", "minimum": -1000, "maximum": 1000},
            "surface": common["surface"], "frame_selector": common["frame_selector"],
        }, "required": ["delta_y"], "additionalProperties": False},
        lambda delta_y, **kwargs: interaction_scroll(delta_y, registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_hotkey",
        "Universal keyboard shortcut. Managed Chrome requires an exact DOM target; desktop apps use the guarded semantic/Rust hotkey path.",
        Risk.MEDIUM,
        {"type": "object", "properties": {
            "keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
            **common,
        }, "required": ["keys"], "additionalProperties": False},
        lambda keys, **kwargs: interaction_hotkey(keys, registry=registry, **kwargs),
    ))
