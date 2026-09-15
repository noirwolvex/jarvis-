# TypeScript implementation and local control center

Status: executable reference implementation, validated on Node 22.23.2. This is a deterministic virtual-filesystem mission, not a connected LLM or native desktop agent.

## Run

From the repository root:

```powershell
rtk proxy npm.cmd ci
rtk proxy npm.cmd run db:generate
rtk proxy npm.cmd run dev
```

Open [the local control center](http://127.0.0.1:3000). On non-Windows shells, use `npm` instead of `npm.cmd`. The development command binds loopback. Production-style local preview uses `npm run build` then `npm start`.

`npm run demo` runs the same mission without a browser. Its four nodes observe the virtual workspace, read a virtual brief, write a virtual report, then verify its SHA-256 ev  idence. The title labels the fixed recipe; it is not interpreted as arbitrary instructions. Host files, applications, and cloud models are untouched by this mission. Runtime restart clears its state.

## Ownership and executable API

| Module | Responsibility and actual behavior |
|---|---|
| `packages/contracts` | TypeScript types, AJV validators, 18 named draft-2020-12 schemas plus the bundled document; nested action unions reject extra fields |
| `services/runtime/src/runtime.ts` | `createRuntime`, bounded task queue, per-action admission, cancellation, postcondition verification, commit and finite read recovery |
| `scheduler.ts` | Reject duplicate/cyclic/missing dependencies; bound parallel work; join cancellation before return |
| `governance.ts` | Issuer-owned HMAC capabilities, subject/task/scope/expiry/use binding, minimum evidence freshness, unsupported actions denied |
| `simulation.ts` | Virtual filesystem and fixed recipe; separate observation after execution; exact expected hash verification |
| `events.ts` | Sequence, previous hash, immutable events and retained-window replay; this is process-local storage |
| `memory-models.ts` | Bounded memory, basic secret-pattern redaction, privacy/cost/resource provider selection; no providers registered by default |
| `resources.ts` | Explicit simulation resource fixture, finite frame buffer and pressure recommendations |
| `gateway.ts` | Loopback bearer-authenticated HTTP API and read-only WebSocket stream, origin allowlist, body/client/rate/output bounds |
| `apps/control-center` | Next.js/Tailwind console, local simulation API, measured host RAM/process RSS, graph, event filter/export, memory search and Ctrl+K |

Minimal runnable use (run with `tsx` inside this workspace):

```ts
import { createRuntime } from '@jarvis/runtime';
const runtime = createRuntime({ stepDelayMs: 0 });
const mission = runtime.submitMission('Workspace verification');
const result = await runtime.waitForTask(mission.id);
if (result.status !== 'completed') throw new Error(result.result?.summary);
console.log(runtime.getState().world);
```

The return from `execute` is not completion. The runtime captures another observation, matches the declared predicates to evidence, verifies the result, and only then updates the world projection. Writes are never automatically retried after an ambiguous outcome. A fresh observation with the wrong task or stale/unstable state is rejected regardless of confidence.

## Two reference HTTP surfaces

The **Next.js `/api/control` route** is a same-origin, loopback-only demonstration endpoint. It has Host/Origin/fetch-site checks, a required custom mutation header, a 2 KiB streamed body limit, and a fixed command vocabulary. It has no account login and must not be exposed through a reverse proxy to other users. `GET` returns the UI projection. `POST` accepts `{action:'run', title:'...'}` or one of `pause`, `resume`, `stop`, `reset` without a title. The runtime additionally caps mission admission and retained state.

The **standalone gateway** is separate and owns its own runtime. Set a cryptographically random `JARVIS_GATEWAY_TOKEN` of at least 32 characters in the process environment, then run `npm run dev --workspace @jarvis/runtime`. It listens on `127.0.0.1:4318` by default. Never put this token in a `NEXT_PUBLIC_` variable.

| Method/path | Input | Output |
|---|---|---|
| `GET /health` | Bearer token | Mode and health |
| `GET /state` | Bearer token | Complete reference state |
| `POST /missions` | Bearer token + `{title}` | Accepted task |
| `POST /controls` | Bearer token + `{command}` | Updated state |
| `GET /events?after=N` | Bearer token + event cursor | Retained events/cursor; 409 if the cursor is outside retention |
| WS `/events?after=N` | Authorization during upgrade | Replay followed by live events; client messages are refused |

Browser WebSocket constructors cannot set Authorization headers directly. Production integration requires a server-side bridge or authenticated session-based upgrade; do not work around this with a token in the URL. The Next demo currently polls its own local API once per second. It does not claim to be connected to this separate WS process or to the Rust daemon.

## Bounds and persistence limits

Default task retention is 32 (configuration capped at 128), pending admission is 8, and active mission dispatch is serialized. Each fixed DAG has four nodes and a 60-second mission deadline. Read retries are limited to one; a write gets none. The virtual filesystem holds at most 64 entries / 4 MiB, so a long-running session eventually refuses new reports. Restart creates a clean demonstration session. Default event retention is 2,048 entries; memory is 256 entries with seven-day session retention. This is finite retention, not a durable audit guarantee.

Pause stops admission of the next action and allows the current action to finish verification. Emergency stop synchronously latches cancellation and revokes grants before emitting its event. Reset is refused until active cancellation settles. Reset does not revive prior grants or cancelled tasks. The web control only stops this simulation. Rust has its own independent latch.

The database client/schema and migration hardening are supplied separately. Wiring the runtime transaction/outbox, leases, checkpoint restore, approval services, multi-user authentication, model consensus, native bridge and full data-loss prevention is production integration work. Regex redaction is a tested fallback, not proof that arbitrary secrets or private screenshots are safe to export. Trusted in-process adapters/callbacks can still block their process; hostile plugins require OS isolation.

## Verification and technology references

`npm test` includes runtime adversarial tests and dashboard API/projection tests. `npm run typecheck` checks all workspace source, and `npm run build` compiles the Next control center. Native and database validations have separate runbooks.

Next.js was selected for a typed server/UI boundary and React composition; direct TypeScript source imports with explicit `.ts` extensions support both `tsx` and Next transpilation. TypeScript's relative import rewrite option permits future emitted JavaScript builds. The npm lockfile pins resolved dependencies. Prisma tooling has scoped patched transitive overrides, verified with CLI generation/validation and SQL tests; reassess those overrides when upgrading Prisma.

Primary references checked 2026-09-12: [Next.js installation](https://nextjs.org/docs/app/getting-started/installation), [AJV schema validation](https://ajv.js.org/json-schema.html), [Node.js test runner](https://nodejs.org/api/test.html), [ws authentication example](https://github.com/websockets/ws#client-authentication).
