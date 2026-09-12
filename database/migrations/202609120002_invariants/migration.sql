-- This migration is part of the required schema, not an optional checklist.
-- Owners/superusers can bypass triggers; production runtime must not own tables.
CREATE FUNCTION jarvis_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
END;
$$;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'AuditEvent', 'AgentEvent', 'EventConsumer', 'CapabilityUse', 'ActionResult',
    'Observation', 'Verification', 'WorldState', 'StateSnapshot', 'VisionSnapshot',
    'Checkpoint', 'TaskVersion', 'TaskNode', 'TaskDependency', 'PolicyDecision',
    'Plugin', 'Tool'
  ] LOOP
    EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION jarvis_reject_mutation()', table_name);
    EXECUTE format('CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION jarvis_reject_mutation()', table_name);
  END LOOP;
END;
$$;

ALTER TABLE "Task" ADD CONSTRAINT task_revision_nonnegative CHECK (revision >= 0);
ALTER TABLE "TaskVersion" ADD CONSTRAINT plan_version_positive CHECK (version > 0);
ALTER TABLE "TaskNode" ADD CONSTRAINT node_budgets CHECK ("maxAttempts" BETWEEN 1 AND 10 AND "timeoutMs" BETWEEN 1 AND 3600000);
ALTER TABLE "TaskDependency" ADD CONSTRAINT dependency_not_self CHECK ("fromNodeId" <> "toNodeId");
ALTER TABLE "TaskDependency" ADD CONSTRAINT dependency_from_same_plan FOREIGN KEY ("fromNodeId", "taskVersionId") REFERENCES "TaskNode"(id, "taskVersionId");
ALTER TABLE "TaskDependency" ADD CONSTRAINT dependency_to_same_plan FOREIGN KEY ("toNodeId", "taskVersionId") REFERENCES "TaskNode"(id, "taskVersionId");
ALTER TABLE "NodeExecution" ADD CONSTRAINT node_attempt_positive CHECK (attempt BETWEEN 1 AND 10);
ALTER TABLE "Action" ADD CONSTRAINT action_risk_timeout CHECK ("riskLevel" BETWEEN 0 AND 5 AND "timeoutMs" BETWEEN 1 AND 3600000);
ALTER TABLE "ActionResult" ADD CONSTRAINT result_budgets CHECK (attempt > 0 AND "durationMs" >= 0);
ALTER TABLE "Observation" ADD CONSTRAINT observation_bounds CHECK (confidence BETWEEN 0 AND 1 AND "stateVersion" >= 0 AND "observedMonotonicNs" >= 0 AND "expiresAt" >= "observedAt");
ALTER TABLE "Verification" ADD CONSTRAINT verification_confidence CHECK (confidence BETWEEN 0 AND 1);
ALTER TABLE "EvidenceArtifact" ADD CONSTRAINT artifact_size CHECK ("byteLength" >= 0);
ALTER TABLE "VisionSnapshot" ADD CONSTRAINT vision_dimensions CHECK (width BETWEEN 1 AND 32768 AND height BETWEEN 1 AND 32768 AND scale > 0 AND scale <= 8);
ALTER TABLE "Capability" ADD CONSTRAINT capability_lifetime CHECK ("expiresAt" > "createdAt" AND "maxUses" BETWEEN 1 AND 100 AND "emergencyEpoch" >= 0);
ALTER TABLE "Session" ADD CONSTRAINT session_lifetime CHECK ("expiresAt" > "createdAt");
ALTER TABLE "PolicyDecision" ADD CONSTRAINT decision_lifetime CHECK ("expiresAt" > "createdAt");
ALTER TABLE "Approval" ADD CONSTRAINT approval_lifetime CHECK ("expiresAt" > "createdAt");
ALTER TABLE "Approval" ADD CONSTRAINT approval_identity CHECK (status <> 'APPROVED' OR ("reviewerId" IS NOT NULL AND "decidedAt" IS NOT NULL));
ALTER TABLE "Memory" ADD CONSTRAINT memory_scores CHECK (confidence BETWEEN 0 AND 1 AND importance BETWEEN 0 AND 1 AND freshness BETWEEN 0 AND 1);
ALTER TABLE "MemoryEmbedding" ADD CONSTRAINT embedding_dimensions CHECK (dimensions BETWEEN 1 AND 8192 AND cardinality("values") = dimensions);
ALTER TABLE "Recovery" ADD CONSTRAINT recovery_attempts CHECK (attempt BETWEEN 1 AND 10 AND "deadlineAt" > "createdAt");
ALTER TABLE "ModelInvocation" ADD CONSTRAINT invocation_costs CHECK ("inputTokens" >= 0 AND "outputTokens" >= 0 AND ("costUsd" IS NULL OR "costUsd" >= 0) AND ("latencyMs" IS NULL OR "latencyMs" >= 0));
ALTER TABLE "ResourceSnapshot" ADD CONSTRAINT resource_ranges CHECK ("cpuPercent" BETWEEN 0 AND 100 AND ("gpuPercent" IS NULL OR "gpuPercent" BETWEEN 0 AND 100) AND "ramUsedBytes" >= 0 AND "ramTotalBytes" > 0 AND "diskFreeBytes" >= 0 AND "networkBytes" >= 0);
ALTER TABLE "OutboxMessage" ADD CONSTRAINT outbox_attempts CHECK (attempts >= 0 AND "maxAttempts" BETWEEN 1 AND 20 AND attempts <= "maxAttempts");
ALTER TABLE "OutboxMessage" ADD CONSTRAINT outbox_lease CHECK (status <> 'LEASED' OR ("leaseOwner" IS NOT NULL AND "leaseExpiresAt" IS NOT NULL));
ALTER TABLE "AgentEvent" ADD CONSTRAINT event_sequence_positive CHECK (sequence > 0);

