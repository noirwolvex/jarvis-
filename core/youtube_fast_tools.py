from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any

from .browser_semantic import BrowserChallengeBlocked
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
        payload = chrome_page_operation("challenge_state")
        if not isinstance(payload, dict) or payload.get("inspection_available") is not True or not isinstance(payload.get("challenge_detected"), bool):
            raise ValueError("Challenge inspection did not return valid evidence")
        if payload["challenge_detected"]:
            raise BrowserChallengeBlocked("BROWSER_ACTION_BLOCKED: " + json.dumps({"action": "STOP_AND_REQUEST_USER", "reason": "Human verification is present", "evidence": payload.get("evidence", [])}))
        return payload
    except BrowserChallengeBlocked:
        raise
    except Exception as exc:
        from .process_control import check_cancelled
        check_cancelled()
        raise BrowserChallengeBlocked("BROWSER_ACTION_BLOCKED: Challenge inspection unavailable; user review is required") from exc


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
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
            continue
        candidate = {"href": "https://www.youtube.com/watch?" + urllib.parse.urlencode({"v": video_id}), "text": text[:500], "video_id": video_id}
        if text:
            return candidate
        fallback = fallback or candidate
    if fallback:
        return fallback
    raise RuntimeError("YouTube search loaded, but no watch result could be resolved")


def youtube_search_open(query: str, new_tab: bool = False, play: bool = False) -> str:
    """Search YouTube and open the first resolved watch result using one managed browser path."""
    text = _query(query)
    if not chrome_is_connected():
        ensure_chrome_connection()
    try:
        return _search_open(text, new_tab, play)
    except BrowserChallengeBlocked as exc:
        return str(exc)


def _search_open(text: str, new_tab: bool, play: bool) -> str:
    from .process_control import check_cancelled
    check_cancelled()
    _challenge()

    search_url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": text})
    if bool(new_tab):
        opened = chrome_new_tab(search_url)
        if not opened.startswith("VERIFIED:"):
            raise RuntimeError("YouTube search tab could not be opened and verified")
    else:
        result = chrome_page_operation("goto", url=search_url)
        if not isinstance(result, dict) or not str(result.get("url") or ""):
            raise RuntimeError("YouTube search navigation could not be verified")

    _challenge()

    current = json.loads(chrome_current_tab())
    current_url = str(current.get("url") or "")
    parsed = urllib.parse.urlparse(current_url)
    if (parsed.hostname or "").casefold() not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parsed.path != "/results":
        raise RuntimeError(f"YouTube search page could not be verified: {current_url}")

    # Results are hydrated after DOMContentLoaded. Poll only their bounded semantic
    # inventory; the search navigation itself is never repeated.
    deadline = time.monotonic() + 8
    while True:
        check_cancelled()
        _challenge()
        links = chrome_page_operation("youtube_results")
        if not isinstance(links, list):
            raise RuntimeError("YouTube did not return a valid link inventory")
        try:
            target = _youtube_watch_target(links)
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)

    navigated = chrome_page_operation("goto", url=target["href"])
    _challenge()
    if not isinstance(navigated, dict):
        raise RuntimeError("YouTube video navigation returned invalid evidence")
    final_url = str(navigated.get("url") or "")
    final_title = str(navigated.get("title") or "")
    final = urllib.parse.urlparse(final_url)
    if (final.hostname or "").casefold() not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or final.path != "/watch":
        raise RuntimeError(f"YouTube watch destination could not be verified: {final_url}")
    if (urllib.parse.parse_qs(final.query).get("v") or [""])[0] != target["video_id"]:
        raise RuntimeError("YouTube navigated to a different video than the selected result")

    playback = None
    if play:
        playback = chrome_page_operation("youtube_playback", action="play", expected_video_id=target["video_id"], timeout_ms=8000)
        if not isinstance(playback, dict) or playback.get("verified") is not True:
            raise RuntimeError("YouTube playback did not return verified evidence")
        _challenge()

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
            "playback": playback,
        },
        ensure_ascii=False,
    )


def youtube_playback(action: str = "status", expected_video_id: str = "") -> str:
    if action not in {"status", "play", "pause"}:
        raise ValueError("YouTube playback action must be status, play, or pause")
    if not chrome_is_connected():
        raise RuntimeError("Connect and select the YouTube watch tab before checking playback")
    try:
        _challenge()
        current = json.loads(chrome_current_tab())
        parsed = urllib.parse.urlparse(str(current.get("url", "")))
        video_id = (urllib.parse.parse_qs(parsed.query).get("v") or [""])[0]
        if (parsed.hostname or "").casefold() not in {"youtube.com", "www.youtube.com", "m.youtube.com"} or parsed.path != "/watch" or not video_id:
            raise RuntimeError("The selected tab is not a YouTube watch page")
        if expected_video_id and expected_video_id != video_id:
            raise RuntimeError("The selected YouTube video does not match expected_video_id")
        result = chrome_page_operation("youtube_state") if action == "status" else chrome_page_operation("youtube_playback", action=action, expected_video_id=video_id, timeout_ms=8000)
        _challenge()
        if not isinstance(result, dict) or result.get("selected_video_id") != video_id:
            raise RuntimeError("YouTube playback evidence does not match the selected video")
        if action != "status" and result.get("verified") is not True:
            raise RuntimeError("YouTube did not verify the requested playback state")
        return ("OBSERVED: " if action == "status" else "VERIFIED: ") + json.dumps(result, ensure_ascii=False)
    except BrowserChallengeBlocked as exc:
        return str(exc)


def register_youtube_fast_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        "youtube_search_open",
        "Search YouTube, open the first real watch result and verify its video ID in one call. Reuses the selected tab by default; set new_tab only when requested. Set play=true to verify actual advancing video playback. Stops at human verification or unavailable inspection; never retries a playback action blindly.",
        Risk.MEDIUM,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "new_tab": {"type": "boolean"},
                "play": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        youtube_search_open,
    ))
    registry.register(ToolSpec("youtube_playback", "Observe, play, or pause the selected YouTube video. Verifies the exact video identity and actual paused/advancing playback state. Stops on CAPTCHA, ads, or unavailable inspection; playback is never retried blindly.", Risk.MEDIUM,
        {"type": "object", "properties": {"action": {"enum": ["status", "play", "pause"]}, "expected_video_id": {"type": "string", "maxLength": 64}}, "additionalProperties": False}, youtube_playback))
