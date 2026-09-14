from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse
from typing import Any

_NEW_TAB_EN = re.compile(r"\b(?:new|another)\s+(?:browser\s+)?tab\b", re.IGNORECASE)
_NEW_TAB_AR = re.compile(r"(?:تبويب|علامة\s+تبويب)\s+(?:جديد|جديدة)")
_SEARCH_EN = re.compile(r"\bsearch(?:\s+for)?\b", re.IGNORECASE)
_SEARCH_AR = re.compile(r"(?:ابحث|بحث)")


def required_new_tabs(user_text: str) -> int:
    text = str(user_text or "")
    return len(_NEW_TAB_EN.findall(text)) + len(_NEW_TAB_AR.findall(text))


def minimum_tab_count(initial_tab_count: int, requested_new_tabs: int) -> int:
    requested = max(0, int(requested_new_tabs))
    initial = max(0, int(initial_tab_count))
    if requested == 0:
        return initial
    return max(1, initial) + requested


def required_google_searches(user_text: str) -> int:
    text = str(user_text or "")
    folded = text.casefold()
    if "google" not in folded and "جوجل" not in text and "غوغل" not in text:
        return 0
    return len(_SEARCH_EN.findall(text)) + len(_SEARCH_AR.findall(text))


def google_search_result_count(tab_rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in tab_rows:
        if not isinstance(row, dict):
            continue
        raw_url = str(row.get("url") or "").strip()
        if not raw_url:
            continue
        try:
            parsed = urlparse(raw_url)
        except Exception:
            continue
        host = (parsed.hostname or "").casefold()
        if host != "google.com" and not host.endswith(".google.com"):
            continue
        if parsed.path.rstrip("/") != "/search":
            continue
        query = parse_qs(parsed.query).get("q", [])
        if any(str(value).strip() for value in query):
            count += 1
    return count
