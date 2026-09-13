import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export type LegacyApplication = "discord";
export type LegacyLaunchResult = {
  ok: true;
  action: "launch_application";
  app: LegacyApplication;
  launcher_pid: number;
  window_title: string;
  window_handle: number;
  visible_window_verified: true;
  focused: boolean;
};

export function parseLegacyLaunchMission(title: string): LegacyApplication | null {
  const normalized = title
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[.,!?؟،:;]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  const english = /^(?:please )?(?:open|launch|start) (?:the )?discord(?: app| application)?$/;
  const arabic = /^(?:افتح|فتح|شغل|شغّل) (?:برنامج )?(?:ديسكورد|دسكورد)$/;
  return english.test(normalized) || arabic.test(normalized) ? "discord" : null;
}

function repoRoot(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env.JARVIS_REPO_ROOT?.trim();
  const candidates = [configured, process.cwd(), resolve(process.cwd(), "../..")].filter((value): value is string => Boolean(value));
  for (const candidate of candidates) {
    if (existsSync(resolve(candidate, "core", "legacy_bridge.py"))) return resolve(candidate);
  }
  throw new Error("Could not locate the JARVIS repository root for the Python legacy bridge");
}

function parseBridgeOutput(stdout: string): LegacyLaunchResult | { ok: false; error: string } {
  const line = stdout.split(/\r?\n/).map(value => value.trim()).filter(Boolean).at(-1);
  if (!line) throw new Error("Python legacy bridge returned no result");
  const value: unknown = JSON.parse(line);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Python legacy bridge returned invalid JSON");
  return value as LegacyLaunchResult | { ok: false; error: string };
}

export async function launchLegacyApplication(app: LegacyApplication, signal?: AbortSignal): Promise<LegacyLaunchResult> {
  if (process.platform !== "win32") throw new Error("The Python legacy application bridge is available on Windows only");
  const root = repoRoot();
  const python = process.env.JARVIS_PYTHON_EXECUTABLE?.trim() || "python";

  return await new Promise<LegacyLaunchResult>((resolvePromise, rejectPromise) => {
    execFile(
      python,
      ["-m", "core.legacy_bridge", "launch", app],
      { cwd: root, windowsHide: true, timeout: 30_000, maxBuffer: 64 * 1024, signal },
      (error, stdout) => {
        let result: LegacyLaunchResult | { ok: false; error: string } | undefined;
        try { result = parseBridgeOutput(stdout); }
        catch (parseError) {
          rejectPromise(parseError instanceof Error ? parseError : new Error("Invalid legacy bridge response"));
          return;
        }
        if (error || !result.ok) {
          const message = !result.ok ? result.error : error?.message || "Legacy bridge execution failed";
          rejectPromise(new Error(message));
          return;
        }
        if (result.app !== app || result.action !== "launch_application" || result.visible_window_verified !== true) {
          rejectPromise(new Error("Legacy bridge did not return a verified launch result"));
          return;
        }
        resolvePromise(result);
      },
    );
  });
}
