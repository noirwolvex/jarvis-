import { createHash, randomUUID } from 'node:crypto';
import { parseContract, type Action, type Condition, type FactValue, type Observation, type Plan, type VerificationResult, type WorldState } from '@jarvis/contracts';
import { abortableDelay } from './scheduler.js';

export function matches(condition: Condition, facts: Record<string, FactValue>): boolean {
  const exists = Object.prototype.hasOwnProperty.call(facts, condition.key);
  if (condition.operator === 'exists') return exists;
  if (condition.operator === 'absent') return !exists;
  return exists && facts[condition.key] === condition.value;
}
export const digest = (content: string) => createHash('sha256').update(content).digest('hex');
export interface ExecutionAdapter { readonly mode: string; observe(taskId: string, signal: AbortSignal): Promise<Observation>; execute(action: Action, signal: AbortSignal): Promise<void> }

/** An isolated virtual file system. This class never reads or writes host files. */
export class SimulationAdapter implements ExecutionAdapter {
  readonly mode = 'simulation';
  #files = new Map<string, string>([['/workspace/mission-brief.txt', 'JARVIS X simulation: observe, authorize, execute, verify, commit.']]);
  #revision = 0;
  constructor(readonly latencyMs = 150) {}
  async observe(taskId: string, signal: AbortSignal): Promise<Observation> {
    await abortableDelay(this.latencyMs, signal);
    const observedAt = new Date().toISOString();
    const facts: Record<string, FactValue> = { 'environment.mode': 'simulation', 'application.active': 'Mission workspace', 'ui.stable': true };
    for (const [path, content] of this.#files) { facts[`file:${path}:exists`] = true; facts[`file:${path}:sha256`] = digest(content); facts[`file:${path}:bytes`] = Buffer.byteLength(content); }
    return parseContract('Observation', { id: randomUUID(), taskId, observedAt, source: 'simulation', confidence: 1, validForMs: 3000, stability: 'UI_STABLE', revision: this.#revision, facts, evidence: Object.entries(facts).map(([key, value]) => ({ id: randomUUID(), source: 'simulation', observedAt, key, value })) });
  }
  async execute(action: Action, signal: AbortSignal): Promise<void> {
    await abortableDelay(action.type === 'WAIT' ? action.args.durationMs : this.latencyMs, signal);
    signal.throwIfAborted();
    switch (action.type) {
      case 'OBSERVE': case 'WAIT': case 'VERIFY': return;
      case 'READ_FILE': {
        const content = this.#files.get(action.args.path);
        if (content === undefined) throw new Error('Virtual file is missing');
        if (Buffer.byteLength(content) > action.args.maxBytes) throw new Error('Virtual read byte limit exceeded');
        return;
      }
      case 'WRITE_FILE': {
        if (!action.args.path.startsWith('/workspace/') || action.args.path.includes('..')) throw new Error('Invalid virtual path');
        if (this.#files.has(action.args.path) && !action.args.overwrite) throw new Error('Virtual file already exists');
        const size = [...this.#files].reduce((sum, [path, content]) => sum + (path === action.args.path ? 0 : Buffer.byteLength(content)), 0) + Buffer.byteLength(action.args.content);
        if (this.#files.size >= 64 || size > 4 * 1024 * 1024) throw new Error('Virtual workspace capacity exceeded');
        this.#files.set(action.args.path, action.args.content); this.#revision++; return;
      }
      default: throw new Error(`Simulation adapter does not implement ${action.type}`);
    }
  }
}
/** Uses post-action observations, not the action's confidence or executor's success flag. */
export function verify(actionInput: unknown, observationInput: unknown): VerificationResult {
  const action = parseContract('Action', actionInput);
  const observation = parseContract('Observation', observationInput);
  const now = Date.now();
  const age = now - Date.parse(observation.observedAt);
  const fresh = action.taskId === observation.taskId && observation.id !== action.observationId && age >= -1000 && age <= Math.min(observation.validForMs, action.verification.timeoutMs) && observation.stability === 'UI_STABLE';
  const checks = action.expectedState.map(condition => {
    const evidence = observation.evidence.filter(item => item.key === condition.key && item.value === observation.facts[condition.key] && item.observedAt === observation.observedAt);
    // Absence needs an explicit false/existence fact; absence alone is not evidence.
    const evidenceIds = [...new Set(evidence.map(item => item.id))];
    return { condition, passed: fresh && matches(condition, observation.facts) && evidenceIds.length >= action.verification.minEvidence, evidenceIds };
  });
  const passed = checks.length > 0 && checks.every(check => check.passed);
  return parseContract('VerificationResult', { id: randomUUID(), taskId: action.taskId, actionId: action.id, observationId: observation.id, status: passed ? 'passed' : 'failed', confidence: passed ? 1 : 0, checkedAt: new Date(now).toISOString(), checks, reason: passed ? 'All postconditions matched fresh independent observation evidence' : 'Postconditions, evidence, observation binding, or freshness requirements failed' });
}
export function initialWorld(): WorldState { return { id: randomUUID(), observedAt: new Date().toISOString(), source: 'simulation', confidence: 0, stalenessMs: 0, dependencies: [], revision: 0, facts: { 'environment.mode': 'simulation' }, committedActionIds: [] }; }
export const missionOutput = 'Verified mission artifact\nSource: in-memory simulation\nControl loop: observe → authorize → execute → verify → commit\n';
export function createSimulationPlan(taskId: string, title: string): Plan {
  const condition = (key: string, value: FactValue): Condition => ({ key, operator: 'equals', value });
  const base = { taskId, observationId: 'pending-observation', confidence: 0.99, riskLevel: 1 as const, preconditions: [condition('environment.mode', 'simulation')], verification: { method: 'FILE_STATE' as const, minEvidence: 1, timeoutMs: 3000 }, timeoutMs: 5000, rollback: { strategy: 'none' as const, snapshotId: null } };
  const outputPath = `/workspace/report-${taskId}.txt`;
  const actions: Action[] = [
    { ...base, id: randomUUID(), type: 'OBSERVE', args: { scope: 'desktop', application: 'Mission workspace' }, requiredCapabilities: ['simulation:observe'], expectedState: [condition('ui.stable', true)] },
    { ...base, id: randomUUID(), type: 'READ_FILE', args: { path: '/workspace/mission-brief.txt', maxBytes: 4096 }, requiredCapabilities: ['simulation:file.read'], expectedState: [condition('file:/workspace/mission-brief.txt:exists', true)] },
    { ...base, id: randomUUID(), type: 'WRITE_FILE', args: { path: outputPath, content: missionOutput, overwrite: false }, requiredCapabilities: ['simulation:file.write'], expectedState: [condition(`file:${outputPath}:sha256`, digest(missionOutput))] },
    { ...base, id: randomUUID(), type: 'VERIFY', args: { conditions: [condition(`file:${outputPath}:sha256`, digest(missionOutput))] }, requiredCapabilities: ['simulation:observe'], expectedState: [condition(`file:${outputPath}:sha256`, digest(missionOutput))] },
  ];
  const labels = ['Observe virtual workspace', 'Read mission brief', 'Create virtual report', 'Verify report evidence'];
  return parseContract('Plan', { id: randomUUID(), taskId, intentId: randomUUID(), version: 1, createdAt: new Date().toISOString(), summary: `Fixed simulation mission: ${title}`, maxDurationMs: 60000, dag: { id: randomUUID(), maxConcurrency: 1, nodes: actions.map((action, index) => ({ id: `node-${index + 1}`, label: labels[index], dependsOn: index ? [`node-${index}`] : [], action, maxRetries: action.type === 'WRITE_FILE' ? 0 : 1 })) } });
}
