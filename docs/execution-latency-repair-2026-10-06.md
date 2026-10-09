# Execution latency repair — 2026-10-06

## Observed bottlenecks

The latest saved WhatsApp / File Explorer mission took approximately 145 seconds
and made 26 model calls. Its trace recorded a 16.2-second File Explorer launch,
failed focus recovery, an unsupported `interaction_wait(state="selected")`, and
verification bookkeeping after a successful observation. Other recent traces
recorded 6.7–13-second full interface inspections after a guessed composer label
failed. These are recorded timings from those missions, not a general benchmark.

## Changes

- Application focus tries direct window activation once, then UI Automation
  SetFocus on the exact window, and checks the actual foreground HWND. An existing
  visible window whose focus still cannot be verified returns delivery evidence
  and a recovery reason instead of repeating the same focus attempt until the
  15-second launch deadline. Already-foreground windows avoid focus actions;
  maximized windows are no longer unnecessarily restored.
- Desktop inspection accepts `control_type`, filters types in the UIA provider,
  and filters labels before reading full geometry, focus and hierarchy metadata.
  The inspection cache distinguishes type filters. Editor targeting retains its
  narrow Edit/Document provider queries and live validation before typing.
- Desktop `interaction_wait` and `ui_wait_state` support `selected` through fresh
  selection-pattern readback. Missing, false, unreadable and ambiguous selection
  evidence cannot pass. Selection does not prove folder navigation or any other
  unrelated outcome.
- Desktop-only missions skip unrelated Chrome tab enumeration in deterministic
  execution, model execution and completion. Resumed new-tab missions reuse their
  saved baseline without evaluating an unnecessary browser read.
- After a successful observation, recovery tells the planner to evaluate that
  evidence and record its result instead of repeating input or requesting another
  screenshot merely for bookkeeping. It does not automatically mark an action
  successful because a control is visible or focused.
- Chrome new-tab navigation skips Ctrl+L when the newly bound empty omnibox is
  already focused. In the regression fixture this reduces full scans from 11 to
  9 and shortcuts from 3 to 2; every remaining dispatch retains fresh guards.
  Loading polls check the independent document URL before rescanning page content.
  Challenge URLs and final destination verification still receive full inspection.
- Checkpoints record aggregate `model_wait_ms`, `model_tool_count`, and
  `model_tool_schema_chars` to help distinguish provider latency from tool work.
  Worker revision 26 loads the changes on the next mission after the old worker is
  idle, preserving an active mission.

## Validation boundaries

Regression coverage uses isolated fixtures for focus denial/recovery, exact input
targets, stale or ambiguous state, inspection filtering, selected-state readback,
Chrome loading, live vision, and mission verification. No personal chat was typed
into or resent to measure these changes. These improvements do not guarantee a
fixed end-to-end latency for live applications, external model calls, or pages.
Unknown workflows still require planning, and genuine uncertainty still pauses
input rather than declaring an unverified result complete.

## Common-action fast paths — 2026-10-07

- Exact requests such as `type "hello" in "Notepad"`, `اكتب "مرحبا"`, and
  `open https://example.com/path` compile directly without model calls. Quoted
  whitespace is preserved, typing sets `submit=false`, and multiline or ambiguous
  instructions fall back to planning. A unique live editor is still required.
- Direct URLs reuse the current Chrome navigation adapter; only an explicit new-tab
  request selects `chrome_new_tab`. Credentials, invalid ports, unsupported URL
  schemes, and unsupported trailing work cannot become guessed app names. Ordered
  sequences retain multiword window titles and literal URL punctuation.
- Native append typing skips one immediately duplicated TextPattern check when
  the caret is already correct (three selection reads reduced to two). Moving a
  caret still requires readback, and dispatch always runs the fresh caret guard.
- Semantic browser clicks and typing perform two page challenge inspections
  instead of three. Iframe actions perform four instead of six. Both the parent
  and target frame remain checked immediately before input and after the action;
  read-only target resolution does not authorize input.
- Worker revision 27 refreshes the idle Python worker on the next mission.

Validation: 840 Python tests and 329 subtests passed; 42 Control Center tests and
TypeScript checks passed. `python -m scripts.verify_browser_control` also passed
against an isolated real headless Chrome using only owned loopback pages. It
verified Unicode typing, iframe input, clicks, scrolling, navigation and explicit
same-window tabs. Its nine-step workflow completed in 563.07 ms with zero model
calls and zero repeated actions on resume. This is a local fixture measurement,
not a speed guarantee for personal desktop applications or external websites.

## Bounded provider waits and responsive cancellation — 2026-10-07

The installed provider SDK defaulted to a 600-second read timeout and two hidden
retries. That delayed JARVIS recovery and fallback handling; a 429 response could
also make the SDK sleep for the provider's Retry-After interval. The latest saved
145-second mission ended in a provider quota error after 26 model calls.

- Both primary and fallback clients now disable hidden SDK retries. Planning
  uses a configurable 45-second budget per provider, 5-second connection/pool
  timeouts, and a 10-second write timeout. `JARVIS_AI_TIMEOUT_SECONDS` accepts
  finite values from 5 to 180. Full Access enforces a wall deadline as well,
  allowing at most two provider budgets when a fallback is configured.
- Quota/access errors, connection timeouts and HTTP 5xx errors can immediately
  use the already-configured fallback once. Invalid requests are not retried.
  No credentials or fallback provider are invented or enabled by this change.
- Full Access waits for planning in a separate executor, checks cancellation
  every 50 ms, and reports continued provider waits every 10 seconds. A pause
  requested while planning holds the returned action until the operator resumes.
- A cancelled/timed-out response cannot execute tools. An abandoned request
  cannot start a later fallback or change the active provider after fallback
  returns. The agent rejects overlapping missions until that request settles.
- Provider outages retain the same checkpoint and verified work in WAITING_USER.
  Worker revision 28 loads these changes after the previous worker becomes idle.

Validation includes the real installed SDK against an owned loopback HTTP server:
429 with Retry-After=40, read timeout, 503 fallback, 400 rejection, both providers
unavailable, and cancellation before/during fallback. Separate mission tests
verify prompt cancellation, discarded late typing, pause/resume, a hard planning
deadline and checkpoint preservation. No personal chat or live provider is used.

Final checks: 853 Python tests and 341 subtests passed; all 42 Control Center tests,
TypeScript checks and `git diff --check` passed. The checked local configuration
does not contain a complete fallback provider configuration, so exhausted primary
quota still requires restored provider availability before model-driven work can
continue. Deterministic missions remain independent of model requests.
