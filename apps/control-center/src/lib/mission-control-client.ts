export type MissionControlAction = "pause" | "resume" | "cancel" | "confirm" | "reject";

export async function requestMissionControl(
  action: MissionControlAction,
  confirmationId?: string,
  request: typeof fetch = fetch,
) {
  if (!["pause", "resume", "cancel", "confirm", "reject"].includes(action)) throw new Error("Unknown mission control action.");
  const decision = action === "confirm" || action === "reject";
  if (decision && (!confirmationId || !/^[a-f0-9]{32}$/.test(confirmationId))) {
    throw new Error("A current confirmation is required. Refresh the mission state.");
  }
  if (!decision && confirmationId !== undefined) throw new Error("Mission controls cannot also grant action approval.");
  const response = await request("/api/mission-control", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Jarvis-Control": "hybrid" },
    body: JSON.stringify({ action, ...(confirmationId ? { confirmationId } : {}) }),
    signal: AbortSignal.timeout(8000),
  });
  let body: unknown;
  try { body = await response.json(); }
  catch { throw new Error("Mission control response was not valid. Refresh the mission state before retrying."); }
  const result = body && typeof body === "object" && !Array.isArray(body) ? body as Record<string, unknown> : undefined;
  if (!response.ok) throw new Error(typeof result?.error === "string" ? result.error : "Mission control request failed.");
  if (result?.ok !== true) throw new Error("Mission control request was not acknowledged. Refresh the mission state before retrying.");
}
