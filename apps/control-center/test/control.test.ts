import test from "node:test";
import assert from "node:assert/strict";
import { createRuntime } from "@jarvis/runtime";
import { assertLocalRequest, parseControlBody, snapshotForView } from "../src/lib/control-service.ts";

function request(body: string, overrides: Record<string, string> = {}) {
  return new Request("http://127.0.0.1:3000/api/control", { method: "POST", body,
    headers: { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", "content-type": "application/json", "x-jarvis-control": "simulation", ...overrides } });
}

test("local demo boundary rejects hostile host/origin and missing control header", () => {
  assert.doesNotThrow(() => assertLocalRequest(request("{}"), true));
  assert.throws(() => assertLocalRequest(request("{}", { host: "attacker.test:3000" }), true));
  assert.throws(() => assertLocalRequest(request("{}", { origin: "https://attacker.test" }), true));
  assert.throws(() => assertLocalRequest(request("{}", { "x-jarvis-control": "" }), true));
});
test("control parser rejects extra fields, non-run titles and oversized streamed bodies", async () => {
  assert.deepEqual(await parseControlBody(request('{"action":"run","title":"Report"}')), { action: "run", title: "Report" });
  await assert.rejects(parseControlBody(request('{"action":"run","title":"Report","admin":true}')));
  await assert.rejects(parseControlBody(request('{"action":"stop","title":"Report"}')));
  await assert.rejects(parseControlBody(request(JSON.stringify({ action: "run", title: "x".repeat(3000) }))));
});
test("UI projects verified task nodes and retains task identity across multiple missions", async () => {
  const runtime = createRuntime({ stepDelayMs: 0 });
  const first = runtime.submitMission("First report");
  await runtime.waitForTask(first.id);
  const second = runtime.submitMission("Second report");
  await runtime.waitForTask(second.id);
  const view = snapshotForView(runtime);
  assert.equal(view.tasks[0]?.id, second.id);
  assert.equal(view.tasks[1]?.id, first.id);
  assert.ok(view.tasks.every(task => task.nodes.every(node => node.status === "VERIFIED")));
  assert.equal(view.mode, "simulation");
  assert.ok(view.telemetry.processRssMb > 0);
  assert.equal(view.models.length, 0);
  assert.ok(view.memory.every(entry => entry.confidence === null));
});
