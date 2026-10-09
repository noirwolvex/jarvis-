import { assertLocalRequest, readControlObject } from "@/lib/control-service";
import {
  controlSessionCookie,
  issueControlSession,
  verifyPairingToken,
} from "@/lib/control-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff" };
  try {
    assertLocalRequest(request);
    if (!request.headers.get("origin")) throw new Error("Same-origin pairing required");
    if (request.headers.get("x-jarvis-control") !== "pair") throw new Error("Pairing header required");
    const body = await readControlObject(request, 1024);
    if (Object.keys(body).some(key => key !== "token") || typeof body.token !== "string") throw new Error("Invalid pairing request");
    verifyPairingToken(body.token);
    return Response.json({ ok: true }, { headers: { ...headers, "Set-Cookie": controlSessionCookie(issueControlSession()) } });
  } catch {
    return Response.json({ error: "This pairing file is invalid or belongs to an earlier runtime. Choose the current control-session.json file." },
      { status: 403, headers });
  }
}

export async function GET(request: Request) {
  try {
    assertLocalRequest(request);
    const url = new URL(request.url);
    const token = url.searchParams.get("token") || "";
    verifyPairingToken(token);
    const response = new Response(null, { status: 303, headers: { Location: new URL("/", request.url).toString() } });
    response.headers.set("Set-Cookie", controlSessionCookie(issueControlSession()));
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  } catch {
    return Response.json(
      { error: "Local runtime pairing failed" },
      { status: 403, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } },
    );
  }
}
