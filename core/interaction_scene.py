"""Unified, incremental semantic scene model for fast browser and desktop control.

The scene cache stores detached metadata only. Actions never consume cached native
objects: browser node targets are version-bound and desktop actions re-resolve live.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any

from .permissions import Risk
from .tools import ToolRegistry, ToolSpec
from .universal_interaction import interaction_inspect


_ACTIONABLE_BROWSER_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio", "slider",
    "tab", "menuitem", "option", "switch",
}
_ACTIONABLE_DESKTOP_TYPES = {
    "Button", "Edit", "Document", "Hyperlink", "ListItem", "TreeItem", "TabItem",
    "CheckBox", "RadioButton", "ComboBox", "MenuItem", "DataItem", "Slider",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _norm(value: Any) -> str:
    return _clean(value).casefold()


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = _norm(value)
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return None


def _rect_browser(bounds: dict[str, Any] | None) -> list[int]:
    if not isinstance(bounds, dict):
        return []
    try:
        x = int(bounds.get("x", 0))
        y = int(bounds.get("y", 0))
        width = int(bounds.get("width", 0))
        height = int(bounds.get("height", 0))
    except (TypeError, ValueError):
        return []
    return [x, y, x + width, y + height]


def _desktop_id(row: dict[str, Any]) -> str:
    runtime = row.get("runtime_id")
    if isinstance(runtime, list) and runtime:
        return "uia:" + ".".join(str(int(item)) for item in runtime)
    auto = _clean(row.get("automation_id"))
    name = _clean(row.get("name"))
    kind = _clean(row.get("type"))
    rect = row.get("rect") if isinstance(row.get("rect"), list) else []
    digest = hashlib.sha1(
        json.dumps([auto, name, kind, rect], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return "uia-fallback:" + digest


def _browser_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    version = _clean(snapshot.get("version"))
    nodes = []
    for row in snapshot.get("nodes", []):
        if not isinstance(row, dict):
            continue
        node_id = _clean(row.get("node_id"))
        role = _clean(row.get("role"))
        name = _clean(row.get("name"))
        if not node_id:
            continue
        nodes.append({
            "id": "dom:" + node_id,
            "source": "DOM",
            "role": role,
            "name": name,
            "parent": "dom:" + _clean(row.get("parent")) if row.get("parent") else "",
            "rect": _rect_browser(row.get("bounds")),
            "enabled": not bool(row.get("disabled")),
            "visible": True,
            "selected": _boolish(row.get("selected")),
            "focused": bool(row.get("focused")),
            "checked": _boolish(row.get("checked")),
            "expanded": _boolish(row.get("expanded")),
            "actionable": role.casefold() in _ACTIONABLE_BROWSER_ROLES,
            "target": {
                "surface": "browser",
                "browser_target": {"node_id": node_id},
                "expected_version": version,
            },
        })
    return nodes


def _desktop_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for row in snapshot.get("controls", []):
        if not isinstance(row, dict):
            continue
        kind = _clean(row.get("type"))
        name = _clean(row.get("name"))
        node = {
            "id": _desktop_id(row),
            "source": "UIA",
            "role": kind,
            "name": name,
            "parent": _clean(row.get("parent_ref")),
            "ancestors": list(row.get("ancestors") or []),
            "rect": list(row.get("rect") or []),
            "enabled": row.get("enabled") is not False,
            "visible": row.get("visible") is not False,
            "selected": row.get("selected"),
            "focused": row.get("focused"),
            "automation_id": _clean(row.get("automation_id")),
            "actionable": kind in _ACTIONABLE_DESKTOP_TYPES,
            "target": {
                "surface": "desktop",
                "target": name or _clean(row.get("automation_id")),
                "control_type": kind,
            },
        }
        nodes.append(node)
    return nodes


def _fingerprint(node: dict[str, Any]) -> str:
    fields = (
        node.get("role"), node.get("name"), node.get("parent"), node.get("rect"),
        node.get("enabled"), node.get("visible"), node.get("selected"),
        node.get("focused"), node.get("checked"), node.get("expanded"),
    )
    return hashlib.sha1(
        json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


@dataclass
class _Scene:
    scene_id: str
    created: float
    nodes: dict[str, dict[str, Any]]


class SceneTracker:
    """Keeps only detached semantic metadata so later observations can return deltas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenes: dict[str, _Scene] = {}
        self._counter = 0

    def reset(self) -> None:
        with self._lock:
            self._scenes.clear()
            self._counter = 0

    def update(self, key: str, nodes: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
        current = {node["id"]: node for node in nodes}
        with self._lock:
            previous = self._scenes.get(key)
            self._counter += 1
            scene_id = f"scene-{self._counter}"
            self._scenes[key] = _Scene(scene_id, time.monotonic(), current)
        if previous is None:
            return scene_id, None

        added = [node for node_id, node in current.items() if node_id not in previous.nodes]
        removed = [node_id for node_id in previous.nodes if node_id not in current]
        changed = [
            node for node_id, node in current.items()
            if node_id in previous.nodes and _fingerprint(node) != _fingerprint(previous.nodes[node_id])
        ]
        return scene_id, {
            "base_scene_id": previous.scene_id,
            "added": added,
            "changed": changed,
            "removed": removed,
            "unchanged_count": max(0, len(current) - len(added) - len(changed)),
        }


_SCENES = SceneTracker()


def reset_interaction_scenes() -> None:
    _SCENES.reset()


def _capture(
    registry: ToolRegistry,
    *,
    surface: str,
    title: str,
    query: str,
    max_controls: int,
    frame_selector: str,
    force_refresh: bool,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], str]:
    raw = interaction_inspect(
        registry=registry,
        surface=surface,
        title=title,
        query=query,
        max_controls=max_controls,
        frame_selector=frame_selector,
        force_refresh=force_refresh,
    )
    wrapped = json.loads(raw)
    actual = str(wrapped["surface"])
    snapshot = wrapped["snapshot"]
    if actual == "browser":
        nodes = _browser_nodes(snapshot)
        context = (
            f"browser:{snapshot.get('url', '')}:{frame_selector}"
            f":q={_norm(query)}:limit={int(max_controls)}"
        )
    else:
        nodes = _desktop_nodes(snapshot)
        context = (
            f"desktop:{snapshot.get('hwnd', '')}:{title}"
            f":q={_norm(query)}:limit={int(max_controls)}"
        )
    return actual, snapshot, nodes, context


