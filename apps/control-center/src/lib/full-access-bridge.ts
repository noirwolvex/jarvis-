import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export type FullAccessMissionResult = {
  ok: true;
  action: "full_access_mission";
  status: string;
  result: string;
  task_id: string;
  tools_used: number;
  failures: number;
  recoveries: number;
  verifications: number;
  verified: boolean;
  read_only_observation_verified?: boolean;
  incomplete_steps: string[];
  access_mode: "full";
  requires_user_action: boolean;
  mission_completed: boolean;
  high_risk_requires_separate_approval: boolean;
};

type WorkerEnvelope = {
  type: "result";
  id: string;
  ok: boolean;
  payload?: FullAccessMissionResult | { ok: false; error: string };
  error?: string;
};

type PendingRequest = {
  resolve: (value: FullAccessMissionResult) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
  abortCleanup?: () => void;
  onProgress?: (kind: string, message: string) => void;
};

type WorkerState = {
  child: ChildProcessWithoutNullStreams;
  buffer: string;
  stderr: string;
  pending: Map<string, PendingRequest>;
  stopping?: boolean;
  onEmergency?: () => void;
};

const globalState = globalThis as typeof globalThis & { jarvisFullAccessWorkerV1?: WorkerState };
const WORKER_PROTOCOL = 1;
const WORKER_TIMEOUT_MS = 30 * 60_000;
const MAX_WORKER_BUFFER = 2 * 1024 * 1024;

function repoRoot(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env.JARVIS_REPO_ROOT?.trim();
  const candidates = [configured, process.cwd(), resolve(process.cwd(), "../..")].filter((value): value is string => Boolean(value));
  for (const candidate of candidates) {
    if (existsSync(resolve(candidate, "core", "full_access_bridge.py"))) return resolve(candidate);
  }
  throw new Error("Could not locate the JARVIS repository root for Full Access");
}

function validateMissionResult(parsed: FullAccessMissionResult | { ok: false; error: string }): FullAccessMissionResult {
  if (!parsed.ok) throw new Error(parsed.error || "Full Access mission failed");
  if (parsed.action !== "full_access_mission" || parsed.access_mode !== "full" || parsed.tools_used < 1) {
    throw new Error("Full Access bridge did not return an executed mission result");
  }
  if (!parsed.requires_user_action && !parsed.mission_completed) {
    throw new Error(parsed.result || "Full Access mission did not complete or reach a user-action checkpoint");
  }
  return parsed;
}

function rejectPending(state: WorkerState, message: string) {
  for (const request of state.pending.values()) {
    clearTimeout(request.timer);
    request.abortCleanup?.();
    request.reject(new Error(message));
  }
  state.pending.clear();
}

function stopWorker(state: WorkerState, reason: string) {
  if (state.stopping) return;
  state.stopping = true;
  // Stop is consumed on the worker's stdin thread, independently of the model/tool thread.
  state.child.stdin.write(JSON.stringify({ protocol: WORKER_PROTOCOL, action: "stop" }) + "\n", () => {});
  rejectPending(state, reason);
  const fallback = setTimeout(() => state.child.kill(), 300);
  fallback.unref();
  state.child.once("exit", () => clearTimeout(fallback));
}

function handleWorkerLine(state: WorkerState, raw: string) {
  const line = raw.trim();
  if (!line) return;
  let message: unknown;
  try { message = JSON.parse(line); }
  catch { return; }
  if (!message || typeof message !== "object" || Array.isArray(message)) return;
  const value = message as Record<string, unknown>;
  if (value.type === "ready") return;
  if (value.type === "stopped") {
    state.onEmergency?.();
    stopWorker(state, "Full Access emergency stop activated");
    return;
  }
  if (value.type === "progress" && typeof value.id === "string" && typeof value.kind === "string" && typeof value.message === "string") {
    state.pending.get(value.id)?.onProgress?.(value.kind, value.message.slice(0, 2000));
    return;
  }
  if (value.type === "observation" && typeof value.id === "string") {
    state.pending.get(value.id)?.onProgress?.("observation", JSON.stringify({ frame: value.frame, preview: value.preview }));
    return;
  }
  if (value.type !== "result" || typeof value.id !== "string") return;

  const pending = state.pending.get(value.id);
  if (!pending) return;
  state.pending.delete(value.id);
  clearTimeout(pending.timer);
  pending.abortCleanup?.();

  const envelope = value as unknown as WorkerEnvelope;
  if (!envelope.ok) {
    pending.reject(new Error(envelope.error || "Full Access worker rejected the mission"));
    return;
  }
  try {
    if (!envelope.payload) throw new Error("Full Access worker returned no mission payload");
    pending.resolve(validateMissionResult(envelope.payload));
  } catch (error) {
    pending.reject(error instanceof Error ? error : new Error("Invalid Full Access worker response"));
  }
}

