# Rust execution engine integration

JARVIS Full Access now supports a hybrid engine architecture: the Python agent remains the planner, model/tool orchestrator, browser layer, semantic UI layer and high-resolution vision path, while the Rust daemon can become the authoritative low-level native input executor.

## Execution model

The integrated path is:

1. The Full Access Python agent plans the mission and obtains semantic or visual evidence.
2. `core.rust_engine` selects the native backend using `JARVIS_NATIVE_ENGINE`.
3. When Rust is selected, JARVIS connects only to the configured loopback daemon using mutual TLS 1.3 and ALPN `jarvis-execution/1`.
4. Before a Rust click or text mutation, the client requests daemon status, resolves the target display, captures a fresh Rust frame and binds the action to the exact foreground process/title.
5. The Rust daemon rechecks capability scope, request expiry, frame freshness, display bounds, foreground identity and the emergency latch immediately before native execution.
6. The Python orchestrator still requires independent post-action evidence before another mutation or final mission completion.

Rust currently owns the supported `desktop_click`, `desktop_type` and left-button `desktop_click_button` primitives when selected. High-resolution `screen_observe`, browser/CDP operations, UI Automation, application discovery, file tools and planning remain in Python because they need richer model-facing semantics than the daemon's bounded IPC preview provides.

## Backend modes

Set `JARVIS_NATIVE_ENGINE` to one of:

- `auto` — preferred default. Use Rust only when its TLS identity, daemon state, capture/input mode and required capability are available before dispatch. Otherwise use the existing Python input path. A transport error after a Rust mutation may have started is treated as an uncertain outcome and is never replayed automatically through Python.
- `python` — force the existing Python native-input implementation.
- `rust` — require Rust. Missing configuration, capabilities, capture or daemon availability fails closed rather than bypassing the daemon.

This selection affects Full Access native desktop mutations. It does not change the separate `JARVIS_CONTROL_MODE=native` Control Center path.

## Development setup on Windows

Build the daemon with its native adapters:

```powershell
cargo build --manifest-path daemon/rust/Cargo.toml --features native
```

Generate disposable development mTLS material and a native config:

```powershell
cargo run --manifest-path daemon/rust/Cargo.toml --example dev_pki -- daemon/rust/local-pki --native
```

The development generator refuses to overwrite an existing directory. Treat its keys as local test credentials, restrict the directory ACL to your Windows account, and regenerate them instead of reusing them as production credentials.

Start the daemon:

```powershell
cargo run --manifest-path daemon/rust/Cargo.toml --features native -- daemon/rust/local-pki/config.json
```

For the generated single-display development config, set:

```text
JARVIS_NATIVE_ENGINE=rust
JARVIS_DAEMON_HOST=127.0.0.1
JARVIS_DAEMON_PORT=7443
JARVIS_DAEMON_SERVER_NAME=localhost
JARVIS_DAEMON_CA=daemon/rust/local-pki/ca.pem
JARVIS_DAEMON_CLIENT_CERT=daemon/rust/local-pki/client.pem
JARVIS_DAEMON_CLIENT_KEY=daemon/rust/local-pki/client-key.pem
JARVIS_DAEMON_OBSERVE_CAPABILITY=dev-observe
JARVIS_DAEMON_INPUT_CAPABILITY=dev-input
```

Then start the normal Full Access dashboard with `JARVIS_CONTROL_MODE=hybrid`.

## Multi-monitor capability mapping

Rust capabilities are scoped to a specific display id. The daemon status reply now includes its capture display inventory so the Python engine can select the display containing a requested desktop coordinate, including displays with negative virtual origins.

For more than one display, grant one observe and one input capability per Rust display id and configure JSON maps, for example:

```text
JARVIS_DAEMON_OBSERVE_CAPABILITIES_JSON={"0":"observe-0","1":"observe-1"}
JARVIS_DAEMON_INPUT_CAPABILITIES_JSON={"0":"input-0","1":"input-1"}
```

The legacy single-value capability variables remain supported for display `0`.

## Safety and failure semantics

The integration deliberately avoids transparent replay after uncertain mutations. A socket failure can occur after some bytes have reached the daemon, so any transport failure after a mutating send begins is reported as an uncertain action. JARVIS must re-observe the desktop and recover through the normal verification path instead of trying the same action through Python.

Emergency stop is propagated to both execution planes. The Full Access worker sets its local cancellation latch, releases Python-held synthetic keys/buttons, terminates tracked process trees and also sends the Rust daemon's independent `emergency_stop` action when configured. The Rust emergency latch requires a daemon restart before native work resumes.

Rust capability grants remain peer-certificate-bound and scope-bound. Their maximum lifetime is now 30 minutes so a grant can cover the dashboard's maximum mission duration; each IPC request still has its own short expiry and sequence/replay checks.

The daemon still does not bypass Windows secure desktop, UAC, elevated-window isolation or account permissions. Rust execution is not an OS sandbox. Full Access terminal commands remain a separate permission path and are not silently converted into unrestricted Rust process execution.

## Validation gates

The Rust mTLS tests require Python 3.11+ on PATH (`JARVIS_TEST_PYTHON` can select another interpreter). They run a real Python/OpenSSL TLS 1.3 status exchange with strict certificate validation. CI installs Python 3.14 for this check.

