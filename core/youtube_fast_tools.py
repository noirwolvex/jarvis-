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


def _query(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or len(text) > 500 or "\0" in text:
        raise ValueError("YouTube query must contain 1-500 safe characters")
    return text


def _challenge() -> dict[str, Any]:
    try:
        payload = json.loads(browser_check_challenge())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _youtube_watch_target(links: list[dict[str, Any]]) -> dict[str, str]:
    fallback: dict[str, str] | None = None
    seen: set[str] = set()
    for item in links[:400]:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not href or href in seen:
            continue
        seen.add(href)
        try:
            parsed = urllib.parse.urlparse(href)
        except Exception:
            continue
        host = (parsed.hostname or "").casefold()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            continue
        if parsed.path != "/watch":
            continue
        video_id = (urllib.parse.parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id:
            continue
        candidate = {"href": href, "text": text[:500], "video_id": video_id}
        if text:
            return candidate
        fallback = fallback or candidate
    if fallback:
        return fallback
    raise RuntimeError("YouTube search loaded, but no watch result could be resolved")


def youtube_search_open(query: str, new_tab: bool = True) -> str:
    """Search YouTube and open the first resolved watch result using one managed browser path."""
    text = _query(query)
    if not chrome_is_connected():
        ensure_chrome_connection()

    search_url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": text})
    if bool(new_tab):
        opened = chrome_new_tab(search_url)
        if not opened.startswith("VERIFIED:"):
            raise RuntimeError("YouTube search tab could not be opened and verified")
    else:
        result = chrome_page_operation("goto", url=search_url)
        if not isinstance(result, dict) or not str(result.get("url") or ""):
            raise RuntimeError("YouTube search navigation could not be verified")

    challenge = _challenge()
    if bool(challenge.get("challenge_detected")):
        return "BROWSER_ACTION_BLOCKED: " + json.dumps(
            {"action": "STOP_AND_REQUEST_USER", "reason": "YouTube presented human verification", "evidence": challenge.get("evidence", [])},
            ensure_ascii=False,
        )

    current = json.loads(chrome_current_tab())
    current_url = str(current.get("url") or "")
    parsed = urllib.parse.urlparse(current_url)
    if (parsed.hostname or "").casefold() not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parsed.path != "/results":
        raise RuntimeError(f"YouTube search page could not be verified: {current_url}")

    links = chrome_page_operation("links")
    if not isinstance(links, list):
        raise RuntimeError("YouTube did not return a valid link inventory")
    target = _youtube_watch_target(links)

    navigated = chrome_page_operation("goto", url=target["href"])
    if not isinstance(navigated, dict):
        raise RuntimeError("YouTube video navigation returned invalid evidence")
    final_url = str(navigated.get("url") or "")
    final_title = str(navigated.get("title") or "")
    final = urllib.parse.urlparse(final_url)
    if (final.hostname or "").casefold() not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or final.path != "/watch":
        raise RuntimeError(f"YouTube watch destination could not be verified: {final_url}")

    challenge = _challenge()
    if bool(challenge.get("challenge_detected")):
        return "BROWSER_ACTION_BLOCKED: " + json.dumps(
            {"action": "STOP_AND_REQUEST_USER", "reason": "YouTube watch page presented human verification", "url": final_url, "evidence": challenge.get("evidence", [])},
            ensure_ascii=False,
        )

    return "VERIFIED: " + json.dumps(
        {
            "action": "youtube_search_open",
            "query": text,
            "search_url": current_url,
            "result_text": target["text"],
            "video_id": target["video_id"],
            "url": final_url,
            "title": final_title,
            "new_tab": bool(new_tab),
        },
        ensure_ascii=False,
    )


def register_youtube_fast_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "youtube_search_open",
        "Fast deterministic YouTube action: search for a requested song/video, resolve the first real /watch result through the managed Chrome/CDP session, open it, and verify the final watch URL in one tool call. Stops at human verification.",
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
        youtube_search_open,
    ))
