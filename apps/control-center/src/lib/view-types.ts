export type PageId = "mission" | "vision" | "graph" | "timeline" | "memory" | "models" | "security" | "resources" | "recovery";
export type NodeView = { id: string; title: string; status: string; action: string; dependencies: string[] };
export type TaskView = { id: string; title: string; status: string; createdAt: string; nodes: NodeView[]; summary: string };
export type EventView = { sequence: number; id: string; type: string; timestamp: string; taskId: string; summary: string };
export type MemoryView = { id: string; kind: string; content: string; confidence: number | null; createdAt: string };
export type ModelView = { id: string; name: string; tier: string; available: boolean; summary: string };
export type NativeView = {
  daemonSimulation: boolean;
  nativeInput: boolean;
  foreground: { process_id: number; title: string } | null;
  capture: {
    frameId: string;
    capturedAtMs: number;
    sha256: string;
    display: { id: number; x: number; y: number; width: number; height: number; scale: number };
    previewDataUrl: string;
    previewWidth: number;
    previewHeight: number;
  } | null;
};
export type Snapshot = {
  mode: "simulation" | "native";
  status: string;
  emergencyStopped: boolean;
  tasks: TaskView[];
  events: EventView[];
  facts: { key: string; value: string }[];
  worldVersion: number;
  memory: MemoryView[];
  models: ModelView[];
  capabilities: { id: string; permission: string; scope: string; expiresAt: string }[];
  telemetry: { processRssMb: number; hostRamUsedGb: number; hostRamTotalGb: number; processUptime: number; sampledAt: string };
  native: NativeView | null;
};

export const initialSnapshot: Snapshot = {
  mode: "simulation", status: "IDLE", emergencyStopped: false, tasks: [], events: [],
  facts: [], worldVersion: 0, memory: [], models: [], capabilities: [], native: null,
  telemetry: { processRssMb: 0, hostRamUsedGb: 0, hostRamTotalGb: 0, processUptime: 0, sampledAt: "" }
};
