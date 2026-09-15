from __future__ import annotations

from typing import Any, Callable

_INSTALLED = False
_ORIGINAL: Callable[[str], list[dict[str, Any]]] | None = None


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


def _fast_discover(query: str) -> list[dict[str, Any]]:
    if _ORIGINAL is None:
        raise RuntimeError("Fast application discovery was not initialized")

    from . import app_tools

    cleaned = app_tools._clean_query(query)
    candidates: list[dict[str, str]] = []
    candidates.extend(app_tools._explicit_path_candidates(cleaned))

    # These sources are bounded and normally resolve common apps such as Discord,
    # WhatsApp, VS Code and Chrome without scanning large install trees.
    for source in (
        app_tools._start_apps,
        app_tools._registry_app_path_candidates,
        app_tools._path_candidates,
    ):
        try:
            candidates.extend(source(cleaned))
        except Exception:
            continue

    ranked = app_tools._rank_candidates(cleaned, candidates)
    if _confident(ranked):
        return ranked

    # Ambiguous/unusual applications keep the exhaustive resolver for accuracy.
    return _ORIGINAL(query)


def enable_fast_app_discovery() -> None:
    """Install an idempotent exact-match fast path before the normal exhaustive resolver."""
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