def interaction_scene(
    *,
    registry: ToolRegistry,
    surface: str = "auto",
    title: str = "",
    query: str = "",
    max_controls: int = 160,
    frame_selector: str = "",
    force_refresh: bool = False,
    mode: str = "auto",
) -> str:
    mode = str(mode or "auto").casefold()
    if mode not in {"auto", "full", "delta"}:
        raise ValueError("interaction_scene mode must be auto, full, or delta")
    actual, snapshot, nodes, context = _capture(
        registry,
        surface=surface,
        title=title,
        query=query,
        max_controls=max_controls,
        frame_selector=frame_selector,
        force_refresh=force_refresh,
    )
    scene_id, delta = _SCENES.update(context, nodes)
    use_delta = delta is not None and mode in {"auto", "delta"}
    payload: dict[str, Any] = {
        "scene_id": scene_id,
        "surface": actual,
        "context": {
            "title": snapshot.get("title", title),
            "url": snapshot.get("url", ""),
            "hwnd": snapshot.get("hwnd"),
            "foreground": snapshot.get("is_foreground"),
        },
        "source_version": snapshot.get("version", snapshot.get("generation")),
        "cached_source": bool(snapshot.get("cached")),
        "truncated": bool(snapshot.get("truncated")),
        "node_count": len(nodes),
        "mode": "delta" if use_delta else "full",
        "note": (
            "Detached semantic metadata only. Actions resolve live targets again; "
            "use screen_observe only when semantic evidence is unavailable or ambiguous."
        ),
    }
    if use_delta:
        payload["delta"] = delta
    else:
        payload["nodes"] = nodes
        if mode == "delta" and delta is None:
            payload["delta_unavailable"] = "No prior scene exists for this surface; returned full scene."
    return "VERIFIED: " + json.dumps(payload, ensure_ascii=False)


def _reading_order(node: dict[str, Any]) -> tuple[int, int]:
    rect = node.get("rect") or []
    if len(rect) != 4:
        return (10**9, 10**9)
    return (int(rect[1]), int(rect[0]))


