# Ticket workflow qualification — 2026-10-12

## Mission

Ran a different real-planner mission against an isolated local ticket queue: filter Atlas READY tickets, choose the highest priority eligible ticket, dismiss its dialog, replace an English/Arabic note and iframe reference, check Reviewed for handoff, scroll, save a draft once, inspect the preview, and create/read matching report and backup files. The checkbox is now an independently checked fixture effect.

The configured Gemini planner received synthetic fixture data and the same isolated harness context as the previous approved test. Only the owned headless Chrome session and new test workspace were available. The run had a 360-second deadline and two permitted same-mission quota resumptions.

## Observed result

**Not passed.** The run ended `waiting_user` after **131.15 seconds** at another Gemini input-token quota limit. It successfully typed the project filter, selected READY, applied the filters, and opened ATL-1042. It did not reach the editor/dialog, checkbox, save, preview, or report steps. Only two of thirteen final checks passed (filtered queue and one tab). The correct-ticket check intentionally requires the saved ticket, so opening the correct page alone does not satisfy it.

Metrics recorded 34 model calls, 81 seconds of provider cooldown, and 47.216 seconds of model wait. Both allowed quota resumptions preserved the same mission. No clean autonomous-completion claim is warranted.

Artifacts: `.jarvis/qualifications/autonomous-tickets-20261012-010405-540243/` contains the objective, events, independent fixture state, result, final screenshot, and workspace trace. Cleanup reported no errors.

## Reproduced problems and changes

- The planner passed scene IDs such as `dom:n4` as browser node IDs. Those requests returned misleading stale-node errors even immediately after observation. The browser now rejects scene IDs before DOM lookup and directs the planner to the observed `target.browser_target`. Scene observations also explicitly describe the distinction. Actual browser node IDs retain version validation.
- An invented workflow tool name was safely rejected before any action, but recovery guidance incorrectly recommended resuming an existing journal. Disallowed workflow tools now report a preflight rejection so recovery asks for corrected tool names/arguments. A regression verifies zero dispatched actions and no journal creation.

These changes were applied after the running AI process loaded its code. They were verified locally, not by a second AI run. This does not resolve provider quota limits or guarantee that the planner always supplies correct targets.

## Validation after changes

- Focused tests: 131 passed, 86 subtests.
- Full Python suite: **1,093 passed, 626 subtests**, 33.20 seconds.
- Real local Chrome qualification: passed all 15 interface checks, including dropdown/checkbox control, Unicode/iframe typing, dialogs, scrolling, and navigation.
- Eleven-action deterministic workflow: 668.17 ms; five-action typed-engine workflow: 328.55 ms; zero model calls or resume replays.
- `git diff --check`: passed.

Local timings measure execution in the owned headless CDP fixture, not AI-planning latency or native desktop input.
