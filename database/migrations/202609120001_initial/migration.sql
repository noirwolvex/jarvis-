-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateEnum
CREATE TYPE "Platform" AS ENUM ('WINDOWS', 'LINUX');

-- CreateEnum
CREATE TYPE "AgentStatus" AS ENUM ('IDLE', 'THINKING', 'PLANNING', 'WAITING_FOR_APPROVAL', 'EXECUTING', 'VERIFYING', 'RECOVERING', 'PAUSED', 'RESOURCE_LIMITED', 'ERROR', 'EMERGENCY_STOP', 'COMPLETED');

-- CreateEnum
CREATE TYPE "TaskStatus" AS ENUM ('CREATED', 'PLANNING', 'READY', 'RUNNING', 'WAITING_FOR_APPROVAL', 'PAUSED', 'RECOVERING', 'COMPLETED', 'FAILED', 'CANCELLED', 'EMERGENCY_STOP');

-- CreateEnum
CREATE TYPE "ExecutionStatus" AS ENUM ('QUEUED', 'RUNNING', 'VERIFYING', 'SUCCEEDED', 'PARTIAL_SUCCESS', 'FAILED', 'UNKNOWN', 'CANCELLED');

-- CreateEnum
CREATE TYPE "NodeKind" AS ENUM ('ACTION', 'CONDITION', 'APPROVAL', 'JOIN', 'COMPENSATION');

-- CreateEnum
CREATE TYPE "ActionKind" AS ENUM ('OBSERVE', 'CLICK', 'DOUBLE_CLICK', 'MOVE_MOUSE', 'TYPE', 'KEY_PRESS', 'SCROLL', 'DRAG', 'CLIPBOARD_READ', 'CLIPBOARD_WRITE', 'NAVIGATE', 'BROWSER_READ', 'BROWSER_SCRIPT', 'READ_FILE', 'WRITE_FILE', 'DELETE_FILE', 'START_PROCESS', 'STOP_PROCESS', 'TERMINAL_COMMAND', 'NETWORK_REQUEST', 'SYSTEM_SHUTDOWN', 'WAIT', 'REQUEST_APPROVAL', 'VERIFY');

-- CreateEnum
CREATE TYPE "Reversibility" AS ENUM ('REVERSIBLE', 'PARTIALLY_REVERSIBLE', 'IRREVERSIBLE', 'UNKNOWN');

