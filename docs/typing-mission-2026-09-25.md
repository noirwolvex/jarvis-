# Typing mission repair — September 25, 2026

## Failures reproduced

The reported WhatsApp launch → first chat → write command reached several independent failures:

- `AFTER WRITE` was not recognized by the deterministic compiler, sending predictable work through model recovery. The compiler now recognizes the supported connectors without consuming later send/delete/navigation clauses or collapsing spaces inside the typing payload.
- WhatsApp invalidated its UI Automation provider after a delivered click (`COMError`, HRESULT `-2147220991`). A single fresh read retry now discards the partial enumeration and preserves the original click. Other provider errors and persistent failures still stop; no mutation is replayed.
- In checkpoint `task-1790303330562-8ffa9630`, typing stopped before dispatch with `Editor caret changed before input`. A live read showed a collapsed caret one range position beyond `DocumentRange.End`, even though TextPattern and ValuePattern agreed on the existing draft. The append guard now verifies the exact text before and after the actual collapsed selection. It accepts that provider representation while rejecting selections, insertion inside the draft, changed drafts, focus changes, and unreadable ranges.
- Input-resolution failures occurred before any input but created a pending action-review barrier. Explicit `InputNotDispatchedError` metadata now distinguishes these failures from uncertain delivery in the affected semantic adapters.
- WhatsApp fast-path telemetry contained a boolean; the dashboard's numeric metrics validator rejected otherwise completed missions. The producer now emits the integer `1`.
- Recovery in the above checkpoint treated an existing `HI` draft as proof of the newly requested `H` write. Compiled WhatsApp typing steps now require a successful, verified typing adapter trace with the exact requested text, bound to WhatsApp after the selected-chat step. A model claim cannot satisfy that obligation. A write-only compiled mission also rejects recovery attempts to add `submit=true` to `ui_type`.

The worker revision is 18. The existing dashboard replaces an idle older Python worker on the next mission; it does not replace an active mission or require a second server on port 3000.

## Live end-to-end verification

With the user's approval, Mission Control ran:

`OPEN WHATSAPP AND PRESS THE SECOND CHAT AFTER WRITE " [Rust draft test]"`

A fresh screenshot identified the second visible row as the user's self-chat before submission. The existing test draft was preserved. The test appended the harmless suffix and did not submit it.

Dashboard mission: `ddc043a9-bd1e-4b68-90ca-51c85e11d55b`.

Checkpoint: `task-1790303689409-ab740888`.

| Step | Actual backend | Duration | Result |
| --- | --- | ---: | --- |
| Launch/focus WhatsApp | Installed-app launcher | 1,015.69 ms | Verified visible foreground window |
| Select self-chat | UIA target resolution + Rust click | 16,278.96 ms | Verified conversation-view change; one transient UIA read retry |
| Append draft text | UIA editor/caret binding + Rust Unicode input | 5,060.11 ms | Exact editor readback, `submitted=false` |

The mission completed in 22.53 seconds with three tools, three verifications, zero tool failures, and zero model calls. The dashboard marked every node `COMPLETED`; a separate screenshot confirmed the appended text remained in the composer. This exercises dashboard → Python agent → Rust daemon → live application → independent UIA readback, not just a direct input helper.

## Regression checks

- Python: 466 tests passed, including post-click provider recovery, native editor resolution, draft preservation, logical caret endpoints, changed focus/drafts, uncertain delivery, compiler ordering, exact typing obligations, and worker/runtime behavior.
- Workspace JavaScript/TypeScript tests: 44 passed.
- Workspace TypeScript checks: passed.

## Remaining limits

The live result proves this WhatsApp typing path; it is not a guarantee for every application or mission. The measured cold chat transition still took 16.28 seconds including provider read recovery. A subsequent read-only chat/control query took 2.68 seconds. Those observations do not establish a latency guarantee or justify replaying clicks. Discord ordinal navigation was not validated by this self-chat test. No message was sent, no existing user draft was cleared, and the test suffix remains unsent for inspection.
