from __future__ import annotations

import unittest
from unittest.mock import patch

from core.browser_guard import _challenge_evidence


class _FakeLocator:
    def __init__(self, text: str = "", count: int = 0) -> None:
        self._text = text
        self._count = count

    def inner_text(self, timeout: int = 3000) -> str:
        return self._text

    def count(self) -> int:
        return self._count


class _FakePage:
    def __init__(self, body: str = "", counts: dict[str, int] | None = None) -> None:
        self.body = body
        self.counts = counts or {}

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "body":
            return _FakeLocator(text=self.body)
        return _FakeLocator(count=self.counts.get(selector, 0))


class BrowserGuardTests(unittest.TestCase):
    def test_detects_human_verification_text(self) -> None:
        page = _FakePage("Please verify you are human before continuing")
        evidence = _challenge_evidence(page)
        self.assertTrue(evidence)
        self.assertTrue(any(item.startswith("text:") for item in evidence))

    def test_detects_recaptcha_selector(self) -> None:
        selector = "iframe[src*='recaptcha']"
        page = _FakePage("Normal page", {selector: 1})
        evidence = _challenge_evidence(page)
        self.assertIn(f"selector:{selector}", evidence)

    def test_normal_page_has_no_challenge(self) -> None:
        page = _FakePage("Welcome to the account page")
        with patch("core.browser_guard._CHALLENGE_PATTERNS", (r"captcha", r"recaptcha")):
            self.assertEqual(_challenge_evidence(page), [])


if __name__ == "__main__":
    unittest.main()
