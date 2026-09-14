import { assertLocalRequest, readControlObject } from "@/lib/control-service";
import {
  assertHybridMutation,
  hybridAccessMode,
  hybridModeEnabled,
  setHybridAccessMode,
} from "@/lib/hybrid-control";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request) {
  try {
    assertLocalRequest(request);
    if (!hybridModeEnabled()) throw new Error("Full Access is available only in local hybrid mode");
    return Response.json({ mode: hybridAccessMode() }, { headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Access state unavailable" }, { status: 403, headers });
  }
}

export async function POST(request: Request) {
  try {
    assertLocalRequest(request);
    assertHybridMutation(request);
    if (!hybridModeEnabled()) throw new Error("Full Access is available only in local hybrid mode");
    if (request.headers.get("content-type")?.split(";")[0]?.trim() !== "application/json") throw new Error("JSON content type required");
    const value = await readControlObject(request, 2048);
    if (Object.keys(value).some(key => !["mode", "confirmation", "allowShell"].includes(key))) throw new Error("Unknown access control property");
    if (value.allowShell !== undefined && typeof value.allowShell !== "boolean") throw new Error("allowShell must be boolean");
    if (value.mode !== "standard" && value.mode !== "full") throw new Error("Access mode must be standard or full");
    if (value.mode === "full" && value.confirmation !== (value.allowShell ? "ENABLE_FULL_ACCESS_AND_SHELL" : "ENABLE_FULL_ACCESS")) throw new Error("Explicit Full Access confirmation required");
    const result = setHybridAccessMode(value.mode, value.allowShell === true);
    return Response.json({ ok: true, ...result }, { headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Access mode change rejected" }, { status: 400, headers });
  }
}
