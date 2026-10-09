"""Select the user's open Chrome before considering a separate managed browser."""
from __future__ import annotations

from typing import Any


def preferred_existing_chrome() -> dict[str, Any] | None:
    """Return a window for the guarded toolbar adapter, or prepare its CDP tab.

    Endpoint availability alone is insufficient: an unrelated managed Chrome
    can remain connected while the user's personal Chrome is in front.
    """
    from .chrome_existing_window import find_chrome_window
    from .chrome_session_tools import cdp_owns_window
    from . import chrome_cdp
    from .process_control import check_cancelled

    check_cancelled()
    window = find_chrome_window()
    if window is None:
        return None
    if cdp_owns_window(window):
        try:
            endpoint = chrome_cdp._cdp_url()
            if (chrome_cdp._RUNTIME is None or chrome_cdp._RUNTIME._endpoint != endpoint
                    or not chrome_cdp.chrome_is_connected()):
                chrome_cdp._runtime().call("connect", endpoint=endpoint, session_type="real")
            chrome_cdp._runtime().call("select_current_window")
            return None
        except Exception:
            # Selection is read-only. An ambiguous set of visible CDP pages can
            # still be addressed through the exact Windows toolbar identity.
            check_cancelled()
    # Retire the old CDP target before the native route. Later CDP-only actions
    # must not silently execute in a different browser from this search.
    if chrome_cdp._RUNTIME is not None:
        chrome_cdp._RUNTIME.call("clear_selection")
    return window


def existing_chrome_connection() -> dict[str, Any] | None:
    window = preferred_existing_chrome()
    if window is None:
        return None
    from .chrome_existing_window import read_existing_chrome
    state = read_existing_chrome(window)
    return {"connected": True, "started": False, "reused": True,
            "session_type": "existing-window", "window": window,
            "active_url": state["url"], "active_title": state["title"],
            "note": "Reusing the open Chrome window through guarded UIA/Rust navigation. Use google_search or browser_navigate; DOM-only operations require a matching CDP session."}
