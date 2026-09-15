import { randomUUID } from "node:crypto";
import { freemem, totalmem } from "node:os";
import { runFullAccessMission, stopFullAccessWorker } from "./full-access-bridge";
import {
  launchLegacyApplication,
  parseLegacyLaunchMission,
  supportedLegacyApplications,
  type LegacyApplication,
} from "./legacy-bridge";
import type { EventView, Snapshot, TaskView, NativeView } from "./view-types";

type AccessMode = "standard" | "full";
type State = {
  tasks: TaskView[];
  events: EventView[];
  seq: number;
  status: string;
  running: boolean;
  version: number;
  windowTitle?: string;
  accessMode: AccessMode;
  allowShell?: boolean;
  emergencyStopped?: boolean;
  abort?: AbortController;
  observation?: NativeView;
};
const globalState = globalThis as typeof globalThis & { jarvisHybridV3?: State };
const getState = () => globalState.jarvisHybridV3 ??= {
  tasks: [], events: [], seq: 0, status: "IDLE", running: false, version: 0, accessMode: "standard",
};

function addEvent(type: string, taskId: string, summary: string) {
  const value = getState();
  value.events.push({ sequence: ++value.seq, id: `hybrid-${value.seq}`, type, timestamp: new Date().toISOString(), taskId, summary });
  if (value.events.length > 200) value.events.splice(0, value.events.length - 200);
}

export function hybridModeEnabled(env: NodeJS.ProcessEnv = process.env) {
  return env.JARVIS_CONTROL_MODE?.trim().toLowerCase() === "hybrid";
}

export function hybridAccessMode(): AccessMode { return getState().accessMode; }

export function setHybridAccessMode(mode: AccessMode, allowShell = false) {
  const value = getState();
  if (mode === "standard" && value.running) {
    emergencyStopHybrid();
    return { mode };
  }
  if (mode === "full" && value.emergencyStopped) throw new Error("Reset emergency stop before enabling Full Access");
  if (value.running) throw new Error("Access mode cannot change while a mission is running");
  value.accessMode = mode;
  value.allowShell = mode === "full" && allowShell;
  if (mode === "standard") stopFullAccessWorker();
  value.status = "IDLE";
  addEvent("ACCESS_MODE_CHANGED", "runtime", mode === "full" ? "Full Access enabled for this local session" : "Full Access disabled; standard hybrid restrictions restored");
  return { mode, allowShell: value.allowShell };
}

export function emergencyStopHybrid() {
  const value = getState();
  value.emergencyStopped = true;
  value.accessMode = "standard";
  value.allowShell = false;
  value.status = "EMERGENCY_STOPPED";
  value.abort?.abort();
  stopFullAccessWorker();
  addEvent("EMERGENCY_STOP", "runtime", "Full Access revoked; active worker cancellation requested");
}

export function resetHybridStop() {
  const value = getState();
  if (value.running) throw new Error("Wait for active execution to stop before resetting");
  value.emergencyStopped = false;
  value.accessMode = "standard";
  value.allowShell = false;
  value.status = "IDLE";
  addEvent("EMERGENCY_RESET", "runtime", "Stop reset; Full Access remains disabled");
}

export function assertHybridMutation(request: Request) {
  const value = request.headers.get("x-jarvis-control");
  if (value !== "hybrid" && value !== "simulation") throw new Error("hybrid control header required");
}

