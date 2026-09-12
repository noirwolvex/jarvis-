import {
  assertLocalRequest,
  controlMode,
  executeNativeControl,
  nativeSnapshot,
  parseControlBody,
  simulationRuntime,
  snapshotForView,
} from "@/lib/control-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request) {
  try { assertLocalRequest(request); }
  catch { return Response.json({ error: "Local same-origin access required" }, { status: 403, headers }); }
  try {
    const mode = controlMode();
    const snapshot = mode === "native" ? await nativeSnapshot() : snapshotForView(simulationRuntime());
    return Response.json(snapshot, { headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Control service unavailable" }, { status: 503, headers });
  }
}

export async function POST(request: Request) {
  let mode: "simulation" | "native";
  try { mode = controlMode(); }
  catch (error) { return Response.json({ error: error instanceof Error ? error.message : "Invalid control mode" }, { status: 500, headers }); }
  try { assertLocalRequest(request, true, mode); }
  catch { return Response.json({ error: `Local same-origin ${mode} control required` }, { status: 403, headers }); }
  try {
    const input = await parseControlBody(request);
    if (mode === "native") {
      const result = await executeNativeControl(input);
      return Response.json(result, { status: input.action === "capture" ? 201 : 200, headers });
    }
    const service = simulationRuntime();
    switch (input.action) {
      case "run": service.submitMission(input.title); break;
      case "pause": service.pause(); break;
      case "resume": service.resume(); break;
      case "stop": service.emergencyStop(); break;
      case "reset": service.resetEmergencyStop(); break;
      case "capture":
      case "click":
      case "type": throw new Error(`${input.action} requires native control mode`);
    }
    return Response.json({ ok: true }, { status: input.action === "run" ? 202 : 200, headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Control request rejected" }, { status: 400, headers });
  }
}
