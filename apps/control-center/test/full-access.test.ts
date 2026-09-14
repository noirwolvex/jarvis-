import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { emergencyStopHybrid, resetHybridStop, hybridSnapshot, setHybridAccessMode, submitHybridMission } from "../src/lib/hybrid-control.ts";
import { parseControlBody, readControlObject } from "../src/lib/control-service.ts";

test("emergency stop revokes access, cancels active worker and stays latched", async () => {
  const state = globalThis as any;
  delete state.jarvisHybridV3;
  const child = new EventEmitter() as any;
  const writes: string[] = [];
  child.exitCode = null;
  child.killed = false;
  child.stdin = { write: (data: string, callback: () => void) => { writes.push(data); callback?.(); } };
  child.kill = () => { child.killed = true; child.exitCode = 1; child.emit("exit", 1); };
  state.jarvisFullAccessWorkerV1 = { child, pending: new Map(), buffer: "", stderr: "" };
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

test("complex missions have bounded extended input without changing simulation limits", async () => {
  const request = (value: unknown) => new Request("http://127.0.0.1/api/control", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  const mission = { action: "run", title: "step ".repeat(100) };
  assert.equal((await parseControlBody(request(mission), 8000)).action, "run");
  await assert.rejects(parseControlBody(request(mission)));
  await assert.rejects(parseControlBody(request({ action: "run", title: "bad\0title" }), 8000));
  await assert.rejects(readControlObject(request({ mode: "x".repeat(3000) }), 2048));
});
