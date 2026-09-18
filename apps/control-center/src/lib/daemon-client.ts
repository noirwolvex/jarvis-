import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import tls, { type TLSSocket } from "node:tls";

const MAX_FRAME_BYTES = 256 * 1024;
const ALPN = "jarvis-execution/1";

type ForegroundBinding = { hwnd: number; process_id: number; title: string };
export type DaemonDisplay = { id: number; x: number; y: number; width: number; height: number; scale: number };
export type DaemonFrame = { id: string; display: DaemonDisplay; captured_at_ms: number; sha256: string; simulation: boolean };
export type DaemonPreview = { mime: string; width: number; height: number; base64: string };
export type DaemonStatus = {
  simulation: boolean;
  emergency_stopped: boolean;
  capture_ring_frames: number;
  capture_ring_bytes: number;
  audit_events_retained: number;
  native_input: boolean;
  foreground: ForegroundBinding | null;
  accessibility: string;
  sandbox: string;
};
export type DaemonCapture = { frame: DaemonFrame; preview: DaemonPreview; pixels_transport: string };

export type DaemonAction =
  | { kind: "status" }
  | { kind: "capture"; display_id: number }
  | { kind: "click"; display_id: number; frame_id: string; x: number; y: number; foreground: ForegroundBinding }
  | { kind: "type_text"; display_id: number; frame_id: string; text: string; foreground: ForegroundBinding }
  | { kind: "emergency_stop" };

type DaemonHello = { type: "hello"; protocol: number; session: string; max_frame_bytes: number; simulation: boolean };
type DaemonResult = { type: "result"; request_id: string; ok: boolean; data: unknown };
type DaemonOutput = { type: "output"; request_id: string; stream: string; text: string };
type DaemonReply = DaemonHello | DaemonResult | DaemonOutput;

export type DaemonConfig = {
  host: "127.0.0.1" | "localhost" | "::1";
  port: number;
  serverName: string;
  caPath: string;
  clientCertPath: string;
  clientKeyPath: string;
  observeCapability?: string;
  inputCapability?: string;
};

