# Autonomous ticket challenge — 8 October 2026

The new real-model challenge did **not** complete. The final attempt reached the correct ticket and dismissed its notice, but the planner skipped the requested filter, invented a destination heading, and spent further turns on invalid recovery/bookkeeping. The configured Gemini provider then returned HTTP 429. JARVIS preserved the mission as `waiting_user`; it did not report completion. No report or draft was saved.

## Task and isolation

The planner received a natural-language objective: filter Atlas READY tickets, select the highest priority, dismiss a notice, wait for an editable form, enter English/Arabic text and an iframe reference, scroll, save a local draft once, inspect its preview, write a report, copy it, and read both files.

The runner used the configured real AI planner, production tools, permission checks, workflow dispatcher, and durable journal. It supplied no prewritten action sequence. Independent HTTP fixture state and actual file bytes determined success. Chrome ran headlessly with a temporary profile against an owned loopback fixture. The test had no access to personal tabs or desktop apps. Local fixture approvals were confined to that origin and the new report path. The real dashboard was not accessed.

Run with `python -m scripts.verify_autonomous_mission --scenario tickets`.

## Real-model results

| Attempt | Wall time | Model calls | Model wait | Outcome |
| --- | ---: | ---: | ---: | --- |
| `autonomous-tickets-20261008-021011-047752` | 38.19 s | 16 | 34.87 s | Typed project search, selected status; quota before filtering/navigation |
| `autonomous-tickets-20261008-021310-343494` | 28.98 s | 18 | 26.33 s | Typed search; attempted typing into dropdown; recovery and quota |
| `autonomous-tickets-20261008-021606-177915` | 41.55 s | 22 | 32.94 s | Opened correct ticket and dismissed notice; skipped filter, incorrect checkpoint, quota |

All three failed the independent end-to-end checks. These are diagnostic reruns after changes, not a controlled speed comparison. Artifact folders are under `.jarvis/qualifications/`; each includes the objective, complete events, result, fixture state, final screenshot, and an isolated workspace with its task checkpoint. The final two successful clicks took 105.10 ms and 60.55 ms. Workflow-container durations overlap their child tools, so summed trace durations are not independent wall time.

## Repairs implemented

- Browser actions now distinguish rejection before input from uncertain delivery. Missing/stale target versions, unavailable targets, and invalid values no longer create fictitious actions requiring verification. Failures after input starts remain uncertain.
- Typing checks whether the target is a writable text editor before dispatch. Native dropdowns reject typing and identify the semantic select operation. The scene reports the input kind.
- Unique labeled browser controls expose reusable role/name targets directly in the scene. Each action resolves the live DOM and rejects ambiguity. Duplicate, custom, and state-selected controls retain version-bound node identities. One observed scene can therefore support multiple known labeled inputs without a stale-node retry after each keystroke.
- Argument errors report the invalid field without dumping the entire schema or private argument values. Unknown tool names suggest existing names, without invoking them. Recovery corrects rejected arguments instead of recommending unrelated window focus or mission rewrites.
- Browser recovery reads the affected browser surface instead of capturing the personal desktop.
- Rejected or running step updates no longer discard valid preceding evidence. Successfully completed steps still consume their evidence.
- Workflow review reruns its declared read-only checkpoint before completion. A model's prose cannot override a failed postcondition, and review never replays the mutation.
- Automatic plan/update replies omit duplicate telemetry and history from model context; the complete durable journal and explicit status tools retain those details. The last run omitted 11,331 repeated bookkeeping characters.
- Removed conflicting instructions that forced tab enumeration/selection for an already connected page. Added exact typing/select argument guidance.
- Worker revision is 29; normal idle-worker replacement loads the changes without terminating an active mission.

## Verification

- Full Python suite: **875 passed, 358 subtests passed**.
- Control Center: **42 passed**.
- Next route generation, TypeScript checking, and `git diff --check` passed.
- Live isolated Chrome control qualification: typing, Unicode, iframe typing, shortcuts, clicks, scroll, navigation, explicit tab creation, and safe ambiguous-focus rejection passed.
- Its separate deterministic nine-step workflow completed in **876.39 ms**, with zero model calls, one explicit fixture confirmation, and zero repeated actions on resume.
- Last sampled individual control times: typing 133.16 ms; click 83.02 ms; scroll 7.81 ms. These are single-run samples, not latency guarantees, and do not establish a speedup over earlier runs.

The deterministic qualification validates control execution, not autonomous planning. Neither test validates native Rust mouse/keyboard input into normal Windows apps or visual-only target recognition.

## Remaining failures

The real planner still makes semantic/ordering errors and spends too many calls on bookkeeping. Its incorrect immutable checkpoint can leave a workflow blocked; replacing it with an arbitrary weaker condition would hide failure, so this was not bypassed. The skipped filter remained a failed independent check. Provider quota remains an external limitation; no credentials, billing plan, or provider were changed. The platform is not yet qualified as reliably autonomous for this full task.
