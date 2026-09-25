import { assertLocalRequest } from "@/lib/control-service";
import {
  controlSessionCookie,
  issueControlSession,
  verifyPairingToken,
} from "@/lib/control-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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