function required(env: NodeJS.ProcessEnv, name: string) {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required for native control mode`);
  return value;
}

function optionalCapability(env: NodeJS.ProcessEnv, name: string) {
  const value = env[name]?.trim();
  if (!value) return undefined;
  if (value.length > 128 || value.includes("\0")) throw new Error(`${name} is invalid`);
  return value;
}

export function daemonConfigFromEnv(env: NodeJS.ProcessEnv = process.env): DaemonConfig {
  const host = (env.JARVIS_DAEMON_HOST?.trim() || "127.0.0.1") as DaemonConfig["host"];
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error("JARVIS_DAEMON_HOST must be loopback");
  const portText = env.JARVIS_DAEMON_PORT?.trim() || "7443";
  const port = Number(portText);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("JARVIS_DAEMON_PORT is invalid");
  const serverName = env.JARVIS_DAEMON_SERVER_NAME?.trim() || "localhost";
  if (!serverName || serverName.length > 253 || serverName.includes("\0")) throw new Error("JARVIS_DAEMON_SERVER_NAME is invalid");
  return {
    host,
    port,
    serverName,
    caPath: required(env, "JARVIS_DAEMON_CA"),
    clientCertPath: required(env, "JARVIS_DAEMON_CLIENT_CERT"),
    clientKeyPath: required(env, "JARVIS_DAEMON_CLIENT_KEY"),
    observeCapability: optionalCapability(env, "JARVIS_DAEMON_OBSERVE_CAPABILITY"),
    inputCapability: optionalCapability(env, "JARVIS_DAEMON_INPUT_CAPABILITY"),
  };
}

class FrameReader {
  private buffer: Buffer<ArrayBufferLike> = Buffer.alloc(0);
  private readonly iterator: AsyncIterator<Buffer<ArrayBufferLike>>;

  constructor(socket: TLSSocket) {
    this.iterator = socket[Symbol.asyncIterator]() as AsyncIterator<Buffer<ArrayBufferLike>>;
  }

  private async ensure(bytes: number) {
    while (this.buffer.length < bytes) {
      const next = await this.iterator.next();
      if (next.done) throw new Error("Rust daemon closed the IPC connection");
      const chunk = Buffer.isBuffer(next.value) ? next.value : Buffer.from(next.value);
      this.buffer = this.buffer.length ? Buffer.concat([this.buffer, chunk]) : chunk;
      if (this.buffer.length > MAX_FRAME_BYTES + 4) throw new Error("Rust daemon frame buffer exceeded limit");
    }
  }

  async read(): Promise<DaemonReply> {
    await this.ensure(4);
    const size = this.buffer.readUInt32BE(0);
    if (size < 1 || size > MAX_FRAME_BYTES) throw new Error("Rust daemon returned an invalid frame length");
    await this.ensure(4 + size);
    const payload = this.buffer.subarray(4, 4 + size);
    this.buffer = this.buffer.subarray(4 + size);
    const parsed: unknown = JSON.parse(payload.toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Rust daemon returned an invalid reply");
    return parsed as DaemonReply;
  }
}

async function connect(config: DaemonConfig) {
  const [ca, cert, key] = await Promise.all([
    readFile(config.caPath),
    readFile(config.clientCertPath),
    readFile(config.clientKeyPath),
  ]);
  const socket = tls.connect({
    host: config.host,
    port: config.port,
    servername: config.serverName,
    ca,
    cert,
    key,
    minVersion: "TLSv1.3",
    ALPNProtocols: [ALPN],
    rejectUnauthorized: true,
  });
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => {
      socket.destroy();
      reject(new Error("Rust daemon TLS handshake timed out"));
    }, 5000);
    socket.once("secureConnect", () => {
      clearTimeout(timeout);
      if (!socket.authorized) {
        socket.destroy();
        reject(new Error("Rust daemon TLS peer was not authorized"));
        return;
      }
      if (socket.alpnProtocol !== ALPN) {
        socket.destroy();
        reject(new Error("Rust daemon ALPN protocol mismatch"));
        return;
      }
      resolve();
    });
    socket.once("error", error => {
      clearTimeout(timeout);
      reject(error);
    });
  });
  return socket;
}

function capabilityFor(config: DaemonConfig, action: DaemonAction) {
  if (action.kind === "capture") return config.observeCapability;
  if (action.kind === "click" || action.kind === "type_text") return config.inputCapability;
  return undefined;
}

export async function sendDaemonAction<T>(action: DaemonAction, env: NodeJS.ProcessEnv = process.env): Promise<T> {
  const config = daemonConfigFromEnv(env);
  const socket = await connect(config);
  try {
    const reader = new FrameReader(socket);
    const hello = await reader.read();
    if (hello.type !== "hello" || hello.protocol !== 1 || typeof hello.session !== "string") throw new Error("Unsupported Rust daemon protocol");
    if (hello.max_frame_bytes < 1024 || hello.max_frame_bytes > MAX_FRAME_BYTES) throw new Error("Rust daemon advertised an invalid frame limit");
    const requestId = randomUUID();
    const request = {
      protocol: 1,
      session: hello.session,
      seq: 1,
      expires_at_ms: Date.now() + 5000,
      request_id: requestId,
      capability_id: capabilityFor(config, action) ?? null,
      action,
    };
    const encoded = Buffer.from(JSON.stringify(request), "utf8");
    if (encoded.length > MAX_FRAME_BYTES) throw new Error("Rust daemon request exceeds frame limit");
    const prefix = Buffer.allocUnsafe(4);
    prefix.writeUInt32BE(encoded.length, 0);
    socket.write(Buffer.concat([prefix, encoded]));
    for (let replies = 0; replies < 64; replies += 1) {
      const reply = await reader.read();
      if (reply.type !== "result" || reply.request_id !== requestId) continue;
      if (!reply.ok) {
        const data = reply.data && typeof reply.data === "object" ? reply.data as Record<string, unknown> : {};
        throw new Error(typeof data.error === "string" ? data.error : "Rust daemon rejected the action");
      }
      return reply.data as T;
    }
    throw new Error("Rust daemon did not return a terminal result");
  } finally {
    socket.destroy();
  }
}

export const daemonStatus = (env?: NodeJS.ProcessEnv) => sendDaemonAction<DaemonStatus>({ kind: "status" }, env);
export const daemonCapture = (displayId = 0, env?: NodeJS.ProcessEnv) => sendDaemonAction<DaemonCapture>({ kind: "capture", display_id: displayId }, env);
export const daemonEmergencyStop = (env?: NodeJS.ProcessEnv) => sendDaemonAction<{ emergency_stopped: boolean; restart_required?: boolean }>({ kind: "emergency_stop" }, env);
export const daemonClick = (frame: DaemonFrame, x: number, y: number, foreground: ForegroundBinding, env?: NodeJS.ProcessEnv) => sendDaemonAction({
  kind: "click", display_id: frame.display.id, frame_id: frame.id, x, y, foreground,
}, env);
export const daemonTypeText = (frame: DaemonFrame, text: string, foreground: ForegroundBinding, env?: NodeJS.ProcessEnv) => sendDaemonAction({
  kind: "type_text", display_id: frame.display.id, frame_id: frame.id, text, foreground,
}, env);
