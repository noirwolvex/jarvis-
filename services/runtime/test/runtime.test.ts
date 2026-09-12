import assert from 'node:assert/strict';
import { once } from 'node:events';
import test from 'node:test';
import { WebSocket } from 'ws';
import { parseContract, contractNames, schemaFor, type Action, type Observation } from '@jarvis/contracts';
import { createRuntime, createSimulationPlan, SimulationAdapter, verify, EventBus, ReplayGapError, PolicyEngine, CapabilityIssuer, validateDAG, runDAG, abortableDelay, FrameRingBuffer, ResourceGovernor, MemoryService, ModelRouter, simulatedResources } from '../src/index.js';
import { createGateway } from '../src/gateway.js';

const signal = () => new AbortController().signal;
async function fixture() {
  const adapter = new SimulationAdapter(0);
  const observation = await adapter.observe('test-task', signal());
  const action = createSimulationPlan('test-task', 'Test mission').dag.nodes[0]!.action;
  action.observationId = observation.id;
  return { action, observation, adapter };
}

test('all requested wire schemas declare draft 2020-12 and closed top-level shapes', () => {
  assert.equal(contractNames.length, 18);
  for (const name of contractNames) assert.equal(schemaFor(name).$schema, 'https://json-schema.org/draft/2020-12/schema');
});
test('strict action schema rejects type/args mismatch, unknown nested properties, and invalid coordinates', async () => {
  const { action } = await fixture();
  assert.throws(() => parseContract('Action', { ...action, injectedCommand: 'unsafe' }));
  assert.throws(() => parseContract('Action', { ...action, type: 'WRITE_FILE' }));
  assert.throws(() => parseContract('Action', { ...action, args: { ...action.args, command: 'unsafe' } }));
  assert.throws(() => parseContract('Action', { ...action, type: 'CLICK', args: { button: 'left', target: { kind: 'coordinates', x: -1, y: 0, displayId: 'main', dpiScale: 1, frameId: 'frame' } } }));
});
test('policy denies stale, unbound, unstable, or unsupported actions even at confidence 1', async () => {
  const { action, observation } = await fixture();
  const policy = new PolicyEngine();
  assert.equal(policy.evaluate(action, observation).decision, 'allow');
  assert.equal(policy.evaluate(action, { ...observation, observedAt: new Date(Date.now() - 10000).toISOString() }).decision, 'deny');
  assert.equal(policy.evaluate({ ...action, observationId: 'other' }, observation).decision, 'deny');
  assert.equal(policy.evaluate(action, { ...observation, stability: 'UI_TRANSITIONING' }).decision, 'deny');
  assert.equal(policy.evaluate({ ...action, type: 'TERMINAL_COMMAND', args: { executable: 'sh', argv: [], cwd: '/', environmentPolicy: 'clean' } }, observation).decision, 'deny');
  assert.equal(policy.evaluate({ ...action, riskLevel: 5 }, observation).decision, 'approval_required');
});
test('capabilities must originate from issuer and bind subject, task, scope and use budget', async () => {
  const { action } = await fixture();
  const issuer = new CapabilityIssuer();
  const capability = issuer.issue('executor', action.taskId, ['OBSERVE'], ['simulation:observe'], 1);
  assert.throws(() => issuer.consume({ ...capability, maxUses: 128 }, 'executor', action, 'simulation:observe'));
  assert.throws(() => issuer.consume(capability, 'another-executor', action, 'simulation:observe'));
  assert.throws(() => issuer.consume(capability, 'executor', { ...action, taskId: 'another-task' }, 'simulation:observe'));
  issuer.consume(capability, 'executor', action, 'simulation:observe');
  assert.throws(() => issuer.consume(capability, 'executor', action, 'simulation:observe'));
  const forgedIssuer = new CapabilityIssuer();
  assert.throws(() => issuer.consume(forgedIssuer.issue('executor', action.taskId, ['OBSERVE'], ['simulation:observe']), 'executor', action, 'simulation:observe'));
});
test('revoked and expired capabilities cannot authorize work', async () => {
  const { action } = await fixture();
  const issuer = new CapabilityIssuer();
  const cap = issuer.issue('executor', action.taskId, ['OBSERVE'], ['simulation:observe'], 1, 1);
  await abortableDelay(5, signal());
  assert.throws(() => issuer.consume(cap, 'executor', action, 'simulation:observe'));
  const next = issuer.issue('executor', action.taskId, ['OBSERVE'], ['simulation:observe']);
  issuer.revokeAll();
  assert.throws(() => issuer.consume(next, 'executor', action, 'simulation:observe'));
});
test('verification requires new observed evidence, independent of model confidence', async () => {
  const { action, observation, adapter } = await fixture();
  assert.equal(verify(action, observation).status, 'failed');
  const after = await adapter.observe(action.taskId, signal());
  assert.equal(verify(action, after).status, 'passed');
  assert.equal(verify({ ...action, confidence: 1 }, { ...after, evidence: [] }).status, 'failed');
  assert.equal(verify(action, { ...after, facts: { ...after.facts, 'ui.stable': false } }).status, 'failed');
  assert.equal(verify(action, { ...after, taskId: 'wrong-task' }).status, 'failed');
});
test('DAG rejects duplicate nodes, action IDs, missing dependencies and cycles', () => {
  const dag = createSimulationPlan('task', 'Test').dag;
  assert.equal(validateDAG(dag).nodes.length, 4);
  assert.throws(() => validateDAG({ ...dag, nodes: [...dag.nodes, dag.nodes[0]] }));
  const cycle = structuredClone(dag); cycle.nodes[0]!.dependsOn = [cycle.nodes[3]!.id];
  assert.throws(() => validateDAG(cycle));
  const missing = structuredClone(dag); missing.nodes[0]!.dependsOn = ['missing'];
  assert.throws(() => validateDAG(missing));
  const duplicate = structuredClone(dag); duplicate.nodes[1]!.action.id = duplicate.nodes[0]!.action.id;
  assert.throws(() => validateDAG(duplicate));
});
test('scheduler bounds concurrency and observes dependencies', async () => {
  const dag = createSimulationPlan('task', 'Test').dag;
  dag.maxConcurrency = 2; dag.nodes[1]!.dependsOn = []; dag.nodes[2]!.dependsOn = ['node-1', 'node-2'];
  let active = 0; let peak = 0; const completed: string[] = [];
  const outcome = await runDAG(dag, async (node, abort) => { assert.ok(node.dependsOn.every(id => completed.includes(id))); active++; peak = Math.max(peak, active); await abortableDelay(10, abort); active--; completed.push(node.id); }, signal());
  assert.equal(peak, 2); assert.equal(outcome.completed.length, 4);
});
test('scheduler stops dependent work after failure and joins active cancellation', async () => {
  const dag = createSimulationPlan('task', 'Test').dag;
  const started: string[] = [];
  const result = await runDAG(dag, async node => { started.push(node.id); throw new Error('injected'); }, signal());
  assert.deepEqual(started, ['node-1']); assert.deepEqual(result.failed, ['node-1']);
});
test('event replay protects immutable ordering, detects tampering and reports retained-history gaps', () => {
  const bus = new EventBus(2); const received: number[] = [];
  bus.subscribe(event => { assert.throws(() => { event.payload.message = 'mutated'; }); });
  bus.subscribe(event => { if (event.sequence === 1) bus.emit('STATE_CHANGED', 'task', 'nested event'); });
  bus.subscribe(event => received.push(event.sequence));
  bus.emit('TASK_CREATED', 'task', 'first event');
  assert.deepEqual(received, [1, 2]);
  const replay = bus.replay(); assert.ok(EventBus.verifyChain(replay)); replay[0]!.payload.message = 'changed';
  assert.ok(!EventBus.verifyChain(replay)); assert.notEqual(bus.replay()[0]!.payload.message, 'changed');
  bus.emit('TASK_COMPLETED', 'task', 'done'); bus.emit('STATE_CHANGED', 'task', 'next');
  assert.throws(() => bus.replay(1), ReplayGapError);
});
test('simulation mission commits only verified actions and returns observable evidence', async () => {
  const runtime = createRuntime({ stepDelayMs: 0 });
  const task = runtime.submitMission('Report simulation');
  const result = await runtime.waitForTask(task.id);
  assert.equal(result.status, 'completed'); assert.equal(result.result?.verificationIds.length, 4);
  const state = runtime.getState(); assert.equal(state.mode, 'simulation'); assert.equal(state.resources.source, 'simulated'); assert.equal(state.world.committedActionIds.length, 4);
  for (const event of state.events.filter(event => event.type === 'STATE_CHANGED')) assert.ok(state.events.some(prior => prior.sequence < event.sequence && prior.type === 'ACTION_VERIFIED' && prior.actionId === event.actionId && prior.payload.status === 'passed'));
  assert.ok(EventBus.verifyChain(state.events)); assert.equal(state.models.length, 0);
});
test('emergency stop cancels active work and queued tasks, denies submit until reset', async () => {
  const runtime = createRuntime({ stepDelayMs: 20 });
  runtime.events.subscribe(event => { if (event.type === 'ACTION_STARTED') runtime.emergencyStop(); });
  const task = runtime.submitMission('Cancellation'); const queued = runtime.submitMission('Queued');
  assert.equal((await runtime.waitForTask(task.id)).status, 'cancelled'); assert.equal((await runtime.waitForTask(queued.id)).status, 'cancelled');
  assert.equal(runtime.getState().world.committedActionIds.length, 0); assert.equal(runtime.getState().status, 'EMERGENCY_STOP');
  assert.throws(() => runtime.submitMission('Blocked'));
  await abortableDelay(0, signal()); runtime.resetEmergencyStop(); assert.equal(runtime.getState().status, 'IDLE');
});
test('paused runtime does not start a queued mission until resumed', async () => {
  const runtime = createRuntime({ stepDelayMs: 0 }); runtime.pause();
  const task = runtime.submitMission('Pause test');
  await abortableDelay(10, signal()); assert.equal(runtime.getState().tasks[0]!.status, 'queued');
  runtime.resume(); assert.equal((await runtime.waitForTask(task.id)).status, 'completed');
});
test('one transient read failure recovers within budget and observes again', async () => {
  class Transient extends SimulationAdapter { calls = 0; override async execute(action: Action, abort: AbortSignal) { if (action.type === 'READ_FILE' && this.calls++ === 0) throw new Error('transient virtual read failure'); await super.execute(action, abort); } }
  const adapter = new Transient(0); const runtime = createRuntime({ adapter }); const task = runtime.submitMission();
  assert.equal((await runtime.waitForTask(task.id)).status, 'completed'); assert.equal(adapter.calls, 2);
  assert.ok(runtime.getState().events.some(event => event.type === 'RECOVERY_COMPLETED'));
});
test('persistent read failure exhausts retry budget without reaching writes', async () => {
  class FailedRead extends SimulationAdapter { calls = 0; writes = 0; override async execute(action: Action, abort: AbortSignal) { if (action.type === 'WRITE_FILE') this.writes++; if (action.type === 'READ_FILE') { this.calls++; throw new Error('persistent virtual read failure'); } await super.execute(action, abort); } }
  const adapter = new FailedRead(0); const runtime = createRuntime({ adapter }); const task = runtime.submitMission();
  assert.equal((await runtime.waitForTask(task.id)).status, 'failed'); assert.equal(adapter.calls, 2); assert.equal(adapter.writes, 0);
});
test('unverified write cannot be committed or blindly retried', async () => {
  class DroppedWrite extends SimulationAdapter { writes = 0; override async execute(action: Action, abort: AbortSignal) { if (action.type === 'WRITE_FILE') { this.writes++; return; } await super.execute(action, abort); } }
  const adapter = new DroppedWrite(0); const runtime = createRuntime({ adapter }); const task = runtime.submitMission();
  assert.equal((await runtime.waitForTask(task.id)).status, 'failed'); assert.equal(adapter.writes, 1); assert.equal(runtime.getState().world.committedActionIds.length, 2);
});
test('stale adapter observations fail closed before execution', async () => {
  class Stale extends SimulationAdapter { calls = 0; override async observe(id: string, abort: AbortSignal): Promise<Observation> { const value = await super.observe(id, abort); return { ...value, observedAt: new Date(Date.now() - 30000).toISOString() }; } override async execute() { this.calls++; } }
  const adapter = new Stale(0); const runtime = createRuntime({ adapter }); const task = runtime.submitMission();
  assert.equal((await runtime.waitForTask(task.id)).status, 'failed'); assert.equal(adapter.calls, 0);
});
test('frame buffers and resource governor enforce physical limits and pressure degradation', () => {
  const frames = new FrameRingBuffer(8, 2); assert.ok(frames.push('a', new Uint8Array(4))); assert.ok(frames.push('b', new Uint8Array(4))); assert.ok(frames.push('c', new Uint8Array(4))); assert.equal(frames.length, 2); assert.equal(frames.byteLength, 8); assert.equal(frames.push('huge', new Uint8Array(9)), false);
  const governor = new ResourceGovernor(); governor.update({ ...simulatedResources(), cpuPercent: 96, ramUsedMb: 16000, vramUsedMb: 7600 });
  assert.equal(governor.recommendations().captureFps, 1); assert.equal(governor.recommendations().resolutionScale, 0.5); assert.equal(governor.recommendations().allowInference, false); assert.throws(() => governor.assertCanExecute());
});
test('memory rejects restricted/secret input, redacts known tokens and bounds retention', () => {
  const memory = new MemoryService(2); const entry = { taskId: 'task', kind: 'episodic' as const, content: 'api_key=abcdefghi', privacy: 'internal' as const };
  assert.equal(memory.put(entry).content, '[REDACTED]');
  assert.throws(() => memory.put({ ...entry, privacy: 'restricted' })); assert.throws(() => memory.put({ ...entry, containsSecrets: true }));
  memory.put({ ...entry, content: 'two' }); memory.put({ ...entry, content: 'three' }); assert.equal(memory.search().length, 2); assert.equal(memory.search('', false).length, 0);
});
test('model router respects restricted privacy and rejects missing providers without invented output', () => {
  const router = new ModelRouter(); const request = { capability: 'reasoning', privacy: 'restricted' as const, inputTokens: 100, maxOutputTokens: 100, maxCostUsd: 1, maxLatencyMs: 1000, cloudAllowed: true };
  assert.throws(() => router.route(request, simulatedResources()));
  router.register({ descriptor: { id: 'cloud', name: 'Test provider', available: true, locality: 'cloud', capabilities: ['reasoning'], contextTokens: 1000, vramMb: 0, estimatedLatencyMs: 100, costPerMillionTokens: 1 }, invoke: async () => 'unused test provider' });
  assert.throws(() => router.route(request, simulatedResources())); assert.equal(router.route({ ...request, privacy: 'public' }, simulatedResources()).descriptor.id, 'cloud');
  assert.throws(() => router.route({ ...request, privacy: 'public' }, { ...simulatedResources(), networkHealthy: false }));
});
test('authenticated HTTP and WS gateway rejects missing credentials, hostile origins and unknown fields', async () => {
  const token = 'a'.repeat(40); const gateway = createGateway({ token, runtime: createRuntime({ stepDelayMs: 0 }) }); const port = await gateway.listen(0); const base = `http://127.0.0.1:${port}`;
  try {
    assert.equal((await fetch(`${base}/state`)).status, 401);
    assert.equal((await fetch(`${base}/state`, { headers: { authorization: `Bearer ${token}`, origin: 'https://evil.example' } })).status, 403);
    assert.equal((await fetch(`${base}/missions`, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: JSON.stringify({ title: 'Gateway test', command: 'unsafe' }) })).status, 400);
    const ws = new WebSocket(`ws://127.0.0.1:${port}/events`, { headers: { authorization: `Bearer ${token}` } }); await once(ws, 'open');
    const received = once(ws, 'message');
    const response = await fetch(`${base}/missions`, { method: 'POST', headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' }, body: JSON.stringify({ title: 'Gateway test' }) });
    assert.equal(response.status, 202); const task = await response.json() as { id: string };
    const [data] = await received; assert.equal(JSON.parse(String(data)).type, 'TASK_CREATED');
    assert.equal((await gateway.runtime.waitForTask(task.id)).status, 'completed'); ws.close(); await once(ws, 'close');
  } finally { await gateway.close(); }
});
