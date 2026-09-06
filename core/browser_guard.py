from __future__ import annotations

import json
import re
from typing import Any


_CHALLENGE_PATTERNS = (
    r"captcha",
    r"recaptcha",
    r"hcaptcha",
    r"turnstile",
    r"verify you are human",
    r"prove you are human",
    r"human verification",
    r"security check",
    r"bot verification",
    r"anti[- ]?bot",
)


def _page():
    from .tools import _PAGE
    if _PAGE is None:
        raise RuntimeError("No browser page is open")
    return _PAGE


def _challenge_evidence(page) -> list[str]:
    evidence: list[str] = []
    try:
        body = page.locator("body").inner_text(timeout=3000)
        lowered = body.lower()
        for pattern in _CHALLENGE_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                evidence.append(f"text:{pattern}")
    except Exception:
        pass

    selectors = (
        "iframe[src*='captcha']",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']",
        "[class*='captcha']",
        "[id*='captcha']",
        "[class*='recaptcha']",
        "[id*='recaptcha']",
        "[class*='hcaptcha']",
        "[id*='hcaptcha']",
        "[class*='turnstile']",
        "[id*='turnstile']",
    )
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                evidence.append(f"selector:{selector}")
        except Exception:
            continue
    return list(dict.fromkeys(evidence))


def browser_check_challenge() -> str:
    """Detect common anti-bot/human-verification challenges without interacting with them."""
    page = _page()
    evidence = _challenge_evidence(page)
    if evidence:
        return json.dumps(
            {
                "challenge_detected": True,
                "action": "STOP_AND_REQUEST_USER",
                "reason": "A human-verification or anti-bot challenge is present. JARVIS must not attempt to solve, bypass, or automate it.",
                "evidence": evidence,
                "url": page.url,
                "title": page.title(),
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "challenge_detected": False,
            "action": "CONTINUE",
            "url": page.url,
            "title": page.title(),
        },
        ensure_ascii=False,
    )


def browser_page_state() -> str:
    """Inspect browser forms and controls without exposing current field values."""
    page = _page()
    challenge = _challenge_evidence(page)
    controls: list[dict[str, Any]] = page.locator("input, textarea, select, button").evaluate_all(
        "els => els.slice(0, 200).map((el, i) => ({"
        "index:i,"
        "tag:el.tagName.toLowerCase(),"
        "type:el.type || '',"
        "name:el.getAttribute('name') || '',"
        "id:el.id || '',"
        "placeholder:el.getAttribute('placeholder') || '',"
        "aria_label:el.getAttribute('aria-label') || '',"
        "text:(el.innerText || el.textContent || '').trim().slice(0,160),"
        "required:!!el.required,"
        "disabled:!!el.disabled,"
        "visible:!!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)"
        "}))"
    )
    return json.dumps(
        {
            "url": page.url,
            "title": page.title(),
            "challenge_detected": bool(challenge),
            "challenge_evidence": challenge,
            "controls": controls,
            "note": "Field values are intentionally not returned.",
        },
        ensure_ascii=False,
    )


def register_browser_guard_tools(registry) -> None:
    from .tools import ToolSpec
    from .permissions import Risk

    registry.register(
        ToolSpec(
            "browser_check_challenge",
            "Detect common CAPTCHA, anti-bot, or human-verification challenges on the current browser page. Never interacts with or attempts to solve the challenge.",
            Risk.SAFE,
            {"type": "object", "properties": {}, "additionalProperties": False},
            browser_check_challenge,
        )
    )
    registry.register(
        ToolSpec(
            "browser_page_state",
            "Inspect current browser form/control metadata without returning entered field values. Use before filling unfamiliar forms.",
            Risk.LOW,
            {"type": "object", "properties": {}, "additionalProperties": False},
            browser_page_state,
        )
    )

    from .chrome_cdp import register_chrome_cdp_tools
    register_chrome_cdp_tools(registry)
