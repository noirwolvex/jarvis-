import { randomUUID } from 'node:crypto';
import type { Privacy, ResourceSnapshot } from '@jarvis/contracts';
import type { MemoryEntry, ModelDescriptor } from './state.js';

export class MemoryService {
  #entries: MemoryEntry[] = [];
  constructor(readonly capacity = 256, readonly retentionMs = 7 * 86400000) { if (!Number.isInteger(capacity) || capacity < 1 || retentionMs < 1) throw new Error('Invalid memory bounds'); }
  put(entry: Omit<MemoryEntry, 'id' | 'createdAt'> & { containsSecrets?: boolean }): MemoryEntry {
    if (entry.containsSecrets || entry.privacy === 'restricted') throw new Error('Secrets and restricted content cannot enter ordinary memory');
    if (entry.content.length > 4096) throw new Error('Memory entry too large');
    const content = entry.content.replace(/\b(?:sk-[\w-]{12,}|Bearer\s+[\w.\-]+|(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S+)/gi, '[REDACTED]');
    const result: MemoryEntry = { id: randomUUID(), taskId: entry.taskId, kind: entry.kind, privacy: entry.privacy, content, createdAt: new Date().toISOString() };
    this.prune(); this.#entries.push(result); if (this.#entries.length > this.capacity) this.#entries.shift();
    return structuredClone(result);
  }
  private prune() { this.#entries = this.#entries.filter(entry => Date.parse(entry.createdAt) >= Date.now() - this.retentionMs); }
  search(query = '', allowInternal = true): MemoryEntry[] { this.prune(); return this.#entries.filter(entry => (allowInternal || entry.privacy === 'public') && entry.content.toLowerCase().includes(query.toLowerCase())).map(entry => structuredClone(entry)); }
  forget(id: string) { this.#entries = this.#entries.filter(entry => entry.id !== id); }
}
export interface ModelRequest { capability: string; privacy: Privacy; inputTokens: number; maxOutputTokens: number; maxCostUsd: number; maxLatencyMs: number; cloudAllowed: boolean }
export interface ModelProvider { descriptor: ModelDescriptor; invoke(input: string, signal: AbortSignal): Promise<string> }
/** Routing is executable; no model output is fabricated when a provider is absent. */
export class ModelRouter {
  #providers = new Map<string, ModelProvider>();
  register(provider: ModelProvider) { this.#providers.set(provider.descriptor.id, provider); }
  list(): ModelDescriptor[] { return [...this.#providers.values()].map(provider => structuredClone(provider.descriptor)); }
  route(request: ModelRequest, resources: ResourceSnapshot): ModelProvider {
    if (!Number.isInteger(request.inputTokens) || !Number.isInteger(request.maxOutputTokens) || request.inputTokens < 0 || request.maxOutputTokens < 0 || !Number.isFinite(request.maxCostUsd) || request.maxCostUsd < 0 || request.maxLatencyMs < 0) throw new Error('Invalid model request budget');
    const candidates = [...this.#providers.values()].filter(({ descriptor: d }) => d.available && d.capabilities.includes(request.capability) && d.contextTokens >= request.inputTokens + request.maxOutputTokens && d.estimatedLatencyMs <= request.maxLatencyMs && ((request.inputTokens + request.maxOutputTokens) / 1000000) * d.costPerMillionTokens <= request.maxCostUsd && (d.locality === 'local' ? d.vramMb <= resources.vramTotalMb - resources.vramUsedMb && resources.cpuPercent < 95 && resources.ramUsedMb / resources.ramTotalMb < 0.9 && !resources.thermalPressure : request.privacy !== 'restricted' && request.cloudAllowed && resources.networkHealthy));
    candidates.sort((a, b) => a.descriptor.costPerMillionTokens - b.descriptor.costPerMillionTokens || a.descriptor.estimatedLatencyMs - b.descriptor.estimatedLatencyMs || a.descriptor.id.localeCompare(b.descriptor.id));
    const selected = candidates[0];
    if (!selected) throw new Error('No configured provider meets privacy, capability, and resource budgets');
    return selected;
  }
}