-- Serialize DAG edge inserts on their parent plan to prevent concurrent cycles.
CREATE FUNCTION jarvis_validate_edge() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM 1 FROM "TaskVersion" WHERE id = NEW."taskVersionId" FOR UPDATE;
  IF EXISTS (
    WITH RECURSIVE reachable(id) AS (
      SELECT NEW."toNodeId"
      UNION
      SELECT d."toNodeId" FROM "TaskDependency" d JOIN reachable r ON d."fromNodeId" = r.id
      WHERE d."taskVersionId" = NEW."taskVersionId"
    ) SELECT 1 FROM reachable WHERE id = NEW."fromNodeId"
  ) THEN RAISE EXCEPTION 'Task dependency would introduce a cycle'; END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER dependency_acyclic BEFORE INSERT ON "TaskDependency" FOR EACH ROW EXECUTE FUNCTION jarvis_validate_edge();

CREATE FUNCTION jarvis_check_execution_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'Execution' THEN
    IF NOT EXISTS (SELECT 1 FROM "TaskVersion" p JOIN "Task" t ON t.id = p."taskId"
      WHERE p.id = NEW."taskVersionId" AND t.id = NEW."taskId" AND t."deviceId" = NEW."deviceId")
      THEN RAISE EXCEPTION 'Execution task, plan, and device mismatch'; END IF;
  ELSIF TG_TABLE_NAME = 'NodeExecution' THEN
    IF NOT EXISTS (SELECT 1 FROM "Execution" e JOIN "TaskNode" n ON n."taskVersionId" = e."taskVersionId"
      WHERE e.id = NEW."executionId" AND n.id = NEW."nodeId")
      THEN RAISE EXCEPTION 'Node execution plan mismatch'; END IF;
  ELSIF TG_TABLE_NAME = 'Action' THEN
    IF NOT EXISTS (SELECT 1 FROM "NodeExecution" n WHERE n.id = NEW."nodeExecutionId" AND n."executionId" = NEW."executionId")
      THEN RAISE EXCEPTION 'Action execution mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM "PolicyDecision" p WHERE p.id = NEW."policyDecisionId" AND p."actionHash" = NEW."contentHash")
      THEN RAISE EXCEPTION 'Action policy digest mismatch'; END IF;
  ELSIF TG_TABLE_NAME = 'Verification' THEN
    IF NOT EXISTS (SELECT 1 FROM "Observation" o JOIN "Action" a ON a."executionId" = o."executionId"
      WHERE o.id = NEW."observationId" AND a.id = NEW."actionId" AND o."actionId" = a.id)
      THEN RAISE EXCEPTION 'Verification evidence action mismatch'; END IF;
  ELSIF TG_TABLE_NAME = 'Checkpoint' THEN
    IF NOT EXISTS (SELECT 1 FROM "StateSnapshot" s WHERE s.id = NEW."stateSnapshotId" AND s."executionId" = NEW."executionId")
      THEN RAISE EXCEPTION 'Checkpoint execution mismatch'; END IF;
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER execution_links BEFORE INSERT OR UPDATE ON "Execution" FOR EACH ROW EXECUTE FUNCTION jarvis_check_execution_links();
CREATE TRIGGER node_execution_links BEFORE INSERT OR UPDATE ON "NodeExecution" FOR EACH ROW EXECUTE FUNCTION jarvis_check_execution_links();
CREATE TRIGGER action_links BEFORE INSERT OR UPDATE ON "Action" FOR EACH ROW EXECUTE FUNCTION jarvis_check_execution_links();
CREATE TRIGGER verification_links BEFORE INSERT ON "Verification" FOR EACH ROW EXECUTE FUNCTION jarvis_check_execution_links();
CREATE TRIGGER checkpoint_links BEFORE INSERT ON "Checkpoint" FOR EACH ROW EXECUTE FUNCTION jarvis_check_execution_links();

