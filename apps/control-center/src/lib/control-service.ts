import { createRuntime, type JarvisRuntime } from "@jarvis/runtime";
import { freemem, totalmem } from "node:os";
import type { Snapshot } from "./view-types";

// Local, single-process simulation. Never share this instance with native execution.
const store = globalThis as typeof globalThis & { jarvisSimulation?: JarvisRuntime };
export function simulationRuntime() {
  return store.jarvisSimulation ??= createRuntime();
}

export function snapshotForView(runtime: JarvisRuntime): Snapshot {
  const state = runtime.getState();
  return {
    mode: "simulation", status: state.status, emergencyStopped: state.emergencyStopped,
    tasks: state.tasks.map(task => ({
      id: task.id, title: task.title, status: task.status.toUpperCase(), createdAt: task.createdAt,
      summary: task.result?.summary ?? "",
      nodes: task.plan.dag.nodes.map(node => {
        const events = state.events.filter(e => e.taskId === task.id && e.payload.nodeId === node.id);
        const last = events.at(-1);
        const committed = task.result?.completedNodeIds.includes(node.id) || events.some(e => e.type === "STATE_CHANGED");
        const failed = task.result?.failedNodeIds.includes(node.id);
        return { id: node.id, title: node.label, action: node.action.type, dependencies: node.dependsOn,
          status: committed ? "VERIFIED" : failed ? "FAILED" : task.status === "cancelled" ? "CANCELLED" : last?.type === "ACTION_STARTED" ? "EXECUTING" : last?.type === "ACTION_EXECUTED" ? "VERIFYING" : "PENDING" };
      })
    })),
    events: state.events.map(e => ({ sequence: e.sequence, id: e.id, type: e.type, timestamp: e.timestamp, taskId: e.taskId, summary: e.payload.message })),
    facts: Object.entries(state.world.facts).map(([key, value]) => ({ key, value: String(value) })),
    worldVersion: state.world.revision,
    memory: state.memory.map(m => ({ id: m.id, kind: m.kind, content: m.content, confidence: null, createdAt: m.createdAt })),
    models: state.models.map(m => ({ id: m.id, name: m.name, tier: m.locality, available: m.available, summary: m.capabilities.join(", ") })),
    capabilities: [], // Private issuer grants are not exposed by the runtime's projection.
    telemetry: {
      processRssMb: Math.round(process.memoryUsage().rss / 1024 ** 2),
      hostRamUsedGb: (totalmem() - freemem()) / 1024 ** 3,
      hostRamTotalGb: totalmem() / 1024 ** 3,
      processUptime: Math.floor(process.uptime()), sampledAt: new Date().toISOString()
    }
  };
}

export function assertLocalRequest(request: Request, mutation = false) {
  const url = new URL(request.url);
  const host = request.headers.get("host");
  if (!host) throw new Error("Local host required");
  const authority = new URL(`${url.protocol}//${host}`);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(authority.hostname)) throw new Error("Local host required");
  const origin = request.headers.get("origin");
  if (origin && origin !== authority.origin) throw new Error("Cross-origin access denied");
  if (request.headers.get("sec-fetch-site") === "cross-site") throw new Error("Cross-site access denied");
  if (mutation && request.headers.get("x-jarvis-control") !== "simulation") throw new Error("Simulation control header required");
}

export async function parseControlBody(request: Request): Promise<{ action: string; title?: string }> {
  if (request.headers.get("content-type")?.split(";")[0]?.trim() !== "application/json") throw new Error("JSON content type required");
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Request body required");
  const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2048) { await reader.cancel(); throw new Error("Control body exceeds 2 KiB"); }
      parts.push(value);
    }
  } finally { reader.releaseLock(); }
  const body: unknown = JSON.parse(Buffer.concat(parts).toString("utf8"));
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Expected a control object");
  const value = body as Record<string, unknown>;
  if (Object.keys(value).some(k => k !== "action" && k !== "title")) throw new Error("Unknown control property");
  if (typeof value.action !== "string" || !["run", "pause", "resume", "stop", "reset"].includes(value.action)) throw new Error("Unknown control action");
  if (value.action === "run") {
    if (typeof value.title !== "string" || !value.title.trim() || value.title.length > 160) throw new Error("Mission title must contain 1–160 characters");
    return { action: value.action, title: value.title.trim() };
  }
  if (value.title !== undefined) throw new Error("Only run accepts a title");
  return { action: value.action };
}
