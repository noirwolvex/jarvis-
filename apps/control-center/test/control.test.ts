import test from "node:test";
import assert from "node:assert/strict";
import { createRuntime } from "@jarvis/runtime";
import { assertLocalRequest, controlMode, parseControlBody, snapshotForView } from "../src/lib/control-service.ts";
import { daemonConfigFromEnv } from "../src/lib/daemon-client.ts";

function request(body: string, overrides: Record<string, string> = {}) {
  return new Request("http://127.0.0.1:3000/api/control", { method: "POST", body,
    headers: { host: "127.0.0.1:3000", origin: "http://127.0.0.1:3000", "content-type": "application/json", "x-jarvis-control": "simulation", ...overrides } });
}

test("local control boundary rejects hostile host origin and wrong mode header", () => {
  assert.doesNotThrow(() => assertLocalRequest(request("{}"), true, "simulation"));
  assert.throws(() => assertLocalRequest(request("{}", { host: "attacker.test:3000" }), true, "simulation"));
  assert.throws(() => assertLocalRequest(request("{}", { origin: "https://attacker.test" }), true, "simulation"));
  assert.throws(() => assertLocalRequest(request("{}", { "x-jarvis-control": "native" }), true, "simulation"));
  assert.doesNotThrow(() => assertLocalRequest(request("{}", { "x-jarvis-control": "native" }), true, "native"));
});

test("control parser validates simulation and typed native actions", async () => {
  assert.deepEqual(await parseControlBody(request('{"action":"run","title":"Report"}')), { action: "run", title: "Report" });
  assert.deepEqual(await parseControlBody(request('{"action":"capture"}')), { action: "capture" });
  assert.deepEqual(await parseControlBody(request('{"action":"click","x":10,"y":20}')), { action: "click", x: 10, y: 20 });
  assert.deepEqual(await parseControlBody(request('{"action":"type","text":"hello"}')), { action: "type", text: "hello" });
  await assert.rejects(parseControlBody(request('{"action":"run","title":"Report","admin":true}')));
  await assert.rejects(parseControlBody(request('{"action":"stop","title":"Report"}')));
  await assert.rejects(parseControlBody(request('{"action":"click","x":1.5,"y":2}')));
  await assert.rejects(parseControlBody(request('{"action":"type","text":""}')));
  await assert.rejects(parseControlBody(request(JSON.stringify({ action: "type", text: "x".repeat(9000) }))));
});

test("control mode defaults to simulation and validates native mode", () => {
  assert.equal(controlMode({}), "simulation");
  assert.equal(controlMode({ JARVIS_CONTROL_MODE: "native" }), "native");
  assert.throws(() => controlMode({ JARVIS_CONTROL_MODE: "remote" }));
});

test("daemon configuration is loopback-only and requires mTLS material", () => {
  const base = {
    JARVIS_DAEMON_CA: "ca.pem",
    JARVIS_DAEMON_CLIENT_CERT: "client.pem",
    JARVIS_DAEMON_CLIENT_KEY: "client-key.pem",
  };
  const config = daemonConfigFromEnv(base);
  assert.equal(config.host, "127.0.0.1");
  assert.equal(config.port, 7443);
  assert.throws(() => daemonConfigFromEnv({ ...base, JARVIS_DAEMON_HOST: "10.0.0.5" }));
  assert.throws(() => daemonConfigFromEnv({ ...base, JARVIS_DAEMON_PORT: "70000" }));
  assert.throws(() => daemonConfigFromEnv({}));
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
  assert.equal(view.native, null);
  assert.ok(view.telemetry.processRssMb > 0);
  assert.equal(view.models.length, 0);
  assert.ok(view.memory.every(entry => entry.confidence === null));
});
