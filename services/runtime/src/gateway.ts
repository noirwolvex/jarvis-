import { timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { ReplayGapError } from './events.js';
import { createRuntime, type JarvisRuntime } from './runtime.js';

export interface GatewayOptions { token: string; runtime?: JarvisRuntime; allowedOrigins?: string[]; maxClients?: number; requestsPerMinute?: number }
class HttpError extends Error { constructor(readonly status: number, message: string) { super(message); } }
const commands = ['pause', 'resume', 'stop', 'reset'] as const;
function json(response: ServerResponse, status: number, value: unknown) { response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); response.end(JSON.stringify(value)); }
async function body(request: IncomingMessage): Promise<Record<string, unknown>> {
  if (!request.headers['content-type']?.startsWith('application/json')) throw new HttpError(415, 'Content-Type must be application/json');
  const chunks: Buffer[] = []; let size = 0;
  for await (const chunk of request) { const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk); size += bytes.length; if (size > 16384) throw new HttpError(413, 'Request body exceeds 16 KiB'); chunks.push(bytes); }
  let value: unknown;
  try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { throw new HttpError(400, 'Invalid JSON'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new HttpError(400, 'Expected a JSON object');
  return value as Record<string, unknown>;
}
/** Single-operator reference gateway, bound to loopback by listen(). TLS/session RBAC are deployment work. */
export function createGateway(options: GatewayOptions) {
  if (typeof options.token !== 'string' || options.token.length < 32) throw new Error('JARVIS_GATEWAY_TOKEN must be a random secret with at least 32 characters');
  const expected = Buffer.from(`Bearer ${options.token}`);
  const runtime = options.runtime ?? createRuntime();
  const origins = new Set(options.allowedOrigins ?? ['http://127.0.0.1:3000', 'http://localhost:3000']);
  const maxClients = options.maxClients ?? 16;
  const requestsPerMinute = options.requestsPerMinute ?? 120;
  const buckets = new Map<string, { start: number; count: number }>();
  const authenticate = (request: IncomingMessage) => {
    const supplied = Buffer.from(request.headers.authorization ?? '');
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) throw new HttpError(401, 'Unauthorized');
    if (request.headers.origin && !origins.has(request.headers.origin)) throw new HttpError(403, 'Origin denied');
    const address = request.socket.remoteAddress ?? 'unknown';
    const now = Date.now();
    for (const [key, bucket] of buckets) if (now - bucket.start > 60000) buckets.delete(key);
    const bucket = buckets.get(address) ?? { start: now, count: 0 };
    if (!buckets.has(address) && buckets.size >= 256) throw new HttpError(429, 'Client capacity exceeded');
    bucket.count++; buckets.set(address, bucket);
    if (bucket.count > requestsPerMinute) throw new HttpError(429, 'Rate limit exceeded');
  };
  const cursor = (url: URL) => { const raw = url.searchParams.get('after') ?? '0'; if (!/^\d+$/.test(raw)) throw new HttpError(400, 'Invalid event cursor'); const value = Number(raw); if (!Number.isSafeInteger(value)) throw new HttpError(400, 'Invalid event cursor'); return value; };
  const server = createServer(async (request, response) => {
    try {
      authenticate(request);
      const url = new URL(request.url ?? '/', 'http://127.0.0.1');
      if (request.method === 'GET' && url.pathname === '/health') { json(response, 200, { status: 'ok', mode: 'simulation' }); return; }
      if (request.method === 'GET' && url.pathname === '/state') { json(response, 200, runtime.getState()); return; }
      if (request.method === 'GET' && url.pathname === '/events') { json(response, 200, { events: runtime.events.replay(cursor(url)), cursor: runtime.events.sequence }); return; }
      if (request.method === 'POST' && url.pathname === '/missions') {
        const input = await body(request);
        if (Object.keys(input).some(key => key !== 'title') || typeof input.title !== 'string') throw new HttpError(400, 'Expected only a mission title');
        json(response, 202, runtime.submitMission(input.title)); return;
      }
      if (request.method === 'POST' && url.pathname === '/controls') {
        const input = await body(request);
        if (Object.keys(input).length !== 1 || !commands.includes(input.command as typeof commands[number])) throw new HttpError(400, 'Expected command: pause, resume, stop, or reset');
        switch (input.command) { case 'pause': runtime.pause(); break; case 'resume': runtime.resume(); break; case 'stop': runtime.emergencyStop(); break; case 'reset': runtime.resetEmergencyStop(); break; }
        json(response, 200, runtime.getState()); return;
      }
      throw new HttpError(404, 'Route not found');
    } catch (error) {
      json(response, error instanceof HttpError ? error.status : error instanceof ReplayGapError ? 409 : 400, { error: error instanceof Error ? error.message : 'Request rejected' });
    }
  });
  server.requestTimeout = 10000; server.headersTimeout = 5000;
  const wss = new WebSocketServer({ noServer: true, maxPayload: 16384, perMessageDeflate: false });
  server.on('upgrade', (request, socket, head) => {
    try {
      authenticate(request);
      const url = new URL(request.url ?? '/', 'http://127.0.0.1');
      if (url.pathname !== '/events') throw new HttpError(404, 'Route not found');
      if (wss.clients.size >= maxClients) throw new HttpError(503, 'WebSocket capacity exceeded');
      const replay = runtime.events.replay(cursor(url));
      wss.handleUpgrade(request, socket, head, ws => {
        const send = (event: unknown) => { if (ws.readyState !== WebSocket.OPEN) return; if (ws.bufferedAmount > 1024 * 1024) { ws.close(1013, 'Slow consumer; reconnect with cursor'); return; } ws.send(JSON.stringify(event)); };
        for (const event of replay) send(event);
        const unsubscribe = runtime.events.subscribe(send);
        let alive = true;
        const heartbeat = setInterval(() => { if (!alive) { ws.terminate(); return; } alive = false; ws.ping(); }, 30000);
        heartbeat.unref();
        ws.on('pong', () => { alive = true; });
        ws.on('message', () => { ws.close(1008, 'Event stream is read-only; use authenticated HTTP controls'); });
        ws.on('error', () => { /* close handles cleanup */ });
        ws.on('close', () => { clearInterval(heartbeat); unsubscribe(); });
      });
    } catch (error) {
      const status = error instanceof HttpError ? error.status : error instanceof ReplayGapError ? 409 : 400;
      socket.end(`HTTP/1.1 ${status} Rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
    }
  });
  return {
    runtime, server,
    listen(port = 4318): Promise<number> { return new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', () => { server.removeListener('error', reject); const address = server.address(); if (address && typeof address !== 'string') resolve(address.port); else reject(new Error('Gateway did not bind')); }); }); },
    async close(): Promise<void> { runtime.emergencyStop(); for (const ws of wss.clients) ws.terminate(); await new Promise<void>(resolve => wss.close(() => resolve())); await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); },
  };
}
