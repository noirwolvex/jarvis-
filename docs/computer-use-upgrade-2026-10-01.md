# Computer-use integration — 2026-10-01

This upgrade extends the existing Python planner, deterministic workflows, UI Automation/CDP targeting, Rust native input, live preview, verification and recovery modules. It does not replace them.

## Integrated behavior

- `core/computer_perception.py` adds `computer_observe`: one bounded semantic scene read, with the existing screen capture/vision path when controls are missing, unlabeled or truncated. It reports focus/selection and visible dialog, alert and progress cues. `visual=always` supports canvas/icon ambiguity. It makes no extra model call. There is no new standalone OCR engine; image understanding uses the configured vision-capable provider.
- The agent, compact tool profiles, worker preview and read-only completion checks recognize the combined observation. Semantic-only evidence invalidates old coordinate authority. Only a valid stable screenshot can authorize raw coordinates. Browser metadata and an unrelated foreground screenshot are explicitly marked as separate surfaces.
- `core/mission_control.py` introduces a thread-safe control channel independent of the model. Pause waits for an action boundary, releases legacy held input, and invalidates coordinate authority on resume. Pending raw input must observe and resolve its target again after a pause. Resume continues the same mission and preserves completed work. Cancel stops the current mission without revoking Full Access. A new mission cannot reset cancellation while a previous adapter is still shutting down. Emergency stop remains the separate latched device stop.
- One-time confirmation is bound to an exact invocation, mission and argument fingerprint. The dashboard shows the tool, reason and complete bounded argument preview with secret redaction. Oversized previews fail closed rather than asking approval for truncated content. Wrong, reused, expired or rejected approvals cannot execute the pending action. Rejection/expiry cancels the mission. No model tool can answer an approval request.
- Send/submit/delete/purchase/critical-setting operations, opaque activations, raw coordinate activation, file replacement and arbitrary commands receive confirmation checks. Existing permission checks still apply first. Plain typing, scrolling and known semantic navigation retain their fast path. Builder-created agents without an operator channel deny confirmation-required actions.
- Approval cannot redirect raw keyboard input into the dashboard: the original foreground identity must still match. Semantic adapters continue resolving targets at dispatch. Browser challenge protection remains active.
- The authenticated, local-only `/api/mission-control` endpoint accepts strict pause/resume/cancel/confirm/reject messages for the current worker request. Worker acknowledgements determine UI state; a request alone does not claim execution has paused. Cancellation is reported as cancellation in both model and compiled paths.
- Optional browser speech synthesis announces fixed mission statuses, completion, recovery and requests for attention. It never reads private screen/task content aloud, does not replay old events and coalesces rapid updates. Operator decisions are kept in the normal final/interrupted mission checkpoint, alongside existing tool results and verification evidence.

## Validation performed

- Python suite: **686 tests passed**. After the final pause-target refresh change, all **31 control tests passed**, including its new regression case. The compiled-cancellation regression also passed in the full suite.
- Workspace tests: **63 passed** (36 Control Center, 21 runtime, 6 database).
- Workspace TypeScript checks passed.
- Rust tests: **44 passed, 3 ignored**. The ignored platform qualification tests were not counted as passes.
- Real native qualification passed through the Python worker and Rust daemon on display `65537`, 1920×1080: screen capture, mouse focus/click, Unicode typing, Ctrl+A selection/replacement and Home-key caret movement. All effects were read back in the owned temporary test window. Capture took 58.33 ms; clicks took 42.14/29.37 ms; keyboard probes took 2.05–43.27 ms. These are local sample timings, not universal latency guarantees.
- Isolated live Chrome/CDP fixture passed typing, iframe typing, focus/hotkeys, click, scroll and navigation. Its nine-step workflow completed in **649.22 ms**, with zero model calls, one exact fixture confirmation, one browser tab and zero replays of completed steps. This includes semantic perception. The test's approval responder only accepts the owned loopback Apply button; it is not installed in the runtime.
- Rendered dashboard test passed Pause, Resume, a complete 3 KB confirmation preview, exact approval ID dispatch, Cancel and the voice toggle. All API mutations and speech output were fixture-bound; no real mission was launched. Actual speaker output/installed voices were not qualified.
- Five live hydration cases passed: clean/extension cases produce no warning; unrelated changes still report real hydration mismatches.
- Managed dashboard restarted successfully with HTTP 200 and strict Rust backend ready. No active user mission was interrupted during qualification.

## Operating limits

Pause is cooperative: it does not freeze an input chord midway or preempt an in-flight provider request. Use Emergency stop for urgent interruption. The existing 30-minute worker deadline includes paused time; confirmation expires after five minutes. Resume in a live worker retains its current mission. After process restart, recall the durable checkpoint and re-observe before continuing uncertain work.

UI effect classification is conservative, but a label cannot prove what arbitrary application code will do. Opaque activations may ask for confirmation even when reversible. Semantic targets remain preferred. Unknown foreground, stale coordinates, denied permissions or unverified outcomes stop execution rather than guessing.

The native result proves the current Python/Rust input path and one connected display. It does not prove every WhatsApp/Discord layout, every monitor configuration, all download/save dialogs or arbitrary long missions. This remains a tested integration rather than a guarantee of universal error-free control. No messages, purchases or destructive device operations were performed for these tests.

## Reproduce

Run from the repository root:

```powershell
python -m unittest discover -s tests -q
npm test
npm run typecheck
cargo test --manifest-path daemon/rust/Cargo.toml --quiet
python -m scripts.verify_browser_control
python -m scripts.verify_mission_controls
python -m scripts.verify_dashboard_hydration
```

The last two scripts require the managed dashboard. For native qualification, stop the idle managed runtime first, then run `npm run verify:rust-live`; it refuses to compete with a running runtime. The test only types/clicks inside its own temporary window. Restart with `npm run dev` afterward.
