import { randomUUID } from 'node:crypto';
import { parseContract, type ResourceSnapshot } from '@jarvis/contracts';

export function simulatedResources(): ResourceSnapshot {
  return { id: randomUUID(), observedAt: new Date().toISOString(), source: 'simulated', cpuPercent: 18, ramUsedMb: 4320, ramTotalMb: 16384, vramUsedMb: 0, vramTotalMb: 8192, gpuPercent: 0, networkHealthy: true, thermalPressure: false, frameBufferBytes: 0, inferenceQueueDepth: 0 };
}
export class ResourceGovernor {
  #snapshot = simulatedResources();
  update(value: unknown) {
    const snapshot = parseContract('ResourceSnapshot', value);
    if (snapshot.ramUsedMb > snapshot.ramTotalMb || snapshot.vramUsedMb > snapshot.vramTotalMb) throw new Error('Resource usage exceeds physical capacity');
    this.#snapshot = snapshot;
  }
  snapshot() { return structuredClone(this.#snapshot); }
  recommendations() {
    const s = this.#snapshot;
    return { captureFps: s.cpuPercent > 90 || s.thermalPressure ? 1 : 5, resolutionScale: s.vramUsedMb / s.vramTotalMb > 0.9 ? 0.5 : 1, releaseFrames: s.ramUsedMb / s.ramTotalMb > 0.85, allowInference: s.vramUsedMb / s.vramTotalMb < 0.9 && s.ramUsedMb / s.ramTotalMb < 0.9 && !s.thermalPressure && s.inferenceQueueDepth < 4 };
  }
  assertCanExecute() { if (this.#snapshot.ramUsedMb / this.#snapshot.ramTotalMb >= 0.95 || this.#snapshot.thermalPressure) throw new Error('Resource budget exhausted'); }
}
/** Byte limit and item limit both apply, and callers cannot mutate retained bytes. */
export class FrameRingBuffer {
  #frames: { id: string; bytes: Uint8Array }[] = [];
  #bytes = 0;
  constructor(readonly maxBytes = 16 * 1024 * 1024, readonly maxFrames = 8) { if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || !Number.isSafeInteger(maxFrames) || maxFrames < 1) throw new Error('Invalid frame bounds'); }
  push(id: string, bytes: Uint8Array): boolean {
    if (bytes.byteLength > this.maxBytes || this.#frames.some(frame => frame.id === id)) return false;
    while (this.#frames.length >= this.maxFrames || this.#bytes + bytes.byteLength > this.maxBytes) { const dropped = this.#frames.shift(); if (dropped) this.#bytes -= dropped.bytes.byteLength; }
    this.#frames.push({ id, bytes: bytes.slice() }); this.#bytes += bytes.byteLength; return true;
  }
  get byteLength() { return this.#bytes; }
  get length() { return this.#frames.length; }
  clear() { this.#frames = []; this.#bytes = 0; }
}
