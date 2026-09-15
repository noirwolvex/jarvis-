from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .browser_guard import browser_check_challenge
from .browser_tab_tools import chrome_new_tab
from .chrome_cdp import chrome_current_tab, chrome_is_connected, chrome_page_operation
from .chrome_session_tools import ensure_chrome_connection
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec


_EXCLUDED_RESULT_HOST_SUFFIXES = (
    "google.com",
    "googleusercontent.com",
    "gstatic.com",
    "googleadservices.com",
    "doubleclick.net",
)
_NON_RESULT_TEXT = {
    "images",
    "videos",
    "news",
    "maps",
    "shopping",
    "books",
    "flights",
    "finance",
    "settings",
    "tools",
    "more",
    "sign in",
    "web",
    "ai mode",
}


def _query(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 500 or "\0" in text:
        raise ValueError("Google search query must contain 1-500 safe characters")
    return text


def _is_google_search_url(url: str, expected_query: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").casefold()
        params = urllib.parse.parse_qs(parsed.query)
        actual = (params.get("q") or [""])[0]
        return (
            (host == "google.com" or host == "www.google.com" or host.endswith(".google.com"))
            and parsed.path.startswith("/search")
            and actual.strip().casefold() == expected_query.strip().casefold()
        )
    except Exception:
        return False


def _host_excluded(host: str) -> bool:
    value = str(host or "").casefold().strip(".")
    return any(value == suffix or value.endswith("." + suffix) for suffix in _EXCLUDED_RESULT_HOST_SUFFIXES)


def _external_result_target(raw_href: str) -> str | None:
    href = str(raw_href or "").strip()
    if not href:
        return None
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return None
    host = (parsed.hostname or "").casefold()

    if host == "google.com" or host == "www.google.com" or host.endswith(".google.com"):
        if parsed.path.rstrip("/") == "/url":
            params = urllib.parse.parse_qs(parsed.query)
            for key in ("q", "url"):
                for candidate in params.get(key, []):
                    target = _external_result_target(candidate)
                    if target:
                        return target
        return None

    if parsed.scheme not in {"http", "https"} or not parsed.netloc or _host_excluded(host):
        return None
    return href


def _first_google_result(links: list[dict[str, Any]]) -> dict[str, str]:
    for item in links[:300]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text or text.casefold() in _NON_RESULT_TEXT:
            continue
        target = _external_result_target(str(item.get("href") or ""))
        if target:
            return {"text": text[:500], "href": target}
    raise RuntimeError("Google search loaded, but no external result link could be resolved")


def _challenge_payload(query: str, new_tab: bool, current_url: str, current_title: str, reason: str) -> str:
    challenge = json.loads(browser_check_challenge())
    if not bool(challenge.get("challenge_detected")):
        return ""
    return "BROWSER_ACTION_BLOCKED: " + json.dumps(
        {
            "action": "STOP_AND_REQUEST_USER",
            "reason": reason,
            "query": query,
            "new_tab": bool(new_tab),
            "url": current_url,
            "title": current_title,
            "evidence": challenge.get("evidence", []),
        },
        ensure_ascii=False,
    )


def google_search(query: str, new_tab: bool = False) -> str:
    """Perform and verify a Google search in one guarded CDP tool call."""
    text = _query(query)
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": text})

    if not chrome_is_connected():
        ensure_chrome_connection()

    if bool(new_tab):
        chrome_new_tab(url)
    else:
        chrome_page_operation("goto", url=url)

    current = json.loads(chrome_current_tab())
    current_url = str(current.get("url") or "")
    current_title = str(current.get("title") or "")

    blocked = _challenge_payload(
        text,
        bool(new_tab),
        current_url,
        current_title,
        "Google presented a human-verification or anti-bot checkpoint after navigation.",
    )
    if blocked:
        return blocked

    if not _is_google_search_url(current_url, text):
        raise RuntimeError(f"Google search navigation could not be verified for query: {text}")

    return "VERIFIED: " + json.dumps(
        {
            "action": "google_search",
            "query": text,
            "new_tab": bool(new_tab),
            "url": current_url,
            "title": current_title,
        },
        ensure_ascii=False,
    )


def browser_google_search_first_result(
    query: str,
    new_tab: bool = False,
    preserve_search_tab: bool = True,
) -> str:
    """Search Google, resolve the first real external result, open it, and verify the destination."""
    text = _query(query)
    searched = google_search(text, new_tab=bool(new_tab))
    if searched.startswith("BROWSER_ACTION_BLOCKED:"):
        return searched
    if not searched.startswith("VERIFIED:"):
        raise RuntimeError("Google search did not produce verified evidence")

    search_tab = json.loads(chrome_current_tab())
    search_url = str(search_tab.get("url") or "")
    search_title = str(search_tab.get("title") or "")
    if not _is_google_search_url(search_url, text):
        raise RuntimeError("The selected Chrome tab is no longer the verified Google results page")

    links = chrome_page_operation("links")
    if not isinstance(links, list):
        raise RuntimeError("Chrome did not return a valid link inventory for the Google results page")
    candidate = _first_google_result(links)

    if bool(preserve_search_tab):
        opened = chrome_new_tab(candidate["href"])
        if not opened.startswith("VERIFIED:"):
            raise RuntimeError("The first Google result could not be opened in a verified Chrome tab")
    else:
        # Fast missions should behave like actually pressing the first result: keep the
        # single user-requested Google tab and navigate it directly to the destination.
        navigated = chrome_page_operation("goto", url=candidate["href"])
        if not isinstance(navigated, dict) or not str(navigated.get("url") or ""):
            raise RuntimeError("The first Google result could not be opened in the current Chrome tab")

    current = json.loads(chrome_current_tab())
    result_url = str(current.get("url") or "")
    result_title = str(current.get("title") or "")
    parsed = urllib.parse.urlparse(result_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or _host_excluded(parsed.hostname or ""):
        raise RuntimeError("The first Google result did not resolve to a verified external destination")

    blocked = _challenge_payload(
        text,
        bool(new_tab),
        result_url,
        result_title,
        "The first Google result opened a human-verification or anti-bot checkpoint.",
    )
    if blocked:
        return blocked

    return "VERIFIED: " + json.dumps(
        {
            "action": "browser_google_search_first_result",
            "query": text,
            "new_tab": bool(new_tab),
            "search_url": search_url,
            "search_title": search_title,
            "result_text": candidate["text"],
            "result_url": result_url,
            "result_title": result_title,
            "preserved_search_tab": bool(preserve_search_tab),
        },
        ensure_ascii=False,
    )


def register_browser_fast_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "google_search",
            "Fast path for an explicit Google search. Opens the verified Google results URL directly through the guarded managed Chrome/CDP session in one tool call. Set new_tab=true only when the user explicitly asks for a new/additional tab. If the same request also says to press/click/open the first result or first link, use browser_google_search_first_result instead so both clauses are executed and verified in one tool call. Automatically stops at CAPTCHA/human verification without interacting with it.",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "new_tab": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            google_search,
        )
    )
    registry.register(
        ToolSpec(
            "browser_google_search_first_result",
            "Fast deterministic path for a request that says to search Google and then press/click/open the first result or first link. Performs the verified Google search, resolves the first real external result while excluding Google navigation/tracking/ad hosts, opens and verifies that destination. Set new_tab=true when the user explicitly asks to start the Google search in a new/additional tab. preserve_search_tab=true keeps the Google results tab and opens the result separately; false navigates the current results tab exactly like pressing the result and is preferred by the deterministic Fast Lane. Prefer this single tool over separate google_search + browser_click calls for lower latency and fewer missed clauses.",
            Risk.MEDIUM,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "new_tab": {"type": "boolean"},
                    "preserve_search_tab": {"type": "boolean"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            browser_google_search_first_result,
        )
    )
