# Device control and typing repair — September 24, 2026

This review continues the [September 23 typing repair](typing-fix-2026-09-23.md). It covers editor resolution, native click dispatch, chat selection, keyboard/scroll guards, browser navigation regression coverage, and recovery loops. It does not certify error-free operation in every application.

## Observed failure

Checkpoint `task-1790258099473-d1d77f18` records a three-step mission to launch WhatsApp, select its first chat, and write text. Launch took 2,450.73 ms, chat selection 28,364.65 ms, and typing resolution 11,449.5 ms. Typing failed before keyboard dispatch with two exact editor candidates. A subsequent UI inspection took another 11,302.92 ms.

Saved WhatsApp UI observations expose a search field named `Search or start a new chat` and a composer named `Type a message to …`. The resolver treated both as composers because both labels matched its `message|chat|reply` expression. The earlier duplicate-identity repair did not address this label collision. The latest inspection also filtered by the application name, so it returned the page root instead of useful editor metadata.

The same checkpoint subsequently recorded repeated failed `task_verify` calls with changing claim/evidence text but the identical missing-observation error. Bookkeeping tools were excluded from the retry counter, allowing this loop to consume model turns without progress.

## Changes

- Exclude search/find/filter labels from automatic composer preference. Explicitly targeting the search field still works. Multiple genuine composers remain ambiguous.
- Resolve typing through UIA provider queries for `Edit` and `Document`, avoiding materialization and property reads for unrelated buttons and messages. Explicit button/control types also use provider filtering. Retain native-identity deduplication, document-root/read-only exclusions, and complete-candidate checks.
- Include bounded candidate labels, types, and automation IDs in ambiguity errors; do not include editor values.
- Reuse the chat-discovery observation for the conversation before-state. Skip conversation enumeration after a fresh positive selection result, and skip it entirely for an already-selected row. When selection evidence is unavailable, observe the conversation again. Keyboard focus alone no longer verifies chat selection.
- Bind native click coordinates to the semantic control geometry and recheck the target after capture and connection preparation, immediately before dispatch. A moved control, changed owner, or invalidated state blocks delivery without a compatibility retry.
- Bound repeated identical bookkeeping failures even when the model changes the evidence wording. Fresh successful observation resets that budget; status bookkeeping does not. Return a result for every undelivered tool call when stopping the batch.
- Advance the Python worker revision to 15 so an idle old worker is replaced before the next mission. An active mission is not replaced mid-execution.

## Live typing follow-up

The subsequent reported mission (`task-1790259306334-ed1187d1`) exposed a second failure: the visible empty WhatsApp composer reports a single newline through UI Automation. The native tool treated it as an existing draft and rejected it. Recovery then incorrectly required verification of a text mutation that had never been dispatched.

Live provider inspection also found that this WhatsApp editor advertises a writable Value pattern, but `SetValue` returned without changing its value. A successful method return is therefore insufficient. The implementation now:

- Recognizes an exact newline-only empty paragraph when TextPattern independently agrees with the value readback. Both `ui_type_native` and unsent, append-only `ui_type` use guarded native input for this case, without trying and replaying an unreliable write.
- Accepts only the exact requested text, optionally followed by the original synthetic paragraph marker, after native delivery. Partial text, extra characters, and additional line breaks still fail verification.
- Uses a verified collapsed TextPattern caret when native typing appends to an existing draft. Providers that cannot establish the caret fail before typing. Multiline input remains on a semantic Value-pattern path to avoid submitting a chat with native newline events.
- Reports explicit pre-dispatch rejection as structured execution metadata. Fast missions, model-driven execution, and persistent workflows do not fabricate a pending text mutation from this rejection. An attempted write with uncertain readback still requires observation and is never replayed automatically.
- Preserves the current control binding when falling back to semantic typing, avoiding a second editor lookup. Foreground, control identity, draft, and mission-context checks still run immediately before native delivery.

With explicit user approval, the actual Python `ui_type_native` function called the existing authenticated Rust daemon and entered `JARVIS typing test` into the user's empty WhatsApp self-chat. The function returned `rust_native_input`, `rust_executed=true`, `simulation=false`, and `submitted=false`. Its exact UIA readback passed, as did a separate second value read. The measured function duration was **4,831.87 ms**, including resolution, focus, native dispatch, and verification; preliminary test setup is excluded. The test draft was left unsent. A preceding attempt lost foreground during resolution and was blocked before keyboard delivery.

This confirms the Python-to-Rust connection and the reported empty-composer typing repair in a real application. It is not a live certification of every app, existing-draft provider, multiline editor, or full launch/navigation mission. The ordinary `ui_type` route additionally has regression coverage for the same paragraph behavior; the successful live call above used `ui_type_native`.

## UI Automation query latency

The WhatsApp provider returned thousands of wrapper aliases for a much smaller set of real controls. Typed lookups now combine control types in one provider traversal, request runtime identity and process ID in the same native cache operation, and deduplicate before constructing Python wrappers. Visibility filtering also happens in the provider. Geometry, focus, values, and other input state are read fresh rather than cached.

Live read-only measurements of the final query implementation were **1,878 ms for three unique editor/document controls** and **2,114 ms for 25 unique chat/editor controls**. An earlier unoptimized query exposed 6,523 wrappers for 83 identities and spent about 3.16 seconds constructing wrappers and reading identity properties, in addition to provider traversal. These observations concern different filtered query shapes; they establish the removed overhead, not a controlled overall mission speedup. Chat lookup tries a narrow list/tree/data-item/editor query first and retains broader discovery for applications exposing different row types.

## Validation

| Check | Result |
| --- | --- |
| Python unittest discovery | 442 passed |
| All npm workspace tests | 44 passed: 17 Control Center, 21 runtime, 6 database (including the database parent test) |
| Cargo tests with native feature | 44 passed; 4 intentionally ignored fixtures/benchmark |
| Next.js production build and TypeScript compilation | Passed |
| Git diff whitespace check | Passed |

Regression coverage includes the real WhatsApp search label, Edit/Document composers, Unicode exact readback, ambiguous editors, a thousand unrelated button aliases, typed button lookup, one-dispatch click guards, foreground/draft/state changes, multi-monitor scrolling, native stop, and bounded recovery. Existing browser tests exercise exact text filling, replaced DOM targets, navigation/state waits, loading, cancellation, and human-verification boundaries in local fixtures.

The deterministic mission fixture completes launch → chat selection → native typing into a newline-only composer without a model call or coordinate recovery. Separate chat-selection fixtures verify one provider-query call with positive selection evidence, versus three tree scans in the previous implementation, and two query calls when conversation-change verification is necessary. These are fixture call counts; live query and typing timings are reported separately above.

## Runtime status and limits

The user authorized stopping a competing Discord mission before the self-chat test. On inspection, that mission had already failed because its model service returned HTTP 503, and its checkpoint had no in-flight action. No worker or daemon was terminated for the live typing test, and no message was submitted.

An already-running Python worker cannot load source edits in place. The updated Control Center bridge replaces an idle worker before its next mission; a production server must first load the updated build. Saved checkpoints remain intact. An active mission must finish or be stopped before replacement, and an emergency-stop latch is never silently cleared by an update.

After the former runtime stopped, the updated production build was started with the repository's `scripts/jarvis_runtime.py start` launcher. Startup reported `backend=rust`, `rust_input_ready=true`, and a healthy dashboard response. The control endpoint returned `IDLE` in hybrid mode. Full Access is `standard` after this restart and must be explicitly enabled in the Control Center for a new desktop mission.
