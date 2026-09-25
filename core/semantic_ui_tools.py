from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import re
import time
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .desktop_input import InputDeliveryError, InputNotDispatchedError, paste_text
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec
from .process_control import check_cancelled
from .ui_state import SnapshotCache, cancellable_delay, wait_until
from .semantic_target import SELECTOR_SCHEMA, select as select_target
from .execution_telemetry import record_backend

_SNAPSHOTS = SnapshotCache()


class BrowserBoundaryError(RuntimeError):
    pass

_ACTIONABLE_TYPES = {
    "Button",
    "Edit",
    "Document",
    "Hyperlink",
    "ListItem",
    "MenuItem",
    "TabItem",
    "TreeItem",
    "CheckBox",
    "RadioButton",
    "ComboBox",
}
_EDIT_TYPES = {"Edit", "Document"}


def _windows_only() -> None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise RuntimeError("Semantic UI automation is supported on Windows only")


def _foreground_hwnd() -> int:
    _windows_only()
    get_foreground = ctypes.windll.user32.GetForegroundWindow
    get_foreground.argtypes = []
    get_foreground.restype = ctypes.wintypes.HWND
    hwnd = int(get_foreground() or 0)
    if not hwnd:
        raise RuntimeError("No foreground Windows window is available")
    return hwnd


def _window(title: str = ""):
    _windows_only()
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    cleaned = str(title or "").strip()
    if cleaned:
        candidates = desktop.windows(title_re=f".*{re.escape(cleaned)}.*", visible_only=True)
        exact = [win for win in candidates if _text(win.window_text()).casefold() == _text(cleaned).casefold()]
        candidates = exact or candidates
        if len(candidates) != 1:
            raise RuntimeError(f"Window title {cleaned!r} matched {len(candidates)} visible windows; specify an unambiguous title")
        return candidates[0]
    return desktop.window(handle=_foreground_hwnd())


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _control_name(control: Any) -> str:
    try:
        return _text(control.element_info.name)
    except Exception:
        try:
            return _text(control.window_text())
        except Exception:
            return ""


def _control_type(control: Any) -> str:
    try:
        return _text(control.element_info.control_type)
    except Exception:
        return ""


def _automation_id(control: Any) -> str:
    try:
        return _text(control.element_info.automation_id)
    except Exception:
        return ""


def _rect(control: Any) -> list[int]:
    try:
        r = control.rectangle()
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return [0, 0, 0, 0]


def _process_id(control: Any) -> int | None:
    try:
        return int(control.element_info.process_id)
    except Exception:
        return None


def _meta(control: Any, index: int | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": _control_name(control)[:512],
        "type": _control_type(control)[:80],
        "automation_id": _automation_id(control)[:256],
        "rect": _rect(control),
    }
    if index is not None:
        row["index"] = int(index)
    try:
        row["enabled"] = bool(control.is_enabled())
    except Exception:
        row["enabled"] = None
    try:
        row["visible"] = bool(control.is_visible())
    except Exception:
        row["visible"] = None
    try:
        row["selected"] = bool(control.is_selected())
    except Exception:
        pass
    try:
        row["focused"] = bool(control.has_keyboard_focus())
    except Exception:
        row["focused"] = None
    try:
        row["runtime_id"] = list(control.element_info.runtime_id)
    except Exception:
        row["runtime_id"] = None
    row["process_id"] = _process_id(control)
    return row


def _node_identity(control: Any) -> tuple:
    try:
        runtime_id = tuple(control.element_info.runtime_id)
        if runtime_id:
            return ("uia", *runtime_id)
    except Exception:
        pass
    return ("object", id(control))


def _add_hierarchy(win: Any, controls: list[tuple[Any, dict]]) -> list[dict]:
    """Attach bounded ancestor metadata, reusing common container reads within a snapshot."""
    root = _node_identity(win)
    refs: dict[tuple, str] = {root: "window"}
    parents: dict[tuple, Any | None] = {}
    containers: list[dict] = []
    for control, row in controls:
        check_cancelled()
        node = control
        chain: list[str] = []
        seen = {_node_identity(control)}
        for _ in range(4):
            identity = _node_identity(node)
            if identity not in parents:
                try:
                    parents[identity] = node.parent()
                except Exception:
                    parents[identity] = None
            parent = parents[identity]
            if parent is None:
                break
            key = _node_identity(parent)
            if key in seen:
                break
            seen.add(key)
            if key not in refs:
                if len(containers) >= 80:
                    break
                refs[key] = f"container-{len(containers)}"
                containers.append({"ref": refs[key], **_meta(parent)})
            chain.append(refs[key])
            if key == root:
                break
            node = parent
        row["ancestors"] = chain
        row["parent_ref"] = chain[0] if chain else None
        row["depth"] = len(chain) if chain and chain[-1] == "window" else None
    return containers


def _invalidate_control(control: Any, hwnd: int | None = None) -> None:
    if hwnd is None:
        try:
            hwnd = int(control.top_level_parent().handle)
        except Exception:
            pass
    _SNAPSHOTS.invalidate(hwnd)


