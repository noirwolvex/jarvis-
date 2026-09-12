# Verified report mission

Run `npm run simulate --workspace @jarvis/runtime` from the repository root. It observes an isolated virtual workspace, reads the seeded mission brief, creates a virtual report, and independently observes the report's SHA-256 evidence before committing each node. No host files or applications are accessed. A user-provided mission title names this fixed recipe; it is not sent to an LLM and does not become executable instructions.

The executable recipe is `services/runtime/src/simulation.ts` (`createSimulationPlan`). Four nodes execute in dependency order. Every action is validated against the draft 2020-12 contract, evaluated by policy, bound to a fresh observation, authorized with an issuer-owned capability, executed, observed again, verified, and only then committed. Events distinguish execution from verification and state commit.

Failure-injection scenarios live in `services/runtime/test/runtime.test.ts`: stale evidence, missing postconditions, forged authority, cancellation, bounded read recovery, DAG cycles/duplicates, resource starvation, and event retention gaps. These are automated behavioral tests against the same runtime used by the control center.
