import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

export type LegacyApplication = "discord" | "notepad" | "chrome" | "vscode";
export type LegacyLaunchResult = {
  ok: true;
  action: "launch_application";
  app: LegacyApplication;
  launcher_pid: number | null;
  process_id: number;
  process_name: string;
  window_title: string;
  window_handle: number;
  visible_window_verified: true;
  process_identity_verified: true;
  focused: boolean;
};

const aliases: Array<{ app: LegacyApplication; names: string[] }> = [
  { app: "discord", names: ["discord", "discord app", "discord application", "ديسكورد", "دسكورد"] },
  { app: "notepad", names: ["notepad", "notepad app", "المفكرة", "المفكره", "نوت باد"] },
  { app: "chrome", names: ["chrome", "google chrome", "كروم", "قوقل كروم", "جوجل كروم"] },
  { app: "vscode", names: ["vscode", "vs code", "visual studio code", "فيجوال ستوديو كود"] },
];

function normalizeMission(title: string) {
  return title
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[.,!?؟،:;]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function parseLegacyLaunchMission(title: string): LegacyApplication | null {
  const normalized = normalizeMission(title);
  const englishPrefix = /^(?:please )?(?:open|launch|start) (?:the )?/;
  const arabicPrefix = /^(?:افتح|فتح|شغل|شغّل) (?:برنامج )?/;
  const target = normalized.replace(englishPrefix, "").replace(arabicPrefix, "").trim();
  if (target === normalized) return null;
  for (const entry of aliases) {
    if (entry.names.includes(target)) return entry.app;
  }
  return null;
}

export function supportedLegacyApplications(): LegacyApplication[] {
  return aliases.map(entry => entry.app);
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
      /* turbopackIgnore: true */ python,
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
        if (
          result.app !== app ||
          result.action !== "launch_application" ||
          result.visible_window_verified !== true ||
          result.process_identity_verified !== true ||
          !Number.isInteger(result.process_id) ||
          !result.window_title
        ) {
          rejectPromise(new Error("Legacy bridge did not return a verified application launch result"));
          return;
        }
        resolvePromise(result);
      },
    );
  });
}