def _query_descendants(win: Any, control_types: tuple[str, ...], *, visible_only: bool = False):
    """One provider traversal for a union of control types, without caching input state."""
    backend = getattr(win, "backend", None)
    if control_types and getattr(backend, "name", None) == "uia":
        from pywinauto.uia_defines import IUIA
        automation = IUIA()
        conditions = [automation.build_condition(control_type=kind) for kind in control_types]
        condition = conditions[0] if len(conditions) == 1 else automation.iuia.CreateOrConditionFromArray(conditions)
        if visible_only:
            condition = automation.iuia.CreateAndCondition(condition, automation.iuia.CreatePropertyCondition(
                automation.UIA_dll.UIA_IsOffscreenPropertyId, False))
        # WhatsApp's provider can return thousands of aliases for a few dozen
        # controls. Fetch identity alongside the query and dedupe BEFORE constructing
        # wrappers (which otherwise perform several live COM reads per alias).
        identity_property = automation.UIA_dll.UIA_RuntimeIdPropertyId
        process_property = automation.UIA_dll.UIA_ProcessIdPropertyId
        cache = automation.iuia.CreateCacheRequest()
        cache.AddProperty(identity_property)
        cache.AddProperty(process_property)
        elements = win.element_info._element.FindAllBuildCache(automation.tree_scope["descendants"], condition, cache)
        seen: set[tuple] = set()
        for index in range(elements.Length):
            check_cancelled()
            element = elements.GetElement(index)
            runtime_id = tuple(element.GetCachedPropertyValue(identity_property) or ())
            pid = element.GetCachedPropertyValue(process_property)
            if runtime_id and isinstance(pid, int):
                identity = (pid, runtime_id)
                if identity in seen:
                    continue
                seen.add(identity)
            # Reuse this provider-cached identity only for read-time dedupe/counting.
            # Geometry, text, focus, binding and every dispatch guard still read live
            # properties from the returned provider immediately before mutation.
            wrapper = backend.generic_wrapper_class(backend.element_info_class(element))
            if runtime_id and isinstance(pid, int):
                try:
                    setattr(wrapper, "_jarvis_provider_identity", (pid, ("uia", *runtime_id)))
                except Exception:
                    pass
            yield wrapper
        return
    for kind in control_types or (None,):
        check_cancelled()
        yield from win.descendants(control_type=kind) if kind else win.descendants()


def _descendants(win: Any, *, require_complete: bool = False,
                 control_types: tuple[str, ...] = (), visible_only: bool = False) -> list[Any]:
    # WebView can invalidate a provider while a click opens a conversation. Retry
    # this read once using a new provider query; never replay the delivered action
    # or accept a partial result from the failed enumeration.
    for attempt in range(2):
        try:
            return _read_descendants(win, require_complete=require_complete,
                                     control_types=control_types, visible_only=visible_only)
        except Exception as exc:
            hresult = getattr(exc, "hresult", None)
            if attempt == 0 and type(exc).__name__ == "COMError" and hresult == -2147220991:
                record_backend("windows_uia", phase="observe", detail="Retry transient UIA provider observation")
                cancellable_delay(0.04)
                continue
            raise RuntimeError(f"UI Automation inspection failed: {type(exc).__name__}: {exc}") from exc
    raise AssertionError("UIA read retry must return or raise")


def _read_descendants(win: Any, *, require_complete: bool = False,
                      control_types: tuple[str, ...] = (), visible_only: bool = False) -> list[Any]:
    check_cancelled()
    result: list[Any] = []
    seen: set[tuple] = set()
    # Apply type conditions inside UIA. Materializing every chat/message/button
    # and reading its properties across processes dominates editor resolution.
    # Keep Document as well as Edit: WebView contenteditable uses either type.
    for control in _query_descendants(win, control_types, visible_only=visible_only):
        check_cancelled()
        # Count native identities, not duplicate WebView wrapper instances. UIA
        # queries already cached RuntimeId + PID in the same provider traversal, so
        # avoid two extra cross-process reads here when that exact snapshot is available.
        identity = getattr(control, "_jarvis_provider_identity", None)
        if identity is None:
            identity = (_process_id(control), _node_identity(control))
        if identity in seen:
            continue
        seen.add(identity)
        if len(result) == 700:
            if require_complete:
                raise RuntimeError("UI tree exceeds 700 controls; narrow the window before ordinal/relational selection")
            break
        result.append(control)
    check_cancelled()
    return result


def _score(control: Any, target: str, control_type: str = "") -> float:
    name = _control_name(control).casefold()
    automation_id = _automation_id(control).casefold()
    ctype = _control_type(control).casefold()
    wanted = _text(target).casefold()
    wanted_type = _text(control_type).casefold()

    if wanted_type and wanted_type != ctype:
        return -1.0
    if not wanted:
        return 1.0
    if name == wanted or automation_id == wanted:
        return 100.0
    if name.startswith(wanted) or automation_id.startswith(wanted):
        return 92.0
    if wanted in name or wanted in automation_id:
        return 84.0

    tokens = [token for token in re.split(r"[^\w]+", wanted) if len(token) >= 2]
    if tokens:
        haystack = f"{name} {automation_id}"
        matched = sum(1 for token in tokens if token in haystack)
        if matched:
            return 55.0 + 25.0 * (matched / len(tokens))
    return -1.0


def _candidate_controls(win: Any, target: str = "", control_type: str = "", *,
                        editable: bool = False) -> list[tuple[float, int, Any]]:
    rows: list[tuple[float, int, Any]] = []
    # Use canonical names for provider conditions while preserving case-insensitive
    # public selectors. Unknown types retain the normal matching behavior.
    known_type = next((kind for kind in _ACTIONABLE_TYPES if kind.casefold() == control_type.casefold()), None)
    kinds = (known_type,) if known_type else (("Edit", "Document") if editable else ())
    for index, control in enumerate(_descendants(win, require_complete=True, control_types=kinds, visible_only=True)):
        check_cancelled()
        try:
            if not control.is_visible() or not control.is_enabled():
                continue
        except Exception:
            continue
        score = _score(control, target, control_type)
        if score < 0:
            continue
        rows.append((score, index, control))
    rows.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return rows


def _selector_controls(win: Any) -> list[tuple[Any, dict]]:
    """Read one bounded live tree; selectors never consume the detached inspection cache."""
    controls = [win, *_descendants(win, require_complete=True)]
    rows = []
    root = _node_identity(win)
    parents: dict[tuple, Any | None] = {root: None}
    for control in controls:
        check_cancelled()
        row = _meta(control)
        row["identity"] = _node_identity(control)
        row["ancestors"] = []
        node, seen = control, {row["identity"]}
        for _ in range(12):
            key = _node_identity(node)
            if key not in parents:
                try:
                    parents[key] = node.parent()
                except Exception:
                    parents[key] = None
            node = parents[key]
            if node is None:
                break
            identity = _node_identity(node)
            if identity in seen:
                break
            seen.add(identity)
            row["ancestors"].append(identity)
            if identity == root:
                break
        row["parent_identity"] = row["ancestors"][0] if row["ancestors"] else None
        rows.append((control, row))
    return rows


