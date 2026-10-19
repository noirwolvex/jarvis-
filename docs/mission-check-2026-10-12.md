# Mission check — 2026-10-12

## Real local execution

Ran `scripts.verify_browser_control` through JARVIS's production dispatcher, permissions, semantic browser adapters, and workflow journal against an owned headless Chrome fixture. No external AI calls or personal browser sessions were used.

The ordered mission types a placeholder, presses Ctrl+A, replaces it with English/Arabic text, verifies and focuses the editor, fills a search field, activates a button once, navigates to another page, returns, and types again. It comprises eleven actions/checkpoints. A second five-action typed-engine mission exercises inspection, focus, typing, clicking, and navigation.

Final live results:

- Eleven-action workflow: **completed**, 666.85 ms, zero model calls, zero replayed actions on resume.
- Five-action typed-engine workflow: **verified**, 337.19 ms; missing-target and cancellation checks passed.
- All **15 interface checks passed**, including Unicode, replacement/appending, contenteditable and shadow DOM, iframe typing, buttons, ordinal targeting, dialogs, loading delays, stale targets, and page navigation.
- Human-verification stop and browser tab checks passed.
- Sampled final tool durations: typing 66.90 ms, shortcut 22.75 ms, click 57.83 ms, scroll 5.28 ms.

These are local CDP execution measurements, not total AI-planning latency or native desktop mouse/keyboard qualification. Differences from the earlier run are not a controlled performance comparison.

## Defect fixed

Checkpoint tool schemas accepted arguments their runtime handlers reject: browser snapshot `node_id` targets, nonempty `interaction_wait.expected_version`, and `selected` state on explicit browser targets. In a compiled workflow, earlier mutations could therefore execute before an impossible checkpoint failed and remained in its immutable journal.

Wait schemas now require stable browser locators, reject snapshot versions, and reject browser-only selection checks before execution. Desktop selection checks and snapshot-bound action tools remain supported. The complete workflow preflight rejects an invalid later checkpoint before the first action or journal creation.

Regression tests cover all these invalid combinations and preserve valid selectors, role/name targets, desktop selection, and action snapshot support. The live fixture independently reads the untouched editor after submitting an invalid two-step workflow, then runs the valid mission successfully. Declared postconditions remain immutable; this fix does not excuse fabricated destination labels or mark unfinished workflows complete.

The approved AI run exposed another invalid target: `role="text"`. Playwright's accessible-role locator does not support that role. Both browser tool families now share an explicit role catalog, and runtime resolution/waits reject unsupported roles immediately. Workflow preflight rejects such checkpoints before any earlier action executes. A regression compares the catalog with the installed Playwright API. The real local fixture additionally verifies that this exact invalid checkpoint leaves the editor untouched. Planner guidance now requires observed or trusted checkpoint targets and explains how to inspect unfamiliar navigation without inventing a destination locator.

## Validation

- Focused suite: 136 passed, 83 subtests.
- Full Python suite: **1,091 passed + 626 subtests**, 31.75 seconds.
- Real local browser qualification after changes: passed.
- `git diff --check`: passed.

## Approved AI-planned mission

After explicit user approval, ran one bounded five-stage natural-language mission: select the highest approved Orion document, edit English/Arabic text using Ctrl+A and an iframe field, save once, verify the summary, and create matching report/backup files.

The configured destination was Google's `generativelanguage.googleapis.com`, model `gemini-3.5-flash-lite`, with no fallback provider. The approved payload included JARVIS system instructions, tool schemas, selected built-in control-guide text, synthetic mission/pages/tool results, local test URLs, and absolute test-workspace paths. The harness used a new workspace/memory database and browser profile, removed desktop/shell/screenshot tools, did not start the desktop monitor, and confined file operations to its new workspace.

Result: **not an end-to-end pass**. The run ended after **221.93 seconds** with `waiting_user` due to provider rate limiting, after the allowed two quota continuations. All **13 independent outcome checks** passed: correct document, exact Unicode note and iframe text, one save, dismissed popup, scrolling, summary visit, complete report, identical backup, one tab, actual shortcut/selection, and five verified plan steps.

However, an unfinished workflow checkpoint prevented clean completion. The model invented `role="text", name="Approval code"` after a valid navigation. The delivered click was not replayed, but six checkpoint attempts waited on an impossible locator. This is the defect fixed above. Provider cooldown metrics recorded **109 seconds**; the planner made repeated calls and hit another quota limit at termination. Native input speed is not the cause of those provider waits.

Artifacts: `.jarvis/qualifications/autonomous-five-stage-20261012-005639-697302/` contains `result.json`, `events.json`, `fixture-state.json`, the final screenshot, and workspace outputs/trace. The role-validation fix was applied after this process imported its code and then verified by the full regression suite and real local browser qualification. It has **not** been requalified with a second external AI run. The bounded authorization covered one run; no second run was started. General invented-but-valid locators and provider quota availability remain limitations.
