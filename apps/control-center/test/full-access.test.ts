import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { emergencyStopHybrid, resetHybridStop, hybridSnapshot, setHybridAccessMode, submitHybridMission } from "../src/lib/hybrid-control.ts";
import { parseControlBody, readControlObject } from "../src/lib/control-service.ts";
import { FULL_ACCESS_WORKER_REVISION, validateMissionResult, validateTaskGraph, runFullAccessMission } from "../src/lib/full-access-bridge.ts";
import { POST as accessPost } from "../src/app/api/full-access/route.ts";
import { CONTROL_SESSION_COOKIE, issueControlSession } from "../src/lib/control-session.ts";

test("emergency stop revokes access, cancels active worker and stays latched", { skip: process.platform !== "win32" }, async () => {
  const state = globalThis as any;
  delete state.jarvisHybridV3;
  const child = new EventEmitter() as any;
  const writes: string[] = [];
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (data: string, callback: () => void) => { writes.push(data); callback?.(); } };
  child.kill = () => { child.killed = true; child.exitCode = 1; child.emit("exit", 1); };
  state.jarvisFullAccessWorkerV1 = { revision: FULL_ACCESS_WORKER_REVISION, child, pending: new Map(), buffer: "", stderr: "" };
  try {
    setHybridAccessMode("full");
    submitHybridMission("A fixture that must never touch the desktop");
    assert.equal(hybridSnapshot().status, "EXECUTING");
    assert.throws(() => submitHybridMission("second"), /already running/);
    emergencyStopHybrid();
    assert.equal(hybridSnapshot().emergencyStopped, true);
    assert.throws(() => setHybridAccessMode("full"), /Reset emergency/);
    assert.throws(() => submitHybridMission("third"), /Emergency stop/);
    assert.throws(() => resetHybridStop(), /Wait for active/);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(hybridSnapshot().tasks[0]?.status, "CANCELLED");
    assert.equal(hybridSnapshot().status, "EMERGENCY_STOPPED");
    assert.ok(writes.some(value => JSON.parse(value).action === "stop"));
    resetHybridStop();
    assert.equal(hybridSnapshot().facts.find(item => item.key === "access.mode")?.value, "standard");
    assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "false");
  } finally {
    child.kill();
    delete state.jarvisFullAccessWorkerV1;
    delete state.jarvisHybridV3;
  }
});

test("terminal authority is opt-in and lost on disable or emergency stop", () => {
  resetHybridStop();
  setHybridAccessMode("full");
  assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "false");
  setHybridAccessMode("standard");
  setHybridAccessMode("full", true);
  assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "true");
  emergencyStopHybrid();
  assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "false");
  resetHybridStop();
});

test("live previews replace the frame without flooding events or surviving revocation", { skip: process.platform !== "win32" }, async () => {
  const globals = globalThis as any;
  delete globals.jarvisHybridV3;
  const child = new EventEmitter() as any;
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (_data: string, callback: () => void) => callback?.() };
  child.kill = () => { child.killed = true; child.exitCode = 1; child.emit("exit", 1); };
  const worker = { revision: FULL_ACCESS_WORKER_REVISION, child, pending: new Map(), buffer: "", stderr: "" };
  globals.jarvisFullAccessWorkerV1 = worker;
  try {
    setHybridAccessMode("full");
    submitHybridMission("A synthetic live preview fixture");
    const progress = worker.pending.values().next().value.onProgress;
    const eventsBefore = hybridSnapshot().events.length;
    const observation = (sha256: string, live = true) => JSON.stringify({
      frame: { sha256, captured_at_ms: 1000, virtual_origin_x: -1920, virtual_origin_y: 0,
        source_width: 3840, source_height: 1080, desktop_scale_x: 4, width: 960, height: 270,
        live, stable: !live, capture_ms: 3 }, preview: "data:image/jpeg;base64,fixture",
    });
    for (let i = 0; i < 10; i++) progress("observation", observation(`frame-${i}`));
    assert.equal(hybridSnapshot().native?.capture?.frameId, "frame-9");
    assert.equal(hybridSnapshot().native?.capture?.live, true);
    assert.equal(hybridSnapshot().native?.capture?.display.x, -1920);
    assert.equal(hybridSnapshot().events.length, eventsBefore);
    progress("observation", observation("explicit", false));
    assert.equal(hybridSnapshot().native?.capture?.live, false);
    assert.equal(hybridSnapshot().events.length, eventsBefore + 1);
    setHybridAccessMode("standard");
    progress("observation", observation("late"));
    assert.equal(hybridSnapshot().native?.capture?.frameId, "explicit");
    await new Promise(resolve => setImmediate(resolve));
  } finally {
    setHybridAccessMode("standard");
    await new Promise(resolve => setImmediate(resolve));
    child.kill();
    delete globals.jarvisFullAccessWorkerV1;
    delete globals.jarvisHybridV3;
  }
});

