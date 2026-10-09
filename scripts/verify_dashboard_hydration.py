"""Check the running local dashboard in isolated Chromium contexts.

Run with `python -m scripts.verify_dashboard_hydration`. The injected case models
the reported extension attributes before React sees the document. The unrelated
case proves real hydration diagnostics remain enabled. No desktop mission runs.
"""
from __future__ import annotations

import argparse
import json

from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:3000/"
PROCESSED = "__processed_10202c7c-8b94-4478-9584-454e6d6775a9__"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-extension-warning", action="store_true")
    args = parser.parse_args()
    reports = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for case in ("clean", "extension", "unrelated", "mixed", "processed_only"):
                context = browser.new_context(timezone_id="Asia/Bahrain")
                page = context.new_page()
                page.set_default_timeout(15000)
                # This fixture checks hydration, not authentication. Keep the
                # reconnect screen stable without granting a real control session.
                page.route("**/api/session", lambda route: route.fulfill(
                    json={"authenticated": False, "pairingAvailable": True}))
                warnings: list[str] = []
                errors: list[str] = []
                failures: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.on("requestfailed", lambda request: failures.append(request.url + ": " + str(request.failure)))
                page.on("console", lambda message: warnings.append(message.text)
                        if message.text.startswith("A tree hydrated but some attributes") else None)
                if case != "clean":
                    # Hold async application scripts while parsing the real server
                    # response. Mutate the DOM as an extension would, then allow the
                    # actual Next bootstrap (and instrumentation) to hydrate it.
                    # Fulfilling the navigation with synthetic HTML changes its
                    # address space in Chromium and blocks local API/HMR requests.
                    held_scripts = []
                    scripts_released = False
                    def hold_script(route):
                        if scripts_released:
                            route.continue_()
                        else:
                            held_scripts.append(route)
                    page.route("**/_next/static/**/*.js*", hold_script)
                    response = page.goto(URL, wait_until="commit")
                    if response is None or response.status != 200:
                        raise RuntimeError("The local dashboard did not serve HTML")
                    page.wait_for_function("document.readyState !== 'loading'")
                    page.locator("main").wait_for(state="attached")
                    injected = page.evaluate("""({caseName, processed}) => {
                        if (document.querySelector('[bis_skin_checked], [bis_register]') ||
                            document.body.hasAttribute(processed)) {
                            throw new Error('Unexpected extension markers in server HTML');
                        }
                        if (caseName === 'extension' || caseName === 'mixed') {
                            document.querySelectorAll('div').forEach(node =>
                                node.setAttribute('bis_skin_checked', '1'));
                            document.body.setAttribute('bis_register', 'fixture');
                            document.body.setAttribute(processed, 'true');
                        }
                        if (caseName === 'unrelated' || caseName === 'mixed') {
                            document.querySelector('main').setAttribute(
                                'data-unrelated-hydration-test', 'keep');
                        }
                        if (caseName === 'processed_only') {
                            document.body.setAttribute(processed, 'true');
                        }
                        return {
                            markers: document.querySelectorAll('[bis_skin_checked="1"]').length,
                            unrelated: document.querySelector('main').getAttribute(
                                'data-unrelated-hydration-test'),
                            processed: document.body.getAttribute(processed),
                        };
                    }""", {"caseName": case, "processed": PROCESSED})
                    if case in ("extension", "mixed") and not injected["markers"]:
                        raise RuntimeError("The extension fixture did not annotate any elements")
                    if case in ("unrelated", "mixed") and injected["unrelated"] != "keep":
                        raise RuntimeError("The unrelated attribute fixture was not injected")
                    if case == "processed_only" and injected["processed"] != "true":
                        raise RuntimeError("The isolated processed marker was not injected")
                    if not held_scripts:
                        raise RuntimeError("No application scripts were held before injection")
                    scripts_released = True
                    for route in held_scripts:
                        route.continue_()
                    page.unroute("**/_next/static/**/*.js*", hold_script)
                else:
                    page.goto(URL, wait_until="domcontentloaded")
                try:
                    page.locator(".connection:not(.connecting), .session-connect button:not([disabled])").first.wait_for()
                except Exception:
                    print(json.dumps({"case": case, "page_errors": errors, "failures": failures,
                                      "warnings": warnings, "scripts": page.locator("script[src]").count()}), flush=True)
                    raise
                # A real event handler proves hydration completed. This is a local
                # display preference in an ephemeral profile, not a device action.
                if page.locator(".session-connect").count():
                    with page.expect_response("**/api/session"):
                        page.get_by_role("button", name="Check connection", exact=True).click()
                else:
                    page.get_by_role("button", name="Use light theme", exact=True).click()
                    page.get_by_role("button", name="Use dark theme", exact=True).wait_for()
                if failures:
                    raise RuntimeError(f"{case}: browser requests failed: {failures}")
                unexpected_errors = [error for error in errors if error not in warnings]
                if unexpected_errors:
                    raise RuntimeError(f"{case}: unexpected browser errors: {unexpected_errors}")
                remaining = page.locator('[bis_skin_checked="1"], [bis_register]').count()
                processed = page.locator("body").get_attribute(PROCESSED)
                unrelated = page.locator("main").get_attribute("data-unrelated-hydration-test")
                expected_warning = case in ("unrelated", "mixed", "processed_only") or (
                    case == "extension" and args.expect_extension_warning)
                if bool(warnings) != expected_warning:
                    raise RuntimeError(f"{case}: expected hydration warning={expected_warning}; observed={warnings}")
                if case in ("extension", "mixed") and not args.expect_extension_warning and (remaining or processed):
                    raise RuntimeError("Extension hydration markers were not removed")
                if case in ("unrelated", "mixed") and unrelated != "keep":
                    raise RuntimeError("Unrelated app attributes were incorrectly removed")
                if case == "processed_only" and processed != "true":
                    raise RuntimeError("An unrelated processed marker was incorrectly removed")
                reports.append({"case": case, "hydration_warnings": len(warnings),
                                "remaining_extension_markers": remaining, "interactive": True})
                context.close()
        finally:
            browser.close()
    print("DASHBOARD_HYDRATION_VERIFIED " + json.dumps(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