def _editable_control(control: Any) -> bool:
    kind = _control_type(control)
    if kind not in _EDIT_TYPES:
        return False
    # A Chromium document root exposes page text, not an editor/composer. It can
    # otherwise win omitted-target resolution when the actual editor is absent.
    if kind == "Document" and _automation_id(control).casefold() == "rootwebarea":
        return False
    try:
        readonly = control.iface_value.CurrentIsReadOnly
    except Exception as exc:
        if isinstance(exc, AttributeError) or type(exc).__name__ == "NoPatternInterfaceError":
            return True  # Editors without ValuePattern can accept guarded input.
        raise RuntimeError("Editor capability inspection failed; refresh its semantic target") from exc
    return not (isinstance(readonly, (bool, int)) and bool(readonly))


def _message_composer(control: Any) -> bool:
    name = _control_name(control)
    # "Search or start a new chat" / "Search messages" describes a search
    # field, not a composer. A chat keyword alone must not make it a recipient.
    return (not re.match(r"^(search|find|filter)\b", name, re.I)
            and bool(re.search(r"\b(message|chat|reply)\b", name, re.I)))


def _find_control(win: Any, target: str = "", control_type: str = "", editable: bool = False,
                  selector: dict[str, Any] | None = None):
    record_backend("windows_uia", phase="resolve", detail="Fresh semantic target resolution")
    if selector is not None:
        rows = _selector_controls(win)
        if editable:
            for control, row in rows:
                row["editable"] = _editable_control(control)
        selected = select_target([row for _, row in rows], _text(target), _text(control_type), selector, editable=editable)
        return next(control for control, row in rows if row is selected)
    candidates = _candidate_controls(win, target, control_type, editable=editable)
    if editable:
        candidates = [row for row in candidates if _editable_control(row[2])]
        if not target and len(candidates) > 1:
            composers = [row for row in candidates if _message_composer(row[2])]
            if len(composers) == 1:
                candidates = composers
    if not candidates:
        qualifier = f" type={control_type!r}" if control_type else ""
        raise RuntimeError(f"No visible enabled UI control matches target={target!r}{qualifier}")

    if target:
        candidates = [row for row in candidates if row[0] == 100.0]
    if len(candidates) != 1:
        choices = [{"name": _control_name(control)[:160], "type": _control_type(control),
                    "automation_id": _automation_id(control)[:160]} for _, _, control in candidates[:6]]
        raise RuntimeError(f"Target {target!r} has {len(candidates)} exact visible enabled matches; inspect and specify an unambiguous name/automation ID and type. Candidates: {json.dumps(choices, ensure_ascii=False)}")
    return candidates[0][2]


def _resolve_input_control(win: Any, target: str = "", control_type: str = "", editable: bool = False,
                           selector: dict[str, Any] | None = None):
    """Resolve before input; a missing target is not a delivered click or text write."""
    try:
        return _find_control(win, target, control_type, editable=editable, selector=selector)
    except BrowserBoundaryError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise InputNotDispatchedError(str(exc)) from exc