test("cancellation during old-worker replacement cannot start a new worker", { skip: process.platform !== "win32" }, async () => {
  const globals = globalThis as any;
  const child = new EventEmitter() as any;
  const abort = new AbortController();
  child.exitCode = null;
  child.killed = false;
  child.kill = () => { abort.abort(); child.exitCode = 1; child.emit("exit", 1); return true; };
  const writes: string[] = [];
  child.stdin = { write: (data: string) => {
    writes.push(data);
    abort.abort();
    child.exitCode = 0;
    child.emit("exit", 0);
  } };
  globals.jarvisFullAccessWorkerV1 = { revision: 1, child, pending: new Map(), buffer: "", stderr: "" };
  try {
    await assert.rejects(runFullAccessMission("fixture", abort.signal), /aborted during worker replacement/);
    assert.deepEqual(writes, [], "Idle replacement must not send stop and latch the shared Rust daemon");
  } finally {
    delete globals.jarvisFullAccessWorkerV1;
  }
});

test("idle Windows worker replacement retires its launcher tree without stopping Rust", { skip: process.platform !== "win32" }, async t => {
  const globals = globalThis as any;
  const abort = new AbortController();
  const child = new EventEmitter() as any;
  child.pid = 12345; // Process execution is completely mocked below.
  child.exitCode = null;
  const writes: string[] = [];
  child.stdin = { write: (value: string) => writes.push(value) };
  const invocations: unknown[][] = [];
  const mocked = t.mock.method(childProcess, "execFile", ((...args: any[]) => {
    invocations.push(args.slice(0, 3));
    abort.abort();
    child.exitCode = 1;
    child.emit("exit", 1);
    args[3](null);
    return child;
  }) as typeof childProcess.execFile);
  syncBuiltinESMExports();
  globals.jarvisFullAccessWorkerV1 = { revision: 1, child, pending: new Map(), buffer: "", stderr: "" };
  try {
    await assert.rejects(runFullAccessMission("fixture", abort.signal), /aborted during worker replacement/);
    assert.deepEqual(writes, []);
    assert.deepEqual(invocations, [["taskkill.exe", ["/PID", "12345", "/T", "/F"], { windowsHide: true, timeout: 1500 }]]);
  } finally {
    mocked.mock.restore();
    syncBuiltinESMExports();
    delete globals.jarvisFullAccessWorkerV1;
  }
});

test("worker upgrade cannot terminate or replace an active mission", { skip: process.platform !== "win32" }, async () => {
  const globals = globalThis as any;
  const child = new EventEmitter() as any;
  child.exitCode = null;
  child.kill = () => { throw new Error("active worker must not be killed"); };
  child.stdin = { write: () => { throw new Error("active worker must not be stopped"); } };
  globals.jarvisFullAccessWorkerV1 = { revision: 1, child, pending: new Map([["mission", {}]]), buffer: "", stderr: "" };
  try {
    await assert.rejects(runFullAccessMission("fixture"), /older worker still has an active mission/);
    assert.equal(child.exitCode, null);
  } finally {
    delete globals.jarvisFullAccessWorkerV1;
  }
});

