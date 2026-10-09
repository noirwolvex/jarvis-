import test, { type TestContext } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, renameSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { FULL_ACCESS_WORKER_REVISION, runFullAccessMission, workerSourceFingerprint } from "../src/lib/full-access-bridge.ts";

function sources() {
  const prefix = resolve(tmpdir(), "jarvis-worker-source-");
  const root = mkdtempSync(prefix);
  const put = (path: string, value = "fixture") => {
    writeFileSync(resolve(root, path), value);
  };
  mkdirSync(resolve(root, "core/resources"), { recursive: true });
  mkdirSync(resolve(root, "core/__pycache__"));
  put("core/full_access_worker.py");
  put("core/full_access_bridge.py");
  put("core/resources/control_guides.json", '{"version":1}');
  return {
    root, put,
    close() {
      assert.ok(resolve(root).startsWith(prefix), "Remove only this test's temporary source directory");
      rmSync(root, { recursive: true, force: true });
    },
  };
}

test("worker fingerprint tracks Python and resource content, path changes and repository identity", () => {
  const a = sources(), b = sources();
  try {
    const initial = workerSourceFingerprint(a.root);
    assert.equal(workerSourceFingerprint(a.root), initial);
    assert.notEqual(workerSourceFingerprint(b.root), initial);
    a.put("core/extra.py", "first");
    const added = workerSourceFingerprint(a.root);
    assert.notEqual(added, initial);
    a.put("core/extra.py", "other"); // Same size: do not depend on coarse timestamp/size caches.
    const modified = workerSourceFingerprint(a.root);
    assert.notEqual(modified, added);
    renameSync(resolve(a.root, "core/extra.py"), resolve(a.root, "core/renamed.py"));
    const renamed = workerSourceFingerprint(a.root);
    assert.notEqual(renamed, modified);
    a.put("core/resources/control_guides.json", '{"version":2}');
    assert.notEqual(workerSourceFingerprint(a.root), renamed);
  } finally { a.close(); b.close(); }
});

test("worker fingerprint excludes runtime data and caches and fails closed on missing execution sources", () => {
  const f = sources();
  try {
    const initial = workerSourceFingerprint(f.root);
    f.put("core/__pycache__/ignored.py", "cache");
    f.put("core/runtime.json", "runtime data");
    f.put(".env", "fixture secret never read");
    assert.equal(workerSourceFingerprint(f.root), initial);
    renameSync(resolve(f.root, "core/full_access_worker.py"), resolve(f.root, "core/worker.missing"));
    assert.throws(() => workerSourceFingerprint(f.root), /Cannot verify Full Access worker sources/);
  } finally { f.close(); }
});

function workerFixture(t: TestContext) {
  const f = sources();
  const globals = globalThis as any;
  const originalWorker = globals.jarvisFullAccessWorkerV1;
  const originalRoot = process.env.JARVIS_REPO_ROOT;
  process.env.JARVIS_REPO_ROOT = f.root;
  const writes: Array<Record<string, unknown>> = [];
  let kills = 0, spawns = 0;
  const newChild = () => {
    const child = new EventEmitter() as any;
    child.exitCode = null;
    child.killed = false;
    child.stdout = Object.assign(new EventEmitter(), { setEncoding() {} });
    child.stderr = Object.assign(new EventEmitter(), { setEncoding() {} });
    child.stdin = Object.assign(new EventEmitter(), {
      write(data: string, callback?: (error?: Error) => void) {
        const message = JSON.parse(data);
        writes.push(message);
        callback?.();
        if (message.action === "run") {
          const state = globals.jarvisFullAccessWorkerV1;
          const pending = state.pending.get(message.id);
          clearTimeout(pending.timer);
          state.pending.delete(message.id);
          pending.resolve({ fixture: true });
        }
      },
    });
    child.kill = () => {
      kills++;
      child.killed = true;
      child.exitCode = 0;
      child.emit("exit", 0);
      return true;
    };
    return child;
  };
  const child = newChild();
  const worker = { revision: FULL_ACCESS_WORKER_REVISION, sourceFingerprint: workerSourceFingerprint(f.root),
    child, pending: new Map<string, any>(), buffer: "", stderr: "" };
  globals.jarvisFullAccessWorkerV1 = worker;
  child.once("exit", () => {
    if (globals.jarvisFullAccessWorkerV1 === worker) delete globals.jarvisFullAccessWorkerV1;
  });
  const spawnMock = t.mock.method(childProcess, "spawn", (() => { spawns++; return newChild(); }) as typeof childProcess.spawn);
  const execMock = t.mock.method(childProcess, "execFile", (() => { throw new Error("No real process execution allowed"); }) as typeof childProcess.execFile);
  syncBuiltinESMExports();
  return {
    ...f, worker, writes, counts: () => ({ kills, spawns }),
    close() {
      spawnMock.mock.restore(); execMock.mock.restore(); syncBuiltinESMExports();
      globals.jarvisFullAccessWorkerV1 = originalWorker;
      if (originalRoot === undefined) delete process.env.JARVIS_REPO_ROOT;
      else process.env.JARVIS_REPO_ROOT = originalRoot;
      f.close();
    },
  };
}

test("unchanged execution sources reuse the current worker", { skip: process.platform !== "win32" }, async t => {
  const f = workerFixture(t);
  try {
    await runFullAccessMission("synthetic mission");
    await runFullAccessMission("second synthetic mission");
    assert.deepEqual(f.counts(), { kills: 0, spawns: 0 });
    assert.deepEqual(f.writes.map(row => row.action), ["run", "run"]);
  } finally { f.close(); }
});

test("same-revision idle worker reloads changed Python or control resources before dispatch", { skip: process.platform !== "win32" }, async t => {
  for (const path of ["core/full_access_worker.py", "core/resources/control_guides.json"]) {
    const f = workerFixture(t);
    try {
      f.put(path, "changed source");
      await runFullAccessMission("synthetic mission");
      assert.deepEqual(f.counts(), { kills: 1, spawns: 1 });
      assert.deepEqual(f.writes.map(row => row.action), ["run"], "Reload must not send emergency stop to Rust");
      assert.equal((globalThis as any).jarvisFullAccessWorkerV1.sourceFingerprint, workerSourceFingerprint(f.root));
    } finally { f.close(); }
  }
});

test("changed sources cannot interrupt an active mission", { skip: process.platform !== "win32" }, async t => {
  const f = workerFixture(t);
  try {
    f.worker.pending.set("already-running", {});
    f.put("core/full_access_worker.py", "changed source");
    await assert.rejects(runFullAccessMission("synthetic mission"), /older worker still has an active mission/);
    assert.deepEqual(f.counts(), { kills: 0, spawns: 0 });
    assert.deepEqual(f.writes, []);
    assert.ok(f.worker.pending.has("already-running"));
  } finally { f.close(); }
});

test("source validation errors do not retire or dispatch to an existing worker", { skip: process.platform !== "win32" }, async t => {
  const f = workerFixture(t);
  try {
    renameSync(resolve(f.root, "core/full_access_worker.py"), resolve(f.root, "core/worker.missing"));
    await assert.rejects(runFullAccessMission("synthetic mission"), /Cannot verify Full Access worker sources/);
    assert.deepEqual(f.counts(), { kills: 0, spawns: 0 });
    assert.deepEqual(f.writes, []);
  } finally { f.close(); }
});
