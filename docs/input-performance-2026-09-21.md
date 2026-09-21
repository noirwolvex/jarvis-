# Mouse, keyboard and interface execution follow-up

Date: September 21, 2026. This follows the [project review](project-review-2026-09-21.md) and preserves its uncommitted changes. The focus is the input delivery and semantic-target hot paths, rather than another architectural rewrite.

## Concrete changes

- **Faster Python clicks:** route the legacy `desktop_click` tool through the guarded click implementation. Disable PyAutoGUI's per-call post-action pause and remove the old unconditional 60 ms interval after a single click. Repeated clicks have one cancellable 45 ms gap between pairs, with no final gap.
- **Precise repeat clicks:** verify foreground and cursor location before each pair. Stop if the pointer moved; never reposition it and repeat an uncertain click. A successful delivery remains `DELIVERED`, requiring application outcome verification.
- **Less redundant movement:** skip repeated identical trajectory points, return immediately for an already-reached destination and independently read the final position. A refused or clipped move cannot report that it reached the target.
- **Stronger Rust pointer targeting:** resolve the top-level window under the physical pixel before clicking or starting a drag. Recheck the pointer, target window and foreground before each click. Scroll batches remain bound to the original pointer location and foreground window. An overlay, another window or pointer drift stops delivery.
- **Cheaper Rust keyboard/motion guards:** compare fresh HWND/PID directly without retrieving or allocating a title on every check. Titles remain available in diagnostic status. Retain cancellation and foreground checks within movement and Unicode batches; remove adjacent redundant checks where no input occurs between them.
- **Bounded typing allocations:** reserve the full existing Unicode batch event capacity up front, avoiding growth during the first full batch. Unicode chunk limits and surrogate-pair boundaries are unchanged.
- **Cheaper UIA binding:** read the window's PID directly instead of collecting an entire unused window metadata record, and reuse the control runtime ID already read in its metadata. Control identity, HWND, PID, generation, rectangle, enabled/visible state and ownership validation remain enforced. A changed process still invalidates the binding.
- **Worker refresh:** increment the worker revision to 8 so a loaded updated dashboard replaces an older idle Python worker through its existing controlled lifecycle.

The physical hit test uses Windows' [WindowFromPhysicalPoint](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfromphysicalpoint) and the parent-chain root from [GetAncestor / GA_ROOT](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getancestor). An owned popup is not automatically treated as the original window. Resolve and focus the intended window before native input; use semantic activation where available.

## Measurements and their limits

### Installed Python click wrappers with OS delivery stubbed out

`scripts/benchmark_pointer_delivery.py` exercises the actual installed PyAutoGUI click function and the JARVIS wrappers. The entire platform input backend, pointer movement and screenshot logging are replaced by fixtures. The cursor and foreground are fixtures. There are **zero native input calls**. PyAutoGUI's configurable pause is set to 100 ms to reproduce its usual wrapper cost. Each value is the median of five runs.

| Path | Median overhead |
|---|---:|
| Previous legacy click | 100.446 ms |
| Previous extended single click | 60.514 ms |
| Updated guarded click | 0.089 ms |
| Updated legacy route | 0.078 ms |

These measurements demonstrate removal of fixed software waits, not application response time or live click latency. Where a caller already changed PyAutoGUI's global pause, the legacy-path savings will differ. Foreground changes, UI verification and application loading still take real time.

### UIA provider reads

`scripts/benchmark_ui_target_binding.py` instruments detached provider fixtures and compares the previous implementation with the updated implementation. The resulting binding records are equal.

| Work per binding | Before | After |
|---|---:|---:|
| Provider property/method reads | 22 | 12 |

This is **45.5% fewer provider reads in this binding routine**, not a measured 45.5% reduction in real UIA latency. The identity and geometry evidence are preserved.

### Native foreground read microbenchmark

An opt-in release-mode Rust test reads the current foreground without moving the mouse, typing, capturing pixels or printing window titles. Seven samples each perform 10,000 checks per variant. The median cost on this host was **98 ns** for full title-bearing diagnostic binding and **19 ns** for the identity-only guard. This is a small isolated read-path measurement; it is not whole-action or whole-mission throughput. The full diagnostic path also uses the new stable HWND/PID read, so this comparison should not be described as a complete before/after executable benchmark.

## Validation

- **366 Python tests passed**, including ten new virtual-pointer delivery regressions and two UIA binding regressions.
- **37 portable Rust tests passed**; three subprocess fixture entry points remain ignored by the normal harness.
- **16 native-feature library tests passed**; the opt-in foreground benchmark is ignored by default and was run separately in release mode.
- Strict Clippy across all targets/features passed with warnings denied.
- Control Center tests and production build validate the worker revision update.
- Updated optimized native daemon built in `.jarvis/validation-native-control/release/jarvis-daemon.exe` because the regular release executable was locked by Windows. The active daemon was not stopped or replaced.

Coverage includes missing/covered pointer targets, drift between clicks, failure without replay, exact final pointer position, cancellation between clicks, no-op movement, unnecessary trajectory samples, equivalent UIA evidence and process-identity changes. Rust target-validation tests use pure fixtures. They do not send native input.

Reproduce the measurements and relevant checks:

```powershell
rtk proxy python scripts/benchmark_pointer_delivery.py
rtk proxy python scripts/benchmark_ui_target_binding.py
rtk proxy cargo test --release --locked --manifest-path daemon/rust/Cargo.toml --features native --lib benchmark_foreground_guards_read_only -- --ignored --nocapture
rtk proxy python -m unittest discover -s tests -p test_*.py
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --features native --lib
rtk cargo clippy --locked --manifest-path daemon/rust/Cargo.toml --all-targets --all-features -- -D warnings
rtk cargo build --release --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control --features native --bin jarvis-daemon
```

## Deployment and remaining qualification

The Python/Rust source changes and validation binary are ready for review. Restart through the normal JARVIS runtime to rebuild/load the regular release executable and refresh the dashboard/worker. This pass did not enable Full Access, restart an active daemon, or send input to the user's applications.

Real multi-monitor and DPI targeting, sustained native typing, overlays, drag/drop destinations, asynchronous UIA providers and end-to-end stop latency still require supervised live qualification. A change can occur between a final check and OS input delivery; the implementation narrows that interval but cannot make external UI state atomic. The earlier emergency-connection saturation limitation also remains. These improvements reduce known delays and error paths; they are not a claim of zero errors in arbitrary desktop missions.