Development certificates generated before the certificate-chain fix can be rejected by Python/OpenSSL. Generate new development material in a new directory with the rebuilt `dev_pki` example; the fix gives the CA/server/client distinct subject names and leaf authority-key identifiers. Existing certificates are not modified, and certificate verification remains enabled.

Before merging changes to this integration, run at minimum:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
cargo fmt --manifest-path daemon/rust/Cargo.toml --check
cargo test --locked --manifest-path daemon/rust/Cargo.toml
cargo check --locked --manifest-path daemon/rust/Cargo.toml --features native
npm run typecheck
npm test
npm run build
```

CI runs the portable Rust test suite on Windows and Linux and compiles the native Rust adapters on Windows. Live native desktop qualification still requires supervised Windows testing because CI must not click or type into a runner desktop.

## Local connection verification — September 16, 2026

The JARVIS X hybrid route calls the Node Full Access bridge, which starts `core.full_access_worker`. Its agent factory registers `core.rust_engine` handlers for `desktop_click`, `desktop_type` and `desktop_click_button`. Other operations retain their Python/semantic backends.

Verified locally:

- A real Node child-process exchange received the Python worker's `ready` and successful `ping` replies without loading a model or executing a mission.
- The real Python `RustDaemonClient` connected to a disposable Rust simulation daemon with mutual TLS 1.3 and ALPN `jarvis-execution/1`. Status and synthetic capture round-trips succeeded.
- The actual dashboard agent factory registered all three Rust handlers, and its `native_engine_status` tool reached that daemon. It correctly refused to classify a simulation daemon as ready for native input.
- Six Python Rust-adapter tests, thirteen control-center tests, and twenty Rust tests passed (three Rust tests remain ignored). Native-feature compilation and Rust formatting passed. The CI workflow was updated but was not executed remotely.

The configured runtime is a separate finding: the local environment returned `mode=auto`, `backend=python`, `rust_configured=false`. No service was listening on the default Rust port 7443. The dashboard initially returned HTTP 200 with `mode=hybrid` and `bridge.runtime=python-agent-full-access`; by the final check it was no longer listening on port 3000. No persistent service or Full Access setting was changed by this verification. The disposable simulation daemon and test worker were stopped after testing.

Therefore the integration is implemented and its process/protocol boundaries are verified, but the normal runtime was **not using Rust** at the final check. Activating that route still requires valid TLS paths/capability mappings and a running native daemon, followed by starting the hybrid dashboard with those settings. This verification did not perform native mouse/keyboard input or an end-to-end model-driven mission.

## Execution evidence and semantic control — September 19, 2026

The current Python registry overlays Rust atomic click, button click, double click, pointer movement, drag, scrolling, typing, key press and shortcut tools. Stateful mouse/key holds remain unavailable in strict Rust mode; compatibility mode retains their Python implementations. A configured backend is not evidence that any particular action used it.

Tool results now retain adapter-reported execution evidence while preserving the existing string interface. Rust records dispatch at the authenticated socket boundary, CDP carries the calling execution context onto its browser thread, and UIA, Python compatibility input, direct process/filesystem tools and screen capture report their own operations. Uninstrumented handlers report `unreported`; permission/schema rejection reports `not_dispatched`. Backend evidence records attempted execution, not successful application outcomes. Checkpoints retain these operations; subsequent observation does not replace the mutation's backend in the task graph.

UIA tools accept deterministic selectors for visible ordinals, selected/focused controls, exact parent scopes and nearest adjacent siblings. `ui_resolve` exposes fresh detached identity evidence without focusing or mutating the window. Actions resolve again and check window ownership, foreground, control identity and geometry. Ambiguity, incomplete trees, stale targets and changed drafts fail closed. Parent reads are reused within each selector observation. Both individual tools and ordered batches support selectors.

The native semantic keyboard path rechecks the editor and the Rust foreground HWND after preflight. It requires an empty composer for native text; existing drafts or multiline text require semantic Value-pattern writes through `ui_type`. This avoids guessing the caret position or accidentally submitting a newline. Completion from delivery alone is rejected; fresh verified readback or an independent verification record is required.

The Python worker streams bounded, redacted task graphs while a mission runs. The Control Center validates graph identities, dependencies, statuses and field sizes, displays actual backends and verification/result details, and ignores updates after revocation. Identical snapshots are deduplicated. A progress transport failure cannot replay an action or invalidate its outcome. Worker revision 6 makes new missions replace older worker code through the existing controlled lifecycle.

Validation in this checkout: 306 Python tests, 42 Node tests across the Control Center/runtime/database, 20 Rust tests (3 ignored), all workspace typechecks, native Rust compilation and the production dashboard build passed. Tests use fixture controls and transports; no real user application input was sent.

A read-only engine check using this shell plus the repository `.env` returned `mode=auto`, `backend=python`, `rust_configured=false`. This does not inspect environment overrides injected into an already-running launcher/dashboard process. Live Rust input and arbitrary multi-application mission reliability remain unqualified by this check. The strict runtime launcher can provision session configuration; no service, certificate, Full Access setting or user desktop state was changed during this verification.
