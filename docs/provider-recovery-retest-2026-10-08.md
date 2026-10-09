# Larger mission retest and provider recovery — 8 October 2026

## Result

The larger real-model mission was run twice. **Neither run completed.** Both stopped before any click or typing because the configured Gemini model returned HTTP 503 with a high-demand message. The checkpoints retained the pending work. No draft was saved and no result files were created.

The objective was unchanged: identify the highest approved Orion revision, open it, dismiss a notice, select and replace a bilingual note, fill an iframe field, scroll, save once, verify the summary, and write/read a report and identical backup.

| Run | Elapsed | Model calls | Model wait | Result |
| --- | ---: | ---: | ---: | --- |
| Before repair, provider-default effort | 77.44 s | 4 | 75.614 s | HTTP 503 after plan, running update, and semantic inspection |
| After repair, test-only low effort | 39.67 s | 2 plus one retry | 37.610 s | HTTP 503 persisted after the bounded retry |

The second run combined its plan and first observation in one model response. The shorter duration is **not** a completion-speed improvement: it stopped at an earlier point. Low effort did not resolve service availability and is not enabled in the user's saved configuration.

Artifacts:

- `.jarvis/qualifications/autonomous-release-20261008-171011-925084/`, checkpoint `task-1791468612956-acb27674`.
- `.jarvis/qualifications/autonomous-release-20261008-171540-196152/`, checkpoint `task-1791468941523-49c195e9`.

Each directory retains the objective, event trace, independent fixture state, final result, screenshot, and isolated workspace. These are owned headless Chrome fixtures; no personal application or dashboard was controlled.

## Repairs

`core/agent.py` now makes at most one retry for explicit HTTP 502/503/504 responses when no fallback provider is configured. It uses a one-second default backoff, respects numeric Retry-After values up to three seconds, and stops for longer or unrecognized delays. The backoff checks cancellation every 50 ms. Full Access's existing overall request deadline still applies. A configured fallback retains priority.

Quota errors, access denials, invalid requests, connection failures, and ambiguous timeouts are not retried by this new path. Retrying a planning request does not replay tool actions. Retry telemetry counts requests actually attempted, not cancelled backoffs. A second rejection preserves the checkpoint and reports the provider failure.

Optional `AI_REASONING_EFFORT` and `AI_FALLBACK_REASONING_EFFORT` settings allow a supported model's effort to be configured independently. Empty values preserve provider defaults. Fallback requests never inherit a primary-only reasoning setting. Google's [OpenAI compatibility documentation](https://ai.google.dev/gemini-api/docs/openai#thinking) documents reasoning-effort support and model-dependent defaults; valid options still depend on the selected provider/model.

`scripts/verify_autonomous_mission.py --reasoning-effort low` applies the setting only to the qualification process and records it in the result. It does not edit `.env`. Planner guidance also permits ordered preparation calls in one response and discourages separate model turns solely to mark a step running.

Worker revision **31** loads the repair through normal idle-worker replacement; active missions are not forcibly interrupted.

## Verification

- Full Python suite: **889 passed, 376 subtests passed**, 25.64 seconds. JUnit: `.jarvis/qualifications/provider-recovery-tests.xml`.
- Provider tests rerun after the final telemetry adjustment: **14 passed, 17 subtests passed**. They cover successful recovery, retry limits, Retry-After handling, cancellation, fallback priority, reasoning-setting isolation, and invalid configuration.
- Control Center: **42 tests passed**; TypeScript typecheck passed.
- Live deterministic browser qualification: **11 ordered steps passed in 693.22 ms**, zero model calls, no completed-action replay. Typing, iframe typing, Ctrl+A, clicks, scrolling, navigation, tab reuse, and an explicit new-tab check passed.
- Diff whitespace check passed.

The browser-control test does not prove that the larger natural-language mission completes. Persistent provider rejection remains the current blocker for that mission. These results also do not qualify native Windows/Rust input or vision-only interaction.
