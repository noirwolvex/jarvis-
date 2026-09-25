# Runtime startup repair — September 25, 2026

## Failure

A production runtime was still listening on port 3000 when `npm run dev` started. The old launcher bootstrapped Rust before checking the dashboard address, replacing the first instance's daemon. Its HTTP readiness probe then accepted a response from that first dashboard and printed `JARVIS_DASHBOARD_READY`, although the new Next.js process subsequently failed with `EADDRINUSE`. The surviving dashboard had lost its original Rust connection.

The separately reported `'C:\Program' is not recognized` line occurred before the pasted npm command. Neither the current-user nor machine Command Processor AutoRun setting was configured during inspection, so its original source was not established. The managed dashboard launch no longer invokes `npm.cmd` through a shell: it passes the Node executable and installed Next.js CLI as separate process arguments, including paths with spaces.

## Changes

- Hold an OS-backed exclusive repository runtime lock through startup, execution, and cleanup. `dev`, `start`, and `verify-live` share the lock. A crashed launcher releases it automatically; the lock file is never deleted while other processes might have it open.
- Check whether port 3000 is available before provisioning certificates, building Rust, or stopping a stale daemon. Reject a conflicting server without modifying it.
- Validate the Node/Next.js installation before bootstrap and launch the CLI directly in the Control Center workspace.
- Accept readiness only when the launched Next.js process or its descendant owns the listener, an HTTP request returns 200, and process/listener ownership still holds afterward.
- Clean up only the owned dashboard process tree, including the dev-server child, on shutdown or startup failure. Always clean up the owned daemon afterward.
- Report startup failures as concise actionable errors instead of a traceback or premature readiness message.

## Validation

- 453 Python tests passed, including 15 runtime-bootstrap tests. New coverage includes occupied ports, concurrent launch rejection, lock release after a real child-process crash, paths with spaces, foreign HTTP responses, listener replacement during readiness, direct startup, and owned process-tree cleanup.
- The updated `npm run dev` was run against the old listener: it exited before Rust bootstrap and preserved that listener.
- The old background production process tree was identified, confirmed idle, and retired. The user's subsequent dev launch started successfully with a direct Node/Next.js process tree.
- Read-only checks against that running instance confirmed an `IDLE` hybrid dashboard and an authenticated Rust connection with `rust_input_ready=true`.
- Duplicate `dev`, `start`, and `verify-live` attempts were blocked by the runtime lock. Dashboard and Rust listener PIDs were unchanged afterward, and Rust remained healthy.

The user's dev instance was left running. No desktop input or chat messages were sent during this startup repair. Actual Ctrl+C shutdown of that instance was not performed; cleanup is covered by focused regression tests.
