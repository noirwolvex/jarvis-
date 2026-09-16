# Rust execution foundation

This document describes the initial foundation. For the implemented Python bridge, foreground-bound native input, current protocol extensions and connection verification, use [Rust execution engine integration](rust-engine-integration.md). The original limitations and protocol examples below are historical and do not fully describe the current implementation.

Status: real compilable reference implementation. Default mode is simulation. Windows native feature compilation is verified; live Windows input and Linux compositor behavior have not been exercised. This is not an OS sandbox or a complete desktop controller.

## Build and tests

```powershell
rtk cargo test --locked --manifest-path daemon/rust/Cargo.toml
rtk cargo check --locked --manifest-path daemon/rust/Cargo.toml --features native
rtk cargo fmt --manifest-path daemon/rust/Cargo.toml --check
```

`Cargo.lock` supplies exact dependency resolution. The native feature uses `xcap 0.9.8` and `enigo 0.6.1`; the portable daemon uses Tokio, tokio-rustls/rustls, serde, SHA-256, UUIDs and bounded channels. No invented native APIs or raw model-to-shell endpoint is present.

## Module coverage

| Module | Real behavior | Remaining boundary |
|---|---|---|
| `capture.rs` | `ScreenCapture` trait, synthetic adapter, optional xcap monitor enumeration/capture, DPI/global coordinates, SHA-256, frame count/byte limits, deduplication | Window capture returns unsupported; ROI, cursors and compositor consent qualification remain |
| `input.rs` | Simulation grounding validation; feature-gated Enigo pointer movement, left click and bounded text input primitives | Native primitives are deliberately not dispatchable until foreground/element binding is implemented |
| `ipc.rs` | TLS 1.3 mutual certificates, framing, peer fingerprint, session/sequence/expiry checks, connection limits | Enrollment, key rotation, independent control socket and cross-language production bridge remain |
| `governance.rs` | Startup grants scoped to peer/action/resource, monotonic expiry, emergency latch, bounded event journal | Durable policy issuance, task/action-digest approvals, audit collector remain |
| `dispatcher.rs` | Single execution worker, bounded admission, authorization recheck after queue wait/capture, no automatic native click | Full state/action verification workflow lives in the TS reference |
| `process.rs` | Absolute executable path + digest + exact argv allowlist, clean environment, simultaneous bounded stdout/stderr, timeout/disconnect/stop cancellation | Direct-child termination only; no process-tree, filesystem or network sandbox |
| `resources.rs` | Supplied-measurement admission, stale/invalid denial, pressure modes and recovery hysteresis | OS/GPU sampling and integration with the dispatcher remain |
| `plugin.rs` | Versioned proposal interface with payload/count checks; no handle/capability inheritance | In-process trait is trusted code, not hostile-plugin containment |
| `capture.rs` accessibility | Typed provider interface with explicit unsupported result | Actual Windows UIA/Linux AT-SPI bridges remain |

`verify_changed` only establishes a fresh frame hash difference, not application success. A successful process exit verifies the exit-status predicate only. Neither proves a file saved, a remote request succeeded, or an intended UI transition occurred. Those effects require independently captured semantic postconditions.

## Exact IPC protocol

Transport is loopback TCP with mandatory mutual TLS 1.3. The server binds only a loopback IP, disables early data/session tickets, and authenticates a client certificate against configured roots. Scoped grants bind to the SHA-256 fingerprint of the leaf certificate. Knowing a capability identifier or connecting from localhost is insufficient. Any authenticated client may request status or latch emergency stop; those paths confer no mutation authority.

Each message is a **4-byte unsigned big-endian length**, followed by UTF-8 JSON, with a hard maximum of **65,536 bytes** before payload allocation. On connection the server sends:

```json
{"type":"hello","protocol":1,"session":"<fresh UUID>","max_frame_bytes":65536,"simulation":true}
```

The client echoes the session challenge. Sequence begins at 1 and increases by exactly one. Request expiry must be in the future and within 30 seconds; admission captures a monotonic deadline so queue time and wall-clock rollback do not extend it.

