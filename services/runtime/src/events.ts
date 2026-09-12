import { createHash, randomUUID } from 'node:crypto';
import { parseContract, type AgentEvent, type EventType } from '@jarvis/contracts';

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') { Object.freeze(value); for (const child of Object.values(value)) freeze(child); }
  return value;
}
export class ReplayGapError extends Error {}
/** Process-local replay is bounded. An explicit gap requires consumers to fetch a snapshot. */
export class EventBus {
  #events: AgentEvent[] = [];
  #listeners = new Set<(event: AgentEvent) => void>();
  #sequence = 0;
  #lastHash = '0'.repeat(64);
  constructor(readonly capacity = 2048) { if (!Number.isInteger(capacity) || capacity < 1 || capacity > 100000) throw new Error('Invalid event capacity'); }
  emit(type: EventType, taskId: string, message: string, options: { actionId?: string; nodeId?: string; status?: string; evidenceIds?: string[]; revision?: number } = {}): AgentEvent {
    const body = { id: randomUUID(), sequence: this.#sequence + 1, taskId, timestamp: new Date().toISOString(), type, source: 'typescript-simulation-runtime', traceId: taskId, actionId: options.actionId ?? null, payload: { message, nodeId: options.nodeId ?? null, status: options.status ?? null, evidenceIds: options.evidenceIds ?? [], revision: options.revision ?? null }, previousHash: this.#lastHash };
    const event = freeze(parseContract('AgentEvent', { ...body, hash: createHash('sha256').update(JSON.stringify(body)).digest('hex') }));
    this.#sequence = event.sequence;
    this.#lastHash = event.hash;
    this.#events.push(event);
    if (this.#events.length > this.capacity) this.#events.shift();
    // A broken telemetry consumer must never alter execution state.
    for (const listener of this.#listeners) { try { listener(event); } catch { /* isolated observer */ } }
    return event;
  }
  replay(afterSequence = 0): AgentEvent[] {
    if (!Number.isSafeInteger(afterSequence) || afterSequence < 0 || afterSequence > this.#sequence) throw new Error('Invalid replay cursor');
    const first = this.#events[0];
    if (afterSequence > 0 && first && afterSequence < first.sequence - 1) throw new ReplayGapError('Replay retention exceeded; refresh state');
    return this.#events.filter(event => event.sequence > afterSequence).map(event => structuredClone(event));
  }
  subscribe(listener: (event: AgentEvent) => void): () => void { this.#listeners.add(listener); return () => this.#listeners.delete(listener); }
  get sequence() { return this.#sequence; }
  static verifyChain(events: AgentEvent[]): boolean {
    return events.every((event, index) => {
      const { hash, ...body } = event;
      const previous = events[index - 1];
      return createHash('sha256').update(JSON.stringify(body)).digest('hex') === hash && (!previous || (event.previousHash === previous.hash && event.sequence === previous.sequence + 1));
    });
  }
}
