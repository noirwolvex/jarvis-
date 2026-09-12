# PostgreSQL and Prisma reference storage

The database is **not connected to the in-memory simulation runtime**. It supplies the durable target model, generated-client construction, initial SQL and required cross-row invariants.

## Offline checks

```powershell
rtk proxy npm.cmd run db:validate
rtk proxy npm.cmd run db:generate
rtk proxy npm.cmd run test --workspace @jarvis/database
```

`schema.prisma` uses Prisma 7 configuration: the datasource URL is in `prisma.config.ts`, and `prisma-client` generates TypeScript into the ignored `generated/prisma` directory. Run generation before typechecking. `src/client.ts` uses PrismaPg with four pooled connections and bounded connect/query timeouts; callers must disconnect during shutdown.

The migration test applies both SQL migrations to [PGlite's PostgreSQL/WASM engine](https://pglite.dev/docs/about). It checks actual SQL/PLpgSQL syntax, DAG cycle/self/cross-plan rejection, audit/plan immutability, outbox terminal/identity immutability and execution/session checks. This does **not** establish multi-session locking, PostgreSQL 17 production role grants, WAL/crash behavior or backup recovery. Those require tests against the actual deployment server.

## Start a local server when Docker is available

Create `infrastructure/.env` from its example and choose a unique local password. Keep it out of version control. Configure a matching `DATABASE_URL` in the service environment or a `database/.env` file. The URL is not needed for offline generation/validation.

```powershell
rtk proxy docker compose --env-file infrastructure/.env -f infrastructure/compose.yaml up -d
rtk proxy npm.cmd run migrate:deploy --workspace @jarvis/database
```

Compose publishes PostgreSQL only on `127.0.0.1:5432` and sets CPU/memory/process/log bounds. Its bootstrap owner is suitable for a local reference, not a production runtime identity. Provision a non-owner service role with explicit grants before connecting any agent. Owners/superusers can disable triggers; append-only SQL is not an audit trust anchor against the database owner.

## Mandatory migrations

1. `202609120001_initial` defines relational entities, enums, uniqueness, indexes and foreign keys.
2. `202609120002_invariants` adds append-only update/delete/truncate protections, range checks, same-plan DAG links/cycle rejection, execution/evidence binding checks, capability-use budget/expiry/identity checks and outbox immutability. It also adds partial, GIN and BRIN indexes.

Apply both. Prisma schema validation alone cannot prove the hardening SQL is present. Startup should verify the deployed schema/migration version before issuing capabilities.

## Runtime integration contract

Persist intent, plan and authorization before execution. On admission, transactionally consume a narrowly bound capability, reserve a fenced node attempt and append `ACTION_REQUESTED` plus its outbox entry. The daemon independently validates identity, authority and fresh target evidence. On outcome, commit verified world state, action result, checkpoint and next event in one transaction. Outbox delivery is at-least-once; consumers deduplicate by event ID.

If the daemon acted but the database transaction failed, mark the outcome unknown and reconcile with fresh evidence. Never re-execute a non-idempotent action merely because an acknowledgement or commit is missing. Global desktop effects cannot be rolled back by a database transaction.

JSON columns contain versioned contract payloads but PostgreSQL does not run AJV. Validate in the admission service and maintain migration/version compatibility. Application authorization must additionally enforce task ownership and all tenant/session links; this schema is not a complete multi-tenant authorization layer. A deployment service must preserve hash-chain anchors separately from the database and apply explicit retention/erasure procedures.

Primary references: [Prisma 7 configuration](https://docs.prisma.io/docs/orm/reference/prisma-config-reference), [PostgreSQL constraints](https://www.postgresql.org/docs/17/ddl-constraints.html), [row security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).
