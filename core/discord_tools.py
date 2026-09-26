from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from .desktop_control_tools import desktop_key_down, desktop_key_up, release_held_inputs
from .desktop_input import InputDeliveryError
from .permissions import Risk
from .process_control import check_cancelled
from .semantic_ui_tools import (
    _control_name, _control_type, _control_value, _descendants, _focus_window,
    _foreground_hwnd, _invoke, ui_type,
)
from .tools import ToolRegistry, ToolSpec
from .ui_state import wait_until

_DISCORD_PROCESSES = {"discord.exe", "discordcanary.exe", "discordptb.exe"}
_DESTINATION_TYPES = {"TreeItem", "ListItem", "TabItem", "Hyperlink"}
_SERVER_TYPES = {"TreeItem", "ListItem", "TabItem", "Button"}
_CONTEXT_TYPES = tuple(sorted(_DESTINATION_TYPES | _SERVER_TYPES | {"Edit", "Document"}))
_QUICK_SEARCH_NAMES = {"quick switcher", "where would you like to go?", "find or start a conversation"}


def _clean(value: str, label: str, *, optional: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or len(value) > 200:
        raise ValueError(f"Discord {label} must contain 1-200 safe characters")
    result = " ".join(value.split())
    if not result and not optional:
        raise ValueError(f"Discord {label} must contain 1-200 safe characters")
    return result


def _message(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > 4000 or "\0" in text:
        raise ValueError("Discord message must contain 1-4000 safe characters")
    return text


def _normalized(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def _channel_name(value: str) -> str:
    # Strip only explicit accessibility-role metadata, never fuzzy/subsequence-match a name.
    result = _normalized(value)
    result = re.sub(r"\s+\((?:direct|group) message\)(?:,.*)?$", "", result)
    for suffix in (" (text channel)", ", text channel"):
        if result.endswith(suffix):
            result = result[:-len(suffix)]
    return (result[1:] if result.startswith(("#", "@")) else result).strip()


def _visible(control: Any) -> bool:
    try:
        return bool(control.is_visible() and control.is_enabled())
    except Exception:
        return False


def _selected(control: Any) -> bool:
    try:
        return bool(control.is_selected())
    except Exception:
        try:
            return bool(control.iface_selection_item.CurrentIsSelected)
        except Exception:
            return False


def _controls(win: Any, control_types: tuple[str, ...] = ()) -> list[Any]:
    # Normal Discord state only needs a small union of semantic control types.
    # Push that filter into UIA so Chromium does not materialize every decorative
    # Text/Group node. Quick-Switcher child-label reads can still request a broad tree.
    controls = _descendants(
        win,
        require_complete=bool(control_types),
        control_types=control_types,
        visible_only=bool(control_types),
    )
    return [control for control in controls if _visible(control)]


def _identity(win: Any) -> tuple[int, int, float]:
    import psutil

    pid = int(win.element_info.process_id)
    process = psutil.Process(pid)
    if process.name().casefold() not in _DISCORD_PROCESSES:
        raise RuntimeError("Target window is not owned by a Discord desktop process")
    return int(win.handle), pid, float(process.create_time())


def _discord_window():
    if os.name != "nt":
        raise RuntimeError("Discord desktop automation is supported on Windows only")
    from pywinauto import Desktop

    candidates: list[Any] = []
    for win in Desktop(backend="uia").windows():
        try:
            if _visible(win):
                _identity(win)
                candidates.append(win)
        except Exception:
            continue
    if not candidates:
        raise RuntimeError("No visible Discord process window was found; open Discord first")
    foreground = _foreground_hwnd()
    for win in candidates:
        if int(win.handle) == foreground:
            return win
    if len(candidates) != 1:
        raise RuntimeError("Multiple Discord windows are open; focus the intended window first")
    return candidates[0]


def _window_guard(win: Any, identity: tuple[int, int, float]) -> None:
    check_cancelled()
    if _identity(win) != identity or _foreground_hwnd() != identity[0]:
        raise RuntimeError("Discord focus or process changed; inspect state before continuing")


def _control_id(control: Any) -> tuple:
    try:
        runtime_id = control.element_info.runtime_id
        if runtime_id:
            return ("runtime", *runtime_id)
    except Exception:
        pass
    try:
        automation_id = str(control.element_info.automation_id or "")
        if automation_id:
            return ("automation", _control_type(control), automation_id)
    except Exception:
        pass
    raise RuntimeError("Discord does not expose a stable UIA identity for the selected control")


def _unique(matches: list[Any], label: str, *, missing_ok: bool = False):
    if not matches and missing_ok:
        return None
    if not matches:
        raise RuntimeError(f"Discord {label} was not found in the current accessibility tree")
    if len(matches) != 1:
        raise RuntimeError(f"Discord {label} is ambiguous; specify an exact server and channel")
    return matches[0]


def _named(controls: list[Any], target: str, types: set[str], *, channel: bool = False) -> list[Any]:
    normalize = _channel_name if channel else _normalized
    return [control for control in controls
            if _control_type(control) in types and normalize(_control_name(control)) == normalize(target)]


def _composer(controls: list[Any], destination: str = ""):
    matches = []
    for control in controls:
        name = _control_name(control)
        if _control_type(control) not in {"Edit", "Document"} or not name.casefold().startswith("message "):
            continue
        current = name[len("message "):]
        if destination.startswith(("#", "@")) and not current.startswith(destination[0]):
            continue
        if not destination or _channel_name(name[len("message "):]) == _channel_name(destination):
            matches.append(control)
    return _unique(matches, "message composer", missing_ok=True)


@dataclass(frozen=True)
class _Context:
    identity: tuple[int, int, float]
    destination: str
    destination_id: tuple
    composer_name: str
    composer_id: tuple
    server: str = ""
    server_id: tuple = ()


def _context(win: Any, identity: tuple[int, int, float], destination: str = "", server: str = "",
             *, controls: list[Any] | None = None) -> _Context | None:
    _window_guard(win, identity)
    controls = _controls(win, _CONTEXT_TYPES) if controls is None else controls
    composer = _composer(controls, destination)
    if composer is None:
        return None
    current = _control_name(composer)[len("message "):]
    selected = [control for control in _named(controls, current, _DESTINATION_TYPES, channel=True) if _selected(control)]
    item = _unique(selected, "selected conversation", missing_ok=True)
    if item is None:
        return None
    server_item = None
    if server:
        server_item = _unique(_named(controls, server, _SERVER_TYPES), "server", missing_ok=True)
        if server_item is None or not _selected(server_item):
            return None
    return _Context(identity, _channel_name(current), _control_id(item), _control_name(composer),
                    _control_id(composer), server, _control_id(server_item) if server_item is not None else ())


def _context_guard(win: Any, context: _Context, policy_guard: Callable[[], None] | None = None) -> None:
    if policy_guard:
        policy_guard()
    current = _context(win, context.identity, context.destination, context.server)
    if current != context:
        raise RuntimeError("Discord selected conversation or composer changed; message was not authorized for this state")


def _hotkey(keys: list[str], guard, *, guard_after: bool = True) -> None:
    held: list[str] = []
    try:
        for key in keys:
            guard()
            held.append(key)
            desktop_key_down(key)
    finally:
        for key in reversed(held):
            try:
                desktop_key_up(key)
            except Exception:
                release_held_inputs()
    if guard_after:
        guard()


def _quick_scope(win: Any):
    controls = _controls(win)
    named = [control for control in controls if _normalized(_control_name(control)) == "quick switcher"
             and _control_type(control) not in {"Edit", "Document"}]
    if named:
        return _unique(named, "Quick Switcher panel")
    editors = [control for control in controls if _control_type(control) in {"Edit", "Document"}
               and _normalized(_control_name(control)) in _QUICK_SEARCH_NAMES]
    if not editors:
        return None
    editor = _unique(editors, "Quick Switcher input")
    parent = editor
    for _ in range(4):
        try:
            parent = parent.parent()
            if int(getattr(parent, "handle", 0) or 0) == int(win.handle):
                break
            if any(_control_type(control) == "ListItem" for control in _controls(parent)):
                return parent
        except AttributeError:
            break
    return None


def _quick_result(scope: Any, destination: str, server: str):
    matches = []
    for row in _controls(scope):
        if _control_type(row) not in {"ListItem", "TreeItem"}:
            continue
        labels = [_control_name(row), *[_control_name(child) for child in _controls(row)]]
        if not any(_channel_name(label) == _channel_name(destination) for label in labels):
            continue
        if server and not any(_normalized(label) == _normalized(server) for label in labels):
            continue
        matches.append(row)
    return _unique(matches, "exact Quick Switcher result", missing_ok=True)


def _navigate(win: Any, identity: tuple[int, int, float], destination: str, server: str,
              policy_guard: Callable[[], None] | None = None) -> _Context:
    def guard():
        if policy_guard:
            policy_guard()
        _window_guard(win, identity)
    guard()
    controls = _controls(win, _CONTEXT_TYPES)
    if server:
        server_item = _unique(_named(controls, server, _SERVER_TYPES), "server")
        if not _selected(server_item):
            guard()
            _invoke(server_item)
            def selected_server():
                guard()
                current = _unique(_named(_controls(win, _CONTEXT_TYPES), server, _SERVER_TYPES), "server", missing_ok=True)
                return current is not None and _selected(current)
            wait_until(selected_server, timeout=3,
                       description="Discord server selection")
            controls = _controls(win, _CONTEXT_TYPES)
    ready = _context(win, identity, destination, server, controls=controls)
    if ready:
        return ready
    target = _unique(_named(controls, destination, _DESTINATION_TYPES, channel=True),
                     "destination", missing_ok=True)
    if target is not None:
        guard()
        _invoke(target)
    else:
        _hotkey(["ctrl", "k"], guard)
        scope = wait_until(lambda: (guard(), _quick_scope(win))[1], timeout=2,
                           description="Discord Quick Switcher")
        editor = _unique([control for control in _controls(scope)
                          if _control_type(control) in {"Edit", "Document"}], "Quick Switcher input")
        if not _control_name(editor):
            raise RuntimeError("Quick Switcher input has no accessible name; inspect the interface")
        ui_type(text=destination, target=_control_name(editor), replace=True, state_guard=guard)
        def search_result():
            guard()
            current_scope = _quick_scope(win)
            return _quick_result(current_scope, destination, server) if current_scope is not None else None
        row = wait_until(search_result, timeout=3,
                         description="exact Discord search result")
        guard()
        method = _invoke(row)
        if method == "select":
            def selected_result_guard():
                guard()
                current_scope = _quick_scope(win)
                if current_scope is None:
                    raise RuntimeError("Discord Quick Switcher closed before result confirmation")
                current_row = _quick_result(current_scope, destination, server)
                if current_row is None or not _selected(current_row):
                    raise RuntimeError("Discord Quick Switcher selection changed before confirmation")
            selected_result_guard()
            _hotkey(["enter"], selected_result_guard, guard_after=False)
    return wait_until(lambda: _context(win, identity, destination, server), timeout=3,
                      description="selected Discord conversation and matching message composer")


def _navigation_result(context: _Context) -> dict[str, Any]:
    return {"action": "discord_go_to", "destination": context.destination, "server": context.server,
            "window_hwnd": context.identity[0], "selected": True, "composer": context.composer_name}


def discord_go_to(destination: str, server: str = "", *, _policy_guard: Callable[[], None] | None = None) -> str:
    target, scope = _clean(destination, "destination"), _clean(server, "server", optional=True)
    if _policy_guard:
        _policy_guard()
    check_cancelled()
    win = _discord_window()
    identity = _identity(win)
    _focus_window(win)
    context = _navigate(win, identity, target, scope, _policy_guard)
    return "VERIFIED: " + json.dumps(_navigation_result(context), ensure_ascii=False)


def _send(win: Any, context: _Context, message: str,
          policy_guard: Callable[[], None] | None = None) -> dict[str, Any]:
    guard = lambda: _context_guard(win, context, policy_guard)
    guard()
    composer = _composer(_controls(win, _CONTEXT_TYPES), context.destination)
    if composer is None or _control_value(composer) != "":
        raise RuntimeError("Discord composer is unreadable or contains a draft; inspect it before sending")
    # The guard runs before focus, writing, Enter, and delivery probes. Never retry a send.
    try:
        result = ui_type(text=message, target=context.composer_name, submit=True, replace=False, state_guard=guard)
    except Exception as exc:
        raise InputDeliveryError(
            f"Discord text or send outcome is uncertain; inspect state and do not resend automatically: {exc}"
        ) from exc
    if not result.startswith("VERIFIED:"):
        raise InputDeliveryError("Discord message send did not verify; do not resend automatically")
    return {"action": "discord_send_message", "destination": context.destination, "server": context.server,
            "characters": len(message), "delivery": result[len("VERIFIED: "):]}


def discord_send_message(text: str, destination: str = "", server: str = "", *,
                         _policy_guard: Callable[[], None] | None = None) -> str:
    message = _message(text)
    target, scope = _clean(destination, "destination", optional=True), _clean(server, "server", optional=True)
    if _policy_guard:
        _policy_guard()
    check_cancelled()
    win = _discord_window()
    identity = _identity(win)
    _focus_window(win)
    context = _context(win, identity, target, scope)
    if context is None:
        raise RuntimeError("Discord selected destination could not be verified; message was not sent")
    return "VERIFIED: " + json.dumps(_send(win, context, message, _policy_guard), ensure_ascii=False)


def discord_navigate_and_send(destination: str, text: str, server: str = "", *,
                              _policy_guard: Callable[[], None] | None = None) -> str:
    target, scope, message = _clean(destination, "destination"), _clean(server, "server", optional=True), _message(text)
    if _policy_guard:
        _policy_guard()
    check_cancelled()
    win = _discord_window()
    identity = _identity(win)
    _focus_window(win)
    context = _navigate(win, identity, target, scope, _policy_guard)
    sent = _send(win, context, message, _policy_guard)
    return "VERIFIED: " + json.dumps({"action": "discord_navigate_and_send", "destination": target,
        "server": scope, "characters": len(message), "navigation": _navigation_result(context), "message": sent},
        ensure_ascii=False)


def register_discord_tools(registry: ToolRegistry) -> None:
    from .discord_navigation import register_discord_navigation_tools
    register_discord_navigation_tools(registry)
    destination = {"type": "string", "minLength": 1, "maxLength": 200}
    server = {"type": "string", "maxLength": 200}
    message = {"type": "string", "minLength": 1, "maxLength": 4000}
    for name, description, properties, required, handler in (
        ("discord_go_to", "Navigate to an exact Discord channel/DM through UI Automation, using the Quick Switcher only when needed. Specify server for server-scoped channels. Rejects ambiguous targets and verifies selected conversation plus matching composer.",
         {"destination": destination, "server": server}, ["destination"], discord_go_to),
        ("discord_send_message", "Send one explicitly requested message in the verified selected Discord conversation. Optional destination/server bind the intended context. Refuses existing drafts and changed focus; never retries uncertain delivery.",
         {"text": message, "destination": server, "server": server}, ["text"], discord_send_message),
        ("discord_navigate_and_send", "Navigate to an exact Discord channel/DM then send exactly one explicitly requested message without another model round-trip. Specify server to disambiguate channels. Verifies selection and composer; aborts before send if context changes.",
         {"destination": destination, "text": message, "server": server}, ["destination", "text"], discord_navigate_and_send),
    ):
        dependencies = {name, "ui_focus", "ui_type", "desktop_type", "desktop_press"}
        if name != "discord_send_message":
            dependencies.update({"discord_go_to", "ui_activate", "ui_hotkey", "desktop_hotkey",
                                 "desktop_key_down", "desktop_key_up"})
        if name != "discord_go_to":
            dependencies.add("discord_send_message")

        def policy_guard(names=tuple(sorted(dependencies))):
            for dependency in names:
                spec = registry._tools.get(dependency)
                allowed, reason = registry.permissions.check(dependency, spec.risk if spec else Risk.MEDIUM,
                                                             approved=True)
                if not allowed:
                    raise PermissionError(reason)

        registry.register(ToolSpec(name, description, Risk.MEDIUM, {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}, partial(handler, _policy_guard=policy_guard)))
