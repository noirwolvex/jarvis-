import { execFile } from "node:child_process";
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
  incomplete_steps: string[];
  access_mode: "full";
  requires_user_action: boolean;
  mission_completed: boolean;
  high_risk_requires_separate_approval: boolean;
};

function repoRoot(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env.JARVIS_REPO_ROOT?.trim();
  const candidates = [configured, process.cwd(), resolve(process.cwd(), "../..")].filter((value): value is string => Boolean(value));
  for (const candidate of candidates) {
    if (existsSync(resolve(candidate, "core", "full_access_bridge.py"))) return resolve(candidate);
  }
  throw new Error("Could not locate the JARVIS repository root for Full Access");
}

function parseBridgeOutput(stdout: string): FullAccessMissionResult | { ok: false; error: string } {
  const line = stdout.split(/\r?\n/).map(value => value.trim()).filter(Boolean).at(-1);
  if (!line) throw new Error("Full Access bridge returned no result");
  const parsed: unknown = JSON.parse(line);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Full Access bridge returned invalid JSON");
  return parsed as FullAccessMissionResult | { ok: false; error: string };
}

export async function runFullAccessMission(title: string, signal?: AbortSignal): Promise<FullAccessMissionResult> {
  if (process.platform !== "win32") throw new Error("JARVIS Full Access is available on Windows only");
  const root = repoRoot();
  const python = process.env.JARVIS_PYTHON_EXECUTABLE?.trim() || "python";

  return await new Promise<FullAccessMissionResult>((resolvePromise, rejectPromise) => {
    execFile(
      python,
      ["-m", "core.full_access_bridge", "run", title],
      {
        cwd: root,
        windowsHide: true,
        timeout: 180_000,
        maxBuffer: 1024 * 1024,
        signal,
        env: { ...process.env, JARVIS_ACCESS_MODE: "full", JARVIS_FULL_ACCESS_REQUIRE_APPROVAL: "false" },
      },
      (error, stdout) => {
        let parsed: FullAccessMissionResult | { ok: false; error: string };
        try { parsed = parseBridgeOutput(stdout); }
        catch (parseError) {
          rejectPromise(parseError instanceof Error ? parseError : new Error("Invalid Full Access bridge response"));
          return;
        }
        if (!parsed.ok) {
          rejectPromise(new Error(parsed.error || "Full Access mission failed"));
          return;
        }
        if (error) {
          rejectPromise(new Error(error.message || "Full Access bridge execution failed"));
          return;
        }
        if (parsed.action !== "full_access_mission" || parsed.access_mode !== "full" || parsed.tools_used < 1) {
          rejectPromise(new Error("Full Access bridge did not return an executed mission result"));
          return;
        }
        if (!parsed.requires_user_action && !parsed.mission_completed) {
          rejectPromise(new Error(parsed.result || "Full Access mission did not complete or reach a user-action checkpoint"));
          return;
        }
        resolvePromise(parsed);
      },
    );
  });
}
