import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const CONTROL_SESSION_COOKIE = "jarvis_control_session";
const SESSION_CONTEXT = "jarvis-control-session-v1:";

function pairingToken(env: NodeJS.ProcessEnv = process.env): string {
  const token = env.JARVIS_CONTROL_PAIRING_TOKEN?.trim() || "";
  if (token.length < 32 || token.length > 256) {
    throw new Error("Runtime control pairing token is unavailable or invalid");
  }
  return token;
}

function equalText(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function signature(nonce: string, env: NodeJS.ProcessEnv = process.env): string {
  return createHmac("sha256", pairingToken(env)).update(SESSION_CONTEXT + nonce).digest("base64url");
}

export function verifyPairingToken(candidate: string, env: NodeJS.ProcessEnv = process.env): void {
  const expected = pairingToken(env);
  if (!candidate || !equalText(candidate, expected)) throw new Error("Invalid runtime pairing token");
}

export function issueControlSession(env: NodeJS.ProcessEnv = process.env): string {
  const nonce = randomBytes(32).toString("base64url");
  return `${nonce}.${signature(nonce, env)}`;
}

function cookieValue(request: Request): string {
  const raw = request.headers.get("cookie") || "";
  for (const part of raw.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === CONTROL_SESSION_COOKIE) return rest.join("=");
  }
  return "";
}

export function assertControlSession(request: Request, env: NodeJS.ProcessEnv = process.env): void {
  // Standalone simulation remains usable without the managed launcher. Real native/hybrid
  // execution always comes from scripts/jarvis_runtime.py and therefore has a pairing token.
  const configured = env.JARVIS_CONTROL_PAIRING_TOKEN?.trim() || "";
  const mode = env.JARVIS_CONTROL_MODE?.trim().toLowerCase() || "simulation";
  if (!configured && mode === "simulation") return;
  if (!configured) throw new Error("Runtime control session is not provisioned");

  const value = cookieValue(request);
  const dot = value.indexOf(".");
  if (dot <= 0 || dot === value.length - 1) throw new Error("Authenticated runtime control session required");
  const nonce = value.slice(0, dot);
  const supplied = value.slice(dot + 1);
  if (nonce.length > 128 || supplied.length > 128 || !equalText(supplied, signature(nonce, env))) {
    throw new Error("Authenticated runtime control session required");
  }
}

export function controlSessionCookie(value: string): string {
  return `${CONTROL_SESSION_COOKIE}=${value}; Path=/; HttpOnly; SameSite=Strict`;
}
