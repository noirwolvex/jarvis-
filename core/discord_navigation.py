"""Bounded, deterministic navigation of Discord's visible direct-message list."""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from . import discord_tools as discord
from . import semantic_ui_tools as ui
from .desktop_input import InputDeliveryError, InputNotDispatchedError
from .execution_telemetry import record_backend
from .ui_state import wait_until


def _dm_route(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme or url.netloc:
        if url.scheme != "https" or url.netloc != "discord.com":
            return None
    if url.query or url.fragment or not re.fullmatch(r"/channels/@me/[0-9]{1,24}", url.path):
        return None
    return url.path


_DISCOVERY_TYPES = ("List", "TreeItem", "Document", "Edit", "Hyperlink")
_VERIFICATION_TYPES = ("Document", "Edit")


def _snapshot(win: Any) -> list[Any]:
    # One provider traversal resolves the DM scope, visible conversation links,
    # current route and composer. This avoids a second scoped Hyperlink traversal.
    return ui._descendants(win, require_complete=True,
                           control_types=_DISCOVERY_TYPES, visible_only=True)


def _verification_snapshot(win: Any) -> list[Any]:
    # After a delivered navigation only route + composer can prove completion.
    # Do not re-enumerate the sidebar while waiting for that postcondition.
    return ui._descendants(win, require_complete=True,
                           control_types=_VERIFICATION_TYPES, visible_only=True)


def _dm_scope(controls: list[Any]):
    return discord._unique([c for c in controls if ui._control_type(c) == "List"
                            and discord._normalized(ui._control_name(c)) == "direct messages"],
                           "Direct Messages list", missing_ok=True)


def _conversation_links(win: Any, scope: Any, controls: list[Any] | None = None) -> list[tuple[Any, str]]:
    bounds = ui._rect(scope)
    if len(bounds) != 4 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise InputNotDispatchedError("Discord Direct Messages scope geometry is unavailable")
    links = []
    source = controls if controls is not None else ui._descendants(
        scope, require_complete=True, control_types=("Hyperlink",), visible_only=True)
    for control in source:
        if ui._control_type(control) != "Hyperlink":
            continue
        route = _dm_route(ui._control_value(control))
        if not route or not discord._visible(control):
            continue
        rect = ui._rect(control)
        if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
            raise InputNotDispatchedError("Discord chat geometry is unavailable; no chat input delivered")
        x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if not (bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]):
            continue
        links.append((control, route))
    links.sort(key=lambda row: (ui._rect(row[0])[1], ui._rect(row[0])[0]))
    positions = [tuple(ui._rect(control)[:2]) for control, _ in links]
    if len(set(positions)) != len(positions) or len({route for _, route in links}) != len(links):
        raise InputNotDispatchedError("Discord chat links are ambiguous; inspect the current list")
    return links


def _destination_name(control: Any) -> str:
    # Preserve decorated Unicode names; the explicit accessible role suffix is
    # metadata, not part of the recipient. Friends/Shop links have no DM route.
    name = ui._control_name(control)
    match = re.fullmatch(r"(.+) \((?:direct|group) message\)(?:,.*)?", name, re.IGNORECASE)
    if not match:
        raise InputNotDispatchedError("Discord chat link has no unambiguous conversation label")
    return match.group(1)


def _opened(controls: list[Any], route: str, destination: str) -> bool:
    documents = [c for c in controls if ui._control_type(c) == "Document"
                 and ui._automation_id(c) == "RootWebArea"]
    document = discord._unique(documents, "application document", missing_ok=True)
    return bool(document is not None and _dm_route(ui._control_value(document)) == route
                and discord._composer(controls, destination) is not None)


