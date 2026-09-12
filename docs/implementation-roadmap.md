# Staged engineering roadmap

This is an acceptance-gated roadmap, not a claim that reference code completes production stages. The architectural baseline and rationale are in [architecture.md](architecture.md). Scope is one user, one interactive desktop, Windows first.

| Stage | Owner | Concrete deliverable | Required acceptance evidence | Current delivery |
|---:|---|---|---|---|
| 0 | Architecture/security | Versioned contracts, threat model, capabilities and risk matrix | Invalid/ambiguous input rejected; compatibility fixtures reviewed | Design + 18 JSON contract validators implemented |
| 1 | Execution | Rust daemon and authenticated local protocol | Untrusted peer, replay, expired request and stopped queue tests; rotation/reconnect drill | mTLS reference and boundary tests; no TS-native bridge |
| 2 | Perception | Bounded capture and display coordinate mapping | Actual Windows/Linux monitor, DPI, permission and cursor tests; memory peak profile | xcap adapter compiles on Windows; real sessions not qualified |
| 3 | Perception/adapters | UIA/AT-SPI/DOM grounding and temporal fusion | Duplicate labels, focus changes, dialogs, navigation, stale frame refusal | Interfaces/design only; native input blocked |
| 4 | Execution/security | Typed actions bound to exact semantic target and grant | Foreground/target race tests, OS containment, clean child tree after cancellation | Simulation dispatcher; native low-level input examples; process allowlist |
| 5 | Verification | Independent semantic postcondition engine | False success and missing/contradictory evidence cannot commit | Deterministic simulation checks and negative tests |
| 6 | Runtime/database | Durable task DAG, fences, leases, transactional outbox | Concurrent workers, crash at action-start/commit, duplicate delivery reconciliation | Bounded in-memory DAG; separate Prisma schema and SQL tests |
| 7 | Inference | Real local/cloud providers, measured routing | Private/restricted egress refusal, fit/latency/cost/accuracy qualification, outage fallback | Executable provider selection; no configured providers |
| 8 | Memory | Durable provenance, retention, freshness and scoped retrieval | No cross-user recall, deletion and secret redaction corpus | Bounded reference memory and DB model |
| 9 | Recovery | Formal bounded recovery and compensation engine | Every failure-matrix case injected; no blind retry of ambiguous writes | Read retries, write escalation and emergency cancellation tests |
| 10 | Product | Authenticated operator control center | Accessibility, stale state, scope-bound approval, evidence/export permissions | Responsive Next simulation console and UI/API checks |
| 11 | Reliability | Deterministic event reduction/replay and branch inspection | Replay never invokes tools; versioned reducer outputs match fixtures | Ordered retained events and hash checking; no full world time travel |
| 12 | Security | Native sandboxes, isolated plugins, key rotation, independent stop | Adversarial assessment, stop saturation/latency, signed plugin and update rollback | Boundary tests + architecture; OS/hardware validation outstanding |
| 13 | Operations | Signed rollout, backup/restore, canary devices and rollback | Restore drills, documented SLOs, supported-platform matrix, incident owner | Local PostgreSQL compose and CI definitions only |

## First live milestones

1. **Read-only observer:** connect authenticated Rust capture and native semantic trees to a redacted evidence projection. No input capability is issued. Measure latency, resource peaks, staleness and supported sessions.
2. **Narrow reversible workflow:** an operator-owned test directory, handle-safe file access, backup/version, explicit expected file hash, verified commit and rollback. Persist request intent before execution; reconcile unknown outcome after restart.
3. **Semantic desktop action:** a uniquely identified element in a foreground-owned window, fresh state lease and post-action semantic observation. Stop if identity or focus changes. Qualify one app before adding another.
4. **External submission:** exact recipient/effect digest bound to approval, provider idempotency where available and reconciliation on lost acknowledgement. Never infer failure from timeout or automatically resubmit an unknown outcome.

## Migration from the existing Python application

Keep the Python/PySide entry point operational. Introduce a typed broker behind its tool registry, then route one read-only capability through the new daemon. Compare observed results before enabling writes. Import only non-secret memory with provenance and explicit schema conversion. Do not copy legacy blanket permissions into new grants. Preserve Python trace history as legacy evidence with its original provenance, not as verified JARVIS X events.

The new TypeScript runtime and the Python runtime currently have independent state. They are not interchangeable orchestrators and are not connected merely because they share a repository. The controlled migration needs an explicit ownership handoff for each task/device and a rollback to the prior tool route.

## Release decisions

Each stage exit records build/contract hashes, environment and OS versions, failing as well as passing fixtures, resource peaks, rollback evidence and unresolved risks. The owner of security and the owner of execution review changes to authorization or native privilege. Performance targets remain objectives until measured on the target 16 GiB/8 GiB hardware.
