# Model provider boundary

No model weights, credentials or inference endpoints are bundled or invoked by the demonstration. `services/runtime/src/memory-models.ts` defines the provider interface and privacy/cost/context/resource filtering. Register only operator-approved providers with measured capability and memory profiles.

Tier A covers small local perception/embedding jobs; tier B admits one advanced local model at a time; tier C requires explicit cloud egress permission after data classification/redaction. The initial 8 GiB VRAM budget reserves 4 GiB weights, 0.75 GiB context, 0.5 GiB activations, 1 GiB compositor/capture, and 1.75 GiB headroom. These are admission allocations, not a promise that a named model fits. Benchmark load-time duplication and full context/image peaks before installation.

Keep provider secrets in an OS vault or service secret store. Persist identifiers and measurements, never credentials. Treat outputs as untrusted structured proposals; provider registration does not grant execution privileges. See architecture §§4, 16–17, 23 for consensus, privacy, fallback and calibration.