export function hybridSnapshot(): Snapshot {
  const value = getState();
  const applications = supportedLegacyApplications();
  const full = value.accessMode === "full";
  return {
    mode: "hybrid", status: value.status, emergencyStopped: Boolean(value.emergencyStopped),
    tasks: value.tasks.map(item => structuredClone(item)), events: value.events.map(item => structuredClone(item)),
    facts: [
      { key: "access.mode", value: value.accessMode },
      { key: "access.terminal", value: String(Boolean(value.allowShell)) },
      { key: "bridge.runtime", value: full ? "python-agent-full-access" : "python-legacy" },
      { key: "bridge.scope", value: full ? "desktop + input + browser + files + git + shell (permission engine)" : "verified application launch" },
      { key: "bridge.allowlist", value: full ? "dynamic via Python agent" : applications.join(", ") },
      ...(value.windowTitle ? [{ key: "last.verified_window", value: value.windowTitle }] : []),
    ],
    worldVersion: value.version, memory: [], models: [], native: value.observation ? structuredClone(value.observation) : null,
    capabilities: full ? [
      { id: "full-desktop", permission: "screen + mouse + keyboard + applications", scope: "local interactive session", expiresAt: "local session" },
      { id: "full-browser", permission: "browser read/write", scope: "local browser guard", expiresAt: "local session" },
      { id: "full-dev", permission: "filesystem + Git + shell", scope: "permission engine + workspace boundaries", expiresAt: "local session" },
    ] : applications.map(app => ({ id: `legacy-launch-${app}`, permission: "launch application", scope: app, expiresAt: "local session" })),
    telemetry: { processRssMb: Math.round(process.memoryUsage().rss / 1024 ** 2), hostRamUsedGb: (totalmem() - freemem()) / 1024 ** 3, hostRamTotalGb: totalmem() / 1024 ** 3, processUptime: Math.floor(process.uptime()), sampledAt: new Date().toISOString() },
  };
}

export function submitHybridMission(title: string) {
  const value = getState();
  if (value.emergencyStopped) throw new Error("Emergency stop is active");
  if (value.running) throw new Error("A hybrid mission is already running");
  const id = randomUUID();

  if (value.accessMode === "full") {
    const task: TaskView = {
      id, title: title.trim(), status: "QUEUED", createdAt: new Date().toISOString(), summary: "Queued for Full Access agent",
      nodes: [
        { id: `${id}:authorize`, title: "Authorize Full Access session", action: "AUTHORIZE_FULL_ACCESS", dependencies: [], status: "VERIFIED" },
        { id: `${id}:execute`, title: "Plan and execute with Python agent", action: "AGENT_EXECUTE", dependencies: [`${id}:authorize`], status: "PENDING" },
        { id: `${id}:verify`, title: "Verify agent outcome", action: "VERIFY_OUTCOME", dependencies: [`${id}:execute`], status: "PENDING" },
      ],
    };
    value.tasks.unshift(task);
    if (value.tasks.length > 32) value.tasks.length = 32;
    addEvent("TASK_CREATED", id, "Full Access mission queued");
    void runFullMission(task);
    return { id };
  }

  const app = parseLegacyLaunchMission(title);
  if (!app) throw new Error("Standard hybrid mode supports verified application launches. Enable Full Access for general device-control missions.");
  const task: TaskView = {
    id, title: title.trim(), status: "QUEUED", createdAt: new Date().toISOString(), summary: "Queued",
    nodes: [
      { id: `${id}:intent`, title: `Parse ${app} intent`, action: "PARSE_INTENT", dependencies: [], status: "VERIFIED" },
      { id: `${id}:launch`, title: `Start ${app}`, action: "LAUNCH_APPLICATION", dependencies: [`${id}:intent`], status: "PENDING" },
      { id: `${id}:verify`, title: `Verify ${app} process and window`, action: "VERIFY_WINDOW", dependencies: [`${id}:launch`], status: "PENDING" },
    ],
  };
  value.tasks.unshift(task);
  if (value.tasks.length > 32) value.tasks.length = 32;
  addEvent("TASK_CREATED", id, `Hybrid ${app} mission queued`);
  void runLaunchMission(task, app);
  return { id };
}

