from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .browser_guard import browser_check_challenge
from .browser_tab_tools import new_tab_in_selected_session as chrome_new_tab
from .chrome_cdp import chrome_current_tab, chrome_is_connected, chrome_page_operation
from .chrome_session_tools import ensure_chrome_connection
from .chrome_user_browser import preferred_existing_chrome
from .browser_semantic import BrowserChallengeBlocked
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
    """Search in the user's current Chrome window with one verified tool call."""
    text = _query(query)
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": text})

    window = preferred_existing_chrome()
    if window is None and not chrome_is_connected():
        connected = json.loads(ensure_chrome_connection())
        if connected.get("session_type") == "existing-window":
            window = connected["window"]
    if window is not None:
        return _search_existing(text, url, bool(new_tab), window)

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


def _search_existing(text: str, url: str, new_tab: bool, window: dict[str, Any]) -> str:
    from .chrome_existing_window import navigate_existing_chrome
    try:
        result = navigate_existing_chrome(url, new_tab=new_tab, window=window)
    except BrowserChallengeBlocked as exc:
        return str(exc)
    if result.get("verified") is not True or not _is_google_search_url(str(result.get("url", "")), text):
        raise RuntimeError("Existing Chrome search did not produce verified Google results; inspect before retrying")
    return "VERIFIED: " + json.dumps({**result, "action": "google_search", "query": text}, ensure_ascii=False)


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

    search_evidence = json.loads(searched[len("VERIFIED:"):])
    if search_evidence.get("session_type") == "existing-window":
        return _existing_first_result(text, search_evidence, bool(new_tab), bool(preserve_search_tab))

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


def _existing_first_result(query: str, searched: dict[str, Any], new_tab: bool, preserve: bool) -> str:
    from .chrome_existing_window import navigate_existing_chrome, read_existing_chrome
    window = searched["window"]
    state = read_existing_chrome(window, include_links=True)
    if state.get("challenge_detected") is True:
        return "BROWSER_ACTION_BLOCKED: Human verification is present in the existing Chrome window; complete it manually"
    if not _is_google_search_url(state["url"], query):
        raise RuntimeError("The existing Chrome window left the verified results page; inspect before continuing")
    candidate = _first_google_result(state["links"])
    try:
        result = navigate_existing_chrome(candidate["href"], new_tab=preserve, window=window)
    except BrowserChallengeBlocked as exc:
        return str(exc)
    parsed = urllib.parse.urlparse(str(result.get("url", "")))
    if (result.get("verified") is not True or parsed.scheme not in {"http", "https"}
            or not parsed.netloc or _host_excluded(parsed.hostname or "")):
        raise RuntimeError("The existing Chrome result did not produce a verified external destination")
    return "VERIFIED: " + json.dumps({**result, "action": "browser_google_search_first_result",
        "initial_tab_count": searched.get("initial_tab_count", result.get("initial_tab_count")),
        "query": query, "new_tab": new_tab, "search_url": state["url"], "search_title": state["title"],
        "result_text": candidate["text"], "result_url": result["url"], "result_title": result["title"],
        "preserved_search_tab": preserve}, ensure_ascii=False)


def register_browser_fast_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "google_search",
            "Fast path for an explicit Google search in the user's existing Chrome window. Use matching CDP or guarded Windows toolbar navigation; start Chrome only when needed. Set new_tab=true only for an explicit new/additional tab. For search plus opening the first result, use browser_google_search_first_result to execute both clauses in the same window. Stops at human verification.",
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