```json
{
  "protocol": 1,
  "session": "<hello session UUID>",
  "seq": 1,
  "expires_at_ms": 1789228800000,
  "request_id": "<unique UUID>",
  "capability_id": "dev-observe",
  "action": { "kind": "capture", "display_id": 0 }
}
```

The timestamp above is illustrative; clients must calculate a new deadline. Valid action variants are `status`, `capture {display_id}`, `click {display_id,frame_id,x,y}`, `run_process {executable_id,args,timeout_ms}`, and `emergency_stop`. Unknown fields, including on empty variants, are rejected. Rust's native envelope is not the TS `Action` schema: a future broker must explicitly translate and bind task/action/evidence identities.

Replies are `result {request_id,ok,data}` or bounded `output {request_id,stream,text}` frames. Capture returns metadata/digest, not pixels; this keeps images out of the control channel. Pixels would need a separately authorized artifact/data transport. Partial framing is never abandoned and reused. Disconnect cancels queued/in-flight client work. Requests expire; retrying a write with another identifier is not made exactly-once by this protocol.

## Run the mTLS example

Use a new private directory outside tracked files. The helper refuses to overwrite any existing directory and generates disposable development identities and simulation grants valid for five minutes.

```powershell
rtk cargo run --manifest-path daemon/rust/Cargo.toml --example dev_pki -- .jarvis-x-pki
rtk cargo run --manifest-path daemon/rust/Cargo.toml --bin jarvis-daemon -- .jarvis-x-pki/config.json
```

In another terminal:

```powershell
rtk proxy python daemon/rust/examples/client.py .jarvis-x-pki status
rtk proxy python daemon/rust/examples/client.py .jarvis-x-pki capture
rtk proxy python daemon/rust/examples/client.py .jarvis-x-pki emergency_stop
```

On Windows, restrict the directory ACL to the operator before using its keys; Unix helper creation uses mode 0700. No test CA belongs in production. Normal startup uses `config.example.json` customized with operator-owned cert paths and narrowly scoped grants; its empty grant/executable lists deny everything except authenticated status/stop.

## Cancellation and bounds

There are at most four connections, 256 messages per connection, a 300-second connection lifetime, five-second TLS handshake timeout, 30-second request idle timeout, 32 dispatch entries and 32 outbound messages per connection. Capture is one blocking operation at a time, at most 32 MiB per image and four / 64 MiB retained frames. A native call that hangs cannot be preempted safely in-process; isolate it in a worker before deployment.

Process rules contain at most 32 exact argument vectors. Each request has at most 32 arguments, 8 KiB aggregate argv, and a 30-second runtime deadline. Combined output is capped at 16 KiB. A slow consumer, output flood, timeout, disconnect or emergency interrupts the direct child and bounds pipe draining. Shells/interpreters are rejected by known names, but a name denylist is not a sandbox: the operator must trust and protect allowlisted binaries against replacement and child-process escape.

Real process execution additionally requires `simulation:false` and `allow_uncontained_processes:true`. Leave that flag false until an OS containment integration is ready. Local Ctrl-C latches cancellation without waiting for a model, database, normal scheduler, or WebSocket. The IPC stop also bypasses the normal dispatch queue. Re-arm requires local daemon restart; no model command resets the latch. A physical kill device and a dedicated saturation-resistant endpoint remain deployment work. Stop cannot undo an already committed external effect.

## Platform limitations and references

Windows must run in the intended user session with monitor DPI and integrity/UIPI constraints respected. Secure desktop is never bypassed. Linux native dependencies and behavior differ between X11 and Wayland; portals can require interactive consent and cannot be silently bypassed. The optional xcap/enigo feature provides actual backend code, but compiled Windows support does not establish tested Linux support.

Primary API references: [xcap](https://docs.rs/xcap/0.9.8/xcap/), [Enigo](https://docs.rs/enigo/0.6.1/enigo/), [Tokio process cancellation](https://docs.rs/tokio/latest/tokio/process/index.html), [rustls client certificate verifier](https://docs.rs/rustls/latest/rustls/server/struct.WebPkiClientVerifier.html), [Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput), [XDG RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html). Versions were checked for the 2026-09-12 baseline.
