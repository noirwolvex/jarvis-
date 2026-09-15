from __future__ import annotations

import copy
import os
import re
import time
from collections import OrderedDict
from typing import Any, Callable

_CACHE: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_MAX_ENTRIES = 64
_INSTALLED = False
_ORIGINAL: Callable[[str], list[dict[str, Any]]] | None = None


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
    if re.search(r"[\\/]", value) or re.match(r"^[A-Za-z]:", value):
        return False
    return True


def clear_app_discovery_cache() -> None:
    _CACHE.clear()


def _cached_discover(query: str) -> list[dict[str, Any]]:
    if _ORIGINAL is None:
        raise RuntimeError("Application discovery cache was not initialized")

    ttl = _ttl_seconds()
    key = str(query or "").strip().casefold()
    if ttl > 0 and _cacheable(query):
        now = time.monotonic()
        hit = _CACHE.get(key)
        if hit is not None:
            created, rows = hit
            if now - created <= ttl:
                _CACHE.move_to_end(key)
                return copy.deepcopy(rows)
            _CACHE.pop(key, None)

        rows = _ORIGINAL(query)
        _CACHE[key] = (now, copy.deepcopy(rows))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ENTRIES:
            _CACHE.popitem(last=False)
        return rows

    return _ORIGINAL(query)


def enable_app_discovery_cache() -> None:
    """Install an idempotent in-process TTL cache around the expensive Windows app resolver."""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    from . import app_tools

    original = getattr(app_tools, "_discover_candidates", None)
    if not callable(original):
        raise RuntimeError("Application discovery implementation is unavailable")
    _ORIGINAL = original
    app_tools._discover_candidates = _cached_discover  # type: ignore[attr-defined]
    _INSTALLED = True