function startWorker(): WorkerState {
  const current = globalState.jarvisFullAccessWorkerV1;
  if (current?.stopping && current.child.exitCode === null) throw new Error("Full Access worker is still stopping");
  if (current && !current.child.killed && current.child.exitCode === null) return current;

  const root = repoRoot();
  const python = process.env.JARVIS_PYTHON_EXECUTABLE?.trim() || "python";
  // Python and the checkout are runtime dependencies, not files to bundle into Next output.
  const child = spawn(/* turbopackIgnore: true */ python, ["-u", "-m", "core.full_access_worker"], {
    cwd: root,
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env, JARVIS_ACCESS_MODE: "standard", JARVIS_FULL_ACCESS_REQUIRE_APPROVAL: "true" },
  });
  const state: WorkerState = { child, buffer: "", stderr: "", pending: new Map() };
  globalState.jarvisFullAccessWorkerV1 = state;

  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => {
    state.buffer += chunk;
    if (state.buffer.length > MAX_WORKER_BUFFER) {
      stopWorker(state, "Full Access worker output exceeded its safety limit");
      return;
    }
    while (true) {
      const newline = state.buffer.indexOf("\n");
      if (newline < 0) break;
      const line = state.buffer.slice(0, newline);
      state.buffer = state.buffer.slice(newline + 1);
      handleWorkerLine(state, line);
    }
  });
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk: string) => {
    state.stderr = (state.stderr + chunk).slice(-8192);
  });
  child.on("error", error => {
    rejectPending(state, `Full Access worker error: ${error.message}`);
  });
  child.on("exit", (code, signal) => {
    const detail = state.stderr.trim();
    const suffix = detail ? `: ${detail}` : "";
    rejectPending(state, `Full Access worker exited (code=${code ?? "null"}, signal=${signal ?? "none"})${suffix}`);
    if (globalState.jarvisFullAccessWorkerV1 === state) delete globalState.jarvisFullAccessWorkerV1;
  });
  return state;
}

function runPersistent(title: string, signal?: AbortSignal, onProgress?: (kind: string, message: string) => void, allowShell = false): Promise<FullAccessMissionResult> {
  if (signal?.aborted) return Promise.reject(new Error("Full Access mission aborted"));
  const state = startWorker();
  if (state.pending.size) return Promise.reject(new Error("A Full Access mission is already running"));
  state.onEmergency = () => onProgress?.("emergency_stop", "Worker emergency stop activated");
  const id = randomUUID();
  return new Promise<FullAccessMissionResult>((resolvePromise, rejectPromise) => {
    const timer = setTimeout(() => {
      const pending = state.pending.get(id);
      if (!pending) return;
      stopWorker(state, "Full Access worker mission timed out");
    }, WORKER_TIMEOUT_MS);

    const pending: PendingRequest = { resolve: resolvePromise, reject: rejectPromise, timer, onProgress };
    if (signal) {
      const onAbort = () => {
        if (!state.pending.has(id)) return;
        stopWorker(state, "Full Access mission aborted");
      };
      if (signal.aborted) {
        clearTimeout(timer);
        state.child.kill();
        rejectPromise(new Error("Full Access mission aborted"));
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
      pending.abortCleanup = () => signal.removeEventListener("abort", onAbort);
    }
    state.pending.set(id, pending);
    state.child.stdin.write(JSON.stringify({ protocol: WORKER_PROTOCOL, id, action: "run", title, allow_shell: allowShell }) + "\n", error => {
      if (!error) return;
      const active = state.pending.get(id);
      if (!active) return;
      state.pending.delete(id);
      clearTimeout(active.timer);
      active.abortCleanup?.();
      rejectPromise(error);
    });
  });
}

export async function runFullAccessMission(title: string, signal?: AbortSignal, onProgress?: (kind: string, message: string) => void, allowShell = false): Promise<FullAccessMissionResult> {
  if (process.platform !== "win32") throw new Error("JARVIS Full Access is available on Windows only");
  if (!title.trim() || title.length > 8000 || title.includes("\0")) throw new Error("Full Access mission requires 1–8000 safe characters");
  // The persistent protocol is required for independent stop handling and progress events.
  return runPersistent(title.trim(), signal, onProgress, allowShell);
}
