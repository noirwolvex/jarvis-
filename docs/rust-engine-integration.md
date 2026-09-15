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
