import { createRuntime, type JarvisRuntime } from "@jarvis/runtime";
import { freemem, totalmem } from "node:os";
import {
  daemonCapture,
  daemonClick,
  daemonEmergencyStop,
  daemonStatus,
  daemonTypeText,
  type DaemonCapture,
  type DaemonStatus,
} from "./daemon-client";
import type { EventView, Snapshot } from "./view-types";

export type ControlMode = "simulation" | "native";
export type ControlInput =
  | { action: "run"; title: string }
  | { action: "pause" | "resume" | "stop" | "reset" | "capture" }
  | { action: "click"; x: number; y: number }
  | { action: "type"; text: string };

// Simulation remains isolated from native execution. Native state contains only
// bounded view metadata and the most recent daemon capture preview.
const store = globalThis as typeof globalThis & {
  jarvisSimulation?: JarvisRuntime;
  jarvisNativeView?: { capture?: DaemonCapture; events: EventView[]; nextSequence: number };
};
export function simulationRuntime() {
  return store.jarvisSimulation ??= createRuntime();
}
function nativeView() {
  return store.jarvisNativeView ??= { events: [], nextSequence: 0 };
}

export function controlMode(env: NodeJS.ProcessEnv = process.env): ControlMode {
  const value = (env.JARVIS_CONTROL_MODE?.trim().toLowerCase() || "simulation");
  if (value !== "simulation" && value !== "native") throw new Error("JARVIS_CONTROL_MODE must be simulation or native");
  return value;
}

function telemetry() {
  return {
    processRssMb: Math.round(process.memoryUsage().rss / 1024 ** 2),
    hostRamUsedGb: (totalmem() - freemem()) / 1024 ** 3,
    hostRamTotalGb: totalmem() / 1024 ** 3,
    processUptime: Math.floor(process.uptime()),
    sampledAt: new Date().toISOString(),
  };
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
    capabilities: [],
    telemetry: telemetry(),
    native: null,
  };
}

function recordNativeEvent(type: string, summary: string) {
  const state = nativeView();
  state.nextSequence += 1;
  state.events.push({
    sequence: state.nextSequence,
    id: `native-${state.nextSequence}`,
    type,
    timestamp: new Date().toISOString(),
    taskId: "native-daemon",
    summary,
  });
  if (state.events.length > 200) state.events.splice(0, state.events.length - 200);
}

function nativeFacts(status: DaemonStatus) {
  const foreground = status.foreground;
  return [
    { key: "daemon.simulation", value: String(status.simulation) },
    { key: "daemon.native_input", value: String(status.native_input) },
    { key: "daemon.capture_ring_frames", value: String(status.capture_ring_frames) },
    { key: "daemon.capture_ring_bytes", value: String(status.capture_ring_bytes) },
    { key: "daemon.audit_events_retained", value: String(status.audit_events_retained) },
    { key: "daemon.sandbox", value: status.sandbox },
    { key: "foreground.process_id", value: foreground ? String(foreground.process_id) : "unavailable" },
    { key: "foreground.title", value: foreground?.title || "unavailable" },
  ];
}

export async function nativeSnapshot(): Promise<Snapshot> {
  const status = await daemonStatus();
  const view = nativeView();
  const capture = view.capture;
  return {
    mode: "native",
    status: status.emergency_stopped ? "EMERGENCY_STOPPED" : status.simulation ? "DAEMON_SIMULATION" : "READY",
    emergencyStopped: status.emergency_stopped,
    tasks: [],
    events: view.events,
    facts: nativeFacts(status),
    worldVersion: capture?.frame.captured_at_ms ?? 0,
    memory: [],
    models: [],
    capabilities: [
      { id: "observe", permission: "screen capture", scope: "daemon capability", expiresAt: "runtime grant" },
      { id: "input", permission: status.native_input ? "mouse + keyboard" : "disabled", scope: "foreground-bound", expiresAt: "runtime grant" },
    ],
    telemetry: telemetry(),
    native: {
      daemonSimulation: status.simulation,
      nativeInput: status.native_input,
      foreground: status.foreground,
      capture: capture ? {
        frameId: capture.frame.id,
        capturedAtMs: capture.frame.captured_at_ms,
        sha256: capture.frame.sha256,
        display: capture.frame.display,
        previewDataUrl: `data:${capture.preview.mime};base64,${capture.preview.base64}`,
        previewWidth: capture.preview.width,
        previewHeight: capture.preview.height,
      } : null,
    },
  };
}

