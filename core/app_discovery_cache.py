from __future__ import annotations

import copy
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

_CACHE: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_MAX_ENTRIES = 64
_INSTALLED = False
_ORIGINAL: Callable[..., list[dict[str, Any]]] | None = None
_LOCK = threading.RLock()
_GENERATION = 0
_CACHE_CONTEXT: tuple[str, ...] | None = None


def _ttl_seconds() -> float:
    raw = os.getenv("JARVIS_APP_DISCOVERY_CACHE_SECONDS", "90").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 90.0
    return max(0.0, min(value, 300.0))


def _cacheable(query: str) -> bool:
    value = str(query or "").strip()
    if not value:
        return False
    # Explicit filesystem paths should always reflect the current filesystem state.
    value = os.path.expandvars(os.path.expanduser(value.strip('"')))
    if re.search(r"[\\/]", value) or re.match(r"^[A-Za-z]:", value):
        return False
    return True


def _discovery_context() -> tuple[str, ...]:
    return (
        os.getcwd(),
        *(os.environ.get(name, "") for name in (
            "PATH", "PATHEXT", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
            "ProgramFiles", "ProgramFiles(x86)", "NoDefaultCurrentDirectoryInExePath",
        )),
    )


def clear_app_discovery_cache() -> None:
    global _GENERATION, _CACHE_CONTEXT
    with _LOCK:
        _GENERATION += 1
        _CACHE.clear()
        _CACHE_CONTEXT = None


def _cached_discover(query: str, *, stop_when_exact: bool = False) -> list[dict[str, Any]]:
    global _GENERATION, _CACHE_CONTEXT
    with _LOCK:
        original = _ORIGINAL
    if original is None:
        raise RuntimeError("Application discovery cache was not initialized")

    # Resolution already caches exact-mode results and invalidates failed launches.
    # A second cache here would resurrect invalidated results or extend their TTL.
    if stop_when_exact:
        return original(query, stop_when_exact=True)

    ttl = _ttl_seconds()
    key = str(query or "").strip().casefold()
    if ttl > 0 and _cacheable(query):
        now = time.monotonic()
        context = _discovery_context()
        with _LOCK:
            if context != _CACHE_CONTEXT:
                _GENERATION += 1
                _CACHE.clear()
                _CACHE_CONTEXT = context
            generation = _GENERATION
            hit = _CACHE.get(key)
            if hit is not None:
                created, rows = hit
                if now - created <= ttl:
                    _CACHE.move_to_end(key)
                    return copy.deepcopy(rows)
                _CACHE.pop(key, None)

        # Slow filesystem/registry discovery must not serialize other queries.
        rows = original(query)
        with _LOCK:
            if generation == _GENERATION:
                _CACHE[key] = (now, copy.deepcopy(rows))
                _CACHE.move_to_end(key)
                while len(_CACHE) > _MAX_ENTRIES:
                    _CACHE.popitem(last=False)
        return rows

    return original(query)


def enable_app_discovery_cache() -> None:
    """Install an idempotent in-process TTL cache around the expensive Windows app resolver."""
    global _INSTALLED, _ORIGINAL
    from . import app_tools

    with _LOCK:
        if _INSTALLED:
            return
        original = getattr(app_tools, "_discover_candidates", None)
        if not callable(original):
            raise RuntimeError("Application discovery implementation is unavailable")
        _ORIGINAL = original
        app_tools._discover_candidates = _cached_discover  # type: ignore[attr-defined]
        _INSTALLED = True