-- CreateEnum
CREATE TYPE "VerificationOutcome" AS ENUM ('SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'UNKNOWN', 'UNSAFE');

-- CreateEnum
CREATE TYPE "DataClassification" AS ENUM ('PUBLIC', 'INTERNAL', 'PRIVATE', 'SENSITIVE', 'RESTRICTED');

-- CreateEnum
CREATE TYPE "ObservationSource" AS ENUM ('SCREEN', 'OCR', 'ACCESSIBILITY', 'DOM', 'PROCESS', 'FILESYSTEM', 'NETWORK', 'INPUT', 'AUDIO', 'SIMULATION');

-- CreateEnum
CREATE TYPE "PolicyEffect" AS ENUM ('ALLOW', 'DENY', 'REQUIRE_APPROVAL');

-- CreateEnum
CREATE TYPE "ApprovalStatus" AS ENUM ('PENDING', 'APPROVED', 'DENIED', 'EXPIRED', 'REVOKED');

-- CreateEnum
CREATE TYPE "MemoryKind" AS ENUM ('WORKING', 'EPISODIC', 'SEMANTIC', 'PROCEDURAL', 'FAILURE', 'USER_PREFERENCE');

-- CreateEnum
CREATE TYPE "RecoveryStatus" AS ENUM ('DETECTED', 'FROZEN', 'CLASSIFIED', 'EVIDENCE_COLLECTED', 'OPTIONS_RANKED', 'POLICY_CHECKED', 'EXECUTING', 'VERIFYING', 'RESUMED', 'ESCALATED', 'EXHAUSTED');

-- CreateEnum
CREATE TYPE "ModelTier" AS ENUM ('LOCAL_FAST', 'LOCAL_ADVANCED', 'CLOUD_FRONTIER', 'RULE_BASED');

-- CreateEnum
CREATE TYPE "OutboxStatus" AS ENUM ('PENDING', 'LEASED', 'DELIVERED', 'DEAD_LETTER');

-- CreateEnum
CREATE TYPE "HealthMode" AS ENUM ('NORMAL', 'DEGRADED', 'SAFE_MODE', 'OBSERVATION_ONLY');

-- CreateEnum
CREATE TYPE "PluginStatus" AS ENUM ('STAGED', 'VERIFIED', 'ENABLED', 'DISABLED', 'QUARANTINED');

-- CreateTable
CREATE TABLE "User" (
    "id" UUID NOT NULL,
    "externalSubject" VARCHAR(255) NOT NULL,
    "displayName" VARCHAR(120) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "disabledAt" TIMESTAMPTZ(3),

    CONSTRAINT "User_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Session" (
    "id" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "deviceId" UUID,
    "tokenDigest" VARCHAR(128) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,
    "revokedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Session_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Device" (
    "id" UUID NOT NULL,
    "ownerId" UUID NOT NULL,
    "name" VARCHAR(120) NOT NULL,
    "platform" "Platform" NOT NULL,
    "publicKey" TEXT NOT NULL,
    "bootId" VARCHAR(128) NOT NULL,
    "emergencyEpoch" INTEGER NOT NULL DEFAULT 0,
    "healthMode" "HealthMode" NOT NULL DEFAULT 'OBSERVATION_ONLY',
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "lastSeenAt" TIMESTAMPTZ(3),

    CONSTRAINT "Device_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Agent" (
    "id" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "role" VARCHAR(64) NOT NULL,
    "status" "AgentStatus" NOT NULL DEFAULT 'IDLE',
    "configuration" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "Agent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Task" (
    "id" UUID NOT NULL,
    "ownerId" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "sessionId" UUID,
    "title" VARCHAR(240) NOT NULL,
    "intent" JSONB NOT NULL,
    "classification" "DataClassification" NOT NULL DEFAULT 'PRIVATE',
    "status" "TaskStatus" NOT NULL DEFAULT 'CREATED',
    "priority" INTEGER NOT NULL DEFAULT 0,
    "revision" INTEGER NOT NULL DEFAULT 0,
    "budget" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "deadlineAt" TIMESTAMPTZ(3),
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Task_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "TaskVersion" (
    "id" UUID NOT NULL,
    "taskId" UUID NOT NULL,
    "version" INTEGER NOT NULL,
    "contractVersion" VARCHAR(32) NOT NULL,
    "plan" JSONB NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "TaskVersion_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "TaskNode" (
    "id" UUID NOT NULL,
    "taskVersionId" UUID NOT NULL,
    "key" VARCHAR(128) NOT NULL,
    "kind" "NodeKind" NOT NULL,
    "contract" JSONB NOT NULL,
    "maxAttempts" INTEGER NOT NULL DEFAULT 1,
    "timeoutMs" INTEGER NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "TaskNode_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "TaskDependency" (
    "id" UUID NOT NULL,
    "taskVersionId" UUID NOT NULL,
    "fromNodeId" UUID NOT NULL,
    "toNodeId" UUID NOT NULL,
    "condition" JSONB,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "TaskDependency_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Execution" (
    "id" UUID NOT NULL,
    "taskId" UUID NOT NULL,
    "taskVersionId" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "status" "ExecutionStatus" NOT NULL DEFAULT 'QUEUED',
    "traceId" VARCHAR(64) NOT NULL,
    "leaseOwner" VARCHAR(128),
    "fence" BIGINT NOT NULL DEFAULT 0,
    "leaseExpiresAt" TIMESTAMPTZ(3),
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "startedAt" TIMESTAMPTZ(3),
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Execution_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "NodeExecution" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "nodeId" UUID NOT NULL,
    "attempt" INTEGER NOT NULL DEFAULT 1,
    "status" "ExecutionStatus" NOT NULL DEFAULT 'QUEUED',
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "NodeExecution_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Action" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "nodeExecutionId" UUID NOT NULL,
    "idempotencyKey" VARCHAR(128) NOT NULL,
    "kind" "ActionKind" NOT NULL,
    "status" "ExecutionStatus" NOT NULL DEFAULT 'QUEUED',
    "riskLevel" INTEGER NOT NULL,
    "reversibility" "Reversibility" NOT NULL,
    "contract" JSONB NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "policyDecisionId" UUID NOT NULL,
    "observedStateHash" CHAR(64) NOT NULL,
    "timeoutMs" INTEGER NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "startedAt" TIMESTAMPTZ(3),
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Action_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ActionResult" (
    "id" UUID NOT NULL,
    "actionId" UUID NOT NULL,
    "attempt" INTEGER NOT NULL,
    "outcome" "VerificationOutcome" NOT NULL,
    "exitCode" INTEGER,
    "durationMs" INTEGER NOT NULL,
    "result" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ActionResult_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Observation" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "actionId" UUID,
    "worldStateId" UUID NOT NULL,
    "artifactId" UUID,
    "source" "ObservationSource" NOT NULL,
    "frameId" VARCHAR(128),
    "bootId" VARCHAR(128) NOT NULL,
    "stateVersion" BIGINT NOT NULL,
    "observedMonotonicNs" BIGINT NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "observedAt" TIMESTAMPTZ(3) NOT NULL,
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "payload" JSONB NOT NULL,

    CONSTRAINT "Observation_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Verification" (
    "id" UUID NOT NULL,
    "actionId" UUID NOT NULL,
    "observationId" UUID NOT NULL,
    "outcome" "VerificationOutcome" NOT NULL,
    "method" VARCHAR(64) NOT NULL,
    "predicate" JSONB NOT NULL,
    "measured" JSONB NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Verification_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "WorldState" (
    "id" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "version" BIGINT NOT NULL,
    "bootId" VARCHAR(128) NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "schemaVersion" VARCHAR(32) NOT NULL,
    "state" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "observedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "WorldState_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "StateSnapshot" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "worldStateId" UUID NOT NULL,
    "artifactId" UUID,
    "contentHash" CHAR(64) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "StateSnapshot_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "EvidenceArtifact" (
    "id" UUID NOT NULL,
    "objectKey" VARCHAR(512) NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "byteLength" BIGINT NOT NULL,
    "mediaType" VARCHAR(128) NOT NULL,
    "classification" "DataClassification" NOT NULL,
    "redactionVersion" VARCHAR(64) NOT NULL,
    "encrypted" BOOLEAN NOT NULL DEFAULT true,
    "encryptionKeyRef" VARCHAR(255) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt" TIMESTAMPTZ(3),
    "deletedAt" TIMESTAMPTZ(3),

    CONSTRAINT "EvidenceArtifact_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "VisionSnapshot" (
    "id" UUID NOT NULL,
    "observationId" UUID NOT NULL,
    "artifactId" UUID NOT NULL,
    "displayId" VARCHAR(128) NOT NULL,
    "width" INTEGER NOT NULL,
    "height" INTEGER NOT NULL,
    "scale" DOUBLE PRECISION NOT NULL,
    "roi" JSONB NOT NULL,
    "detections" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "VisionSnapshot_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Policy" (
    "id" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "version" INTEGER NOT NULL,
    "effect" "PolicyEffect" NOT NULL,
    "rules" JSONB NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "Policy_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Permission" (
    "id" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "policyId" UUID NOT NULL,
    "capabilityName" VARCHAR(128) NOT NULL,
    "scope" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "expiresAt" TIMESTAMPTZ(3),
    "revokedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Permission_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "PolicyDecision" (
    "id" UUID NOT NULL,
    "policyId" UUID NOT NULL,
    "effect" "PolicyEffect" NOT NULL,
    "actionHash" CHAR(64) NOT NULL,
    "stateHash" CHAR(64) NOT NULL,
    "evaluatedInput" JSONB NOT NULL,
    "reasons" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "PolicyDecision_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Capability" (
    "id" UUID NOT NULL,
    "userId" UUID NOT NULL,
    "sessionId" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "taskId" UUID NOT NULL,
    "policyId" UUID NOT NULL,
    "policyDecisionId" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "scope" JSONB NOT NULL,
    "issuerKeyId" VARCHAR(128) NOT NULL,
    "tokenDigest" VARCHAR(128) NOT NULL,
    "actionHash" CHAR(64) NOT NULL,
    "emergencyEpoch" INTEGER NOT NULL,
    "maxUses" INTEGER NOT NULL DEFAULT 1,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,
    "revokedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Capability_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "CapabilityUse" (
    "id" UUID NOT NULL,
    "capabilityId" UUID NOT NULL,
    "actionId" UUID NOT NULL,
    "nonceDigest" CHAR(64) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "CapabilityUse_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Approval" (
    "id" UUID NOT NULL,
    "actionId" UUID NOT NULL,
    "reviewerId" UUID,
    "status" "ApprovalStatus" NOT NULL DEFAULT 'PENDING',
    "actionHash" CHAR(64) NOT NULL,
    "policyHash" CHAR(64) NOT NULL,
    "stateHash" CHAR(64) NOT NULL,
    "presentation" JSONB NOT NULL,
    "decision" JSONB,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "decidedAt" TIMESTAMPTZ(3),
    "expiresAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "Approval_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Memory" (
    "id" UUID NOT NULL,
    "ownerId" UUID NOT NULL,
    "taskId" UUID,
    "artifactId" UUID,
    "kind" "MemoryKind" NOT NULL,
    "classification" "DataClassification" NOT NULL,
    "content" JSONB NOT NULL,
    "source" VARCHAR(255) NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "importance" DOUBLE PRECISION NOT NULL,
    "freshness" DOUBLE PRECISION NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "verifiedAt" TIMESTAMPTZ(3),
    "expiresAt" TIMESTAMPTZ(3),
    "tombstonedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Memory_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "MemoryEmbedding" (
    "id" UUID NOT NULL,
    "memoryId" UUID NOT NULL,
    "modelId" UUID NOT NULL,
    "dimensions" INTEGER NOT NULL,
    "values" DOUBLE PRECISION[],
    "contentHash" CHAR(64) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "MemoryEmbedding_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Failure" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "actionId" UUID,
    "category" VARCHAR(64) NOT NULL,
    "severity" INTEGER NOT NULL,
    "evidence" JSONB NOT NULL,
    "summary" VARCHAR(1024) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Failure_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Recovery" (
    "id" UUID NOT NULL,
    "failureId" UUID NOT NULL,
    "attempt" INTEGER NOT NULL,
    "status" "RecoveryStatus" NOT NULL DEFAULT 'DETECTED',
    "plan" JSONB NOT NULL,
    "budget" JSONB NOT NULL,
    "outcome" JSONB,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "deadlineAt" TIMESTAMPTZ(3) NOT NULL,
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "Recovery_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Model" (
    "id" UUID NOT NULL,
    "provider" VARCHAR(128) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "version" VARCHAR(128) NOT NULL,
    "tier" "ModelTier" NOT NULL,
    "capabilities" JSONB NOT NULL,
    "configuration" JSONB NOT NULL,
    "artifactHash" CHAR(64),
    "enabled" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "Model_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ModelInvocation" (
    "id" UUID NOT NULL,
    "modelId" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "agentId" UUID,
    "requestHash" CHAR(64) NOT NULL,
    "response" JSONB,
    "classification" "DataClassification" NOT NULL,
    "inputTokens" INTEGER NOT NULL DEFAULT 0,
    "outputTokens" INTEGER NOT NULL DEFAULT 0,
    "costUsd" DECIMAL(16,8),
    "latencyMs" INTEGER,
    "peakRamBytes" BIGINT,
    "peakVramBytes" BIGINT,
    "successful" BOOLEAN,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMPTZ(3),

    CONSTRAINT "ModelInvocation_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ResourceSnapshot" (
    "id" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "cpuPercent" DOUBLE PRECISION NOT NULL,
    "gpuPercent" DOUBLE PRECISION,
    "ramUsedBytes" BIGINT NOT NULL,
    "ramTotalBytes" BIGINT NOT NULL,
    "vramUsedBytes" BIGINT,
    "vramTotalBytes" BIGINT,
    "diskFreeBytes" BIGINT NOT NULL,
    "networkBytes" BIGINT NOT NULL,
    "temperatureC" DOUBLE PRECISION,
    "mode" "HealthMode" NOT NULL,
    "observedAt" TIMESTAMPTZ(3) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ResourceSnapshot_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AuditEvent" (
    "id" UUID NOT NULL,
    "sequence" BIGSERIAL NOT NULL,
    "userId" UUID,
    "sessionId" UUID,
    "deviceId" UUID,
    "taskId" UUID,
    "actionId" UUID,
    "type" VARCHAR(96) NOT NULL,
    "actor" VARCHAR(128) NOT NULL,
    "previousHash" CHAR(64),
    "eventHash" CHAR(64) NOT NULL,
    "payload" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AuditEvent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AgentEvent" (
    "id" UUID NOT NULL,
    "taskId" UUID NOT NULL,
    "executionId" UUID,
    "actionId" UUID,
    "agentId" UUID,
    "sequence" BIGINT NOT NULL,
    "type" VARCHAR(96) NOT NULL,
    "schemaVersion" VARCHAR(32) NOT NULL,
    "traceId" VARCHAR(64) NOT NULL,
    "payload" JSONB NOT NULL,
    "occurredAt" TIMESTAMPTZ(3) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "AgentEvent_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "OutboxMessage" (
    "id" UUID NOT NULL,
    "eventId" UUID NOT NULL,
    "destination" VARCHAR(128) NOT NULL,
    "status" "OutboxStatus" NOT NULL DEFAULT 'PENDING',
    "attempts" INTEGER NOT NULL DEFAULT 0,
    "maxAttempts" INTEGER NOT NULL DEFAULT 5,
    "availableAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "leaseOwner" VARCHAR(128),
    "leaseExpiresAt" TIMESTAMPTZ(3),
    "lastError" VARCHAR(1024),
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "deliveredAt" TIMESTAMPTZ(3),

    CONSTRAINT "OutboxMessage_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "EventConsumer" (
    "id" UUID NOT NULL,
    "eventId" UUID NOT NULL,
    "consumer" VARCHAR(128) NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "EventConsumer_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Checkpoint" (
    "id" UUID NOT NULL,
    "executionId" UUID NOT NULL,
    "stateSnapshotId" UUID NOT NULL,
    "resourceSnapshotId" UUID,
    "sequence" INTEGER NOT NULL,
    "worldHash" CHAR(64) NOT NULL,
    "state" JSONB NOT NULL,
    "permissionSnapshot" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Checkpoint_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Application" (
    "id" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "fingerprint" VARCHAR(255) NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "version" VARCHAR(128) NOT NULL,
    "metadata" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "Application_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ApplicationAdapter" (
    "id" UUID NOT NULL,
    "applicationId" UUID NOT NULL,
    "pluginId" UUID NOT NULL,
    "contract" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "ApplicationAdapter_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Plugin" (
    "id" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "version" VARCHAR(64) NOT NULL,
    "manifest" JSONB NOT NULL,
    "contentHash" CHAR(64) NOT NULL,
    "signerKeyId" VARCHAR(128) NOT NULL,
    "signature" TEXT NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Plugin_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "PluginInstallation" (
    "id" UUID NOT NULL,
    "pluginId" UUID NOT NULL,
    "deviceId" UUID NOT NULL,
    "status" "PluginStatus" NOT NULL DEFAULT 'STAGED',
    "grantedScope" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "PluginInstallation_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Tool" (
    "id" UUID NOT NULL,
    "pluginId" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "version" VARCHAR(64) NOT NULL,
    "inputSchema" JSONB NOT NULL,
    "outputSchema" JSONB NOT NULL,
    "capabilities" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Tool_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "SecretReference" (
    "id" UUID NOT NULL,
    "ownerId" UUID NOT NULL,
    "name" VARCHAR(128) NOT NULL,
    "vaultLocator" VARCHAR(512) NOT NULL,
    "allowedScope" JSONB NOT NULL,
    "createdAt" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(3) NOT NULL,
    "rotatedAt" TIMESTAMPTZ(3),

    CONSTRAINT "SecretReference_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "User_externalSubject_key" ON "User"("externalSubject");

-- CreateIndex
CREATE UNIQUE INDEX "Session_tokenDigest_key" ON "Session"("tokenDigest");

-- CreateIndex
CREATE INDEX "Session_userId_expiresAt_idx" ON "Session"("userId", "expiresAt");

-- CreateIndex
CREATE INDEX "Session_deviceId_idx" ON "Session"("deviceId");

-- CreateIndex
CREATE INDEX "Device_ownerId_lastSeenAt_idx" ON "Device"("ownerId", "lastSeenAt");

-- CreateIndex
CREATE UNIQUE INDEX "Agent_deviceId_role_key" ON "Agent"("deviceId", "role");

-- CreateIndex
CREATE INDEX "Task_ownerId_createdAt_idx" ON "Task"("ownerId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "Task_deviceId_status_priority_createdAt_idx" ON "Task"("deviceId", "status", "priority" DESC, "createdAt");

-- CreateIndex
CREATE INDEX "Task_sessionId_idx" ON "Task"("sessionId");

-- CreateIndex
CREATE UNIQUE INDEX "TaskVersion_taskId_version_key" ON "TaskVersion"("taskId", "version");

-- CreateIndex
CREATE UNIQUE INDEX "TaskNode_taskVersionId_key_key" ON "TaskNode"("taskVersionId", "key");

-- CreateIndex
CREATE UNIQUE INDEX "TaskNode_id_taskVersionId_key" ON "TaskNode"("id", "taskVersionId");

-- CreateIndex
CREATE INDEX "TaskDependency_fromNodeId_idx" ON "TaskDependency"("fromNodeId");

-- CreateIndex
CREATE INDEX "TaskDependency_toNodeId_idx" ON "TaskDependency"("toNodeId");

-- CreateIndex
CREATE UNIQUE INDEX "TaskDependency_taskVersionId_fromNodeId_toNodeId_key" ON "TaskDependency"("taskVersionId", "fromNodeId", "toNodeId");

-- CreateIndex
CREATE INDEX "Execution_taskId_createdAt_idx" ON "Execution"("taskId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "Execution_taskVersionId_idx" ON "Execution"("taskVersionId");

-- CreateIndex
CREATE INDEX "Execution_deviceId_status_leaseExpiresAt_idx" ON "Execution"("deviceId", "status", "leaseExpiresAt");

-- CreateIndex
CREATE INDEX "Execution_traceId_idx" ON "Execution"("traceId");

-- CreateIndex
CREATE INDEX "NodeExecution_nodeId_idx" ON "NodeExecution"("nodeId");

-- CreateIndex
CREATE INDEX "NodeExecution_executionId_status_idx" ON "NodeExecution"("executionId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "NodeExecution_executionId_nodeId_attempt_key" ON "NodeExecution"("executionId", "nodeId", "attempt");

-- CreateIndex
CREATE INDEX "Action_nodeExecutionId_createdAt_idx" ON "Action"("nodeExecutionId", "createdAt");

-- CreateIndex
CREATE INDEX "Action_policyDecisionId_idx" ON "Action"("policyDecisionId");

-- CreateIndex
CREATE INDEX "Action_executionId_status_idx" ON "Action"("executionId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "Action_executionId_idempotencyKey_key" ON "Action"("executionId", "idempotencyKey");

-- CreateIndex
CREATE UNIQUE INDEX "ActionResult_actionId_attempt_key" ON "ActionResult"("actionId", "attempt");

-- CreateIndex
CREATE INDEX "Observation_executionId_observedAt_idx" ON "Observation"("executionId", "observedAt" DESC);

-- CreateIndex
CREATE INDEX "Observation_actionId_idx" ON "Observation"("actionId");

-- CreateIndex
CREATE INDEX "Observation_worldStateId_stateVersion_idx" ON "Observation"("worldStateId", "stateVersion");

-- CreateIndex
CREATE INDEX "Observation_artifactId_idx" ON "Observation"("artifactId");

-- CreateIndex
CREATE INDEX "Verification_actionId_createdAt_idx" ON "Verification"("actionId", "createdAt");

-- CreateIndex
CREATE INDEX "Verification_observationId_idx" ON "Verification"("observationId");

-- CreateIndex
CREATE INDEX "WorldState_deviceId_observedAt_idx" ON "WorldState"("deviceId", "observedAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "WorldState_deviceId_bootId_version_key" ON "WorldState"("deviceId", "bootId", "version");

-- CreateIndex
CREATE INDEX "StateSnapshot_executionId_createdAt_idx" ON "StateSnapshot"("executionId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "StateSnapshot_worldStateId_idx" ON "StateSnapshot"("worldStateId");

-- CreateIndex
CREATE INDEX "StateSnapshot_artifactId_idx" ON "StateSnapshot"("artifactId");

-- CreateIndex
CREATE UNIQUE INDEX "EvidenceArtifact_objectKey_key" ON "EvidenceArtifact"("objectKey");

-- CreateIndex
CREATE INDEX "EvidenceArtifact_expiresAt_idx" ON "EvidenceArtifact"("expiresAt");

-- CreateIndex
CREATE INDEX "EvidenceArtifact_contentHash_idx" ON "EvidenceArtifact"("contentHash");

-- CreateIndex
CREATE UNIQUE INDEX "VisionSnapshot_observationId_key" ON "VisionSnapshot"("observationId");

-- CreateIndex
CREATE INDEX "VisionSnapshot_artifactId_idx" ON "VisionSnapshot"("artifactId");

-- CreateIndex
CREATE UNIQUE INDEX "Policy_name_version_key" ON "Policy"("name", "version");

-- CreateIndex
CREATE INDEX "Permission_userId_capabilityName_idx" ON "Permission"("userId", "capabilityName");

-- CreateIndex
CREATE INDEX "Permission_policyId_idx" ON "Permission"("policyId");

-- CreateIndex
CREATE INDEX "PolicyDecision_policyId_createdAt_idx" ON "PolicyDecision"("policyId", "createdAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "Capability_tokenDigest_key" ON "Capability"("tokenDigest");

-- CreateIndex
CREATE INDEX "Capability_taskId_expiresAt_idx" ON "Capability"("taskId", "expiresAt");

-- CreateIndex
CREATE INDEX "Capability_userId_idx" ON "Capability"("userId");

-- CreateIndex
CREATE INDEX "Capability_sessionId_idx" ON "Capability"("sessionId");

-- CreateIndex
CREATE INDEX "Capability_deviceId_emergencyEpoch_idx" ON "Capability"("deviceId", "emergencyEpoch");

-- CreateIndex
CREATE INDEX "Capability_policyId_idx" ON "Capability"("policyId");

-- CreateIndex
CREATE INDEX "Capability_policyDecisionId_idx" ON "Capability"("policyDecisionId");

-- CreateIndex
CREATE UNIQUE INDEX "CapabilityUse_nonceDigest_key" ON "CapabilityUse"("nonceDigest");

-- CreateIndex
CREATE INDEX "CapabilityUse_actionId_idx" ON "CapabilityUse"("actionId");

-- CreateIndex
CREATE UNIQUE INDEX "CapabilityUse_capabilityId_actionId_key" ON "CapabilityUse"("capabilityId", "actionId");

-- CreateIndex
CREATE INDEX "Approval_actionId_status_idx" ON "Approval"("actionId", "status");

-- CreateIndex
CREATE INDEX "Approval_reviewerId_status_expiresAt_idx" ON "Approval"("reviewerId", "status", "expiresAt");

-- CreateIndex
CREATE INDEX "Memory_ownerId_kind_updatedAt_idx" ON "Memory"("ownerId", "kind", "updatedAt" DESC);

-- CreateIndex
CREATE INDEX "Memory_taskId_idx" ON "Memory"("taskId");

-- CreateIndex
CREATE INDEX "Memory_artifactId_idx" ON "Memory"("artifactId");

-- CreateIndex
CREATE INDEX "Memory_expiresAt_idx" ON "Memory"("expiresAt");

-- CreateIndex
CREATE INDEX "MemoryEmbedding_modelId_idx" ON "MemoryEmbedding"("modelId");

-- CreateIndex
CREATE UNIQUE INDEX "MemoryEmbedding_memoryId_modelId_key" ON "MemoryEmbedding"("memoryId", "modelId");

-- CreateIndex
CREATE INDEX "Failure_executionId_createdAt_idx" ON "Failure"("executionId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "Failure_actionId_idx" ON "Failure"("actionId");

-- CreateIndex
CREATE INDEX "Recovery_status_deadlineAt_idx" ON "Recovery"("status", "deadlineAt");

-- CreateIndex
CREATE UNIQUE INDEX "Recovery_failureId_attempt_key" ON "Recovery"("failureId", "attempt");

-- CreateIndex
CREATE INDEX "Model_tier_enabled_idx" ON "Model"("tier", "enabled");

-- CreateIndex
CREATE UNIQUE INDEX "Model_provider_name_version_key" ON "Model"("provider", "name", "version");

-- CreateIndex
CREATE INDEX "ModelInvocation_executionId_createdAt_idx" ON "ModelInvocation"("executionId", "createdAt");

-- CreateIndex
CREATE INDEX "ModelInvocation_modelId_createdAt_idx" ON "ModelInvocation"("modelId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "ModelInvocation_agentId_idx" ON "ModelInvocation"("agentId");

-- CreateIndex
CREATE INDEX "ResourceSnapshot_deviceId_observedAt_idx" ON "ResourceSnapshot"("deviceId", "observedAt" DESC);

-- CreateIndex
CREATE UNIQUE INDEX "AuditEvent_sequence_key" ON "AuditEvent"("sequence");

-- CreateIndex
CREATE INDEX "AuditEvent_taskId_sequence_idx" ON "AuditEvent"("taskId", "sequence");

-- CreateIndex
CREATE INDEX "AuditEvent_userId_createdAt_idx" ON "AuditEvent"("userId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "AuditEvent_sessionId_idx" ON "AuditEvent"("sessionId");

-- CreateIndex
CREATE INDEX "AuditEvent_deviceId_createdAt_idx" ON "AuditEvent"("deviceId", "createdAt" DESC);

-- CreateIndex
CREATE INDEX "AuditEvent_actionId_idx" ON "AuditEvent"("actionId");

-- CreateIndex
CREATE INDEX "AgentEvent_executionId_createdAt_idx" ON "AgentEvent"("executionId", "createdAt");

-- CreateIndex
CREATE INDEX "AgentEvent_actionId_idx" ON "AgentEvent"("actionId");

-- CreateIndex
CREATE INDEX "AgentEvent_agentId_idx" ON "AgentEvent"("agentId");

-- CreateIndex
CREATE INDEX "AgentEvent_createdAt_idx" ON "AgentEvent"("createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "AgentEvent_taskId_sequence_key" ON "AgentEvent"("taskId", "sequence");

-- CreateIndex
CREATE INDEX "OutboxMessage_status_availableAt_leaseExpiresAt_idx" ON "OutboxMessage"("status", "availableAt", "leaseExpiresAt");

-- CreateIndex
CREATE UNIQUE INDEX "OutboxMessage_eventId_destination_key" ON "OutboxMessage"("eventId", "destination");

-- CreateIndex
CREATE UNIQUE INDEX "EventConsumer_eventId_consumer_key" ON "EventConsumer"("eventId", "consumer");

-- CreateIndex
CREATE INDEX "Checkpoint_stateSnapshotId_idx" ON "Checkpoint"("stateSnapshotId");

-- CreateIndex
CREATE INDEX "Checkpoint_resourceSnapshotId_idx" ON "Checkpoint"("resourceSnapshotId");

-- CreateIndex
CREATE UNIQUE INDEX "Checkpoint_executionId_sequence_key" ON "Checkpoint"("executionId", "sequence");

-- CreateIndex
CREATE UNIQUE INDEX "Application_deviceId_fingerprint_key" ON "Application"("deviceId", "fingerprint");

-- CreateIndex
CREATE INDEX "ApplicationAdapter_pluginId_idx" ON "ApplicationAdapter"("pluginId");

-- CreateIndex
CREATE UNIQUE INDEX "ApplicationAdapter_applicationId_pluginId_key" ON "ApplicationAdapter"("applicationId", "pluginId");

-- CreateIndex
CREATE UNIQUE INDEX "Plugin_name_version_key" ON "Plugin"("name", "version");

-- CreateIndex
CREATE INDEX "PluginInstallation_deviceId_status_idx" ON "PluginInstallation"("deviceId", "status");

-- CreateIndex
CREATE UNIQUE INDEX "PluginInstallation_pluginId_deviceId_key" ON "PluginInstallation"("pluginId", "deviceId");

-- CreateIndex
CREATE UNIQUE INDEX "Tool_pluginId_name_version_key" ON "Tool"("pluginId", "name", "version");

-- CreateIndex
CREATE UNIQUE INDEX "SecretReference_ownerId_name_key" ON "SecretReference"("ownerId", "name");

-- AddForeignKey
ALTER TABLE "Session" ADD CONSTRAINT "Session_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Session" ADD CONSTRAINT "Session_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Device" ADD CONSTRAINT "Device_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Agent" ADD CONSTRAINT "Agent_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Task" ADD CONSTRAINT "Task_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Task" ADD CONSTRAINT "Task_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Task" ADD CONSTRAINT "Task_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES "Session"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "TaskVersion" ADD CONSTRAINT "TaskVersion_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "TaskNode" ADD CONSTRAINT "TaskNode_taskVersionId_fkey" FOREIGN KEY ("taskVersionId") REFERENCES "TaskVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "TaskDependency" ADD CONSTRAINT "TaskDependency_taskVersionId_fkey" FOREIGN KEY ("taskVersionId") REFERENCES "TaskVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "TaskDependency" ADD CONSTRAINT "TaskDependency_fromNodeId_fkey" FOREIGN KEY ("fromNodeId") REFERENCES "TaskNode"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "TaskDependency" ADD CONSTRAINT "TaskDependency_toNodeId_fkey" FOREIGN KEY ("toNodeId") REFERENCES "TaskNode"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Execution" ADD CONSTRAINT "Execution_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Execution" ADD CONSTRAINT "Execution_taskVersionId_fkey" FOREIGN KEY ("taskVersionId") REFERENCES "TaskVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Execution" ADD CONSTRAINT "Execution_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NodeExecution" ADD CONSTRAINT "NodeExecution_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "NodeExecution" ADD CONSTRAINT "NodeExecution_nodeId_fkey" FOREIGN KEY ("nodeId") REFERENCES "TaskNode"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Action" ADD CONSTRAINT "Action_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Action" ADD CONSTRAINT "Action_nodeExecutionId_fkey" FOREIGN KEY ("nodeExecutionId") REFERENCES "NodeExecution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Action" ADD CONSTRAINT "Action_policyDecisionId_fkey" FOREIGN KEY ("policyDecisionId") REFERENCES "PolicyDecision"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ActionResult" ADD CONSTRAINT "ActionResult_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Observation" ADD CONSTRAINT "Observation_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Observation" ADD CONSTRAINT "Observation_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Observation" ADD CONSTRAINT "Observation_worldStateId_fkey" FOREIGN KEY ("worldStateId") REFERENCES "WorldState"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Observation" ADD CONSTRAINT "Observation_artifactId_fkey" FOREIGN KEY ("artifactId") REFERENCES "EvidenceArtifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Verification" ADD CONSTRAINT "Verification_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Verification" ADD CONSTRAINT "Verification_observationId_fkey" FOREIGN KEY ("observationId") REFERENCES "Observation"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "WorldState" ADD CONSTRAINT "WorldState_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StateSnapshot" ADD CONSTRAINT "StateSnapshot_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StateSnapshot" ADD CONSTRAINT "StateSnapshot_worldStateId_fkey" FOREIGN KEY ("worldStateId") REFERENCES "WorldState"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "StateSnapshot" ADD CONSTRAINT "StateSnapshot_artifactId_fkey" FOREIGN KEY ("artifactId") REFERENCES "EvidenceArtifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "VisionSnapshot" ADD CONSTRAINT "VisionSnapshot_observationId_fkey" FOREIGN KEY ("observationId") REFERENCES "Observation"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "VisionSnapshot" ADD CONSTRAINT "VisionSnapshot_artifactId_fkey" FOREIGN KEY ("artifactId") REFERENCES "EvidenceArtifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Permission" ADD CONSTRAINT "Permission_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Permission" ADD CONSTRAINT "Permission_policyId_fkey" FOREIGN KEY ("policyId") REFERENCES "Policy"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "PolicyDecision" ADD CONSTRAINT "PolicyDecision_policyId_fkey" FOREIGN KEY ("policyId") REFERENCES "Policy"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES "Session"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_policyId_fkey" FOREIGN KEY ("policyId") REFERENCES "Policy"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Capability" ADD CONSTRAINT "Capability_policyDecisionId_fkey" FOREIGN KEY ("policyDecisionId") REFERENCES "PolicyDecision"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CapabilityUse" ADD CONSTRAINT "CapabilityUse_capabilityId_fkey" FOREIGN KEY ("capabilityId") REFERENCES "Capability"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "CapabilityUse" ADD CONSTRAINT "CapabilityUse_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Approval" ADD CONSTRAINT "Approval_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Approval" ADD CONSTRAINT "Approval_reviewerId_fkey" FOREIGN KEY ("reviewerId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Memory" ADD CONSTRAINT "Memory_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Memory" ADD CONSTRAINT "Memory_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Memory" ADD CONSTRAINT "Memory_artifactId_fkey" FOREIGN KEY ("artifactId") REFERENCES "EvidenceArtifact"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "MemoryEmbedding" ADD CONSTRAINT "MemoryEmbedding_memoryId_fkey" FOREIGN KEY ("memoryId") REFERENCES "Memory"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "MemoryEmbedding" ADD CONSTRAINT "MemoryEmbedding_modelId_fkey" FOREIGN KEY ("modelId") REFERENCES "Model"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Failure" ADD CONSTRAINT "Failure_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Failure" ADD CONSTRAINT "Failure_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Recovery" ADD CONSTRAINT "Recovery_failureId_fkey" FOREIGN KEY ("failureId") REFERENCES "Failure"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ModelInvocation" ADD CONSTRAINT "ModelInvocation_modelId_fkey" FOREIGN KEY ("modelId") REFERENCES "Model"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ModelInvocation" ADD CONSTRAINT "ModelInvocation_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ModelInvocation" ADD CONSTRAINT "ModelInvocation_agentId_fkey" FOREIGN KEY ("agentId") REFERENCES "Agent"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ResourceSnapshot" ADD CONSTRAINT "ResourceSnapshot_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AuditEvent" ADD CONSTRAINT "AuditEvent_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AuditEvent" ADD CONSTRAINT "AuditEvent_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES "Session"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AuditEvent" ADD CONSTRAINT "AuditEvent_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AuditEvent" ADD CONSTRAINT "AuditEvent_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AuditEvent" ADD CONSTRAINT "AuditEvent_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AgentEvent" ADD CONSTRAINT "AgentEvent_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AgentEvent" ADD CONSTRAINT "AgentEvent_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AgentEvent" ADD CONSTRAINT "AgentEvent_actionId_fkey" FOREIGN KEY ("actionId") REFERENCES "Action"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "AgentEvent" ADD CONSTRAINT "AgentEvent_agentId_fkey" FOREIGN KEY ("agentId") REFERENCES "Agent"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "OutboxMessage" ADD CONSTRAINT "OutboxMessage_eventId_fkey" FOREIGN KEY ("eventId") REFERENCES "AgentEvent"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "EventConsumer" ADD CONSTRAINT "EventConsumer_eventId_fkey" FOREIGN KEY ("eventId") REFERENCES "AgentEvent"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Checkpoint" ADD CONSTRAINT "Checkpoint_executionId_fkey" FOREIGN KEY ("executionId") REFERENCES "Execution"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Checkpoint" ADD CONSTRAINT "Checkpoint_stateSnapshotId_fkey" FOREIGN KEY ("stateSnapshotId") REFERENCES "StateSnapshot"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Checkpoint" ADD CONSTRAINT "Checkpoint_resourceSnapshotId_fkey" FOREIGN KEY ("resourceSnapshotId") REFERENCES "ResourceSnapshot"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Application" ADD CONSTRAINT "Application_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ApplicationAdapter" ADD CONSTRAINT "ApplicationAdapter_applicationId_fkey" FOREIGN KEY ("applicationId") REFERENCES "Application"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ApplicationAdapter" ADD CONSTRAINT "ApplicationAdapter_pluginId_fkey" FOREIGN KEY ("pluginId") REFERENCES "Plugin"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "PluginInstallation" ADD CONSTRAINT "PluginInstallation_pluginId_fkey" FOREIGN KEY ("pluginId") REFERENCES "Plugin"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "PluginInstallation" ADD CONSTRAINT "PluginInstallation_deviceId_fkey" FOREIGN KEY ("deviceId") REFERENCES "Device"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Tool" ADD CONSTRAINT "Tool_pluginId_fkey" FOREIGN KEY ("pluginId") REFERENCES "Plugin"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "SecretReference" ADD CONSTRAINT "SecretReference_ownerId_fkey" FOREIGN KEY ("ownerId") REFERENCES "User"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
