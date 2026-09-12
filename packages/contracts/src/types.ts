/** Wire contracts. Unknown object fields are rejected by the matching JSON schemas. */
export type Privacy = 'public' | 'internal' | 'restricted';
export type RiskLevel = 0 | 1 | 2 | 3 | 4 | 5;
export type FactValue = string | number | boolean | null;
export type AgentStatus = 'IDLE' | 'THINKING' | 'PLANNING' | 'WAITING_FOR_APPROVAL' | 'EXECUTING' | 'VERIFYING' | 'RECOVERING' | 'PAUSED' | 'RESOURCE_LIMITED' | 'ERROR' | 'EMERGENCY_STOP' | 'COMPLETED';
export interface AgentIntent { id: string; taskId: string; goal: string; privacy: Privacy; requestedBy: string; createdAt: string; constraints: string[] }
export interface Condition { key: string; operator: 'equals' | 'exists' | 'absent'; value: FactValue }
export interface SemanticTarget { kind: 'semantic'; application: string; elementId: string; label: string }
export interface CoordinateTarget { kind: 'coordinates'; displayId: string; x: number; y: number; dpiScale: number; frameId: string }
export type Target = SemanticTarget | CoordinateTarget;
export interface VerificationSpec { method: 'FILE_STATE' | 'UI_TREE_DIFF' | 'DOM_DIFF' | 'PROCESS_STATE' | 'NETWORK_STATE' | 'COMPOSITE'; minEvidence: number; timeoutMs: number }
export interface ActionBase { id: string; taskId: string; observationId: string; confidence: number; riskLevel: RiskLevel; preconditions: Condition[]; expectedState: Condition[]; verification: VerificationSpec; timeoutMs: number; requiredCapabilities: string[]; rollback: { strategy: 'none' | 'restore_snapshot'; snapshotId: string | null } }
export type Action = ActionBase & (
  | { type: 'OBSERVE'; args: { scope: 'desktop' | 'window' | 'browser'; application: string } }
  | { type: 'CLICK' | 'DOUBLE_CLICK'; args: { target: Target; button: 'left' | 'right' | 'middle' } }
  | { type: 'TYPE'; args: { target: SemanticTarget; text: string } }
  | { type: 'KEY_PRESS'; args: { keys: string[] } }
  | { type: 'SCROLL'; args: { target: Target; deltaX: number; deltaY: number } }
  | { type: 'DRAG'; args: { from: Target; to: Target } }
  | { type: 'NAVIGATE'; args: { url: string; tabId: string } }
  | { type: 'READ_FILE'; args: { path: string; maxBytes: number } }
  | { type: 'WRITE_FILE'; args: { path: string; content: string; overwrite: boolean } }
  | { type: 'START_PROCESS'; args: { executable: string; argv: string[]; cwd: string } }
  | { type: 'STOP_PROCESS'; args: { processId: number; expectedExecutable: string } }
  | { type: 'TERMINAL_COMMAND'; args: { executable: string; argv: string[]; cwd: string; environmentPolicy: 'clean' } }
  | { type: 'WAIT'; args: { durationMs: number } }
  | { type: 'REQUEST_APPROVAL'; args: { reason: string; permissionRequestId: string } }
  | { type: 'VERIFY'; args: { conditions: Condition[] } }
);
export interface Node { id: string; label: string; dependsOn: string[]; action: Action; maxRetries: number }
export interface DAG { id: string; nodes: Node[]; maxConcurrency: number }
export interface Plan { id: string; taskId: string; intentId: string; version: number; createdAt: string; summary: string; dag: DAG; maxDurationMs: number }
export type AgentPlan = Plan;
export type DAGNode = Node;
export interface Evidence { id: string; source: 'simulation' | 'accessibility' | 'dom' | 'file' | 'process' | 'network' | 'vision'; observedAt: string; key: string; value: FactValue }
export interface Observation { id: string; taskId: string; observedAt: string; source: Evidence['source']; confidence: number; validForMs: number; stability: 'UI_STABLE' | 'UI_TRANSITIONING' | 'UI_UNKNOWN'; revision: number; facts: Record<string, FactValue>; evidence: Evidence[] }
export interface WorldState { id: string; observedAt: string; source: Evidence['source']; confidence: number; stalenessMs: number; dependencies: string[]; revision: number; facts: Record<string, FactValue>; committedActionIds: string[] }
export interface VerificationResult { id: string; taskId: string; actionId: string; observationId: string; status: 'passed' | 'failed' | 'inconclusive'; confidence: number; checkedAt: string; checks: { condition: Condition; passed: boolean; evidenceIds: string[] }[]; reason: string }
export interface RecoveryPlan { id: string; taskId: string; actionId: string; category: 'stale_observation' | 'verification_failed' | 'adapter_error' | 'timeout' | 'resource_exhaustion' | 'permission_denied'; strategy: 'reobserve' | 'retry_read' | 'escalate'; attempt: number; maxAttempts: number; backoffMs: number; requiresApproval: boolean; reason: string }
export interface Capability { id: string; issuer: string; subject: string; taskId: string; actionTypes: Action['type'][]; scopes: string[]; issuedAt: string; expiresAt: string; maxUses: number; signature: string }
export interface PermissionRequest { id: string; taskId: string; actionId: string; requestedBy: string; scopes: string[]; reason: string; riskLevel: RiskLevel; expiresAt: string }
export interface PolicyDecision { id: string; taskId: string; actionId: string; decision: 'allow' | 'deny' | 'approval_required'; reasons: string[]; requiredCapabilities: string[]; evaluatedAt: string; policyVersion: number }
export type EventType = 'TASK_CREATED' | 'PLAN_CREATED' | 'PLAN_APPROVED' | 'ACTION_STARTED' | 'ACTION_EXECUTED' | 'OBSERVATION_CAPTURED' | 'ACTION_VERIFIED' | 'STATE_CHANGED' | 'ERROR_DETECTED' | 'RECOVERY_STARTED' | 'RECOVERY_COMPLETED' | 'RESOURCE_WARNING' | 'PERMISSION_REQUESTED' | 'USER_APPROVAL_REQUIRED' | 'TASK_COMPLETED' | 'TASK_FAILED' | 'TASK_CANCELLED' | 'EMERGENCY_STOP' | 'RUNTIME_PAUSED' | 'RUNTIME_RESUMED';
export interface AgentEvent { id: string; sequence: number; taskId: string; timestamp: string; type: EventType; source: string; traceId: string; actionId: string | null; payload: { message: string; nodeId: string | null; status: string | null; evidenceIds: string[]; revision: number | null }; previousHash: string; hash: string }
export interface TaskResult { taskId: string; status: 'completed' | 'failed' | 'cancelled'; startedAt: string; finishedAt: string; completedNodeIds: string[]; failedNodeIds: string[]; verificationIds: string[]; summary: string }
export interface ModelInvocation { id: string; taskId: string; modelId: string; role: 'commander' | 'planner' | 'research' | 'vision' | 'grounding' | 'executor' | 'verifier' | 'security' | 'recovery' | 'memory' | 'critic' | 'resource'; privacy: Privacy; locality: 'local' | 'cloud'; inputTokens: number; outputTokens: number; durationMs: number; costUsd: number; status: 'pending' | 'completed' | 'failed' | 'rejected' }
export interface ResourceSnapshot { id: string; observedAt: string; source: 'simulated' | 'measured'; cpuPercent: number; ramUsedMb: number; ramTotalMb: number; vramUsedMb: number; vramTotalMb: number; gpuPercent: number; networkHealthy: boolean; thermalPressure: boolean; frameBufferBytes: number; inferenceQueueDepth: number }
export interface ContractTypes { AgentIntent: AgentIntent; Plan: Plan; AgentPlan: Plan; DAG: DAG; Node: Node; DAGNode: Node; Action: Action; Observation: Observation; WorldState: WorldState; VerificationResult: VerificationResult; RecoveryPlan: RecoveryPlan; Capability: Capability; PermissionRequest: PermissionRequest; PolicyDecision: PolicyDecision; AgentEvent: AgentEvent; TaskResult: TaskResult; ModelInvocation: ModelInvocation; ResourceSnapshot: ResourceSnapshot }
