# Five-stage autonomous qualification — 2026-10-10

## Scope

This qualification runs the configured real planner against a local web application in an owned, headless Chrome/CDP session. It creates an isolated workspace below `.jarvis/qualifications/`. It does not send messages, access personal files, or interact with the user's foreground applications.

The objective contains five outcomes, not a precompiled sequence of tool calls:

1. Select the highest approved Orion revision from a library containing a newer draft and another project's approved release; read the selected approval code.
2. Dismiss a notice, wait for an initially disabled editor, actually press Ctrl+A, replace its text with `JARVIS validation — مرحبا`, and fill an iframe's Audit label with `reviewed-r4`. Verify both values.
3. Scroll, save the draft exactly once, and verify the receipt.
4. Open the summary and verify the record, approval, receipt, note, and audit text.
5. Write `output/result.md`, copy it to `output/result-backup.md`, and verify both files match.

The independent checker examines application events and state, actual keyboard selection, save count, browser tabs, report bytes, and five completed plan nodes. A planner claim of completion alone does not pass this test. The fixture has separate approval data for every record, including summary pages, so a draft cannot masquerade as the approved release.

## Repairs driven by the test

- Browser snapshots now include bounded row context and unique observed iframe selectors. Compact scenes preserve status/alert text, including save receipts.
- Compact browser scenes retain scroll position and actual scroll limits, including in delta responses with no visible controls. A blank middle section of a long page is explicitly distinguished from a page with no controls.
- DOM-bound actions use the connected CDP page even when another desktop application is foreground. The action guard and adapter share routing. Disconnected or mixed targets fail before input; they never fall back to typing into another window.
- Rejected pre-input guards carry `not_dispatched` evidence rather than creating an uncertain mutation that blocks the next action.
- New workflows reject reused snapshot versions before their first mutation. Unique semantic targets still resolve live. Existing journals remain immutable; uncertain effects cannot be replayed automatically.
- Exact file writes reopen and compare UTF-8 bytes locally before returning `VERIFIED`, eliminating a separate model read/verify cycle. Mismatched or unreadable results remain failures without an automatic rewrite. Line endings are preserved.
- Recovery selects file readback for uncertain file writes. Plan bookkeeping failures no longer suggest replaying a UI save, send, or click.
- Observation JSON is compacted without removing observed fields. Planner guidance distinguishes append from replacement and requires explicitly requested shortcuts.
- A validated short per-minute provider cooldown can be retried once when no fallback provider is configured. Daily, billing, zero-limit, and malformed quota errors are not automatically retried. Cancellation remains available.
- Same-instance mission continuation preserves the plan, journal, and completed effects. It rejects changed goals, cancelled missions, or requests still stopping. The qualification runner can opt into a bounded number of longer minute-reset resumptions; the dashboard does not automatically acquire that test-only retry policy.

## Earlier run evidence

All directories below are under `.jarvis/qualifications/` and contain `result.json`, `events.json`, fixture state, and a durable task trace.

| Run suffix | Seconds | Independent result |
| --- | ---: | --- |
| `151405-630059` | 28.06 | Provider quota interrupted the mission before save/report. |
| `151933-773855` | 31.06 | Keyboard selection observed; provider quota interrupted remaining work. |
| `152628-726812` | 156.36 | Iframe targeting and replacement errors; quota interrupted recovery. |
| `153032-120637` | 172.58 | Planner claimed completion, but selected the wrong record. Independent checks rejected it. |
| `153429-019617` | 170.46 | Correct saved values; missing explicit Ctrl+A, summary, and report. |
| `153814-598598` | 187.81 | Browser outcomes verified; report/backup and plan completion missing. |
| `154406-601847` | 310.33 | Browser outcomes and report verified; backup and plan completion missing. |
| `155040-113101` | 280.48 | Correct selection and editing; redundant scrolling and an invalid follow-up save target prevented later stages. This prompted the viewport-context repair. |

These are diagnostic runs made while repairs were evolving, not repeated measurements of one unchanged implementation. Provider errors in these runs reported per-minute request/input-token quotas; longer allowances alone did not fix planner bookkeeping errors.

## Latest live result

Run `autonomous-five-stage-20261010-155639-031798` completed all thirteen independent outcome checks, including the correct record, actual Ctrl+A, exact Unicode/iframe values, one save, scrolling, summary, report, identical backup, one tab, and five completed plan nodes. Cleanup reported no errors.

**The overall qualification still failed.** `mission_completed` and `independent_pass` are false. The mission ended `waiting_user` after the bounded provider continuations were exhausted. Three internal workflows also retained invalid model-generated checkpoints: the planner expected headings such as `Orion revision 4` and `Draft saved successfully`, which were not actual elements on the destination pages. Later correct readback did not satisfy those immutable, incorrectly chosen locators. The completion guard correctly refused to ignore unfinished workflows.

Observed timings for this run:

- Total: **291.11 seconds**.
- Explicit provider cooldowns: **167 seconds**, across four continuations.
- Model calls: **54**; recorded model wait: **91.066 seconds**.
- Ten successful browser click/focus/shortcut/type/scroll calls: **48.575 ms median**, **122.85 ms maximum**. These are tool-call samples, not end-to-end task latency or a native desktop benchmark.

Artifacts: `.jarvis/qualifications/autonomous-five-stage-20261010-155639-031798/result.json`, `events.json`, `fixture-state.json`, and `workspace/.jarvis/traces/task-1791637000111-f398cac3.json`. The generated report and backup are inside that run's `workspace/output/` directory.

A stricter requirement for checkpoints on every new browser-click batch was evaluated during this run and then removed to preserve the supported observe-and-review path for unknown destinations. It encouraged specifying future elements before they were observed and did not solve invalid checkpoint selection. The final implementation retains stale-version preflight and clearer recovery guidance; neither silently rewrites failed postconditions nor reports this live run as passed. The latest workflow recovery guidance and this compatibility restoration were regression-tested after the live run; their effect on real planner behavior has not been qualified in another live mission.

Remaining work is planner checkpoint selection and safe recovery from an incorrectly specified postcondition, plus reduction of model round-trips. Provider quota limits remain an external source of delay. These issues prevent claiming fast, reliable completion of arbitrary complex missions.

## Regression validation

The final Python suite passes **1,079 tests**, with **598 subtests**. This includes real isolated Chromium fixture tests as well as unit tests for routing, stale references, Unicode readback, cancellation, quota handling, and preserving mission state. `git diff --check` passes. The real planner qualification above remains a separate failing gate.

## Reproduction

```powershell
python -X utf8 -m scripts.verify_autonomous_mission --scenario five-stage --reasoning-effort medium --quota-resumes 4 --deadline-seconds 420
python -X utf8 -m pytest -q
```

The qualification is opt-in and consumes calls to the configured model. Its deadline and continuation count are bounded. Each run uses its own browser, workspace, and artifacts; cleanup is checked before a run can pass.

## Limits

This test exercises DOM/CDP control and filesystem integration. It does not certify Rust mouse/keyboard injection, Windows UI Automation, OCR accuracy, or interaction with arbitrary desktop applications. Independent validation remains necessary for those paths. A successful run also does not establish zero-error operation or consistent performance across models and applications.
