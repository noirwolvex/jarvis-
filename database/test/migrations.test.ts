import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { PGlite } from "@electric-sql/pglite";

// PostgreSQL compiled to WASM exercises real SQL/PLpgSQL. It does not qualify
// multi-connection lock contention, native server permissions, or crash recovery.
test("PostgreSQL migrations and critical relational invariants", async t => {
  const db = new PGlite();
  try {
    await t.test("both migrations execute on an empty database", async () => {
      for (const migration of ["202609120001_initial", "202609120002_invariants"]) {
        const sql = await readFile(new URL(`../migrations/${migration}/migration.sql`, import.meta.url), "utf8");
        await db.exec(sql);
      }
      const tables = await db.query<{ tablename: string }>("SELECT tablename FROM pg_tables WHERE schemaname = 'public'");
      for (const name of ["Task", "Action", "Verification", "Capability", "AuditEvent", "OutboxMessage"]) {
        assert.ok(tables.rows.some(row => row.tablename === name));
      }
    });
    const user = randomUUID(), device = randomUUID(), task = randomUUID(), plan = randomUUID(), otherPlan = randomUUID();
    const a = randomUUID(), b = randomUUID(), otherNode = randomUUID();
    await db.query('INSERT INTO "User" (id,"externalSubject","displayName","updatedAt") VALUES ($1,$2,$3,now())', [user, "test-operator", "Test operator"]);
    await db.query('INSERT INTO "Device" (id,"ownerId",name,platform,"publicKey","bootId","updatedAt") VALUES ($1,$2,$3,$4,$5,$6,now())', [device, user, "Test device", "WINDOWS", "test-key", "boot-1"]);
    await db.query('INSERT INTO "Task" (id,"ownerId","deviceId",title,intent,budget,"updatedAt") VALUES ($1,$2,$3,$4,$5,$6,now())', [task, user, device, "Test", {}, {}]);
    for (const [id, version] of [[plan, 1], [otherPlan, 2]] as const) {
      await db.query('INSERT INTO "TaskVersion" (id,"taskId",version,"contractVersion",plan,"contentHash") VALUES ($1,$2,$3,$4,$5,$6)', [id, task, version, "1", {}, "a".repeat(64)]);
    }
    for (const [id, parent] of [[a, plan], [b, plan], [otherNode, otherPlan]]) {
      await db.query('INSERT INTO "TaskNode" (id,"taskVersionId",key,kind,contract,"timeoutMs") VALUES ($1,$2,$3,$4,$5,$6)', [id, parent, id, "ACTION", {}, 1000]);
    }
    const edge = (from: string, to: string) => db.query('INSERT INTO "TaskDependency" (id,"taskVersionId","fromNodeId","toNodeId") VALUES ($1,$2,$3,$4)', [randomUUID(), plan, from, to]);
    await t.test("DAG rejects cycles, self edges, and cross-plan links", async () => {
      await edge(a, b);
      await assert.rejects(edge(b, a), /cycle/);
      await assert.rejects(edge(a, a));
      await assert.rejects(edge(a, otherNode), /dependency_to_same_plan/);
    });
    await t.test("audit and plan history reject update, delete, and truncate", async () => {
      const audit = randomUUID();
      await db.query('INSERT INTO "AuditEvent" (id,type,actor,"eventHash",payload) VALUES ($1,$2,$3,$4,$5)', [audit, "TEST", "test", "b".repeat(64), {}]);
      await assert.rejects(db.query('UPDATE "AuditEvent" SET actor=$1 WHERE id=$2', ["changed", audit]), /append-only/);
      await assert.rejects(db.query('DELETE FROM "AuditEvent" WHERE id=$1', [audit]), /append-only/);
      await assert.rejects(db.exec('TRUNCATE "AuditEvent"'), /append-only/);
      await assert.rejects(db.query('UPDATE "TaskVersion" SET plan=$1 WHERE id=$2', [{ changed: true }, plan]), /append-only/);
    });
    await t.test("outbox terminal state and event identity cannot be rewritten", async () => {
      const event = randomUUID(), outbox = randomUUID();
      await db.query('INSERT INTO "AgentEvent" (id,"taskId",sequence,type,"schemaVersion","traceId",payload,"occurredAt") VALUES ($1,$2,1,$3,$4,$5,$6,now())', [event, task, "TEST", "1", "test-trace", {}]);
      await db.query('INSERT INTO "OutboxMessage" (id,"eventId",destination,"updatedAt") VALUES ($1,$2,$3,now())', [outbox, event, "test-consumer"]);
      await assert.rejects(db.query('UPDATE "OutboxMessage" SET destination=$1 WHERE id=$2', ["other", outbox]), /immutable/);
      await db.query('UPDATE "OutboxMessage" SET status=$1 WHERE id=$2', ["DELIVERED", outbox]);
      await assert.rejects(db.query('UPDATE "OutboxMessage" SET status=$1 WHERE id=$2', ["PENDING", outbox]), /immutable/);
    });
    await t.test("invalid execution bindings and expired sessions fail closed", async () => {
      await assert.rejects(db.query('INSERT INTO "Execution" (id,"taskId","taskVersionId","deviceId","traceId","updatedAt") VALUES ($1,$2,$3,$4,$5,now())', [randomUUID(), randomUUID(), plan, device, "test-trace"]), /mismatch/);
      await assert.rejects(db.query('INSERT INTO "Session" (id,"userId","tokenDigest","updatedAt","expiresAt") VALUES ($1,$2,$3,now(),now()-interval \'1 hour\')', [randomUUID(), user, "test-token-digest"]), /session_lifetime/);
    });
  } finally { await db.close(); }
});
