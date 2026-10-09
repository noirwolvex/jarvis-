"""Render the real dashboard with isolated API fixtures; never run a device mission."""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


def main():
    base = "http://127.0.0.1:3000"
    with urllib.request.urlopen(base + "/api/control", timeout=5) as response:
        snapshot = json.load(response)
    snapshot.update(status="EXECUTING", mode="hybrid", emergencyStopped=False, tasks=[], events=[],
                    missionControl={"paused": False, "pauseRequested": False})
    requests, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1360, "height": 1000})
            page.set_default_timeout(10000)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""window.fixtureSpeech = [];
                window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
                Object.defineProperty(window, 'speechSynthesis', {value: {
                    speak: utterance => window.fixtureSpeech.push(utterance.text), cancel: () => {}
                }});""")

            def api(route):
                request = route.request
                path = urlsplit(request.url).path
                if request.method == "GET" and path == "/api/session":
                    route.fulfill(json={"authenticated": True, "pairingAvailable": True})
                    return
                if request.method == "GET" and path == "/api/full-access":
                    route.fulfill(json={"mode": "full"})
                    return
                if request.method == "GET" and path == "/api/control":
                    route.fulfill(json=snapshot)
                    return
                if request.method == "POST" and path == "/api/mission-control":
                    command = request.post_data_json
                    requests.append(command)
                    action = command["action"]
                    if action in {"confirm", "reject"}:
                        assert command["confirmationId"] == "c" * 32
                        snapshot["missionControl"].pop("pendingConfirmation", None)
                    if action == "pause":
                        snapshot["missionControl"] = {"paused": True, "pauseRequested": True}
                        snapshot["status"] = "PAUSED"
                    elif action == "resume":
                        snapshot["missionControl"] = {"paused": False, "pauseRequested": False}
                        snapshot["status"] = "EXECUTING"
                    elif action == "cancel":
                        snapshot["status"] = "CANCELLED"
                    route.fulfill(status=202, json={"ok": True})
                    return
                errors.append(f"Unexpected API request: {request.method} {path}")
                route.fulfill(status=400, json={"error": "Fixture refuses real API dispatch"})

            page.route("**/api/**", api)
            page.goto(base, wait_until="networkidle")
            page.get_by_role("button", name="Pause mission", exact=True).click()
            expect(page.get_by_role("button", name="Resume mission", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Resume mission", exact=True).click()
            expect(page.get_by_role("button", name="Pause mission", exact=True)).to_be_enabled()
            details = json.dumps({"destination": "owned fixture only", "text": "Draft / مرحبا " + "x" * 3000})
            snapshot["missionControl"]["pendingConfirmation"] = {
                "id": "c" * 32, "tool": "fixture_send", "summary": "Review the fixture action",
                "details": details, "reason": "Fixture only; no message will be sent",
            }
            snapshot["status"] = "WAITING_USER"
            expect(page.locator(".mission-action-details")).to_have_text(details)
            page.get_by_role("button", name="Approve this action once", exact=True).click()
            expect(page.locator(".mission-action-details")).to_have_count(0)
            page.get_by_role("button", name="Enable voice", exact=True).click()
            expect(page.get_by_role("button", name="Mute voice", exact=True)).to_have_attribute("aria-pressed", "true")
            assert page.evaluate("window.fixtureSpeech") == ["Voice feedback enabled."]
            page.get_by_role("button", name="Cancel mission", exact=True).click()
            expect(page.get_by_role("button", name="Cancel mission", exact=True)).to_be_disabled()
            assert [item["action"] for item in requests] == ["pause", "resume", "confirm", "cancel"]
            assert not errors, errors
            print("MISSION_CONTROLS_UI_VERIFIED " + json.dumps({
                "pause": True, "resume": True, "confirmation_details": True,
                "exact_confirmation_id": True, "cancel": True, "voice_toggle": True,
                "speech_sink": "mocked", "real_device_missions": 0,
            }))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
