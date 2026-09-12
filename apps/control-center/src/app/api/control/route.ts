import { assertLocalRequest, parseControlBody, simulationRuntime, snapshotForView } from "@/lib/control-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request) {
  try { assertLocalRequest(request); }
  catch { return Response.json({ error: "Local same-origin access required" }, { status: 403, headers }); }
  return Response.json(snapshotForView(simulationRuntime()), { headers });
}

export async function POST(request: Request) {
  try { assertLocalRequest(request, true); }
  catch { return Response.json({ error: "Local same-origin simulation control required" }, { status: 403, headers }); }
  try {
    const input = await parseControlBody(request);
    const service = simulationRuntime();
    switch (input.action) {
      case "run": service.submitMission(input.title); break;
      case "pause": service.pause(); break;
      case "resume": service.resume(); break;
      case "stop": service.emergencyStop(); break;
      case "reset": service.resetEmergencyStop(); break;
    }
    return Response.json({ ok: true }, { status: input.action === "run" ? 202 : 200, headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Control request rejected" }, { status: 400, headers });
  }
}