def _target_binding(win: Any, control: Any, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = _meta(control) if metadata is None else metadata
    runtime_id = metadata["runtime_id"]
    # Window labels, rectangles and selection/focus patterns are not part of this
    # binding. Avoid fetching that whole UIA record merely to obtain its PID.
    return {"window_hwnd": int(win.handle), "window_identity": _node_identity(win),
            "window_process_id": _process_id(win),
            "identity": ("uia", *runtime_id) if runtime_id else ("object", id(control)),
            "control": metadata, "generation": _SNAPSHOTS.generation}


def _validate_target(win: Any, control: Any, binding: dict[str, Any],
                     state_guard: Callable[[], None] | None = None) -> None:
    """Validate recycled providers, moved controls and changed foreground before delivery."""
    _guard_foreground(binding["window_hwnd"], state_guard)
    current = _target_binding(win, control)
    fields = ("name", "type", "automation_id", "runtime_id", "process_id", "rect")
    if (any(current[key] != binding[key] for key in
            ("window_hwnd", "window_identity", "window_process_id", "identity", "generation"))
            or any(current["control"][key] != binding["control"][key] for key in fields)
            or current["control"]["visible"] is not True or current["control"]["enabled"] is not True):
        raise InputDeliveryError("Resolved semantic target changed before input; inspect and resolve again")
    try:
        owner = int(control.top_level_parent().handle)
    except Exception as exc:
        raise InputDeliveryError("Resolved control window identity is unavailable") from exc
    if owner != binding["window_hwnd"]:
        raise InputDeliveryError("Resolved control belongs to another window; no input delivered")


def ui_resolve(target: str = "", title: str = "", control_type: str = "",
               selector: dict[str, Any] | None = None) -> str:
    """Resolve a detached identity only. Subsequent actions always resolve again."""
    win = _window(title)
    control = _find_control(win, target, control_type, selector=selector)
    binding = _target_binding(win, control)
    return "VERIFIED: " + json.dumps({"action": "ui_resolve", **binding,
        "is_foreground": _foreground_hwnd() == int(win.handle), "observed_at_ms": int(time.time() * 1000),
        "note": "Read evidence only; this identity does not authorize later input."}, ensure_ascii=False)


def _focus_window(win: Any) -> int:
    check_cancelled()
    hwnd = int(win.handle)
    foreground = _foreground_hwnd()
    if foreground == hwnd:
        return hwnd
    _SNAPSHOTS.invalidate(foreground)
    _SNAPSHOTS.invalidate(hwnd)
    try:
        record_backend("windows_uia", detail="Focus target window")
        win.set_focus()
    except Exception:
        try:
            win.restore()
            win.set_focus()
        except Exception as exc:
            raise RuntimeError(f"Could not focus target window: {type(exc).__name__}: {exc}") from exc
    wait_until(lambda: _foreground_hwnd() == hwnd, description="target window foreground")
    return hwnd


def _invoke(control: Any, hwnd: int | None = None) -> str:
    # Resolve support BEFORE delivery. An invocation exception may occur after a side effect.
    for attribute, method, name in (("iface_invoke", "Invoke", "invoke"), ("iface_selection_item", "Select", "select")):
        try:
            pattern = getattr(control, attribute)
        except Exception as exc:
            if isinstance(exc, AttributeError) or type(exc).__name__ == "NoPatternInterfaceError":
                continue
            raise
        check_cancelled()
        _invalidate_control(control, hwnd)
        try:
            record_backend("windows_uia", detail=f"UIA {name}")
            getattr(pattern, method)()
        except Exception as exc:
            raise InputDeliveryError(f"Semantic {name} outcome is uncertain; inspect before retrying") from exc
        return name
    raise RuntimeError("Control has no supported semantic activation pattern; use fresh screen observation and the guarded desktop input tool")


def _guard_foreground(hwnd: int, state_guard: Callable[[], None] | None = None) -> None:
    check_cancelled()
    if _foreground_hwnd() != hwnd:
        raise InputDeliveryError("Target window lost foreground; inspect before further input")
    from .semantic_ui_guard import _browser_block
    blocked = _browser_block()
    if blocked:
        raise BrowserBoundaryError(blocked)
    if state_guard:
        state_guard()
    check_cancelled()


def _has_focus(control: Any) -> bool:
    try:
        return bool(control.has_keyboard_focus())
    except Exception:
        return False


def _focus_control(control: Any, hwnd: int, state_guard: Callable[[], None] | None = None) -> None:
    _guard_foreground(hwnd, state_guard)
    if not _has_focus(control):
        _SNAPSHOTS.invalidate(hwnd)
        record_backend("windows_uia", detail="Focus resolved control")
        control.set_focus()
    def focused():
        _guard_foreground(hwnd, state_guard)
        return _has_focus(control)
    wait_until(focused, description="resolved control keyboard focus")


def ui_inspect(title: str = "", query: str = "", actionable_only: bool = True, max_controls: int = 120,
               force_refresh: bool = False) -> str:
    """Return a compact UIA snapshot optimized for fast agent decisions."""
    win = _window(title)
    hwnd = int(win.handle)
    wanted = _text(query).casefold()
    limit = max(1, min(int(max_controls), 250))
    key = (hwnd, title, wanted, actionable_only, limit)
    cached = None if force_refresh else _SNAPSHOTS.get(key)
    if cached is not None:
        record_backend("windows_uia_cache", phase="observe", detail="Read detached UIA snapshot")
        return json.dumps(cached, ensure_ascii=False)
    generation = _SNAPSHOTS.generation
    record_backend("windows_uia", phase="observe", detail="Read UI Automation tree")
    controls: list[dict[str, Any]] = []
    returned: list[tuple[Any, dict]] = []
    for index, control in enumerate(_descendants(win)[:700]):
        row = _meta(control, index)
        if actionable_only and row.get("type") not in _ACTIONABLE_TYPES:
            continue
        if wanted:
            haystack = f"{row.get('name', '')} {row.get('automation_id', '')} {row.get('type', '')}".casefold()
            if wanted not in haystack:
                continue
        if row.get("visible") is False:
            continue
        controls.append(row)
        returned.append((control, row))
        if len(controls) >= limit:
            break
    try:
        window_title = _text(win.window_text())
    except Exception:
        window_title = _text(title)
    containers = _add_hierarchy(win, returned)
    window_meta = _meta(win)
    for field in ("process_id", "framework_id", "class_name"):
        try:
            value = getattr(win.element_info, field)
            window_meta[field] = value if isinstance(value, int) else str(value)[:160]
        except Exception:
            pass
    try:
        foreground = _foreground_hwnd()
    except RuntimeError:
        foreground = None
    data = {"hwnd": hwnd, "title": window_title, "controls": controls, "cached": False,
            "cache_age_ms": 0, "cache_ttl_ms": 250, "generation": generation,
            "window": window_meta, "foreground_hwnd": foreground, "is_foreground": hwnd == foreground,
            "focused_controls": [row["index"] for row in controls if row.get("focused")],
            "containers": containers, "hierarchy_depth_limit": 4,
            "truncated": len(controls) >= limit,
            "note": "Read snapshot only; actions resolve live controls. Use force_refresh after external UI changes."}
    _SNAPSHOTS.put(key, data, generation)
    return json.dumps(data, ensure_ascii=False)


def ui_focus(target: str = "", title: str = "", control_type: str = "", selector: dict[str, Any] | None = None) -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    if not _text(target) and selector is None:
        return f"VERIFIED: focused window hwnd={hwnd}"
    control = _find_control(win, target, control_type, selector=selector)
    _validate_target(win, control, _target_binding(win, control))
    _focus_control(control, hwnd)
    return "VERIFIED: " + json.dumps({"action": "ui_focus", "window_hwnd": hwnd, "control": _meta(control)}, ensure_ascii=False)


def ui_activate(target: str = "", title: str = "", control_type: str = "", selector: dict[str, Any] | None = None) -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    control = _resolve_input_control(win, target, control_type, selector=selector)
    before = _meta(control)
    binding = _target_binding(win, control, before)
    try:
        _validate_target(win, control, binding)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    method = _activate_control(control, hwnd)
    if _foreground_hwnd() != hwnd:
        # Some controls intentionally open another foreground window; report this instead of
        # pretending the original app remained active.
        foreground = _foreground_hwnd()
        _SNAPSHOTS.invalidate(foreground)
    else:
        foreground = hwnd
    return "DELIVERED: " + json.dumps(
        {"action": "ui_activate", "method": method, "window_hwnd": hwnd, "foreground_hwnd": foreground, "control": before},
        ensure_ascii=False,
    )


def _activate_control(control: Any, hwnd: int) -> str:
    """Activate an exact UIA-resolved control, using Rust for geometry-only controls."""
    try:
        return _invoke(control, hwnd)
    except RuntimeError as exc:
        if "no supported semantic activation pattern" not in str(exc).casefold():
            raise

    identity = _meta(control)
    rect = identity["rect"]
    if len(rect) != 4 or rect[2] <= rect[0] or rect[3] <= rect[1]:
        raise RuntimeError(
            "Exact UI control has no activation pattern and no usable screen rectangle"
        )
    x = (int(rect[0]) + int(rect[2])) // 2
    y = (int(rect[1]) + int(rect[3])) // 2

    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode

    _guard_foreground(hwnd)
    _SNAPSHOTS.invalidate(hwnd)
    generation = _SNAPSHOTS.generation

    def click_guard() -> None:
        _guard_foreground(hwnd)
        if _SNAPSHOTS.generation != generation or _meta(control) != identity:
            raise InputDeliveryError("Semantic click target changed before dispatch; resolve again")
        try:
            owner = int(control.top_level_parent().handle)
        except Exception as exc:
            raise InputDeliveryError("Semantic click window identity is unavailable") from exc
        if owner != hwnd:
            raise InputDeliveryError("Semantic click target belongs to another window; no input delivered")

    client, status = _preflight()
    click_guard()
    if client is not None:
        _guard_native_target(hwnd, status)
    if client is None:
        if native_engine_mode() not in {"auto", "python"}:
            raise RustEngineUnavailable(
                "Strict Rust mode requires the native daemon before UIA-resolved mouse activation"
            )
        try:
            record_backend("python_native", detail="UIA-resolved physical click")
            control.click_input()
        except Exception as click_exc:
            raise InputDeliveryError(
                "UIA-resolved click outcome is uncertain; inspect before retrying"
            ) from click_exc
        return "uia_resolved_click_input"

    try:
        result = client.click(x, y, status, before_dispatch=click_guard)
    except RustEngineUnavailable:
        if native_engine_mode() != "auto":
            raise
        click_guard()
        try:
            record_backend("python_native", detail="UIA-resolved physical click")
            control.click_input()
        except Exception as click_exc:
            raise InputDeliveryError(
                "UIA-resolved click outcome is uncertain; inspect before retrying"
            ) from click_exc
        return "uia_resolved_click_input"

    if result.get("executed") is not True or result.get("simulation") is not False:
        raise InputDeliveryError(
            "Rust daemon did not confirm UIA-resolved native click execution; inspect before retrying"
        )
    return "rust_uia_center_click"


def _control_value(control: Any) -> str | None:
    try:
        value = control.get_value()
        if value is not None:
            return str(value)
    except Exception:
        pass
    try:
        value = control.iface_value.CurrentValue
        if value is not None:
            return str(value)
    except Exception:
        pass
    try:
        # Electron contenteditable surfaces may expose TextPattern but no ValuePattern.
        # The returned text is bounded and comes from the editor, never its label/name.
        value = str(control.iface_text.DocumentRange.GetText(4097))
        return value if len(value) <= 4096 else None
    except Exception:
        pass
    return None


def _guard_native_target(hwnd: int, status: dict) -> None:
    from .rust_engine import RustDaemonClient
    if RustDaemonClient._foreground(status)["hwnd"] != hwnd:
        raise InputDeliveryError("Rust foreground does not match the resolved application; no input delivered")


def _rust_type_or_python(text: str, *, hwnd: int | None = None,
                         guard: Callable[[], None] | None = None) -> str:
    """Deliver focused text through Rust in strict mode; auto mode may retain Python compatibility."""
    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode

    client, status = _preflight()
    if guard:
        guard()
    if client is not None and hwnd is not None:
        _guard_native_target(hwnd, status)
    if client is None:
        if native_engine_mode() in {"auto", "python"}:
            record_backend("python_native", detail="Focused Unicode input")
            paste_text(text)
            return "windows_unicode_input"
        raise RustEngineUnavailable(
            "Strict Rust mode requires the native daemon before semantic keyboard input"
        )
    try:
        result = client.type_text(text, status, before_dispatch=guard)
    except RustEngineUnavailable:
        if native_engine_mode() != "auto":
            raise
        if guard:
            guard()
        record_backend("python_native", detail="Focused Unicode input")
        paste_text(text)
        return "windows_unicode_input"
    if result.get("executed") is not True or result.get("simulation") is not False:
        raise InputDeliveryError(
            "Rust daemon did not confirm semantic keyboard execution; inspect before retrying"
        )
    return "rust_native_input"


def _rust_hotkey_or_python(keys: list[str], *, hwnd: int | None = None,
                           guard: Callable[[], None] | None = None) -> str:
    """Dispatch an atomic focused shortcut through Rust whenever strict native mode is active."""
    from .rust_engine import RustEngineUnavailable, _preflight, native_engine_mode

    client, status = _preflight()
    if guard:
        guard()
    if client is not None and hwnd is not None:
        _guard_native_target(hwnd, status)
    if client is None:
        if native_engine_mode() in {"auto", "python"}:
            record_backend("python_native", detail="Focused keyboard shortcut")
            if len(keys) == 1:
                from .tools import _desktop_press
                _desktop_press(keys[0])
                return "python_key_press"
            from .tools import _desktop_hotkey
            _desktop_hotkey(keys)
            return "python_hotkey"
        raise RustEngineUnavailable(
            "Strict Rust mode requires the native daemon before semantic hotkeys"
        )
    try:
        result = client.hotkey(keys, status, before_dispatch=guard)
    except RustEngineUnavailable:
        if native_engine_mode() != "auto":
            raise
        if guard:
            guard()
        record_backend("python_native", detail="Focused keyboard shortcut")
        if len(keys) == 1:
            from .tools import _desktop_press
            _desktop_press(keys[0])
            return "python_key_press"
        from .tools import _desktop_hotkey
        _desktop_hotkey(keys)
        return "python_hotkey"
    if result.get("executed") is not True or result.get("simulation") is not False:
        raise InputDeliveryError(
            "Rust daemon did not confirm semantic hotkey execution; inspect before retrying"
        )
    return "rust_native_hotkey"


def ui_type(
    text: str,
    target: str = "",
    title: str = "",
    submit: bool = False,
    replace: bool = False,
    *,
    state_guard: Callable[[], None] | None = None,
    selector: dict[str, Any] | None = None,
    _resolved_editor: tuple[Any, Any, dict[str, Any]] | None = None,
) -> str:
    if len(str(text)) > 4096 or "\0" in str(text):
        raise ValueError("Semantic UI text must contain at most 4096 characters and no NUL")
    check_cancelled()
    if state_guard:
        state_guard()
    win = _resolved_editor[0] if _resolved_editor is not None else _window(title)
    try:
        hwnd = _focus_window(win)
        _guard_foreground(hwnd, state_guard)
        control = _resolved_editor[1] if _resolved_editor is not None else _resolve_input_control(win, target, editable=True, selector=selector)
        _focus_control(control, hwnd, state_guard)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    binding = _resolved_editor[2] if _resolved_editor is not None else _target_binding(win, control)
    before_value = _control_value(control)
    if before_value is None:
        raise InputNotDispatchedError("Editor value cannot be read semantically; use fresh observation and guarded desktop input")
    if submit and not replace and before_value:
        raise InputNotDispatchedError("Composer already contains text; inspect before submitting an existing draft")
    expected = str(text) if replace else before_value + str(text)
    wanted = _text(text)
    def echo_count():
        return sum(1 for candidate in _descendants(win) if candidate != control
                   and _control_type(candidate) not in _EDIT_TYPES and _control_name(candidate) == wanted)
    before_echoes = echo_count() if submit else 0
    try:
        _guard_foreground(hwnd, state_guard)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    # Prefer one Value-pattern write. If not supported, only an empty composer can use
    # focused Unicode delivery without relying on an unknown caret/selection position.
    try:
        pattern = control.iface_value
    except Exception as exc:
        if not isinstance(exc, AttributeError) and type(exc).__name__ != "NoPatternInterfaceError":
            raise
        pattern = None
    expected_values = {expected}
    caret_guard = None
    if (before_value in ("\n", "\r\n") and str(text) and not replace and not submit
            and not any(char in str(text) for char in "\r\n")):
        # Some WebView composers advertise SetValue but silently ignore it.
        # Their independently readable empty TextPattern paragraph can instead
        # be typed once through the guarded native path, without trying a write
        # and replaying it after an uncertain result.
        from .native_ui_input import _append_caret
        append = _append_caret(control, before_value)
        if append is not None:
            prefix, suffix, caret_guard = append
            expected_values = {prefix + str(text) + suffix, str(text)}
            pattern = None
    wrote_with = "uia_value_pattern" if pattern is not None else "native_keyboard_input"
    if pattern is None and ((before_value and caret_guard is None) or replace):
        raise InputNotDispatchedError("Editor has no writable Value pattern; use guarded input to control selection explicitly")
    if pattern is None and any(char in str(text) for char in "\r\n"):
        raise InputNotDispatchedError("Multiline composer text requires a writable Value pattern; Unicode newlines could submit messages prematurely")
    try:
        _validate_target(win, control, binding, state_guard)
    except InputDeliveryError as exc:
        raise InputNotDispatchedError(str(exc)) from exc
    if _control_value(control) != before_value:
        raise InputNotDispatchedError("Editor changed before input; inspect the current draft before retrying")
    _SNAPSHOTS.invalidate(hwnd)
    # Preserve all target evidence while accounting for this action's own cache
    # invalidation; concurrent invalidations must still fail the final guard.
    binding["generation"] += 1
    try:
        if pattern is not None:
            record_backend("windows_uia", detail="Write editor Value pattern")
            pattern.SetValue(expected)
        else:
            def input_guard():
                try:
                    _validate_target(win, control, binding, state_guard)
                except InputDeliveryError as exc:
                    raise InputNotDispatchedError(str(exc)) from exc
                if not _has_focus(control) or _control_value(control) != before_value:
                    raise InputNotDispatchedError("Editor focus or draft changed during input preparation")
                if caret_guard is not None:
                    caret_guard()
            wrote_with = _rust_type_or_python(str(text), hwnd=hwnd, guard=input_guard)
    except (BrowserBoundaryError, InputNotDispatchedError):
        raise
    except Exception as exc:
        raise InputDeliveryError("Text delivery is uncertain; inspect the editor before retrying") from exc

    def value_matches():
        _guard_foreground(hwnd, state_guard)
        record_backend("windows_uia", phase="verify", detail="Exact editor value readback")
        return _control_value(control) in expected_values
    try:
        wait_until(value_matches, description="exact editor value readback")
    except TimeoutError as exc:
        raise InputDeliveryError("Editor value did not match exactly; inspect before retrying") from exc
    if not submit:
        return "VERIFIED: " + json.dumps(
            {"action": "ui_type", "window_hwnd": hwnd, "control": _meta(control), "characters": len(str(text)), "method": wrote_with, "submitted": False},
            ensure_ascii=False,
        )

    _guard_foreground(hwnd, state_guard)
    if not _has_focus(control) or _control_value(control) != expected:
        raise InputDeliveryError("Editor lost keyboard focus before submit; text was not submitted")
    try:
        def submit_guard():
            _validate_target(win, control, binding, state_guard)
            if not _has_focus(control) or _control_value(control) != expected:
                raise InputDeliveryError("Editor focus or draft changed during submit preparation")
        submit_method = _rust_hotkey_or_python(["enter"], hwnd=hwnd, guard=submit_guard)
    except BrowserBoundaryError:
        raise
    except Exception as exc:
        raise InputDeliveryError("Text was entered but Enter delivery failed; do not retry blindly") from exc
    state = {"cleared": False, "echoed": False}
    def submitted():
        _guard_foreground(hwnd, state_guard)
        state["cleared"] = bool(expected) and _control_value(control) == ""
        state["echoed"] = bool(wanted) and echo_count() > before_echoes
        return state["cleared"] or state["echoed"]
    try:
        wait_until(submitted, timeout=2.0, description="new post-submit evidence")
    except Exception as exc:
        raise InputDeliveryError("Text may have been submitted; inspect the conversation and do not resend automatically") from exc
    return "VERIFIED: " + json.dumps(
        {
            "action": "ui_type",
            "window_hwnd": hwnd,
            "control": _meta(control),
            "characters": len(str(text)),
            "method": wrote_with,
            "submit_method": submit_method,
            "submitted": True,
            "composer_cleared": state["cleared"],
            "new_message_visible": state["echoed"],
        },
        ensure_ascii=False,
    )


def _hotkey_tokens(keys: list[str]) -> list[str]:
    allowed = {
        "ctrl", "control", "alt", "shift", "win", "windows", "enter", "tab", "esc", "escape",
        "space", "backspace", "delete", "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    }
    result: list[str] = []
    for key in keys:
        value = str(key or "").strip().casefold()
        if len(value) == 1 and value.isprintable():
            result.append(value)
            continue
        if value not in allowed and not re.fullmatch(r"f(?:[1-9]|1[0-2])", value):
            raise ValueError(f"Unsupported semantic hotkey key: {key}")
        result.append(value)
    if not result:
        raise ValueError("At least one hotkey key is required")
    return result


def ui_hotkey(keys: list[str], title: str = "") -> str:
    win = _window(title)
    hwnd = _focus_window(win)
    normalized = _hotkey_tokens(keys)
    aliases = {"control": "ctrl", "windows": "win", "escape": "esc"}
    _guard_foreground(hwnd)
    _SNAPSHOTS.invalidate(hwnd)
    method = _rust_hotkey_or_python([aliases.get(value, value) for value in normalized],
                                   hwnd=hwnd, guard=lambda: _guard_foreground(hwnd))
    return "DELIVERED: " + json.dumps(
        {"action": "ui_hotkey", "window_hwnd": hwnd, "keys": normalized, "method": method},
        ensure_ascii=False,
    )


def ui_wait_state(target: str = "", title: str = "", control_type: str = "", state: str = "visible",
                  timeout_ms: int = 1500, selector: dict[str, Any] | None = None) -> str:
    """Read a fresh live target until a condition holds; never focus a window/control."""
    if (not _text(target) and selector is None) or state not in {"visible", "focused"}:
        raise ValueError("Provide an exact target or selector and state visible or focused")
    def probe():
        try:
            win = _window(title)
            control = _find_control(win, target, control_type, selector=selector)
        except RuntimeError as exc:
            if "Emergency stop" in str(exc):
                raise
            return False
        if state == "focused" and (int(win.handle) != _foreground_hwnd() or not _has_focus(control)):
            return False
        return {"action": "ui_wait_state", "state": state, "window_hwnd": int(win.handle), "control": _meta(control)}
    result = wait_until(probe, timeout=max(0, min(timeout_ms, 15000)) / 1000, description=f"{target!r} {state}")
    _SNAPSHOTS.invalidate(result["window_hwnd"])
    return "VERIFIED: " + json.dumps(result, ensure_ascii=False)


_TARGET_FIELDS = {"target": {"type": "string", "maxLength": 500}, "title": {"type": "string", "maxLength": 500},
                  "control_type": {"type": "string", "maxLength": 80}, "selector": SELECTOR_SCHEMA}

_TARGET_REQUIRED = [{"required": ["target"]}, {"required": ["selector"]}]


def _action_schema(op: str, fields: dict[str, Any], required: list[str]) -> dict[str, Any]:
    schema = {"type": "object", "properties": {"op": {"const": op}, **fields},
              "required": ["op", *[key for key in required if key != "target"]], "additionalProperties": False}
    if "target" in required:
        schema["anyOf"] = _TARGET_REQUIRED
    return schema


_NAMED_FIELDS = {**_TARGET_FIELDS, "target": {"type": "string", "maxLength": 500, "pattern": r"\S"}}
_KEYS = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}
_BATCH_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string", "maxLength": 500},
    "actions": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"oneOf": [
        _action_schema("activate", _NAMED_FIELDS, ["target"]),
        _action_schema("focus", _TARGET_FIELDS, []),
        _action_schema("type", {"target": _NAMED_FIELDS["target"], "title": _TARGET_FIELDS["title"],
                               "selector": SELECTOR_SCHEMA,
                               "text": {"type": "string", "maxLength": 4096},
                               "submit": {"type": "boolean"}, "replace": {"type": "boolean"}}, ["target", "text"]),
        _action_schema("hotkey", {"title": _TARGET_FIELDS["title"], "keys": _KEYS}, ["keys"]),
        _action_schema("wait", {"seconds": {"type": "number", "minimum": 0, "maximum": 5}}, ["seconds"]),
        _action_schema("assert_visible", {**_NAMED_FIELDS, "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 15000}}, ["target"]),
    ]}},
}, "required": ["actions"], "additionalProperties": False}