async function runFullMission(task: TaskView) {
  const value = getState();
  const abort = new AbortController();
  value.abort = abort;
  const execute = task.nodes[1]!;
  const verify = task.nodes[2]!;
  value.running = true;
  value.status = "EXECUTING";
  task.status = "RUNNING";
  execute.status = "EXECUTING";
  addEvent("ACTION_STARTED", task.id, "Full Access agent execution started");
  try {
    const result = await runFullAccessMission(task.title, abort.signal, (kind, message) => {
      if (kind === "emergency_stop") { emergencyStopHybrid(); return; }
      if (kind === "observation") {
        if (value.emergencyStopped) return;
        const observation = JSON.parse(message);
        const frame = observation.frame;
        if (!frame || typeof observation.preview !== "string" || observation.preview.length > 2_000_100 || !observation.preview.startsWith("data:image/jpeg;base64,")) return;
        value.observation = { daemonSimulation: false, nativeInput: true, foreground: null,
          capture: { frameId: frame.sha256, capturedAtMs: frame.captured_at_ms, sha256: frame.sha256,
            display: { id: 0, x: frame.virtual_origin_x, y: frame.virtual_origin_y, width: frame.source_width, height: frame.source_height, scale: frame.desktop_scale_x },
            previewDataUrl: observation.preview, previewWidth: frame.width, previewHeight: frame.height } };
        value.version += 1;
        addEvent("SCREEN_OBSERVED", task.id, `Captured ${frame.source_width}×${frame.source_height} desktop in ${frame.capture_ms} ms; stable=${frame.stable}`);
        return;
      }
      if (!value.emergencyStopped) {
        task.summary = message;
        addEvent(kind === "tool_result" ? "TOOL_RESULT" : "AGENT_PROGRESS", task.id, message);
      }
    }, Boolean(value.allowShell));
    if (abort.signal.aborted) throw new Error("Full Access mission stopped");
    execute.status = "VERIFIED";

    if (result.requires_user_action || result.status === "waiting_user") {
      verify.status = "WAITING_USER";
      task.status = "WAITING_USER";
      task.summary = `${result.result} [tools=${result.tools_used}, failures=${result.failures}]`;
      value.version += 1;
      value.status = "WAITING_USER";
      addEvent("ACTION_PAUSED", task.id, "Full Access paused at a human-verification checkpoint");
      addEvent("USER_ACTION_REQUIRED", task.id, result.result);
      return;
    }

    if (!result.mission_completed || !(result.verified || result.read_only_observation_verified)) {
      throw new Error(result.result || "Full Access mission ended without verified completion");
    }

    verify.status = "VERIFIED";
    task.status = "COMPLETED";
    task.summary = `${result.result} [tools=${result.tools_used}, failures=${result.failures}, verifications=${result.verifications}]`;
    value.version += 1;
    value.status = "COMPLETED";
    addEvent("ACTION_EXECUTED", task.id, `Python agent executed ${result.tools_used} tool calls`);
    addEvent("ACTION_VERIFIED", task.id, "Agent verification records passed");
    addEvent("TASK_COMPLETED", task.id, task.summary);
  } catch (error) {
    execute.status = "FAILED";
    verify.status = "FAILED";
    task.status = value.emergencyStopped ? "CANCELLED" : "FAILED";
    task.summary = error instanceof Error ? error.message : "Full Access mission failed";
    value.status = value.emergencyStopped ? "EMERGENCY_STOPPED" : "ERROR";
    addEvent("TASK_FAILED", task.id, task.summary);
  } finally {
    value.running = false;
    delete value.abort;
  }
}

async function runLaunchMission(task: TaskView, app: LegacyApplication) {
  const value = getState();
  const abort = new AbortController();
  value.abort = abort;
  const launch = task.nodes[1]!;
  const verify = task.nodes[2]!;
  value.running = true;
  value.status = "EXECUTING";
  task.status = "RUNNING";
  launch.status = "EXECUTING";
  addEvent("ACTION_STARTED", task.id, `${app} launch started`);
  try {
    const result = await launchLegacyApplication(app, abort.signal);
    if (abort.signal.aborted) throw new Error("Application launch stopped");
    launch.status = "VERIFIED";
    verify.status = "VERIFIED";
    task.status = "COMPLETED";
    task.summary = `${app} verified: ${result.window_title} (${result.process_name} pid ${result.process_id})`;
    value.windowTitle = result.window_title;
    value.version += 1;
    value.status = "COMPLETED";
    addEvent("ACTION_VERIFIED", task.id, task.summary);
    addEvent("TASK_COMPLETED", task.id, task.summary);
  } catch (error) {
    launch.status = "FAILED";
    verify.status = "FAILED";
    task.status = value.emergencyStopped ? "CANCELLED" : "FAILED";
    task.summary = error instanceof Error ? error.message : `${app} launch failed`;
    value.status = value.emergencyStopped ? "EMERGENCY_STOPPED" : "ERROR";
    addEvent("TASK_FAILED", task.id, task.summary);
  } finally {
    value.running = false;
    delete value.abort;
  }
}
