# Desktop execution review and performance changes

The subsequent [input performance follow-up](input-performance-2026-09-21.md) records additional mouse, keyboard and UIA changes and their newer validation results. Counts below describe the earlier review pass.

Review window: September 19–21, 2026. Baseline: `e36f19af676f08fc0abf01a66a7644ceba3f781a` on `main`. Changes described here are in the working tree; no branch was merged, reset, deleted or published.

## Outcome and scope

This pass improves the existing Python → authenticated Rust daemon → native input path and the separate browser CDP path. It removes repeated checkpoint work, reduces transport writes, preserves exact browser targets, makes cancellation more responsive, and rejects several forms of misleading completion or stale screen evidence. Existing permission boundaries and explicit Full Access activation remain in place.

The repository inventory covers **222 tracked files and 47 local/remote-tracking branch refs representing 31 distinct commit tips**. Every tracked file is listed with its size, line count and SHA-256 in [the inventory](review-inventory-2026-09-21.json). Python, JSON and TOML files are parsed by that inventory script. The new scripts, regression tests and this report are additional working-tree files, covered by their review and test runs rather than the tracked-file manifest.

This is a broad architecture and critical-path code review plus repository-wide automated validation. It is **not a manual line-by-line certification of every historical branch**, an end-to-end desktop speed benchmark, or a guarantee that arbitrary application tasks are error-free. Historical branches were compared by commit graph and changed paths, without checking out or executing each branch. Remote-tracking refs describe the local repository; no remote fetch was performed.

## Architecture retained

The live hybrid path is Control Center → Python worker/agent → mission parser and orchestrator → permission-gated tool registry → direct integration / browser CDP / UIA / Rust native input / guarded visual fallback. Tool delivery and independently verified outcomes remain distinct. The Python agent retains durable local mission checkpoints; the dashboard receives task graph and execution-backend updates.

The TypeScript simulation/runtime, PostgreSQL schema, and native daemon are also separately testable components. A passing simulation or database test does not establish that all production orchestration is backed by PostgreSQL. The existing simulation defaults and hybrid mode remain compatible.

## Findings fixed

| Area | Failure or cost identified | Change and regression coverage |
|---|---|---|
| Mission persistence | Base and enriched orchestrator updates serialized and fsynced the whole checkpoint twice, publishing intermediate metadata | Coalesce each synchronous state update into one durable checkpoint with complete metadata. Keep action-intent persistence separate and before execution. Test disk state, callbacks, restore and exact fsync counts |
| Verification association | A claim for `step-10` could also match `step-1` | Match whole step identifiers; regression covers the prefix collision |
| New browser tabs | Count polling and tab indexes could select an unrelated concurrently created tab | Create/navigate the exact Playwright `Page` in one runtime command. Retain that object after navigation failure; reuse the selected managed blank placeholder when appropriate |
| Browser selection | Closing a target could silently retarget another page; failed focus could commit a selection | Clear lost or uncertain selections. Commit selection only after focus and metadata succeed. Connectivity no longer depends on a selected tab |
| Browser lifecycle | Repeated failed attachments could leak Playwright drivers; empty attachment created an unsolicited tab | Stop failed/replaced drivers, preserve same-session selection, create tabs only on explicit request; bound the runtime queue to 32 pending commands |
| Browser page reading | The legacy adapter requested `page/current`, which the dispatcher did not implement | Restore the compatible current-page route and cover it through the actual dispatcher |
| Human-verification pages | Legacy click/fill/key operations relied on an earlier challenge observation | Inspect the exact page immediately before input; block if inspection fails or detects a challenge, and check cancellation again after inspection |
| Rust sessions | Long model or user delays could exceed server idle/lifetime limits | Renew the connection before dispatch at 25 seconds idle or 270 seconds lifetime; retain the existing sequence limit and never replay an uncertain mutation |
| IPC throughput | Reply prefix and payload were separate writes; small TCP writes could be delayed | Send one bounded prefix-plus-payload frame and enable TCP_NODELAY on Python, Node and Rust sockets. Test a single writer call and zero writes for oversized output |
| Keyboard compatibility path | Long text used an unbounded event array and fixed post-input sleeps | Use bounded Unicode batches that preserve surrogate pairs; check cancellation and foreground between batches. Remove unconditional post-delivery delay and use exact UIA value polling |
| Keyboard target/cancellation | UIA could choose an ambiguous editor or mutate after stop was already active | Require a unique visible enabled editor; check cancellation before and after blocking discovery/focus. Uncertain or cancelled writes never enter clipboard/native fallback |
| Partial keyboard delivery | A partial send could leave input state uncertain | Attempt key-up cleanup only, then report uncertainty without retyping. Tests cover partial sends, focus changes and cancellation between batches |
| Emergency stop | The Python client held its execution lock while waiting for a Rust reply, delaying stop behind that reply | Send stop once over a separate authenticated control connection and close it in `finally`. Threaded regression proves dispatch while the execution lock is held |
| Screen precision | Similar resized pixels could hide changed monitor geometry; slow encoding refreshed an old frame's age | Validate capture size and virtual origin, reject geometry/focus changes during capture, and timestamp freshness at capture rather than encoding completion |
| Application discovery | Concurrent invalidation could race LRU updates or refill the cache with stale results | Protect bookkeeping/installation with an RLock and generation counter; keep slow discovery outside the lock. Test clear during an in-flight lookup and independent query progress |
| Completion reporting | Cyclic graphs or completed results with unfinished/explicitly failed nodes could be accepted | Reject dependency cycles, unfinished completion graphs and contradictory FAILED verification; preserve older payload compatibility when optional metadata is absent |
| Diagnostic worker | Blocking `readline()` defeated the response deadline; a full stderr pipe could stall the worker | Add bounded stdout queuing and concurrent bounded stderr draining. Synthetic-process tests cover incomplete lines, timeout, malformed messages, noisy stderr and cleanup |
| Native boundaries | Foreground title length counted UTF-8 bytes instead of characters; a scroll cast failed strict lint | Match Python's Unicode title limit and use unsigned magnitude for bounded scrolling. Arabic boundary and chunk tests pass |