export async function executeNativeControl(input: ControlInput) {
  switch (input.action) {
    case "capture": {
      const capture = await daemonCapture(0);
      nativeView().capture = capture;
      recordNativeEvent("SCREEN_CAPTURED", `Captured display ${capture.frame.display.id} as frame ${capture.frame.id}`);
      return { ok: true, frameId: capture.frame.id };
    }
    case "stop": {
      const result = await daemonEmergencyStop();
      recordNativeEvent("EMERGENCY_STOP", "Native daemon emergency stop latched; local daemon restart is required.");
      return { ok: true, ...result };
    }
    case "click": {
      const status = await daemonStatus();
      if (!status.native_input) throw new Error("Native input is disabled in the Rust daemon");
      if (!status.foreground) throw new Error("No foreground window is available for a bound click");
      const capture = await daemonCapture(0);
      nativeView().capture = capture;
      const result = await daemonClick(capture.frame, input.x, input.y, status.foreground);
      recordNativeEvent("NATIVE_CLICK", `Requested foreground-bound click at (${input.x}, ${input.y})`);
      return { ok: true, result };
    }
    case "type": {
      const status = await daemonStatus();
      if (!status.native_input) throw new Error("Native input is disabled in the Rust daemon");
      if (!status.foreground) throw new Error("No foreground window is available for bound keyboard input");
      const capture = await daemonCapture(0);
      nativeView().capture = capture;
      const result = await daemonTypeText(capture.frame, input.text, status.foreground);
      recordNativeEvent("NATIVE_TYPE", `Requested ${input.text.length} characters of foreground-bound keyboard input`);
      return { ok: true, result };
    }
    case "run":
    case "pause":
    case "resume":
    case "reset":
      throw new Error(`${input.action} is a simulation mission control and is unavailable in native daemon mode`);
  }
}

export function assertLocalRequest(request: Request, mutation = false, expectedControl?: ControlMode) {
  const url = new URL(request.url);
  const host = request.headers.get("host");
  if (!host) throw new Error("Local host required");
  const authority = new URL(`${url.protocol}//${host}`);
  if (!["127.0.0.1", "localhost", "[::1]"].includes(authority.hostname)) throw new Error("Local host required");
  const origin = request.headers.get("origin");
  if (origin && origin !== authority.origin) throw new Error("Cross-origin access denied");
  if (request.headers.get("sec-fetch-site") === "cross-site") throw new Error("Cross-site access denied");
  if (mutation) {
    const expected = expectedControl ?? controlMode();
    if (request.headers.get("x-jarvis-control") !== expected) throw new Error(`${expected} control header required`);
  }
}

export async function parseControlBody(request: Request): Promise<ControlInput> {
  if (request.headers.get("content-type")?.split(";")[0]?.trim() !== "application/json") throw new Error("JSON content type required");
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Request body required");
  const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 8192) { await reader.cancel(); throw new Error("Control body exceeds 8 KiB"); }
      parts.push(value);
    }
  } finally { reader.releaseLock(); }
  const body: unknown = JSON.parse(Buffer.concat(parts).toString("utf8"));
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Expected a control object");
  const value = body as Record<string, unknown>;
  const allowed = new Set(["action", "title", "x", "y", "text"]);
  if (Object.keys(value).some(k => !allowed.has(k))) throw new Error("Unknown control property");
  if (typeof value.action !== "string" || !["run", "pause", "resume", "stop", "reset", "capture", "click", "type"].includes(value.action)) throw new Error("Unknown control action");
  if (value.action === "run") {
    if (typeof value.title !== "string" || !value.title.trim() || value.title.length > 160) throw new Error("Mission title must contain 1–160 characters");
    if (value.x !== undefined || value.y !== undefined || value.text !== undefined) throw new Error("Run accepts only a title");
    return { action: "run", title: value.title.trim() };
  }
  if (value.action === "click") {
    if (typeof value.x !== "number" || !Number.isInteger(value.x) || typeof value.y !== "number" || !Number.isInteger(value.y)) throw new Error("Click requires integer x and y coordinates");
    if (value.title !== undefined || value.text !== undefined) throw new Error("Click accepts only x and y");
    return { action: "click", x: value.x, y: value.y };
  }
  if (value.action === "type") {
    if (typeof value.text !== "string" || !value.text || value.text.length > 4096 || value.text.includes("\0")) throw new Error("Type requires 1–4096 characters");
    if (value.title !== undefined || value.x !== undefined || value.y !== undefined) throw new Error("Type accepts only text");
    return { action: "type", text: value.text };
  }
  if (value.title !== undefined || value.x !== undefined || value.y !== undefined || value.text !== undefined) throw new Error(`${value.action} does not accept additional properties`);
  return { action: value.action as "pause" | "resume" | "stop" | "reset" | "capture" };
}
