import { randomUUID } from "node:crypto";
import { freemem, totalmem } from "node:os";
import { runFullAccessMission } from "./full-access-bridge";
import {
  launchLegacyApplication,
  parseLegacyLaunchMission,
  supportedLegacyApplications,
  type LegacyApplication,
} from "./legacy-bridge";
import type { EventView, Snapshot, TaskView } from "./view-types";

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

export function setHybridAccessMode(mode: AccessMode) {
  const value = getState();
  if (value.running) throw new Error("Access mode cannot change while a mission is running");
  value.accessMode = mode;
  value.status = "IDLE";
  addEvent("ACCESS_MODE_CHANGED", "runtime", mode === "full" ? "Full Access enabled for this local session" : "Full Access disabled; standard hybrid restrictions restored");
  return { mode };
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
    mode: "hybrid", status: value.status, emergencyStopped: false,
    tasks: value.tasks.map(item => structuredClone(item)), events: value.events.map(item => structuredClone(item)),
    facts: [
      { key: "access.mode", value: value.accessMode },
      { key: "bridge.runtime", value: full ? "python-agent-full-access" : "python-legacy" },
      { key: "bridge.scope", value: full ? "desktop + input + browser + files + git + shell (permission engine)" : "verified application launch" },
      { key: "bridge.allowlist", value: full ? "dynamic via Python agent" : applications.join(", ") },
      ...(value.windowTitle ? [{ key: "last.verified_window", value: value.windowTitle }] : []),
    ],
    worldVersion: value.version, memory: [], models: [], native: null,
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
  const execute = task.nodes[1]!;
  const verify = task.nodes[2]!;
  value.running = true;
  value.status = "EXECUTING";
  task.status = "RUNNING";
  execute.status = "EXECUTING";
  addEvent("ACTION_STARTED", task.id, "Full Access agent execution started");
  try {
    const result = await runFullAccessMission(task.title);
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

    if (!result.mission_completed || !result.verified) {
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
    task.status = "FAILED";
    task.summary = error instanceof Error ? error.message : "Full Access mission failed";
    value.status = "ERROR";
    addEvent("TASK_FAILED", task.id, task.summary);
  } finally {
    value.running = false;
  }
}

async function runLaunchMission(task: TaskView, app: LegacyApplication) {
  const value = getState();
  const launch = task.nodes[1]!;
  const verify = task.nodes[2]!;
  value.running = true;
  value.status = "EXECUTING";
  task.status = "RUNNING";
  launch.status = "EXECUTING";
  addEvent("ACTION_STARTED", task.id, `${app} launch started`);
  try {
    const result = await launchLegacyApplication(app);
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
    task.status = "FAILED";
    task.summary = error instanceof Error ? error.message : `${app} launch failed`;
    value.status = "ERROR";
    addEvent("TASK_FAILED", task.id, task.summary);
  } finally {
    value.running = false;
  }
}