## Measured performance

`scripts/benchmark_mission_checkpoints.py` runs a 50-step synthetic ordered mission, with three repetitions, using temporary durable checkpoints and restore assertions. It performs no device actions and makes no model calls. The baseline was measured before the checkpoint change on the same host.

| Metric | Baseline | Updated |
|---|---:|---:|
| Total checkpoint/state bookkeeping, median of three runs | 4,680.752 ms | 2,582.175 ms |
| Durable publications during 50 steps | 450 | 250 |
| Total time across the three individual runs | 4,734.960 / 4,680.752 / 4,605.804 ms | 2,589.482 / 2,579.022 / 2,582.175 ms |

The measured bookkeeping reduction is **44.8%**. The benchmark excludes plan setup and measures each running → intent → trace → verification → completion sequence. It does not imply that whole desktop missions are 44.8% faster. Browser round-trip reduction, TCP behavior and keyboard changes have targeted correctness tests; no new end-to-end latency percentage is claimed for them.

Reproduce the updated benchmark with:

```powershell
rtk proxy python scripts/benchmark_mission_checkpoints.py --steps 50 --repeats 3
```

## Component coverage

| Component | Review and validation performed | Limits |
|---|---|---|
| Python planning, execution and memory | Critical-path reads of agent, fast mission, workflow, orchestrator, permissions, tools, progress and memory; full Python suite | Supported deterministic missions and failure fixtures do not cover every natural-language request |
| Mouse, keyboard, UIA and observation | Reviewed foreground/geometry guards, native bridge, fallback delivery, semantic controls and live capture; mocked boundary tests plus native compilation | No real mouse/keyboard input was sent in this review pass |
| Browser | CDP lifecycle, target ownership, tab reuse, legacy/semantic operations and recovery; isolated local browser fixtures and mocks | User browser sessions and external application workflows were not exercised |
| Rust daemon | Input, IPC, dispatcher/governance boundaries and existing motion/capture integration; portable tests, native library tests, strict Clippy and optimized build | Native hardware behavior, secure desktop and worst-case stop latency require live qualification |
| Control Center and worker bridge | Worker revision, access/cancellation, backend visibility, result/graph validation; Node tests, TypeScript checks and production build | The built artifact does not prove a currently running server has loaded the changes |
| TypeScript runtime/contracts | Scheduler, verification, recovery, gateway and schema tests | The runtime's simulation adapters are not production desktop adapters |
| Database | Existing SQL/PLpgSQL migration and invariant tests through PGlite; Prisma validation and typechecks | No deployed PostgreSQL multi-client/crash/backup qualification |
| Launch and diagnostics | Bootstrap source and fixture tests, bounded worker protocol regression tests | The live qualification command was not run; it intentionally controls a test window |
| Configuration, docs and repository | Tracked-file inventory, syntax parsing, CI/manifest inspection, dependency audit and repository secret scanner | Scanner has its documented patterns; this is not a comprehensive dependency or secret-security audit |

