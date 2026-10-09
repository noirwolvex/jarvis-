import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { POST } from "../src/app/api/mission-control/route.ts";
import { CONTROL_SESSION_COOKIE, issueControlSession } from "../src/lib/control-session.ts";
import { workerSourceFingerprint, FULL_ACCESS_WORKER_REVISION } from "../src/lib/full-access-bridge.ts";
import { hybridSnapshot, setHybridAccessMode, submitHybridMission } from "../src/lib/hybrid-control.ts";

const confirmationId = "abc1".repeat(8);

function controlFixture() {
  // These globals exist only in this isolated test process. No child is spawned and
  // no request reaches the user's running dashboard, daemon, or desktop.
  const globals = globalThis as any;
  const original = {
    worker: globals.jarvisFullAccessWorkerV1,
    hybrid: globals.jarvisHybridV3,
    mode: process.env.JARVIS_CONTROL_MODE,
    token: process.env.JARVIS_CONTROL_PAIRING_TOKEN,
  };
  const writes: Array<Record<string, unknown>> = [];
  const child = new EventEmitter() as any;
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (data: string, callback?: () => void) => { writes.push(JSON.parse(data)); callback?.(); } };
  child.kill = () => { child.killed = true; child.exitCode = 0; child.emit("exit", 0); };
  const worker = { revision: FULL_ACCESS_WORKER_REVISION, sourceFingerprint: workerSourceFingerprint(), child, pending: new Map<string, any>([["request-one", {}]]), buffer: "", stderr: "", stopping: false };
  globals.jarvisFullAccessWorkerV1 = worker;
  globals.jarvisHybridV3 = {
    tasks: [], events: [], seq: 0, status: "EXECUTING", running: true, version: 0, accessMode: "full",
    missionControl: { paused: false, pauseRequested: false, pendingConfirmation: {
      id: confirmationId, summary: "Send the reviewed fixture message", tool: "fixture_send", category: "send",
      reason: "Messages leave the device", details: '{"recipient":"fixture","text":"fixture draft"}',
    } },
  };
  process.env.JARVIS_CONTROL_MODE = "hybrid";
  process.env.JARVIS_CONTROL_PAIRING_TOKEN = "fixture-control-session-0123456789abcdefghijklmnopqrstuvwxyz";
  const cookie = `${CONTROL_SESSION_COOKIE}=${issueControlSession()}`;
  return {
    writes, worker, globals,
    request: (body: unknown, headers: Record<string, string> = {}) => new Request("http://127.0.0.1:3000/api/mission-control", {
      method: "POST", headers: { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", "Content-Type": "application/json",
        "X-Jarvis-Control": "hybrid", cookie, ...headers }, body: JSON.stringify(body),
    }),
    close() {
      globals.jarvisFullAccessWorkerV1 = original.worker;
      globals.jarvisHybridV3 = original.hybrid;
      if (original.mode === undefined) delete process.env.JARVIS_CONTROL_MODE;
      else process.env.JARVIS_CONTROL_MODE = original.mode;
      if (original.token === undefined) delete process.env.JARVIS_CONTROL_PAIRING_TOKEN;
      else process.env.JARVIS_CONTROL_PAIRING_TOKEN = original.token;
    },
  };
}

test("mission controls require local origin, authenticated session and the explicit control header", async () => {
  const f = controlFixture();
  try {
    for (const headers of [
      { origin: "https://attacker.invalid" }, { host: "attacker.invalid:3000" },
      { "sec-fetch-site": "cross-site" }, { cookie: "" }, { cookie: `${CONTROL_SESSION_COOKIE}=forged.cookie` },
      { "X-Jarvis-Control": "native" }, { "X-Jarvis-Control": "" },
    ]) {
      const result = await POST(f.request({ action: "pause" }, headers));
      assert.equal(result.status, 400);
      assert.equal(result.headers.get("cache-control"), "no-store");
    }
    assert.equal(f.writes.length, 0);
  } finally { f.close(); }
});

test("mission controls reject unknown, oversized and ambiguous commands before worker dispatch", async () => {
  const f = controlFixture();
  try {
    for (const body of [
      null, [], { action: "run" }, { action: "pause", extra: true }, { action: "confirm" },
      { action: "confirm", confirmationId: "bad-id" }, { action: "reject", confirmationId: 123 },
      ...["pause", "resume", "cancel"].map(action => ({ action, confirmationId })),
      { action: "confirm", confirmationId, details: "x".repeat(3000) },
    ]) assert.equal((await POST(f.request(body))).status, 400);
    assert.equal((await POST(f.request({ action: "pause" }, { "Content-Type": "text/plain" }))).status, 400);
    process.env.JARVIS_CONTROL_MODE = "simulation";
    assert.equal((await POST(f.request({ action: "pause" }))).status, 400);
    assert.equal(f.writes.length, 0);
  } finally { f.close(); }
});

