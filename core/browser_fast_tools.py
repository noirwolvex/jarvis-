from __future__ import annotations

import json
import urllib.parse

from .browser_guard import browser_check_challenge
from .browser_tab_tools import chrome_new_tab
from .chrome_cdp import chrome_current_tab, chrome_is_connected, chrome_page_operation
from .chrome_session_tools import ensure_chrome_connection
from .permissions import Risk
from .tools import ToolRegistry, ToolSpec


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

    challenge = json.loads(browser_check_challenge())
    if bool(challenge.get("challenge_detected")):
        return "BROWSER_ACTION_BLOCKED: " + json.dumps(
            {
                "action": "STOP_AND_REQUEST_USER",
                "reason": "Google presented a human-verification or anti-bot checkpoint after navigation.",
                "query": text,
                "new_tab": bool(new_tab),
                "url": current_url,
                "title": current_title,
                "evidence": challenge.get("evidence", []),
            },
            ensure_ascii=False,
        )

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


def register_browser_fast_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            "google_search",
            "Fast path for an explicit Google search. Opens the verified Google results URL directly through the guarded managed Chrome/CDP session in one tool call. Set new_tab=true only when the user explicitly asks for a new/additional tab. Automatically stops at CAPTCHA/human verification without interacting with it.",
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
