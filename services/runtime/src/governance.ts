import { createHmac, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { parseContract, type Action, type Capability, type Observation, type PolicyDecision } from '@jarvis/contracts';

export class ExecutionDenied extends Error { constructor(message: string) { super(message); this.name = 'ExecutionDenied'; } }
/** Independent latch: no database, model or event service is called on the stop path. */
export class StopLatch {
  #stopped = false;
  #controller = new AbortController();
  get stopped() { return this.#stopped; }
  get signal() { return this.#controller.signal; }
  assertRunning() { if (this.#stopped) throw new ExecutionDenied('Emergency stop latched'); }
  stop() { this.#stopped = true; this.#controller.abort(new ExecutionDenied('Emergency stop latched')); }
  reset() { this.#controller = new AbortController(); this.#stopped = false; }
}
export class CapabilityIssuer {
  #key = randomBytes(32);
  #issued = new Map<string, { capability: Capability; used: number }>();
  readonly issuer = randomUUID();
  issue(subject: string, taskId: string, actionTypes: Action['type'][], scopes: string[], maxUses = 32, ttlMs = 60000): Capability {
    if (!Number.isInteger(ttlMs) || ttlMs < 1 || ttlMs > 600000) throw new ExecutionDenied('Invalid capability lifetime');
    if (this.#issued.size >= 256) throw new ExecutionDenied('Capability capacity exhausted');
    const body = { id: randomUUID(), issuer: this.issuer, subject, taskId, actionTypes, scopes, issuedAt: new Date().toISOString(), expiresAt: new Date(Date.now() + ttlMs).toISOString(), maxUses };
    const capability = parseContract('Capability', { ...body, signature: this.sign(body) });
    this.#issued.set(capability.id, { capability: structuredClone(capability), used: 0 });
    return capability;
  }
  private sign(value: object) { return createHmac('sha256', this.#key).update(JSON.stringify(value)).digest('hex'); }
  consume(value: unknown, subject: string, action: Action, requiredScope: string): void {
    const capability = parseContract('Capability', value);
    const record = this.#issued.get(capability.id);
    const { signature, ...body } = capability;
    const expected = Buffer.from(this.sign(body), 'hex');
    const actual = Buffer.from(signature, 'hex');
    if (!record || actual.length !== expected.length || !timingSafeEqual(actual, expected) || JSON.stringify(record.capability) !== JSON.stringify(capability)) throw new ExecutionDenied('Capability not issued by this authority');
    if (capability.subject !== subject || capability.taskId !== action.taskId || Date.parse(capability.expiresAt) <= Date.now() || Date.parse(capability.issuedAt) > Date.now() || record.used >= capability.maxUses) throw new ExecutionDenied('Capability expired, exhausted, or incorrectly bound');
    if (!capability.actionTypes.includes(action.type) || !capability.scopes.includes(requiredScope) || !action.requiredCapabilities.every(scope => capability.scopes.includes(scope))) throw new ExecutionDenied('Capability scope denied');
    record.used++;
  }
  revokeTask(taskId: string) { for (const [key, record] of this.#issued) if (record.capability.taskId === taskId) this.#issued.delete(key); }
  revokeAll() { this.#issued.clear(); }
}
export const policyConfiguration = Object.freeze({ version: 1, mode: 'simulation-only', maxObservationAgeMs: 3000, minimumConfidence: 0.8, approvalRiskThreshold: 3, allowedActionTypes: ['OBSERVE', 'READ_FILE', 'WRITE_FILE', 'WAIT', 'VERIFY'], deniedActionTypes: ['TERMINAL_COMMAND', 'START_PROCESS', 'STOP_PROCESS', 'NAVIGATE', 'CLICK', 'DOUBLE_CLICK', 'TYPE', 'KEY_PRESS', 'SCROLL', 'DRAG'] });
export function scopeFor(action: Action): string {
  switch (action.type) {
    case 'READ_FILE': return 'simulation:file.read';
    case 'WRITE_FILE': return 'simulation:file.write';
    case 'OBSERVE': case 'WAIT': case 'VERIFY': return 'simulation:observe';
    default: return 'unsupported';
  }
}
export class PolicyEngine {
  evaluate(actionInput: unknown, observationInput: unknown, stopped = false, now = Date.now()): PolicyDecision {
    const action = parseContract('Action', actionInput);
    const observation = parseContract('Observation', observationInput);
    const reasons: string[] = [];
    if (stopped) reasons.push('Emergency stop is latched');
    if (!policyConfiguration.allowedActionTypes.includes(action.type)) reasons.push('Real OS and network execution are disabled in the simulation runtime');
    const age = now - Date.parse(observation.observedAt);
    if (action.observationId !== observation.id || action.taskId !== observation.taskId || age < -1000 || age > Math.min(observation.validForMs, policyConfiguration.maxObservationAgeMs)) reasons.push('Observation binding or freshness failed');
    if (observation.stability !== 'UI_STABLE') reasons.push('Observation is not stable');
    if (action.confidence < policyConfiguration.minimumConfidence || observation.confidence < policyConfiguration.minimumConfidence) reasons.push('Confidence requires additional observation');
    if (observation.evidence.length === 0) reasons.push('Independent observation evidence is missing');
    const scope = scopeFor(action);
    if (!action.requiredCapabilities.includes(scope)) reasons.push('Action omitted its authority-derived required capability');
    if ((action.type === 'READ_FILE' || action.type === 'WRITE_FILE') && (!/^\/workspace\/[A-Za-z0-9_/-]+\.[A-Za-z0-9]+$/.test(action.args.path) || action.args.path.includes('..'))) reasons.push('Path is outside the virtual workspace allowlist');
    const decision = reasons.length ? 'deny' : action.riskLevel >= policyConfiguration.approvalRiskThreshold ? 'approval_required' : 'allow';
    return parseContract('PolicyDecision', { id: randomUUID(), taskId: action.taskId, actionId: action.id, decision, reasons: reasons.length ? reasons : [decision === 'allow' ? 'Simulation scope, freshness, and evidence requirements met' : 'Impact requires a separately authenticated approval'], requiredCapabilities: [scope], evaluatedAt: new Date(now).toISOString(), policyVersion: policyConfiguration.version });
  }
}
export function ensureFresh(observation: Observation, action: Action) {
  const decision = new PolicyEngine().evaluate(action, observation);
  if (decision.decision !== 'allow') throw new ExecutionDenied(decision.reasons.join('; '));
}
