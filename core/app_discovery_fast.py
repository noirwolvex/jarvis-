from __future__ import annotations

from typing import Any, Callable

_INSTALLED = False
_ORIGINAL: Callable[..., list[dict[str, Any]]] | None = None


def _confident(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    best = float(rows[0].get("score", 0.0) or 0.0)
    if best < 108.0:
        return False
    if len(rows) == 1:
        return True
    second = float(rows[1].get("score", 0.0) or 0.0)
    best_name = str(rows[0].get("name") or "").casefold().strip()
    second_name = str(rows[1].get("name") or "").casefold().strip()
    return best_name == second_name or best - second >= 4.0


def _fast_discover(query: str, *, stop_when_exact: bool = False) -> list[dict[str, Any]]:
    if _ORIGINAL is None:
        raise RuntimeError("Fast application discovery was not initialized")

    # The core resolver now owns the exact-match fast path, including cancellation
    # and ambiguity checks. Keep this installed compatibility layer mode-aware so
    # full listings remain exhaustive and explicit paths can return immediately.
    if stop_when_exact:
        return _ORIGINAL(query, stop_when_exact=True)
    return _ORIGINAL(query)


def enable_fast_app_discovery() -> None:
    """Install the compatibility wrapper for the core's mode-aware fast resolver."""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    from . import app_tools

    original = getattr(app_tools, "_discover_candidates", None)
    if not callable(original):
        raise RuntimeError("Application discovery implementation is unavailable")
    _ORIGINAL = original
    app_tools._discover_candidates = _fast_discover  # type: ignore[attr-defined]
    _INSTALLED = True
