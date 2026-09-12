import type { ContractTypes } from './types.ts';

type Schema = Record<string, unknown>;
const str = (maxLength = 4096): Schema => ({ type: 'string', minLength: 1, maxLength });
const text = (maxLength = 16384): Schema => ({ type: 'string', maxLength });
const num = (minimum = 0, maximum = Number.MAX_SAFE_INTEGER): Schema => ({ type: 'number', minimum, maximum });
const int = (minimum = 0, maximum = Number.MAX_SAFE_INTEGER): Schema => ({ type: 'integer', minimum, maximum });
const bool: Schema = { type: 'boolean' };
const enumeration = (...values: string[]): Schema => ({ type: 'string', enum: values });
const ref = (name: string): Schema => ({ $ref: `#/$defs/${name}` });
const array = (items: Schema, maxItems = 256, minItems = 0): Schema => ({ type: 'array', items, minItems, maxItems });
const object = (properties: Record<string, Schema>): Schema => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
const nullable = (schema: Schema): Schema => ({ anyOf: [schema, { type: 'null' }] });
const id = str(128);
const date: Schema = { type: 'string', format: 'date-time' };
const factValue: Schema = { type: ['string', 'number', 'boolean', 'null'], maxLength: 16384 };
const facts: Schema = { type: 'object', propertyNames: { type: 'string', pattern: '^[a-zA-Z0-9_.:/-]{1,256}$' }, additionalProperties: factValue, maxProperties: 256 };
const risk = int(0, 5);
const confidence = num(0, 1);
const privacy = enumeration('public', 'internal', 'restricted');
const source = enumeration('simulation', 'accessibility', 'dom', 'file', 'process', 'network', 'vision');
const uniqueIds = { ...array(id), uniqueItems: true };
const actionBase = { id, taskId: id, observationId: id, confidence, riskLevel: risk, preconditions: array(ref('Condition')), expectedState: array(ref('Condition'), 256, 1), verification: ref('VerificationSpec'), timeoutMs: int(1, 120000), requiredCapabilities: { ...array(str(128), 32, 1), uniqueItems: true }, rollback: object({ strategy: enumeration('none', 'restore_snapshot'), snapshotId: nullable(id) }) };
const actionArgs: Record<string, Schema> = {
  OBSERVE: object({ scope: enumeration('desktop', 'window', 'browser'), application: str(256) }),
  CLICK: object({ target: ref('Target'), button: enumeration('left', 'right', 'middle') }),
  DOUBLE_CLICK: object({ target: ref('Target'), button: enumeration('left', 'right', 'middle') }),
  TYPE: object({ target: ref('SemanticTarget'), text: text() }),
  KEY_PRESS: object({ keys: array(str(32), 8, 1) }),
  SCROLL: object({ target: ref('Target'), deltaX: int(-10000, 10000), deltaY: int(-10000, 10000) }),
  DRAG: object({ from: ref('Target'), to: ref('Target') }),
  NAVIGATE: object({ url: { type: 'string', format: 'uri', pattern: '^https?://', maxLength: 4096 }, tabId: id }),
  READ_FILE: object({ path: str(), maxBytes: int(1, 1048576) }),
  WRITE_FILE: object({ path: str(), content: text(1048576), overwrite: bool }),
  START_PROCESS: object({ executable: str(), argv: array(text(4096), 64), cwd: str() }),
  STOP_PROCESS: object({ processId: int(1), expectedExecutable: str() }),
  TERMINAL_COMMAND: object({ executable: str(), argv: array(text(4096), 64), cwd: str(), environmentPolicy: { const: 'clean', type: 'string' } }),
  WAIT: object({ durationMs: int(0, 30000) }),
  REQUEST_APPROVAL: object({ reason: str(), permissionRequestId: id }),
  VERIFY: object({ conditions: array(ref('Condition'), 256, 1) }),
};
export const definitions: Record<string, Schema> = {
  Condition: object({ key: str(256), operator: enumeration('equals', 'exists', 'absent'), value: factValue }),
  SemanticTarget: object({ kind: { const: 'semantic', type: 'string' }, application: str(256), elementId: id, label: str(256) }),
  CoordinateTarget: object({ kind: { const: 'coordinates', type: 'string' }, displayId: id, x: int(0, 65535), y: int(0, 65535), dpiScale: num(0.25, 8), frameId: id }),
  Target: { oneOf: [ref('SemanticTarget'), ref('CoordinateTarget')] },
  VerificationSpec: object({ method: enumeration('FILE_STATE', 'UI_TREE_DIFF', 'DOM_DIFF', 'PROCESS_STATE', 'NETWORK_STATE', 'COMPOSITE'), minEvidence: int(1, 16), timeoutMs: int(1, 120000) }),
  Action: { oneOf: Object.entries(actionArgs).map(([type, args]) => object({ ...actionBase, type: { const: type, type: 'string' }, args })) },
  AgentIntent: object({ id, taskId: id, goal: str(), privacy, requestedBy: id, createdAt: date, constraints: array(str()) }),
  Node: object({ id, label: str(256), dependsOn: uniqueIds, action: ref('Action'), maxRetries: int(0, 3) }),
  DAG: object({ id, nodes: array(ref('Node'), 64, 1), maxConcurrency: int(1, 4) }),
  Plan: object({ id, taskId: id, intentId: id, version: int(1), createdAt: date, summary: str(), dag: ref('DAG'), maxDurationMs: int(1, 600000) }),
  Evidence: object({ id, source, observedAt: date, key: str(256), value: factValue }),
  Observation: object({ id, taskId: id, observedAt: date, source, confidence, validForMs: int(1, 30000), stability: enumeration('UI_STABLE', 'UI_TRANSITIONING', 'UI_UNKNOWN'), revision: int(), facts, evidence: array(ref('Evidence'), 256) }),
  WorldState: object({ id, observedAt: date, source, confidence, stalenessMs: int(), dependencies: uniqueIds, revision: int(), facts, committedActionIds: array(id, 4096) }),
  VerificationResult: object({ id, taskId: id, actionId: id, observationId: id, status: enumeration('passed', 'failed', 'inconclusive'), confidence, checkedAt: date, checks: array(object({ condition: ref('Condition'), passed: bool, evidenceIds: uniqueIds })), reason: str() }),
  RecoveryPlan: object({ id, taskId: id, actionId: id, category: enumeration('stale_observation', 'verification_failed', 'adapter_error', 'timeout', 'resource_exhaustion', 'permission_denied'), strategy: enumeration('reobserve', 'retry_read', 'escalate'), attempt: int(0, 3), maxAttempts: int(0, 3), backoffMs: int(0, 30000), requiresApproval: bool, reason: str() }),
  Capability: object({ id, issuer: id, subject: id, taskId: id, actionTypes: { ...array(enumeration(...Object.keys(actionArgs)), 16, 1), uniqueItems: true }, scopes: { ...array(str(128), 32, 1), uniqueItems: true }, issuedAt: date, expiresAt: date, maxUses: int(1, 128), signature: { type: 'string', pattern: '^[a-f0-9]{64}$' } }),
  PermissionRequest: object({ id, taskId: id, actionId: id, requestedBy: id, scopes: array(str(128), 32, 1), reason: str(), riskLevel: risk, expiresAt: date }),
  PolicyDecision: object({ id, taskId: id, actionId: id, decision: enumeration('allow', 'deny', 'approval_required'), reasons: array(str(), 32, 1), requiredCapabilities: array(str(128), 32), evaluatedAt: date, policyVersion: int(1) }),
  AgentEvent: object({ id, sequence: int(1), taskId: id, timestamp: date, type: enumeration('TASK_CREATED', 'PLAN_CREATED', 'PLAN_APPROVED', 'ACTION_STARTED', 'ACTION_EXECUTED', 'OBSERVATION_CAPTURED', 'ACTION_VERIFIED', 'STATE_CHANGED', 'ERROR_DETECTED', 'RECOVERY_STARTED', 'RECOVERY_COMPLETED', 'RESOURCE_WARNING', 'PERMISSION_REQUESTED', 'USER_APPROVAL_REQUIRED', 'TASK_COMPLETED', 'TASK_FAILED', 'TASK_CANCELLED', 'EMERGENCY_STOP', 'RUNTIME_PAUSED', 'RUNTIME_RESUMED'), source: id, traceId: id, actionId: nullable(id), payload: object({ message: str(), nodeId: nullable(id), status: nullable(str(128)), evidenceIds: uniqueIds, revision: nullable(int()) }), previousHash: { type: 'string', pattern: '^[a-f0-9]{64}$' }, hash: { type: 'string', pattern: '^[a-f0-9]{64}$' } }),
  TaskResult: object({ taskId: id, status: enumeration('completed', 'failed', 'cancelled'), startedAt: date, finishedAt: date, completedNodeIds: uniqueIds, failedNodeIds: uniqueIds, verificationIds: uniqueIds, summary: str() }),
  ModelInvocation: object({ id, taskId: id, modelId: id, role: enumeration('commander', 'planner', 'research', 'vision', 'grounding', 'executor', 'verifier', 'security', 'recovery', 'memory', 'critic', 'resource'), privacy, locality: enumeration('local', 'cloud'), inputTokens: int(0, 1000000), outputTokens: int(0, 1000000), durationMs: num(0, 600000), costUsd: num(0, 10000), status: enumeration('pending', 'completed', 'failed', 'rejected') }),
  ResourceSnapshot: object({ id, observedAt: date, source: enumeration('simulated', 'measured'), cpuPercent: num(0, 100), ramUsedMb: num(), ramTotalMb: num(1), vramUsedMb: num(), vramTotalMb: num(1), gpuPercent: num(0, 100), networkHealthy: bool, thermalPressure: bool, frameBufferBytes: int(), inferenceQueueDepth: int(0, 1024) }),
  AgentPlan: ref('Plan'),
  DAGNode: ref('Node'),
};
export const contractNames: (keyof ContractTypes)[] = ['AgentIntent', 'Plan', 'AgentPlan', 'DAG', 'Node', 'DAGNode', 'Action', 'Observation', 'WorldState', 'VerificationResult', 'RecoveryPlan', 'Capability', 'PermissionRequest', 'PolicyDecision', 'AgentEvent', 'TaskResult', 'ModelInvocation', 'ResourceSnapshot'];
export const schemaDocument = { $schema: 'https://json-schema.org/draft/2020-12/schema', $id: 'https://jarvis.local/schemas/contracts.schema.json', $defs: definitions };
export function schemaFor(name: keyof ContractTypes) { return { ...schemaDocument, $id: `https://jarvis.local/schemas/${name}.schema.json`, title: name, $ref: `#/$defs/${name}` }; }
