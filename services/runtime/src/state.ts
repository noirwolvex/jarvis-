import type { AgentEvent, AgentStatus, Plan, ResourceSnapshot, TaskResult, WorldState, Privacy } from '@jarvis/contracts';
export interface TaskRecord { id: string; title: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; plan: Plan; result: TaskResult | null; createdAt: string }
export interface ModelDescriptor { id: string; name: string; locality: 'local' | 'cloud'; available: boolean; capabilities: string[]; contextTokens: number; vramMb: number; estimatedLatencyMs: number; costPerMillionTokens: number }
export interface MemoryEntry { id: string; taskId: string; kind: 'working' | 'episodic' | 'semantic' | 'procedural' | 'failure' | 'preference'; content: string; privacy: Privacy; createdAt: string }
export interface RuntimeState {
  mode: 'simulation'; status: AgentStatus; emergencyStopped: boolean; paused: boolean;
  tasks: TaskRecord[]; events: AgentEvent[]; world: WorldState; resources: ResourceSnapshot;
  models: ModelDescriptor[]; memory: MemoryEntry[];
  policy: { version: number; mode: string; maxObservationAgeMs: number; minimumConfidence: number; approvalRiskThreshold: number; allowedActionTypes: string[]; deniedActionTypes: string[] };
}