## Branch review

The inventory records every one of the 47 refs with commit ID, commits ahead/behind the baseline and all changed paths. Refs sharing a tip were deduplicated for comparison work. No historical branch was merged merely because its name suggests a newer version.

Several long-lived feature/hardening branches diverge substantially from `main`; their changes need individual integration review before adoption. `origin/feature/native-input-throughput-v9` has a different commit history but the same tree as the baseline (zero changed paths). This is why reapplying a similarly named branch would not establish an additional performance improvement.

## Validation results

The final regression run reports:

| Check | Result |
|---|---|
| Python unittest discovery | **354 passed** |
| Node workspace tests | **42 passed**: Control Center 15, runtime 21, database harness 6 |
| Portable Rust tests | **33 passed**, 3 ignored subprocess fixture entry points |
| Rust native library tests | **12 passed**; no live input |
| Rust Clippy, all targets/all features, warnings denied | Passed |
| Optimized Rust native daemon build with locked dependencies | Passed |
| Next.js production build | Passed |
| Workspace TypeScript typecheck | Passed |
| Prisma schema validation | Passed |
| npm dependency audit | Zero vulnerabilities reported at validation time |
| Repository secret scanner | Passed its configured checks |
| Tracked Python/JSON/TOML parsing | No syntax errors |

Commands used include:

```powershell
rtk proxy python -m unittest discover -s tests -p test_*.py
rtk proxy npm.cmd test
rtk proxy npm.cmd run typecheck
rtk proxy npm.cmd run build
rtk proxy npm.cmd run db:validate
rtk proxy npm.cmd audit --audit-level=high
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --target-dir .jarvis/validation-native-control
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml --features native --lib
rtk cargo clippy --locked --manifest-path daemon/rust/Cargo.toml --all-targets --all-features -- -D warnings
rtk cargo build --release --locked --manifest-path daemon/rust/Cargo.toml --features native --bin jarvis-daemon
rtk proxy python scripts/scan_repository_secrets.py
rtk proxy python scripts/review_repository_inventory.py --output docs/review-inventory-2026-09-21.json
```

The alternate portable-test target directory avoids replacing a binary potentially held by an existing daemon. Test output that names a stopped fixture daemon comes from mocked bootstrap tests; this review did not stop or restart an active JARVIS daemon.

## Remaining qualification and known limitations

1. **Emergency connection saturation:** the separate stop connection avoids the Python execution lock, but the daemon admits at most four connections. A stop connection can still be rejected when all slots are occupied. Dedicated stop capacity and measured end-to-end stop latency remain required before claiming a guaranteed immediate stop under resource saturation.
2. **Live device qualification:** multi-monitor/DPI behavior, foreground races, drag cancellation, Unicode/IME behavior, sustained typing, UI changes and arbitrary application missions still need supervised native tests. Passing fixtures cannot guarantee zero errors in external applications.
3. **Long-session production qualification:** durable local checkpoints are covered; deployed PostgreSQL reconciliation, sustained load, process crashes, memory/CPU budgets and multi-hour live workflows need dedicated integration/load runs.
4. **Environment limits:** UAC/secure desktop, privileged applications and unsupported controls can legitimately refuse automation. Uncertain outcomes must remain unresolved until observation supplies evidence.
5. **Activation:** the release binary and dashboard have been built, but active processes were not restarted and Full Access was not enabled. Use the normal runtime restart to load these changes. Live qualification is a separate operation that deliberately moves the pointer and types into its test window.

These limits are part of the release assessment. The reviewed patch is validated for the stated automated coverage; the entire platform is not being certified as error-free or fully production-qualified.