-- Consumer state can change; the event and destination cannot be substituted.
CREATE FUNCTION jarvis_outbox_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.id <> OLD.id OR NEW."eventId" <> OLD."eventId" OR NEW.destination <> OLD.destination OR NEW."createdAt" <> OLD."createdAt" THEN
    RAISE EXCEPTION 'Outbox identity is immutable';
  END IF;
  IF OLD.status IN ('DELIVERED', 'DEAD_LETTER') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'Terminal outbox record is immutable; create an audited redelivery';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER outbox_identity BEFORE UPDATE ON "OutboxMessage" FOR EACH ROW EXECUTE FUNCTION jarvis_outbox_identity();

-- Consume inside the same transaction as action admission. Daemon still performs
-- its own authorization: database consistency is not an execution boundary.
CREATE FUNCTION jarvis_check_capability_use() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cap "Capability"%ROWTYPE;
BEGIN
  SELECT * INTO STRICT cap FROM "Capability" WHERE id = NEW."capabilityId" FOR UPDATE;
  IF cap."revokedAt" IS NOT NULL OR cap."expiresAt" <= clock_timestamp() THEN
    RAISE EXCEPTION 'Capability expired or revoked';
  END IF;
  IF (SELECT count(*) FROM "CapabilityUse" WHERE "capabilityId" = cap.id) >= cap."maxUses" THEN
    RAISE EXCEPTION 'Capability use budget exhausted';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM "Action" a JOIN "Execution" e ON e.id = a."executionId"
    JOIN "Device" d ON d.id = e."deviceId"
    JOIN "Session" s ON s.id = cap."sessionId"
    WHERE a.id = NEW."actionId" AND a."contentHash" = cap."actionHash"
      AND e."taskId" = cap."taskId" AND e."deviceId" = cap."deviceId"
      AND d."emergencyEpoch" = cap."emergencyEpoch" AND s."userId" = cap."userId"
      AND s."revokedAt" IS NULL AND s."expiresAt" > clock_timestamp()) THEN
    RAISE EXCEPTION 'Capability task, identity, action, or emergency epoch mismatch';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER capability_use_valid BEFORE INSERT ON "CapabilityUse" FOR EACH ROW EXECUTE FUNCTION jarvis_check_capability_use();

CREATE INDEX outbox_ready_partial ON "OutboxMessage" ("availableAt", "createdAt") WHERE status = 'PENDING';
CREATE INDEX execution_active_partial ON "Execution" ("deviceId", "leaseExpiresAt") WHERE status IN ('QUEUED', 'RUNNING', 'VERIFYING');
CREATE INDEX resource_time_brin ON "ResourceSnapshot" USING BRIN ("observedAt");
CREATE INDEX audit_time_brin ON "AuditEvent" USING BRIN ("createdAt");
CREATE INDEX memory_content_gin ON "Memory" USING GIN (content jsonb_path_ops);
