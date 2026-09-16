import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { emergencyStopHybrid, resetHybridStop, hybridSnapshot, setHybridAccessMode, submitHybridMission } from "../src/lib/hybrid-control.ts";
import { parseControlBody, readControlObject } from "../src/lib/control-service.ts";
import { validateMissionResult, runFullAccessMission } from "../src/lib/full-access-bridge.ts";
import { POST as accessPost } from "../src/app/api/full-access/route.ts";

test("emergency stop revokes access, cancels active worker and stays latched", { skip: process.platform !== "win32" }, async () => {
  const state = globalThis as any;
  delete state.jarvisHybridV3;
  const child = new EventEmitter() as any;
  const writes: string[] = [];
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (data: string, callback: () => void) => { writes.push(data); callback?.(); } };
  child.kill = () => { child.killed = true; child.exitCode = 1; child.emit("exit", 1); };
  state.jarvisFullAccessWorkerV1 = { revision: 4, child, pending: new Map(), buffer: "", stderr: "" };
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
  const worker = { revision: 4, child, pending: new Map(), buffer: "", stderr: "" };
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
  child.kill = () => { child.exitCode = 1; child.emit("exit", 1); };
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
    assert.deepEqual(writes.map(line => JSON.parse(line).action), ["stop"]);
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
  assert.throws(() => validateMissionResult({ ...result, execution_metrics: { model_calls: -1 } }), /metrics/);
  assert.throws(() => validateMissionResult({ ok: false, result: "Provider timeout", task_id: "task-1-01234567" }), /Provider timeout.*checkpoint/);
});

test("access endpoint requires separate explicit confirmation for terminal authority", async () => {
  const oldMode = process.env.JARVIS_CONTROL_MODE;
  process.env.JARVIS_CONTROL_MODE = "hybrid";
  const request = (body: unknown, origin = "http://127.0.0.1:3000") => new Request("http://127.0.0.1:3000/api/full-access", {
    method: "POST", headers: { host: "127.0.0.1:3000", origin, "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" }, body: JSON.stringify(body),
  });
  try {
    resetHybridStop();
    assert.equal((await accessPost(request({ mode: "full" }))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", allowShell: true, confirmation: "ENABLE_FULL_ACCESS" }))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", confirmation: "ENABLE_FULL_ACCESS" }, "https://attacker.invalid"))).status, 400);
    assert.equal((await accessPost(request({ mode: "full", allowShell: true, confirmation: "ENABLE_FULL_ACCESS_AND_SHELL" }))).status, 200);
    assert.equal(hybridSnapshot().facts.find(item => item.key === "access.terminal")?.value, "true");
  } finally {
    setHybridAccessMode("standard");
    if (oldMode === undefined) delete process.env.JARVIS_CONTROL_MODE;
    else process.env.JARVIS_CONTROL_MODE = oldMode;
  }
});