def ui_batch(actions: list[dict[str, Any]], title: str = "", *,
             authorize: Callable[[str], None] | None = None) -> str:
    """Preflight a whole ordered batch, dispatch each action once, preserve partial outcomes."""
    encoded = json.dumps({"actions": actions, "title": title}, allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536 or "\\u0000" in encoded:
        raise ValueError("Batch arguments exceed 64 KiB or contain NUL")
    Draft202012Validator(_BATCH_SCHEMA).validate({"actions": actions, "title": title})
    for action in actions:
        if action["op"] == "hotkey":
            _hotkey_tokens(action["keys"])
        if authorize:
            authorize(action["op"])
    # A read checkpoint must follow delivery-only navigation; reject malformed sequences
    # before side effects rather than completing an unverifiable batch.
    pending = False
    for action in actions:
        if pending and action["op"] not in {"wait", "assert_visible"}:
            raise ValueError("Batch requires assert_visible after every activation/hotkey before another mutation; a text write cannot stand in for navigation/send verification")
        if action["op"] in {"activate", "hotkey"}:
            pending = True
        elif action["op"] == "assert_visible":
            pending = False
    significant = [action for action in actions if action["op"] != "wait"]
    if pending or not significant or significant[-1]["op"] not in {"assert_visible", "type"}:
        raise ValueError("Batch needs a final assert_visible or verified text outcome; elapsed waits and delivered actions are not completion evidence")

    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        op = action["op"]
        action_title = action.get("title") or title
        common = {"target": action.get("target", ""), "title": action_title,
                  "control_type": action.get("control_type", "")}
        if "selector" in action:
            common["selector"] = action["selector"]
        try:
            check_cancelled()
            if authorize:
                authorize(op)
            if op == "activate":
                raw = ui_activate(**common)
            elif op == "focus":
                raw = ui_focus(**common)
            elif op == "type":
                raw = ui_type(text=action["text"], target=common["target"], title=action_title,
                              submit=action.get("submit", False), replace=action.get("replace", False),
                              **({"selector": action["selector"]} if "selector" in action else {}))
            elif op == "hotkey":
                raw = ui_hotkey(action["keys"], title=action_title)
            elif op == "wait":
                cancellable_delay(action["seconds"])
                raw = "WAITED: elapsed delay is not verification evidence"
            else:
                raw = ui_wait_state(**common, timeout_ms=action.get("timeout_ms", 1500))
            if not raw.startswith(("VERIFIED:", "DELIVERED:", "WAITED:")):
                raise RuntimeError(raw)
            results.append({"index": index, "op": op,
                            "status": "verified" if raw.startswith("VERIFIED:") else "delivered",
                            "result": raw[:2000]})
        except Exception as exc:
            prefix = "BROWSER_ACTION_BLOCKED: " if isinstance(exc, BrowserBoundaryError) else "PERMISSION_DENIED: " if isinstance(exc, PermissionError) else "ERROR: "
            failure = {"action": "ui_batch", "status": "interrupted", "failed_index": index,
                       "completed_count": len(results), "steps": results,
                       "failed": {"index": index, "op": op, "error": str(exc)[:2000],
                                  "outcome": "uncertain" if isinstance(exc, InputDeliveryError) else "failed"},
                       "not_run": list(range(index + 1, len(actions))),
                       "recovery": "Inspect current state and resume only remaining steps; never replay the whole batch."}
            return prefix + json.dumps(failure, ensure_ascii=False)
    return "VERIFIED: " + json.dumps({"action": "ui_batch", "status": "completed", "completed_count": len(results),
                                      "steps": results, "not_run": []}, ensure_ascii=False)


def register_semantic_ui_tools(registry: ToolRegistry) -> None:
    def authorize_batch_op(op: str) -> None:
        name = {"activate": "ui_activate", "focus": "ui_focus", "type": "ui_type",
                "hotkey": "ui_hotkey", "wait": "wait", "assert_visible": "ui_wait_state"}[op]
        spec = registry._tools.get(name)
        allowed, reason = registry.permissions.check(name, spec.risk if spec else Risk.SAFE, approved=True)
        if not allowed:
            raise PermissionError(reason)

    def registered_batch(actions: list[dict[str, Any]], title: str = "") -> str:
        return ui_batch(actions, title, authorize=authorize_batch_op)

    registry.register(ToolSpec(
        "ui_resolve", "Read-only resolution of an exact or ordinal, selected, focused, parent-scoped or adjacent UIA target. Ordinals require control_type and one container. Returns fresh window/control identities; actions resolve again.",
        Risk.LOW, {"type": "object", "properties": _NAMED_FIELDS,
                   "anyOf": _TARGET_REQUIRED, "additionalProperties": False}, ui_resolve,
    ))

    registry.register(ToolSpec(
        "ui_inspect",
        "Compact read-only Windows UI Automation snapshot. Reuses detached metadata for up to 250 ms; force_refresh bypasses cache. Actions always resolve live exact controls. Does not focus the window.",
        Risk.LOW,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "query": {"type": "string"},
                "actionable_only": {"type": "boolean"},
                "max_controls": {"type": "integer", "minimum": 1, "maximum": 250},
                "force_refresh": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        ui_inspect,
    ))
    registry.register(ToolSpec(
        "ui_focus",
        "Focus a named Windows application/control through UI Automation without guessing screen coordinates.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": _TARGET_FIELDS,
            "additionalProperties": False,
        },
        ui_focus,
    ))
    registry.register(ToolSpec(
        "ui_activate",
        "Activate one exact or selector-resolved visible enabled control using UIA Invoke/Select, then guarded native input only when no semantic pattern exists. Reports delivery only; follow with ui_wait_state. Never blindly retry uncertain delivery.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": _NAMED_FIELDS,
            "anyOf": _TARGET_REQUIRED,
            "additionalProperties": False,
        },
        ui_activate,
    ))
    registry.register(ToolSpec(
        "ui_type",
        "Enter text in an exact semantic editor, verifying the entire value before optional Enter. Omitted target requires a unique editor/composer. submit refuses existing drafts and verifies new post-submit evidence; uncertain delivery must never be resent automatically.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "maxLength": 4096},
                "target": {"type": "string"},
                "title": {"type": "string"},
                "submit": {"type": "boolean"},
                "replace": {"type": "boolean"},
                "selector": SELECTOR_SCHEMA,
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        ui_type,
    ))
    registry.register(ToolSpec(
        "ui_hotkey",
        "Focus a named Windows application and deliver a tracked keyboard shortcut. Reports delivery only; verify the intended result with ui_wait_state.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}, "title": {"type": "string"}},
            "required": ["keys"],
            "additionalProperties": False,
        },
        ui_hotkey,
    ))
    registry.register(ToolSpec(
        "ui_wait_state",
        "Read a fresh exact UIA target until it is visible/enabled or keyboard-focused. Cancellable bounded polling without focusing, typing, or clicking; returns verified read evidence for a preceding action.",
        Risk.LOW,
        {"type": "object", "properties": {**_NAMED_FIELDS,
            "state": {"type": "string", "enum": ["visible", "focused"]},
            "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 15000}},
         "anyOf": _TARGET_REQUIRED, "additionalProperties": False},
        ui_wait_state,
    ))
    registry.register(ToolSpec(
        "ui_batch",
        "Preflight and execute 1-20 known semantic actions in order with cancellation, nested permission checks, and live targets. Each delivery-only activate/hotkey requires assert_visible before another mutation; end with assert_visible or verified text. First failure returns partial ledger and remaining indexes; never replay completed or uncertain actions. Supported ops: activate, focus, type, hotkey, wait, assert_visible.",
        Risk.MEDIUM,
        _BATCH_SCHEMA,
        registered_batch,
    ))
