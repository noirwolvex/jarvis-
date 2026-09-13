from __future__ import annotations

import re

_NEW_TAB_EN = re.compile(r"\b(?:new|another)\s+(?:browser\s+)?tab\b", re.IGNORECASE)
_NEW_TAB_AR = re.compile(r"(?:تبويب|علامة\s+تبويب)\s+(?:جديد|جديدة)")


def required_new_tabs(user_text: str) -> int:
    """Count explicit user requirements to create additional browser tabs."""
    text = str(user_text or "")
    return len(_NEW_TAB_EN.findall(text)) + len(_NEW_TAB_AR.findall(text))


def minimum_tab_count(initial_tab_count: int, requested_new_tabs: int) -> int:
    """Return the minimum final tab count needed to prove requested new tabs exist."""
    requested = max(0, int(requested_new_tabs))
    initial = max(0, int(initial_tab_count))
    if requested == 0:
        return initial
    # A fresh managed Chrome starts with one real startup tab. Treat that as the
    # baseline even if CDP was not connected yet when the mission began.
    return max(1, initial) + requested