test("approval is bound to the current confirmation and active worker request", async () => {
  const f = controlFixture();
  try {
    assert.equal((await POST(f.request({ action: "confirm", confirmationId: "f".repeat(32) }))).status, 400);
    for (const action of ["confirm", "reject"]) {
      const response = await POST(f.request({ action, confirmationId }));
      assert.equal(response.status, 202);
      assert.deepEqual(await response.json(), { ok: true });
      assert.deepEqual(f.writes.at(-1), { protocol: 1, action, id: "request-one", confirmation_id: confirmationId });
    }
    delete f.globals.jarvisHybridV3.missionControl.pendingConfirmation;
    assert.equal((await POST(f.request({ action: "confirm", confirmationId }))).status, 400);
    assert.equal(f.writes.length, 2);
  } finally { f.close(); }
});

test("pause and cancel dispatch without inventing acknowledgement or revoking Full Access", async () => {
  const f = controlFixture();
  try {
    delete f.globals.jarvisHybridV3.missionControl.pendingConfirmation;
    for (const action of ["pause", "resume", "cancel"]) {
      assert.equal((await POST(f.request({ action }))).status, 202);
      assert.deepEqual(f.writes.at(-1), { protocol: 1, action, id: "request-one" });
    }
    const snapshot = hybridSnapshot();
    assert.equal(snapshot.missionControl?.paused, false);
    assert.equal(snapshot.emergencyStopped, false);
    assert.equal(snapshot.facts.find(item => item.key === "access.mode")?.value, "full");
    assert.ok(f.writes.every(item => item.action !== "stop"));
  } finally { f.close(); }
});

test("stale, stopped or ambiguous worker sessions cannot accept control commands", async () => {
  const f = controlFixture();
  try {
    f.worker.revision = FULL_ACCESS_WORKER_REVISION - 1;
    assert.equal((await POST(f.request({ action: "pause" }))).status, 400);
    f.worker.revision = FULL_ACCESS_WORKER_REVISION;
    f.worker.pending.set("other-request", {});
    assert.equal((await POST(f.request({ action: "pause" }))).status, 400);
    f.worker.pending.delete("other-request");
    f.worker.stopping = true;
    assert.equal((await POST(f.request({ action: "pause" }))).status, 400);
    f.worker.stopping = false;
    f.worker.child.exitCode = 0;
    assert.equal((await POST(f.request({ action: "pause" }))).status, 400);
    f.worker.child.exitCode = null;
    f.globals.jarvisHybridV3.emergencyStopped = true;
    assert.equal((await POST(f.request({ action: "confirm", confirmationId }))).status, 400);
    assert.equal(f.writes.length, 0);
  } finally { f.close(); }
});

test("worker acknowledgements project pause and confirmation state without accepting malformed updates", { skip: process.platform !== "win32" }, async () => {
  const f = controlFixture();
  try {
    f.worker.pending.clear();
    delete f.globals.jarvisHybridV3;
    setHybridAccessMode("full");
    submitHybridMission("Synthetic confirmation fixture; never interact with the desktop");
    const progress = f.worker.pending.values().next().value!.onProgress;
    progress("mission_control", JSON.stringify({ paused: false, pauseRequested: true }));
    assert.equal(hybridSnapshot().missionControl?.pauseRequested, true);
    assert.equal(hybridSnapshot().missionControl?.paused, false);
    progress("mission_control", JSON.stringify({ paused: true, pauseRequested: true }));
    assert.equal(hybridSnapshot().status, "PAUSED");
    const confirmation = { id: confirmationId, summary: "Review fixture message", tool: "fixture_send", category: "send",
      reason: "Sending leaves the device", details: '{"text":"private fixture text"}' };
    progress("mission_control", JSON.stringify({ paused: false, pauseRequested: false, pendingConfirmation: confirmation }));
    assert.equal(hybridSnapshot().status, "WAITING_USER");
    assert.equal(hybridSnapshot().missionControl?.pendingConfirmation?.id, confirmationId);
    const before = hybridSnapshot().missionControl;
    for (const invalid of ["not json", JSON.stringify({ paused: "yes" }), JSON.stringify({ paused: false, pendingConfirmation: { ...confirmation, id: "bad" } })]) {
      progress("mission_control", invalid);
      assert.deepEqual(hybridSnapshot().missionControl, before);
    }
    progress("mission_control", JSON.stringify({ paused: false, pauseRequested: false }));
    assert.equal(hybridSnapshot().status, "EXECUTING");
    assert.equal(hybridSnapshot().missionControl?.pendingConfirmation, undefined);
  } finally {
    setHybridAccessMode("standard");
    await new Promise(resolve => setImmediate(resolve));
    f.worker.child.kill();
    f.close();
  }
});
