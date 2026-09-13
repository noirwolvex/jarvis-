import { randomUUID } from "node:crypto";
import { freemem, totalmem } from "node:os";
import { launchLegacyApplication, parseLegacyLaunchMission } from "./legacy-bridge";
import type { EventView, Snapshot, TaskView } from "./view-types";

type State = { tasks: TaskView[]; events: EventView[]; seq: number; status: string; running: boolean; version: number; windowTitle?: string };
const globalState = globalThis as typeof globalThis & { jarvisHybridV2?: State };
const getState = () => globalState.jarvisHybridV2 ??= { tasks: [], events: [], seq: 0, status: "IDLE", running: false, version: 0 };

function addEvent(type: string, taskId: string, summary: string) {
  const value = getState();
  value.events.push({ sequence: ++value.seq, id: `hybrid-${value.seq}`, type, timestamp: new Date().toISOString(), taskId, summary });
  if (value.events.length > 200) value.events.splice(0, value.events.length - 200);
}

export function hybridModeEnabled(env: NodeJS.ProcessEnv = process.env) {
  return env.JARVIS_CONTROL_MODE?.trim().toLowerCase() === "hybrid";
}

export function assertHybridMutation(request: Request) {
  if (request.headers.get("x-jarvis-control") !== "hybrid") throw new Error("hybrid control header required");
}

export function hybridSnapshot(): Snapshot {
  const value = getState();
  return {
    mode: "hybrid", status: value.status, emergencyStopped: false,
    tasks: value.tasks.map(item => structuredClone(item)), events: value.events.map(item => structuredClone(item)),
    facts: [{ key: "bridge.runtime", value: "python-legacy" }, { key: "bridge.allowlist", value: "discord" }, ...(value.windowTitle ? [{ key: "last.verified_window", value: value.windowTitle }] : [])],
    worldVersion: value.version, memory: [], models: [], native: null,
    capabilities: [{ id: "legacy-launch-discord", permission: "launch application", scope: "Discord only", expiresAt: "local session" }],
    telemetry: { processRssMb: Math.round(process.memoryUsage().rss / 1024 ** 2), hostRamUsedGb: (totalmem() - freemem()) / 1024 ** 3, hostRamTotalGb: totalmem() / 1024 ** 3, processUptime: Math.floor(process.uptime()), sampledAt: new Date().toISOString() },
  };
}

export function submitHybridMission(title: string) {
  const value = getState();
  if (value.running) throw new Error("A hybrid mission is already running");
  const app = parseLegacyLaunchMission(title);
  if (!app) throw new Error("Hybrid mode currently accepts: open discord app");
  const id = randomUUID();
  const task: TaskView = {
    id, title: title.trim(), status: "QUEUED", createdAt: new Date().toISOString(), summary: "Queued",
    nodes: [
      { id: `${id}:intent`, title: "Parse Discord intent", action: "PARSE_INTENT", dependencies: [], status: "VERIFIED" },
      { id: `${id}:launch`, title: "Start Discord", action: "LAUNCH_APPLICATION", dependencies: [`${id}:intent`], status: "PENDING" },
      { id: `${id}:verify`, title: "Verify Discord window", action: "VERIFY_WINDOW", dependencies: [`${id}:launch`], status: "PENDING" },
    ],
  };
  value.tasks.unshift(task);
  if (value.tasks.length > 32) value.tasks.length = 32;
  addEvent("TASK_CREATED", id, "Hybrid Discord mission queued");
  void runMission(task, app);
  return { id };
}

async function runMission(task: TaskView, app: "discord") {
  const value = getState();
  const launch = task.nodes[1]!;
  const verify = task.nodes[2]!;
  value.running = true;
  value.status = "EXECUTING";
  task.status = "RUNNING";
  launch.status = "EXECUTING";
  addEvent("ACTION_STARTED", task.id, "Discord launch started");
  try {
    const result = await launchLegacyApplication(app);
    launch.status = "VERIFIED";
    verify.status = "VERIFIED";
    task.status = "COMPLETED";
    task.summary = `Discord window verified: ${result.window_title}`;
    value.windowTitle = result.window_title;
    value.version += 1;
    value.status = "COMPLETED";
    addEvent("ACTION_VERIFIED", task.id, task.summary);
    addEvent("TASK_COMPLETED", task.id, task.summary);
  } catch (error) {
    launch.status = "FAILED";
    verify.status = "FAILED";
    task.status = "FAILED";
    task.summary = error instanceof Error ? error.message : "Discord launch failed";
    value.status = "ERROR";
    addEvent("TASK_FAILED", task.id, task.summary);
  } finally {
    value.running = false;
  }
}
