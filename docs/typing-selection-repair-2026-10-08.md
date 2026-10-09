# Browser typing and workflow repair — 8 October 2026

## Root cause and changes

The previous release test selected the placeholder with Ctrl+A, but the shortcut returned delivery-only evidence. The next typing action was blocked by the pending-action review gate. The model spent additional turns attempting to recover and exhausted its provider quota.

`core/browser_semantic.py` now reads the pinned editor before and after Ctrl+A. It verifies that the editor remains focused, its contents are unchanged, and all text is selected. This works for text inputs, textareas, and contenteditable text. The check does not change selection or expose draft contents in tool results. Prevented selection, changed contents, detached controls, and stolen focus remain uncertain and require observation. Enter and shortcuts on non-editor controls still require their own outcome verification.

Other repairs from the reruns:

- Invalid action arguments now return the registry's concise validation error and `not_dispatched` telemetry before confirmation. They are no longer mislabeled as denied consent. Permission denial still takes precedence.
- Invalid combined browser targets explain the accepted exclusive field sets without echoing private input. Strict target validation remains in place.
- `workflow_resume` loads the saved immutable program by ID, skips completed steps, and retains policy checks and uncertain-action review. The model no longer needs to reconstruct the original program to continue.
- Planner guidance groups editor preparation, input, and readback into one meaningful outcome, requires timely step updates, and explains exact browser target shapes and editor readback.
- Worker revision 30 requests normal idle-worker replacement. Active missions are not forcibly interrupted.
- The real-planner qualification now includes an isolated unsent-draft scenario. Its independent checks require actual Ctrl+A selection, an input event with the exact bilingual text, a fresh exact editor readback, no saves, and one tab. Overall success additionally requires `mission_completed=true`.

## Validation

- Full Python suite: **882 passed, 368 subtests passed**, no failures or skips, 24.02 seconds. JUnit: `.jarvis/qualifications/typing-repair-tests.xml`.
- Control Center: **42 tests passed**; TypeScript typecheck passed.
- Deterministic browser qualification: **11 ordered steps completed in 625.51 ms**, zero model calls, no replay on resume, one browser tab. It covered placeholder typing, Ctrl+A, exact bilingual replacement, iframe typing, clicking, scrolling, navigation, and a separate explicit new-tab check.
- Diff whitespace check passed.

The timing figures are individual local samples, not guarantees for other computers, apps, pages, or providers.

## Real-model evidence

The first full release rerun still failed before reaching typing. It selected the correct record and dismissed the notice but spent model calls reconstructing an immutable workflow and then reached the provider quota. This prompted the resume-by-ID repair. Artifact directory: `.jarvis/qualifications/autonomous-release-20261008-022719-319569/`.

The first focused typing run successfully selected all text and entered exactly `JARVIS validation — مرحبا`. All four independent control checks passed, with no save and one tab. Ctrl+A took 44.50 ms and typing took 58.30 ms. However, late plan updates consumed evidence in the wrong order; the model then hit HTTP 429 before finishing the task graph. The complete mission did **not** pass: 17 model calls, 28.311 seconds of model wait, 30.15 seconds elapsed. Artifact directory: `.jarvis/qualifications/autonomous-typing-20261008-023023-774873/`.

The follow-up typing run after the planner guidance and diagnostic changes **passed end to end**: `status=completed`, `mission_completed=true`, and all four independent checks passed. It used one workflow to select all, replace the text, and verify the exact value, with no saves and one tab. One malformed checkpoint argument was rejected during preflight before any action; the model corrected it on the next call.

- Workflow duration: **166.46 ms**. Individual actions: Ctrl+A **58.91 ms**, typing **34.97 ms**, exact readback **16.65 ms**.
- Model calls: **6**, compared with 17 in the earlier focused run. This comparison does not establish a general performance guarantee.
- Total elapsed: **151.61 seconds**, including **149.375 seconds waiting for model responses**. Despite fewer calls and fast execution, this run was slower overall because the provider took roughly 14–32 seconds per response. End-to-end latency is still unacceptable for a simple typing task; the control repair does not resolve upstream inference latency.
- Artifact directory: `.jarvis/qualifications/autonomous-typing-20261008-170353-007562/`; checkpoint `task-1791468234373-6d88b2d4`. It contains the unchanged objective, events, final result, independent fixture state, and screenshot. Cleanup reported no errors.

This successful run used the workflow journal without creating a separate top-level plan. It proves the requested typing outcome, not a general repair of every plan/evidence-association case. The earlier plan-bookkeeping failure remains useful regression evidence for further work.

## Scope

These live tests run the configured real planner and production execution modules against an owned headless Chrome fixture and isolated workspace. They do not operate personal browser sessions, WhatsApp, Discord, or the dashboard. This is browser DOM/CDP qualification, not proof of native Rust input, Windows application typing, or vision-only control. The original larger release mission remains unqualified end to end.
