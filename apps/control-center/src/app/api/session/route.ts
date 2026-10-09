import { assertLocalRequest } from "@/lib/control-service";
import { assertControlSession } from "@/lib/control-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request) {
  try {
    assertLocalRequest(request);
    let authenticated = false;
    try { assertControlSession(request); authenticated = true; } catch { /* No credential is issued by a status read. */ }
    const length = process.env.JARVIS_CONTROL_PAIRING_TOKEN?.trim().length ?? 0;
    return Response.json({ authenticated, pairingAvailable: length >= 32 && length <= 256 }, { headers });
  } catch {
    return Response.json({ error: "Local session status unavailable" }, { status: 403, headers });
  }
}
