# Rapid and long-task device control validation

The later [typing failure repair](typing-fix-2026-09-23.md) addresses a reported WhatsApp editor-resolution failure and updates worker replacement to avoid latching Rust during an idle code refresh. Its validation supersedes the counts below.

September 23, 2026. This continues the [input performance work](input-performance-2026-09-21.md) on top of commit `455cc13`. It reuses the Python planner, semantic tools, CDP runtime and Rust native daemon. This is an implementation and regression report, not certification of arbitrary desktop missions.

## Changes in this follow-up

- **Faster recovery from loading:** a workflow retries a read-only checkpoint once after an explicit timeout. It never repeats the delivered click, text, submit or navigation. Ambiguity, challenges, cancellation and other failures do not get this automatic retry. The retry count and checkpoint attempts are persisted.
- **Reliable resumption:** completed steps no longer require authorization or tool availability again. A delivered action waiting for verification only revalidates its checkpoint. Every remaining action is still preflighted before continuing. Missing, duplicated, reordered or invalid journal records are rejected instead of silently truncating the requested program.
- **Final typing guard:** semantic typing and shortcuts can pass a guard into the Rust bridge. It runs under the dispatch lock after frame preparation and any connection renewal, before allocating a request sequence or sending input. Focus, draft, control identity, geometry and state generation are checked at this point. Failed guards do not trigger Python fallback. Browser challenge errors retain their manual-intervention classification.
- **Correct cache generations:** the action accounts for its own single UIA cache invalidation without accepting additional invalidations. This preserves normal typing and submit while rejecting a concurrent state change. Tests exercise the real binding and validation functions, rather than replacing those guards with mocks.
- **Scroll belongs to the pointer's monitor:** Python selects the display and observation/input grants from the observed pointer, including negative monitor coordinates. It cannot borrow the foreground window center's monitor grant. Rust validates the pointer against the authorized frame before each bounded wheel batch, as well as checking foreground, pointer ownership and cancellation.
- **Guarded legacy waits:** legacy browser waits use the shared cancellable, challenge-aware state verifier and refuse ambiguous targets. Their 60-second maximum remains compatible; semantic waits retain their 30-second maximum. Ready controls return immediately.
- **Browser stop checks:** cancellation is checked immediately before a semantic mutation and after final inspection. Tests also cover the existing exact-element pinning, parent-page challenges during iframe waits and Unicode contenteditable readback.
- **Worker refresh:** revision 9 lets the updated Control Center replace an older idle Python worker through its existing lifecycle.

The existing 750 ms native frame reuse remains in place. A fixture covering semantic Unicode typing followed by submit uses one capture and two separately guarded native requests. Guards still run when a frame is reused.

## Capability evidence

| Capability | Execution path and verification covered |
|---|---|
| Mouse movement and clicking | Rust native input and guarded Python compatibility; exact target, pointer drift, overlays at the Rust hit-test boundary, repeated-click cancellation and final position fixtures |
| Typing and shortcuts | UIA target binding followed by Rust dispatch; Unicode, exact editor readback, focus/draft changes during capture, no uncertain fallback, and submit ordering |
| Scrolling | Actual pointer display/grant selection, rapid capture reuse, frame boundaries and emergency cancellation between batches |
| Page navigation and buttons | CDP/DOM tools, pinned elements, tab reuse and ownership, guarded waits and challenge detection in isolated browser fixtures |
| Screen observation | Synthetic live-frame, freshness, display-origin/geometry, encoding, cancellation and frame-reuse tests |
| Long missions | Ordered durable workflows, one bounded read-only recovery, resumption without replay, malformed journal rejection and no model call for known compiled steps |
| Python/Rust connection | Authenticated protocol tests, including Python/OpenSSL interoperability with a fixture Rust server; simulated dispatch and error boundaries |

The Rust server also now has one bounded emergency-only admission slot when its four normal sessions are full. The saturation, invalid-envelope, authentication and admission-bound tests pass. This supersedes the earlier report's four-session saturation finding. The extra slot is itself bounded; this does not establish guaranteed stop latency under every form of resource exhaustion.

## Rechecked performance evidence

These measurements exercise the already implemented fast paths. They must not be interpreted as end-to-end application speedups.

| Fixture | Earlier path | Current path |
|---|---:|---:|
| Installed Python legacy click wrapper, median of 5 runs, OS delivery stubbed | 100.468 ms | 0.056 ms |
| Extended single-click wrapper, same fixture | 60.537 ms | 0.062 ms |
| UIA metadata provider reads per binding, equivalent returned evidence | 22 | 12 |
| Synthetic 1280×720 JPEG encode/write, median of 30 runs | 9.570 ms | 2.210 ms |
| Semantic browser snapshot runtime dispatches | 3 | 1 |

The click fixture removes OS delivery entirely and sets PyAutoGUI's configurable pause to 100 ms. The screenshot fixture does not capture the desktop; the faster encoder produces a slightly larger JPEG (106,198 versus 101,968 bytes). Browser snapshot tests assert one owner-thread dispatch and that title reads are limited to the first 40 tabs. Real application loading, UIA provider latency and model inference remain separate costs.

Reproduce the performance fixtures:

```powershell
rtk proxy python scripts/benchmark_pointer_delivery.py
rtk proxy python scripts/benchmark_ui_target_binding.py
rtk proxy python scripts/benchmark_desktop_pipeline.py --iterations 30
```

## Regression results

- Python discovery: **396 passed**.
- Node workspaces: **42 passed**; the 15 Control Center tests were rerun after the worker revision update.
- Portable Rust: **44 passed**, with 3 ignored subprocess fixture entry points.
- Native-feature Rust library: **19 passed**, with the opt-in foreground benchmark ignored.
- Next.js production build: passed.
- Rust formatting and strict Clippy across all targets/features: passed.
- Optimized native daemon build: passed, producing `.jarvis/validation-native-control/release/jarvis-daemon.exe` without replacing the active executable.
- Git diff whitespace check: passed.

Reproduce the regression and build checks:

```powershell
rtk proxy python -m unittest discover -s tests -p test_*.py
rtk proxy npm.cmd test
rtk proxy npm.cmd run build
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control --features native --lib
rtk cargo clippy --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control --all-targets --all-features -- -D warnings
rtk cargo fmt --manifest-path daemon/rust/Cargo.toml --check
rtk cargo build --release --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control --features native --bin jarvis-daemon
```

The tracked-file inventory scanned 232 files and 47 local/remote-tracking refs without a fetch or checkout; tracked Python, JSON and TOML syntax checks found no errors. This is an inventory plus critical-path review, not a claim that every historical line was manually audited. The local inventory is `.jarvis/review-inventory-2026-09-23.json`.

## Activation and limits

Use the normal JARVIS runtime restart to load the updated Python worker, dashboard and rebuilt Rust daemon. The active user's applications were not controlled, Full Access was not enabled, and the running daemon was not replaced during validation. Browser tests use isolated local headless fixtures; native boundary tests do not send OS input.

Supervised live qualification is still needed for sustained typing, actual multi-monitor/DPI behavior, arbitrary application workflows and measured emergency-stop latency. External UI state cannot be made atomic with input delivery. Native success means input was delivered; an important mission result still needs independent verification.
