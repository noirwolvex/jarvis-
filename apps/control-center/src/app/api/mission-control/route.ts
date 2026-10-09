import { assertControlSession } from "@/lib/control-session";
import { assertLocalRequest, readControlObject } from "@/lib/control-service";
import { assertHybridMutation, hybridModeEnabled, controlHybridMission } from "@/lib/hybrid-control";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function POST(request: Request) {
  try {
    assertLocalRequest(request);
    assertControlSession(request);
    assertHybridMutation(request);
    if (request.headers.get("x-jarvis-control") !== "hybrid") throw new Error("Hybrid control header required");
    if (!hybridModeEnabled()) throw new Error("Mission controls require hybrid mode");
    const body = await readControlObject(request, 2048);
    if (Object.keys(body).some(key => !["action", "confirmationId"].includes(key))) throw new Error("Unknown control property");
    const action = body.action;
    if (action !== "pause" && action !== "resume" && action !== "confirm" && action !== "reject" && action !== "cancel") throw new Error("Unknown mission control action");
    if (action === "confirm" || action === "reject") {
      if (typeof body.confirmationId !== "string" || !/^[a-f0-9]{32}$/.test(body.confirmationId)) throw new Error("Exact pending confirmation ID required");
    } else if (body.confirmationId !== undefined) throw new Error("Pause/resume cannot grant action approval");
    controlHybridMission(action, body.confirmationId as string | undefined);
    return Response.json({ ok: true }, { status: 202, headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Control rejected" }, { status: 400, headers });
  }
}