test("complex missions have bounded extended input without changing simulation limits", async () => {
  const request = (value: unknown) => new Request("http://127.0.0.1/api/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  const mission = { action: "run", title: "step ".repeat(100) };
  assert.equal((await parseControlBody(request(mission), 8000)).action, "run");
  await assert.rejects(parseControlBody(request(mission)));
  await assert.rejects(parseControlBody(request({ action: "run", title: "bad\0title" }), 8000));
  await assert.rejects(readControlObject(request({ mode: "x".repeat(3000) }), 2048));
});

test("completion rejects missing evidence and retains actionable failure checkpoints", () => {
  const result = { ok: true, action: "full_access_mission", access_mode: "full", status: "completed", result: "observed", task_id: "task-1-01234567", tools_used: 1,
    failures: 0, recoveries: 0, verifications: 0, verified: false, read_only_observation_verified: true, requires_user_action: false, mission_completed: true, incomplete_steps: [] };
  assert.equal(validateMissionResult(result).mission_completed, true);
  assert.throws(() => validateMissionResult({ ...result, read_only_observation_verified: false }), /evidence/);
  assert.throws(() => validateMissionResult({ ...result, tools_used: undefined }), /executed mission/);
  assert.throws(() => validateMissionResult({ ...result, incomplete_steps: ["unfinished"] }), /evidence/);
  assert.equal(validateMissionResult({ ...result, execution_metrics: { model_calls: 2, workflow_batches: 1 } }).execution_metrics?.model_calls, 2);
  const graph = [{ id: "step-1", action: "launch", description: "Open WhatsApp", dependencies: [], status: "COMPLETED", execution_backend: "DIRECT" }];
  assert.equal(validateMissionResult({ ...result, task_graph: graph }).task_graph?.[0]?.execution_backend, "DIRECT");
  for (const status of ["RUNNING", "DELIVERED", "VERIFIED", "FAILED", "WAITING_USER"]) {
    assert.throws(() => validateMissionResult({ ...result, task_graph: [{ ...graph[0], status }] }), /unfinished/);
  }
  for (const verification_result of ["FAILED", " failed "]) {
    assert.throws(() => validateMissionResult({ ...result, verified: true,
      task_graph: [{ ...graph[0], verification_result }] }), /contradicts failed/);
  }
  assert.equal(validateMissionResult({ ...result,
    task_graph: [{ ...graph[0], verification_result: "VERIFIED" }] }).mission_completed, true);
  assert.throws(() => validateMissionResult({ ...result, task_graph: [{ id: 1 }] }), /task graph/);
  assert.throws(() => validateMissionResult({ ...result, execution_metrics: { model_calls: -1 } }), /metrics/);
  assert.throws(() => validateMissionResult({ ...result, execution_metrics: { whatsapp_ordinal_fast_path: true } }), /metrics/);
  assert.equal(validateMissionResult({ ...result, execution_metrics: { whatsapp_ordinal_fast_path: 1 } }).execution_metrics?.whatsapp_ordinal_fast_path, 1);
  assert.throws(() => validateMissionResult({ ok: false, result: "Provider timeout", task_id: "task-1-01234567" }), /Provider timeout.*checkpoint/);
});

test("live task graph validation rejects ambiguous identities, missing dependencies and invalid phases", () => {
  const node = { id: "one", action: "type", description: "Type fixture", dependencies: [], status: "RUNNING" };
  assert.equal(validateTaskGraph([node])[0]?.id, "one");
  assert.throws(() => validateTaskGraph([node, node]), /identity/);
  assert.throws(() => validateTaskGraph([{ ...node, dependencies: ["missing"] }]), /dependency/);
  assert.throws(() => validateTaskGraph([{ ...node, status: "SUCCESS_GUESSED" }]), /phase/);
  assert.throws(() => validateTaskGraph([{ ...node, execution_backend: {} }]), /backend/);
  assert.throws(() => validateTaskGraph([{ ...node, action: "x".repeat(1001) }]), /graph/);
  assert.throws(() => validateTaskGraph([
    { ...node, dependencies: ["two"] }, { ...node, id: "two", dependencies: ["one"] },
  ]), /cycle/);
});

test("live execution graph updates before completion and rejects stale updates after revocation", { skip: process.platform !== "win32" }, async () => {
  const globals = globalThis as any;
  delete globals.jarvisHybridV3;
  const child = new EventEmitter() as any;
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (_data: string, callback: () => void) => callback?.() };
  child.kill = () => { child.killed = true; child.exitCode = 1; child.emit("exit", 1); };
  const worker = { revision: FULL_ACCESS_WORKER_REVISION, child, pending: new Map(), buffer: "", stderr: "" };
  globals.jarvisFullAccessWorkerV1 = worker;
  try {
    setHybridAccessMode("full");
    submitHybridMission("Synthetic graph fixture; never interact with desktop");
    const progress = worker.pending.values().next().value.onProgress;
    const nodes = [{ id: "one", action: "type", description: "Type fixture", dependencies: [], status: "DELIVERED", execution_backend: "python_native" },
      { id: "two", action: "verify", description: "Read editor", dependencies: ["one"], status: "QUEUED" }];
    progress("task_graph", JSON.stringify(nodes));
    const task = hybridSnapshot().tasks[0]!;
    assert.equal(task.status, "RUNNING");
    assert.equal(task.nodes[0]?.status, "DELIVERED");
    assert.match(task.nodes[0]!.action, /python_native/);
    assert.deepEqual(task.nodes[1]?.dependencies, [task.nodes[0]?.id]);
    progress("task_graph", "invalid JSON");
    assert.equal(hybridSnapshot().tasks[0]?.nodes[0]?.status, "DELIVERED");
    setHybridAccessMode("standard");
    progress("task_graph", JSON.stringify([{ ...nodes[0], status: "COMPLETED" }]));
    assert.equal(hybridSnapshot().tasks[0]?.nodes[0]?.status, "DELIVERED");
    await new Promise(resolve => setImmediate(resolve));
  } finally {
    setHybridAccessMode("standard");
    await new Promise(resolve => setImmediate(resolve));
    child.kill();
    delete globals.jarvisFullAccessWorkerV1;
    delete globals.jarvisHybridV3;
  }
});

