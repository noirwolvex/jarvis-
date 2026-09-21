# Validation evidence — 2026-09-12

This is the historical September 12 snapshot. See the [September 21 project review](project-review-2026-09-21.md) for current hybrid Python/Rust integration coverage, performance changes, regression results and remaining limitations.

Environment: Windows, Node.js 22.23.2, TypeScript 5.9.3, Next.js 16.3.5, Rust/Cargo 1.97.1, Python 3.14. These results cover the committed/reference source plus the current working-tree changes. They are not production certification or benchmark results.

| Check | Result | What it establishes |
|---|---|---|
| `npm run build` | PASS | Next.js production bundle, TypeScript compilation and static/dynamic route generation |
| `npm run typecheck` | PASS | Control-center, contract, runtime and generated database-client types |
| `npm test` | PASS | 3 control API/projection tests, 21 runtime/gateway tests, and 5 database scenarios in one test harness (30 reported Node tests) |
| `npm run demo` | PASS | Four verified virtual-workspace nodes, 24 ordered events, no failed nodes |
| `npm run db:validate` | PASS | Prisma schema/config correctness |
| `npm run db:generate` | PASS | Prisma 7.10 generated client; output intentionally ignored |
| `npm audit` | PASS | Zero known vulnerabilities reported by the registry audit at validation time |
| `cargo test --manifest-path daemon/rust/Cargo.toml` | PASS | 17 tests; three ignored functions are subprocess fixtures invoked by those tests |
| `cargo check --manifest-path daemon/rust/Cargo.toml --features native` | PASS | Optional xcap/Enigo native feature compiles on this Windows host |
| `cargo fmt --manifest-path daemon/rust/Cargo.toml --check` | PASS | Rust formatting |
| `python -m unittest discover -s tests -p test_*.py` | PASS | 11 existing Python tests, including memory cleanup on Windows |
| `git diff --check` | PASS | No whitespace errors in tracked changes |

## Browser inspection

Opened the production preview at `http://127.0.0.1:3000` through the Codex in-app browser. Observed connection readiness, ran the workspace simulation, and verified its completed task plus all four verified nodes in the task graph. Opened the node inspector and the global Ctrl+K command palette. Inspected rendering at the browser's narrow viewport and an explicit 1440×1000 desktop breakpoint, then reset the viewport override. The preview is marked as a deliverable. These were manual browser checks, not an automated accessibility conformance certification.

## Bugs found and corrected

- Empty Rust action variants accepted unknown JSON fields despite the enum's Serde setting. Struct-style empty variants now reject extra fields, covered by the boundary test.
- The Rust request-TTL test sampled the wall clock before creating its session, making a one-millisecond boundary assertion timing-sensitive. The test now samples after initialization.
- Source `.js` imports pointed at TypeScript-only workspace files and failed Next/Turbopack resolution. Explicit `.ts` imports and shared TypeScript extension/rewrite settings support both execution paths.
- The existing Python memory store used SQLite's transaction context manager without closing the connections. Explicit closing now releases Windows file handles; the original memory test passes.
- Scoped Prisma tooling overrides replace vulnerable deepmerge-ts and mysql2 versions. Schema validation, client generation, embedded SQL tests and the zero-finding audit passed after the update.
- Mission-history inspection now retains the selected task identity rather than always showing the newest task. The world view groups actual virtual-file existence facts instead of repeating each metadata property as an artifact.

## Limits of this evidence

The database tests use actual PostgreSQL SQL/PLpgSQL through PGlite/WASM. No native PostgreSQL server or Docker service was available here. Multi-session races, deployed roles, WAL, backup/restore and daemon-crash reconciliation still need deployment integration tests.

Native input was **compiled, not executed**. Real screen permissions, DPI/focus races, Linux X11/Wayland behavior, UIA/AT-SPI, hardware stop latency, hostile-plugin containment and OS resource quotas have not been qualified. The GitHub Windows/Linux CI workflow is supplied but was not run remotely during this task.

The dashboard is an in-memory demonstration. It does not connect to the Rust daemon, persisted Prisma orchestration or any real model provider. It uses synthetic screen/world data and labels unavailable hardware metrics. Resource/latency matrices are proposed allocations and targets, not measured performance claims.
