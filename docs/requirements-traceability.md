# Requirement and implementation map

Both pasted specifications are reconciled in the 28-section [architecture](architecture.md). This map separates design coverage from executed code and remaining integrations. Section numbers below refer to the expanded JARVIS X specification.

| Requirement group | Specification sections | Design / code | Delivery status |
|---|---|---|---|
| Five planes, invariants and trust boundaries | 0–3, 54, 58, 78 | Architecture §§1–3, 11; runtime/governance; daemon policy | Design plus bounded reference enforcement; no formal proof or certification |
| Specialized agents and consensus | 4–5, 64–66 | Architecture §4; ModelInvocation role schema | Typed roles and architecture; no autonomous multi-model implementation |
| World state, hashing and freshness | 6, 8–11, 46–47 | Architecture §§5, 7, 9; observation schemas, runtime commit, frame IDs/digests | Simulation implementation; real semantic fusion pending |
| Twin and simulation | 7, 25, 48 | Architecture §6; fixed virtual workspace recipe | Real deterministic simulation; predictive twin and branching replay are experimental targets |
| Capture/OCR/vision/browser/native semantics | 12–15, 49, 63 | Architecture §7; xcap adapter, accessibility trait | Capture feature compiles; OCR, UIA/AT-SPI, browser adapter and health integration not connected |
| Execution/input/processes/terminal | 16, 19 | Rust modules and runbook; strict Action union | Real mTLS/dispatcher/process foundation; optional native pointer/click/text examples; no raw-shell IPC |
| Capability/policy/sandbox/plugin | 17–18, 36, 51–52, 59–60 | Architecture §§11–13, 22; issuer, policy, proposal trait, plugin manifest | Reference checks; OS sandbox and full secret/egress isolation remain |
| Recovery/rollback/checkpoint/crash consistency | 20–24, 61–62 | Architecture §§9–10, 19; runtime retries; DB snapshots/outbox/checkpoints | Bounded read recovery implemented; durable rollback/restart reconciliation is a target |
| DAG/scheduler/budgets | 26–28, 55–57 | Architecture §§14, 17, 23; DAG scheduler and resource governors | Executable bounded scheduling; full production CPU/GPU quotas not implemented |
| Model routing/privacy/fallback/cost | 29–32 | Architecture §16; ModelRouter, ModelInvocation, resource schema | Selection logic implemented; no model endpoints or actual inference |
| Memory/decay/knowledge graph | 33–35, 64–65 | Architecture §5/§16; bounded MemoryService and Prisma memory/embeddings | Session memory implemented; graph and durable retrieval optional |
| Emergency path | 37 | Architecture §11; Rust independent latch/Ctrl-C, TS stop latch | Cancellation tested; no physical kill device or hard real-time guarantee |
| Observability/event history | 38–39 | Architecture §19; event hash chain, gateway replay, UI ledger | Process-local evidence and export; durable audit collector not connected |
| Premium control center and palette | 40–43 | Architecture §18; apps/control-center | Runnable responsive dark/light console, controls, graph, search, export and Ctrl+K |
| Strict JSON contracts | 44–45, 68 | packages/contracts/schemas and AJV validators | 18 named strict schemas plus bundle. Wire action surface is narrower than target DB enums |
| Database | 50, 69 | database/schema.prisma, two SQL migrations, generated client helper | 42 relational models; validation/generation and embedded PostgreSQL SQL tests |
| Testing/failure/security matrices | 53–54, 72–73 | Architecture §§21–22, 25; runtime/Rust/API/SQL tests and CI | Local checks executed; Linux/OS security/chaos/hardware qualification outstanding |
| IPC comparison and selection | 70 | Architecture §12; docs/rust-implementation.md | mTLS TCP reference implemented; native pipe/UDS and shared memory discussed as alternatives |
| Resource allocation | 71 | Architecture §§17, 23; UI resource view | 16 GiB RAM/8 GiB VRAM allocations; all latency/utilization numbers marked as objectives |
| Repository and roadmap | 74–77 | Architecture §§24, 26–28; roadmap/runbooks/README | Code, detailed design and acceptance gates delivered |

## Important distinctions

- TypeScript privacy currently collapses the five target classes to public/internal/restricted. The database retains all five classes. Add an explicit conservative mapping before a native/cloud integration; never downgrade private or sensitive content by default.
- The UI's virtual screen is synthetic. Process RSS and host RAM are actual local measurements; GPU/VRAM remains unavailable. Model tier panels show architecture, not connected providers.
- The browser API, standalone WS gateway and Rust daemon are separate reference surfaces. Only the browser and its local TS simulation are currently integrated.
- JSON validity is not authorization, model agreement is not verification, and a changed screenshot or process exit is not proof of the intended application outcome.
- Every production readiness item in architecture §28 is an acceptance gate. The tested foundation is deliberately labeled a reference implementation.
