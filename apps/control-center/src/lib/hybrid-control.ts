import { randomUUID } from "node:crypto";
import { freemem, totalmem } from "node:os";
import { launchLegacyApplication, parseLegacyLaunchMission } from "./legacy-bridge";
import type { EventView, Snapshot, TaskView } from "./view-types";

type HybridStore = {
  tasks: TaskView[];
  events: EventView[];
  nextSequence: number;
  status: string;
  running: boolean;
  worldVersion: number;
  lastWindow?: string;
};

const globalStore = globalThis as typeof globalThis & { jarvisHybrid?: HybridStore };

function store(): HybridStore {
  return globalStore.jarvisHybrid ??= { tasks: [], events: [], nextSequence: 0, status: "IDLE", running: false, worldVersion: 0 };
}

function record(type: string, taskId: string, summary: string) {
  const current = store();
  current.events.push({ sequence: ++current.nextSequence, id: `hybrid-${current.nextSequence}`, type, timestamp: new Date().toISOString(), taskId, summary });
  if (current.events.length > 200) current.events.splice(0, current.events.length - 200);
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

export function hybridModeEnabled(env: NodeJS.ProcessEnv = process.env) {
  return env.JARVIS_CONTROL_MODE?.trim().toLowerCase() === "hybrid";
}

export function assertHybridMutation(request: Request) {
  if (request.headers.get("x-jarvis-control") !== "hybrid") throw new Error("hybrid control header required");
}

export function hybridSnapshot(): Snapshot {
  const current = store();
  return {
    mode: "hybrid",
    status: current.status,
    emergencyStopped: false,
    tasks: current.tasks.map(task => structuredClone(task)),
    events: current.events.map(event => structuredClone(event)),
    facts: [
      { key: "bridge.runtime", value: "python-legacy" },
      { key: "bridge.allowlist", value: "discord" },
      ...(current.lastWindow ? [{ key: "last.verified_window", value: current.lastWindow }] : []),
    ],
    worldVersion: current.worldVersion,
    memory: [], models: [], native: null,
    capabilities: [{ id: "legacy-launch-discord", permission: "launch application", scope: "Discord only", expiresAt: "local session" }],
    telemetry: telemetry(),
  };
}

export function submitHybridMission(title: string) {
  const current = store();
  if (current.running) throw new Error("A hybrid mission is already executing");
  const app = parseLegacyLaunchMission(title);
  if (!app) throw new Error("Hybrid mode currently accepts: open discord app");

  const id = randomUUID();
  const task: TaskView = {
    id, title: title.trim(), status: "QUEUED", createdAt: new Date().toISOString(), summary: "Queued for verified local execution",
    nodes: [
      { id: `${id}:intent`, title: "Parse Discord launch intent", action: "PARSE_INTENT", dependencies: [], status: "VERIFIED" },
      { id: `${id}:launch`, title: "Start Discord", action: "LAUNCH_APPLICATION", dependencies: [`${id}:intent`], status: "PENDING" },
      { id: `${id}:verify`, title: "Verify visible Discord window", action: "VERIFY_WINDOW", dependencies: [`${id}:launch`], status: "PENDING" },
    ],
  };
  current.tasks.unshift(task);
  if (current.tasks.length > 32) current.tasks.length = 32;
  record("TASK_CREATED", id, "Hybrid Discord mission queued");
  void runMission(task, app);
  return { id };
}

async function runMission(task: TaskView, app: "discord") {
  const current = store();
  current.running = true;
  current.status = "EXECUTING";
  task.status = "RUNNING";
  task.nodes[1].status = "EXECUTING";
  record("ACTION_STARTED", task.id, "Starting Discord through the Python compatibility adapter");
  try {
    const result = await launchLegacyApplication(app);
    task.nodes[1].status = "VERIFIED";
    task.nodes[2].status = "VERIFIED";
    task.status = "COMPLETED";
    task.summary = `Discord window verified: ${result.window_title}`;
    current.lastWindow = result.window_title;
    current.worldVersion += 1;
    current.status = "COMPLETED";
    record("ACTION_VERIFIED", task.id, task.summary);
    record("TASK_COMPLETED", task.id, task.summary);
  } catch (error) {
    task.nodes[1].status = "FAILED";
    task.nodes[2].status = "FAILED";
    task.status = "FAILED";
    task.summary = error instanceof Error ? error.message : "Discord launch failed";
    current.status = "ERROR";
    record("TASK_FAILED", task.id, task.summary);
  } finally {
    current.running = false;
  }
}