def _activate(win: Any, control: Any, guard: Callable[[], None], route: str | None = None) -> str:
    from .rust_engine import _preflight, RustEngineUnavailable
    binding = ui._target_binding(win, control)
    def validate():
        guard()
        ui._validate_target(win, control, binding)
        if route is not None and _dm_route(ui._control_value(control)) != route:
            raise InputNotDispatchedError("Discord chat identity changed before activation")
    validate()
    try:
        return "uia_" + ui._invoke(control, int(win.handle))
    except RuntimeError as exc:
        # Only unsupported patterns allow a fallback. An attempted Invoke or
        # Select with an uncertain outcome must never be followed by a click.
        if str(exc) != "Control has no supported semantic activation pattern; use fresh screen observation and the guarded desktop input tool":
            raise
    client, status = _preflight()
    if client is None:
        raise RustEngineUnavailable("Discord native fallback requires the Rust daemon")
    validate()
    ui._guard_native_target(int(win.handle), status)
    left, top, right, bottom = binding["control"]["rect"]
    result = client.click((left + right) // 2, (top + bottom) // 2, status, before_dispatch=validate)
    if result.get("executed") is not True or result.get("simulation") is not False:
        raise InputDeliveryError("Discord click delivery is uncertain; inspect without replaying")
    ui._SNAPSHOTS.invalidate(int(win.handle))
    return "rust_native_click"


def discord_select_chat(position: int, *, _policy_guard: Callable[[], None] | None = None) -> str:
    if type(position) is not int or not 1 <= position <= 20:
        raise ValueError("Discord chat position must be an integer between 1 and 20")
    if _policy_guard:
        _policy_guard()
    win = discord._discord_window()
    identity = discord._identity(win)
    ui._focus_window(win)
    def guard():
        if _policy_guard:
            _policy_guard()
        discord._window_guard(win, identity)
    guard()
    record_backend("windows_uia", phase="resolve", detail="Resolve Discord Direct Messages list and conversation links")
    controls = _snapshot(win)
    scope = _dm_scope(controls)
    if scope is None:
        home = discord._unique([c for c in controls if ui._control_type(c) == "TreeItem"
                                and discord._normalized(ui._control_name(c)) == "direct messages"], "Direct Messages navigation")
        _activate(win, home, guard)
        def list_ready():
            nonlocal controls
            guard()
            controls = _snapshot(win)
            return _dm_scope(controls)
        scope = wait_until(list_ready, timeout=3, description="Discord Direct Messages list")
    guard()
    links = _conversation_links(win, scope, controls)
    if len(links) < position:
        raise RuntimeError(f"Discord exposes {len(links)} visible conversation links; cannot select chat {position}")
    control, route = links[position - 1]
    destination = _destination_name(control)
    guard()
    # Independent current route + matching composer also make repeated requests
    # idempotent when Discord is already showing the requested conversation.
    already_open = _opened(controls, route, destination)
    method = "already_open"
    if not already_open:
        method = _activate(win, control, guard, route)
        def opened():
            guard()
            record_backend("windows_uia", phase="verify", detail="Exact Discord document route and matching composer")
            return _opened(_verification_snapshot(win), route, destination)
        try:
            wait_until(opened, timeout=3, description="exact Discord conversation route and composer")
        except TimeoutError as exc:
            raise InputDeliveryError("Discord navigation was attempted but the requested conversation did not verify; inspect before retrying") from exc
    else:
        record_backend("windows_uia", phase="verify", detail="Requested Discord route and composer already open")
    return "VERIFIED: " + json.dumps({"action": "discord_select_chat", "position": position,
        "window_hwnd": identity[0], "route": route, "destination": destination, "method": method,
        "already_open": already_open, "selected": True, "route_verified": True, "composer_verified": True}, ensure_ascii=False)


def register_discord_navigation_tools(registry) -> None:
    from .permissions import Risk
    from .tools import ToolSpec
    def policy_guard():
        for name in ("discord_select_chat", "ui_focus", "ui_activate", "desktop_click_button"):
            spec = registry._tools.get(name)
            allowed, reason = registry.permissions.check(name, spec.risk if spec else Risk.MEDIUM, approved=True)
            if not allowed:
                raise PermissionError(reason)
    registry.register(ToolSpec("discord_select_chat",
        "Open the Nth visible conversation in Discord's Direct Messages list, excluding Friends/Nitro/Shop. "
        "Uses exact accessible conversation links, semantic activation then guarded Rust fallback when unsupported, "
        "and independently verifies the document route and matching composer. Never types or sends a message.",
        Risk.MEDIUM, {"type": "object", "properties": {"position": {"type": "integer", "minimum": 1, "maximum": 20}},
                      "required": ["position"], "additionalProperties": False},
        lambda position: discord_select_chat(position, _policy_guard=policy_guard)))
