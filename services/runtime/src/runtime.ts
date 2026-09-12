import { randomUUID } from 'node:crypto';
import { parseContract, type Action, type AgentStatus, type Node, type Plan, type RecoveryPlan, type TaskResult, type VerificationResult, type WorldState } from '@jarvis/contracts';
import { EventBus } from './events.js';
import { CapabilityIssuer, ExecutionDenied, PolicyEngine, StopLatch, policyConfiguration, scopeFor } from './governance.js';
import { MemoryService, ModelRouter } from './memory-models.js';
import { ResourceGovernor } from './resources.js';
import { abortableDelay, runDAG, validateDAG } from './scheduler.js';
import { SimulationAdapter, createSimulationPlan, initialWorld, matches, verify, type ExecutionAdapter } from './simulation.js';
import type { RuntimeState, TaskRecord } from './state.js';

export interface RuntimeOptions { adapter?: ExecutionAdapter; stepDelayMs?: number; eventCapacity?: number; maxTasks?: number }
/** Reference runtime. Durable storage and a real daemon transport are separate integration work. */
export class JarvisRuntime {
  readonly events: EventBus;
  readonly resources = new ResourceGovernor();
  readonly memory = new MemoryService();
  readonly models = new ModelRouter();
  #latch = new StopLatch();
  #issuer = new CapabilityIssuer();
  #policy = new PolicyEngine();
  #adapter: ExecutionAdapter;
  #world: WorldState = initialWorld();
  #tasks = new Map<string, TaskRecord>();
  #queue: string[] = [];
  #draining = false;
  #paused = false;
  #status: AgentStatus = 'IDLE';
  #waiters = new Map<string, ((task: TaskRecord) => void)[]>();
  #maxTasks: number;
  constructor(options: RuntimeOptions = {}) {
    this.events = new EventBus(options.eventCapacity);
    this.#adapter = options.adapter ?? new SimulationAdapter(options.stepDelayMs ?? 150);
    if (this.#adapter.mode !== 'simulation') throw new ExecutionDenied('This reference runtime only accepts simulation adapters');
    this.#maxTasks = options.maxTasks ?? 32;
    if (!Number.isInteger(this.#maxTasks) || this.#maxTasks < 1 || this.#maxTasks > 128) throw new Error('Invalid task retention bound');
  }
  getState(): RuntimeState {
    const resources = this.resources.snapshot();
    resources.observedAt = new Date().toISOString();
    const world = structuredClone(this.#world);
    world.stalenessMs = Math.max(0, Date.now() - Date.parse(world.observedAt));
    return { mode: 'simulation', status: this.#latch.stopped ? 'EMERGENCY_STOP' : this.#paused ? 'PAUSED' : this.#status, emergencyStopped: this.#latch.stopped, paused: this.#paused, tasks: [...this.#tasks.values()].reverse().map(task => structuredClone(task)), events: this.events.replay(), world, resources, models: this.models.list(), memory: this.memory.search(), policy: structuredClone(policyConfiguration) };
  }
  submitMission(title = 'Verify a mission report'): TaskRecord {
    this.#latch.assertRunning();
    if (typeof title !== 'string' || !title.trim() || title.length > 160) throw new Error('Mission title must contain 1–160 characters');
    if (this.#queue.length >= 8) throw new Error('Mission queue capacity reached');
    while (this.#tasks.size >= this.#maxTasks) {
      const terminal = [...this.#tasks.values()].find(task => task.result !== null);
      if (!terminal) throw new Error('Task capacity reached');
      this.#tasks.delete(terminal.id);
    }
    const id = randomUUID();
    const task: TaskRecord = { id, title: title.trim(), status: 'queued', plan: createSimulationPlan(id, title.trim()), result: null, createdAt: new Date().toISOString() };
    this.#tasks.set(id, task);
    this.events.emit('TASK_CREATED', id, 'Simulation mission queued', { status: 'queued' });
    this.events.emit('PLAN_CREATED', id, 'Four bounded nodes created from the fixed simulation recipe');
    this.#queue.push(id);
    void this.drain();
    return structuredClone(task);
  }
  pause(): void { this.#latch.assertRunning(); this.#paused = true; this.events.emit('RUNTIME_PAUSED', 'runtime', 'Scheduling paused; any in-progress action completes verification'); }
  resume(): void { this.#latch.assertRunning(); this.#paused = false; this.events.emit('RUNTIME_RESUMED', 'runtime', 'Scheduling resumed'); void this.drain(); }
  emergencyStop(): void {
    // Cancellation and revocation happen synchronously before optional telemetry.
    this.#latch.stop(); this.#issuer.revokeAll(); this.#paused = false; this.#status = 'EMERGENCY_STOP';
    for (const id of this.#queue.splice(0)) {
      const task = this.#tasks.get(id);
      if (task) this.finish(task, { taskId: id, status: 'cancelled', startedAt: task.createdAt, finishedAt: new Date().toISOString(), completedNodeIds: [], failedNodeIds: [], verificationIds: [], summary: 'Cancelled by the emergency stop latch' });
    }
    this.events.emit('EMERGENCY_STOP', 'runtime', 'Action queue cancelled and all execution capabilities revoked');
  }
  resetEmergencyStop(): void {
    if (this.#draining) throw new Error('Wait for active cancellation to settle before resetting');
    this.#latch.reset(); this.#status = 'IDLE';
    this.events.emit('RUNTIME_RESUMED', 'runtime', 'Emergency latch reset; old capabilities remain revoked');
  }
  async waitForTask(id: string): Promise<TaskRecord> {
    const task = this.#tasks.get(id);
    if (!task) throw new Error('Unknown task');
    if (task.result) return structuredClone(task);
    return new Promise(resolve => { const waiters = this.#waiters.get(id) ?? []; waiters.push(resolve); this.#waiters.set(id, waiters); });
  }
  private async gate(signal: AbortSignal): Promise<void> { signal.throwIfAborted(); while (this.#paused) { await abortableDelay(25, signal); } this.#latch.assertRunning(); }
  private async drain(): Promise<void> {
    if (this.#draining || this.#paused || this.#latch.stopped) return;
    this.#draining = true;
    try {
      while (this.#queue.length && !this.#latch.stopped) {
        await this.gate(this.#latch.signal);
        const id = this.#queue.shift();
        const task = id ? this.#tasks.get(id) : undefined;
        if (!task) continue;
        task.status = 'running';
        try { this.finish(task, await this.executePlan(task.plan)); }
        catch (error) { this.finish(task, { taskId: task.id, status: this.#latch.stopped ? 'cancelled' : 'failed', startedAt: task.createdAt, finishedAt: new Date().toISOString(), completedNodeIds: [], failedNodeIds: [], verificationIds: [], summary: error instanceof Error ? error.message : 'Execution failed closed' }); }
      }
    } catch { /* latch cancellation exits the bounded queue */ }
    finally { this.#draining = false; }
  }
  private finish(task: TaskRecord, value: TaskResult) {
    task.result = parseContract('TaskResult', value); task.status = value.status;
    this.#issuer.revokeTask(task.id);
    this.#status = this.#latch.stopped ? 'EMERGENCY_STOP' : value.status === 'completed' ? 'COMPLETED' : 'ERROR';
    this.events.emit(value.status === 'completed' ? 'TASK_COMPLETED' : value.status === 'cancelled' ? 'TASK_CANCELLED' : 'TASK_FAILED', task.id, value.summary, { status: value.status });
    this.memory.put({ taskId: task.id, kind: value.status === 'completed' ? 'episodic' : 'failure', privacy: 'internal', content: `${value.status}: ${value.completedNodeIds.length} verified simulation steps.` });
    for (const resolve of this.#waiters.get(task.id) ?? []) resolve(structuredClone(task));
    this.#waiters.delete(task.id);
  }
  private async executePlan(planInput: Plan): Promise<TaskResult> {
    const plan = parseContract('Plan', planInput);
    validateDAG(plan.dag);
    if (plan.dag.nodes.some(node => node.action.taskId !== plan.taskId)) throw new ExecutionDenied('Plan/action task binding mismatch');
    const startedAt = new Date().toISOString();
    const deadline = AbortSignal.timeout(plan.maxDurationMs);
    const signal = AbortSignal.any([this.#latch.signal, deadline]);
    const capability = this.#issuer.issue('simulation-executor', plan.taskId, ['OBSERVE', 'READ_FILE', 'WRITE_FILE', 'WAIT', 'VERIFY'], ['simulation:observe', 'simulation:file.read', 'simulation:file.write'], 64, plan.maxDurationMs);
    const verificationIds: string[] = [];
    this.#status = 'PLANNING';
    this.events.emit('PLAN_APPROVED', plan.taskId, 'Fixed simulation recipe admitted for per-action policy checks');
    const outcome = await runDAG(plan.dag, async (node, nodeSignal) => {
      for (let attempt = 0; ; attempt++) {
        try {
          await this.gate(nodeSignal);
          this.resources.assertCanExecute();
          const before = await this.#adapter.observe(plan.taskId, nodeSignal);
          const action = parseContract('Action', { ...node.action, observationId: before.id });
          this.events.emit('OBSERVATION_CAPTURED', plan.taskId, 'Fresh virtual workspace evidence captured', { nodeId: node.id, actionId: action.id, evidenceIds: before.evidence.map(item => item.id), revision: before.revision });
          const decision = this.#policy.evaluate(action, before, this.#latch.stopped);
          if (decision.decision !== 'allow') {
            if (decision.decision === 'approval_required') this.events.emit('USER_APPROVAL_REQUIRED', plan.taskId, 'High-impact action requires an approval integration', { actionId: action.id, nodeId: node.id });
            throw new ExecutionDenied(decision.reasons.join('; '));
          }
          if (!action.preconditions.every(condition => matches(condition, before.facts))) throw new ExecutionDenied('Action preconditions were not observed');
          nodeSignal.throwIfAborted(); this.#latch.assertRunning();
          this.#issuer.consume(capability, 'simulation-executor', action, scopeFor(action));
          const actionSignal = AbortSignal.any([nodeSignal, AbortSignal.timeout(action.timeoutMs)]);
          this.#status = 'EXECUTING';
          this.events.emit('ACTION_STARTED', plan.taskId, node.label, { actionId: action.id, nodeId: node.id, status: 'EXECUTING' });
          await this.#adapter.execute(action, actionSignal);
          actionSignal.throwIfAborted();
          this.events.emit('ACTION_EXECUTED', plan.taskId, 'Simulation adapter returned; state is still uncommitted', { actionId: action.id, nodeId: node.id });
          this.#status = 'VERIFYING';
          const after = await this.#adapter.observe(plan.taskId, actionSignal);
          const verification = verify(action, after);
          verificationIds.push(verification.id);
          this.events.emit('ACTION_VERIFIED', plan.taskId, verification.reason, { actionId: action.id, nodeId: node.id, status: verification.status, evidenceIds: verification.checks.flatMap(check => check.evidenceIds) });
          if (verification.status !== 'passed') throw new Error('Postcondition verification failed');
          actionSignal.throwIfAborted(); this.#latch.assertRunning();
          this.commit(action, after, verification);
          this.events.emit('STATE_CHANGED', plan.taskId, 'Verified observation committed to the world model', { actionId: action.id, nodeId: node.id, revision: this.#world.revision });
          if (attempt) this.events.emit('RECOVERY_COMPLETED', plan.taskId, 'Recovery succeeded after fresh observation and verification', { actionId: action.id, nodeId: node.id });
          break;
        } catch (error) {
          if (nodeSignal.aborted || this.#latch.stopped) throw error;
          const recovery = this.recoveryFor(node, attempt, error);
          this.events.emit('ERROR_DETECTED', plan.taskId, recovery.reason, { actionId: node.action.id, nodeId: node.id });
          if (recovery.strategy === 'escalate') throw error;
          this.#status = 'RECOVERING';
          this.events.emit('RECOVERY_STARTED', plan.taskId, `Bounded read recovery ${recovery.attempt}/${recovery.maxAttempts}; re-observe before retry`, { actionId: node.action.id, nodeId: node.id });
          await abortableDelay(recovery.backoffMs, nodeSignal);
        }
      }
    }, signal);
    const status = this.#latch.stopped ? 'cancelled' : outcome.failed.length || outcome.completed.length !== plan.dag.nodes.length ? 'failed' : 'completed';
    return { taskId: plan.taskId, status, startedAt, finishedAt: new Date().toISOString(), completedNodeIds: outcome.completed, failedNodeIds: outcome.failed, verificationIds, summary: status === 'completed' ? 'Simulation mission completed with every postcondition verified. No host files or applications were changed.' : status === 'cancelled' ? 'Mission cancelled; no further state commits are allowed.' : 'Mission failed closed; inspect the recorded evidence and recovery events.' };
  }
  private recoveryFor(node: Node, attempt: number, error: unknown): RecoveryPlan {
    const safeToRetry = ['OBSERVE', 'READ_FILE', 'VERIFY'].includes(node.action.type) && !(error instanceof ExecutionDenied);
    return parseContract('RecoveryPlan', { id: randomUUID(), taskId: node.action.taskId, actionId: node.action.id, category: error instanceof ExecutionDenied ? 'permission_denied' : 'adapter_error', strategy: safeToRetry && attempt < node.maxRetries ? 'retry_read' : 'escalate', attempt: attempt + 1, maxAttempts: node.maxRetries, backoffMs: Math.min(2000, 100 * 2 ** attempt), requiresApproval: !safeToRetry, reason: error instanceof Error ? error.message.slice(0, 4096) : 'Unknown adapter failure' });
  }
  private commit(action: Action, observation: Parameters<typeof verify>[1], result: VerificationResult) {
    if (result.status !== 'passed') throw new ExecutionDenied('Unverified state cannot be committed');
    const observed = parseContract('Observation', observation);
    this.#world = parseContract('WorldState', { id: randomUUID(), observedAt: observed.observedAt, source: observed.source, confidence: observed.confidence, stalenessMs: 0, dependencies: [observed.id, result.id], revision: this.#world.revision + 1, facts: observed.facts, committedActionIds: [...this.#world.committedActionIds, action.id].slice(-4096) });
  }
}
export function createRuntime(options: RuntimeOptions = {}) { return new JarvisRuntime(options); }