def interaction_resolve(
    *,
    registry: ToolRegistry,
    query: str = "",
    role: str = "",
    ordinal: int | str | None = None,
    selected: bool | None = None,
    focused: bool | None = None,
    surface: str = "auto",
    title: str = "",
    frame_selector: str = "",
    max_controls: int = 200,
    force_refresh: bool = False,
) -> str:
    wanted = _norm(query)
    wanted_role = _norm(role)
    actual, snapshot, nodes, context = _capture(
        registry,
        surface=surface,
        title=title,
        query="",
        max_controls=max_controls,
        frame_selector=frame_selector,
        force_refresh=force_refresh,
    )
    candidates = [
        node for node in nodes
        if node.get("visible") is not False and node.get("enabled") is not False
        and (not wanted_role or _norm(node.get("role")) == wanted_role)
        and (selected is None or node.get("selected") is selected)
        and (focused is None or node.get("focused") is focused)
    ]

    match_kind = "state"
    if wanted:
        exact = [
            node for node in candidates
            if wanted in {_norm(node.get("name")), _norm(node.get("automation_id"))}
        ]
        if exact:
            candidates = exact
            match_kind = "exact"
        else:
            contains = [
                node for node in candidates
                if wanted in _norm(node.get("name")) or wanted in _norm(node.get("automation_id"))
            ]
            candidates = contains
            match_kind = "unique_contains"

    candidates.sort(key=_reading_order)
    if ordinal is not None:
        if ordinal == "last":
            index = len(candidates) - 1
        else:
            if type(ordinal) is not int or ordinal < 1 or ordinal > 700:
                raise ValueError("interaction_resolve ordinal must be 1-700 or 'last'")
            index = ordinal - 1
        if index < 0 or index >= len(candidates):
            raise RuntimeError(f"Ordinal target exceeds {len(candidates)} semantic candidates")
        selected_node = candidates[index]
        match_kind = "ordinal_" + match_kind
    else:
        if len(candidates) != 1:
            summary = [
                {"id": node["id"], "role": node.get("role"), "name": node.get("name"), "rect": node.get("rect")}
                for node in candidates[:12]
            ]
            if not candidates:
                raise RuntimeError(
                    "No semantic target matched. Use a fresh interaction_scene and, only if the control is "
                    "unlabeled/canvas-only, fall back to screen_observe."
                )
            raise RuntimeError(
                f"Semantic target is ambiguous across {len(candidates)} candidates: "
                + json.dumps(summary, ensure_ascii=False)
            )
        selected_node = candidates[0]

    target = dict(selected_node.get("target") or {})
    if actual == "browser" and match_kind == "exact" and selected_node.get("name") and selected_node.get("role"):
        # Exact role/name survives unrelated DOM changes better than a snapshot node.
        target = {
            "surface": "browser",
            "browser_target": {
                "role": selected_node["role"],
                "name": selected_node["name"],
            },
        }
    elif actual == "desktop":
        exact_token = _clean(selected_node.get("name")) or _clean(selected_node.get("automation_id"))
        selector: dict[str, Any] = {}
        if ordinal is not None:
            selector["ordinal"] = ordinal
        if selected is not None:
            selector["selected"] = selected
        if focused is not None:
            selector["focused"] = focused
        if selector:
            target = {
                "surface": "desktop",
                "control_type": role or selected_node.get("role", ""),
                "selector": selector,
            }
            if query:
                target["target"] = query
        elif exact_token:
            target = {
                "surface": "desktop",
                "target": exact_token,
                "control_type": selected_node.get("role", ""),
            }
        else:
            target = {}

    confidence = 1.0 if match_kind == "exact" else 0.97 if match_kind.startswith("ordinal_exact") else 0.93
    return "VERIFIED: " + json.dumps({
        "surface": actual,
        "context": context,
        "source_version": snapshot.get("version", snapshot.get("generation")),
        "match": match_kind,
        "confidence": confidence,
        "node": selected_node,
        "action_target": target,
        "fallback": "screen_observe" if confidence < 0.95 else "",
        "note": "Read-only resolution. The eventual action must revalidate or re-resolve before input.",
    }, ensure_ascii=False)


def register_interaction_scene_tools(registry: ToolRegistry) -> None:
    surface = {"enum": ["auto", "browser", "desktop"]}
    ordinal = {"oneOf": [
        {"type": "integer", "minimum": 1, "maximum": 700},
        {"const": "last"},
    ]}
    registry.register(ToolSpec(
        "interaction_scene",
        "Read a unified semantic scene for the current browser or Windows application. "
        "The first read returns the full detached scene; later auto/delta reads return only added, changed, "
        "and removed nodes when possible. Use this instead of repeated screenshots for labeled interfaces.",
        Risk.LOW,
        {"type": "object", "properties": {
            "surface": surface,
            "title": {"type": "string", "maxLength": 500},
            "query": {"type": "string", "maxLength": 500},
            "max_controls": {"type": "integer", "minimum": 1, "maximum": 250},
            "frame_selector": {"type": "string", "maxLength": 500},
            "force_refresh": {"type": "boolean"},
            "mode": {"enum": ["auto", "full", "delta"]},
        }, "additionalProperties": False},
        lambda **kwargs: interaction_scene(registry=registry, **kwargs),
    ))
    registry.register(ToolSpec(
        "interaction_resolve",
        "Resolve one semantic target across browser DOM or Windows UIA using exact name/role/state/ordinal evidence. "
        "Returns a live-action target hint but performs no input. Exact matches are preferred; ambiguous matches fail closed.",
        Risk.LOW,
        {"type": "object", "properties": {
            "query": {"type": "string", "maxLength": 500},
            "role": {"type": "string", "maxLength": 80},
            "ordinal": ordinal,
            "selected": {"type": "boolean"},
            "focused": {"type": "boolean"},
            "surface": surface,
            "title": {"type": "string", "maxLength": 500},
            "frame_selector": {"type": "string", "maxLength": 500},
            "max_controls": {"type": "integer", "minimum": 1, "maximum": 250},
            "force_refresh": {"type": "boolean"},
        }, "minProperties": 1, "additionalProperties": False},
        lambda **kwargs: interaction_resolve(registry=registry, **kwargs),
    ))
