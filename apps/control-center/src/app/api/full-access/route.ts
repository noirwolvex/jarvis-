import { assertLocalRequest } from "@/lib/control-service";
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
    const body: unknown = await request.json();
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Expected an access control object");
    const value = body as Record<string, unknown>;
    if (Object.keys(value).some(key => !["mode", "confirmation"].includes(key))) throw new Error("Unknown access control property");
    if (value.mode !== "standard" && value.mode !== "full") throw new Error("Access mode must be standard or full");
    if (value.mode === "full" && value.confirmation !== "ENABLE_FULL_ACCESS") throw new Error("Explicit Full Access confirmation required");
    const result = setHybridAccessMode(value.mode);
    return Response.json({ ok: true, ...result }, { headers });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Access mode change rejected" }, { status: 400, headers });
  }
}