test("access endpoint requires an authenticated session and separate terminal confirmation", async () => {
  const oldMode = process.env.JARVIS_CONTROL_MODE;
  const oldToken = process.env.JARVIS_CONTROL_PAIRING_TOKEN;
  process.env.JARVIS_CONTROL_MODE = "hybrid";
  process.env.JARVIS_CONTROL_PAIRING_TOKEN = "full-access-pairing-0123456789-abcdefghijklmnopqrstuvwxyz";
  const cookie = `${CONTROL_SESSION_COOKIE}=${issueControlSession()}`;
  const request = (body: unknown, origin = "http://127.0.0.1:3000", authenticated = true) => new Request("http://127.0.0.1:3000/api/full-access", {
    method: "POST", headers: { host: "127.0.0.1:3000", origin, "Content-Type": "application/json", "X-Jarvis-Control": "hybrid", ...(authenticated ? { cookie } : {}) }, body: JSON.stringify(body),
  });
  try {
    resetHybridStop();
    assert.equal((await accessPost(request({ mode: "full", confirmation: "ENABLE_FULL_ACCESS" }, undefined, false))).status, 400);
    assert.equal((await accessPost(request({ mode: "full" }))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", allowShell: true, confirmation: "ENABLE_FULL_ACCESS" }))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", confirmation: "ENABLE_FULL_ACCESS" }, "https://attacker.invalid"))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", allowShell: true, confirmation: "ENABLE_FULL_ACCESS_AND_SHELL" }))).status, 200);
    assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "true");
  } finally {
    setHybridAccessMode("standard");
    if (oldMode === undefined) delete process.env.JARVIS_CONTROL_MODE;
    else process.env.JARVIS_CONTROL_MODE = oldMode;
    if (oldToken === undefined) delete process.env.JARVIS_CONTROL_PAIRING_TOKEN;
    else process.env.JARVIS_CONTROL_PAIRING_TOKEN = oldToken;
  }
});
