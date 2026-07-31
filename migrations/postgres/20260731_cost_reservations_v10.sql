-- Transactional run-cost authority for the commercial control plane.
-- Monthly metering is durable even when a concurrency lease expires. Client
-- timestamps and REAL projections never participate in authoritative checks.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE workspace_entitlements
ADD COLUMN IF NOT EXISTS max_concurrent_runs INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid='workspace_entitlements'::regclass
      AND conname='workspace_entitlements_max_concurrent_runs_v10_check'
  ) THEN
    ALTER TABLE workspace_entitlements
    ADD CONSTRAINT workspace_entitlements_max_concurrent_runs_v10_check
    CHECK(max_concurrent_runs >= 0);
  END IF;
END
$$;

-- runs.cost_usd remains a compatibility projection, but converting it to
-- NUMERIC prevents additional float32 loss. Authority lives in the ledger.
DO $$
DECLARE
  v_data_type TEXT;
BEGIN
  SELECT data_type
  INTO v_data_type
  FROM information_schema.columns
  WHERE table_schema=current_schema()
    AND table_name='runs'
    AND column_name='cost_usd';

  IF v_data_type IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE='42703',
      MESSAGE='runs_cost_usd_missing';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM runs
    WHERE CASE
      WHEN cost_usd IS NULL THEN FALSE
      WHEN lower(cost_usd::TEXT) IN ('nan','infinity','-infinity') THEN TRUE
      ELSE abs(cost_usd::NUMERIC) > 999999999999.999999
    END
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE='22003',
      MESSAGE='runs_cost_usd_not_numeric_18_6';
  END IF;

  IF v_data_type<>'numeric' OR EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema=current_schema()
      AND table_name='runs'
      AND column_name='cost_usd'
      AND (
        numeric_precision IS DISTINCT FROM 18
        OR numeric_scale IS DISTINCT FROM 6
      )
  ) THEN
    ALTER TABLE runs
    ALTER COLUMN cost_usd TYPE NUMERIC(18,6)
    USING round(COALESCE(cost_usd,0)::NUMERIC,6);
  END IF;
END
$$;

UPDATE runs
SET cost_usd=0.000000
WHERE cost_usd IS NULL;

ALTER TABLE runs
ALTER COLUMN cost_usd SET DEFAULT 0.000000,
ALTER COLUMN cost_usd SET NOT NULL;

-- A run has one explicit billing class. Existing execution runs become
-- historical_execution exactly once. Agent enrollment approval runs are the
-- sole non-billable management class and require their canonical graph.
ALTER TABLE runs
ADD COLUMN IF NOT EXISTS billing_class TEXT;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM runs run
    JOIN agent_gateway_enrollment_requests request
      ON request.run_id=run.run_id
    WHERE run.billing_class IS NULL
      AND (
        request.workspace_id<>run.workspace_id
        OR request.task_id<>run.task_id
        OR request.agent_id<>run.agent_id
        OR run.model_provider IS DISTINCT FROM 'agent-gateway'
        OR run.model_name IS DISTINCT FROM 'enrollment-request'
      )
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE='23514',
      MESSAGE='nonbillable_management_run_binding_invalid';
  END IF;

  UPDATE runs run
  SET billing_class='nonbillable_management'
  WHERE run.billing_class IS NULL
    AND EXISTS (
      SELECT 1
      FROM agent_gateway_enrollment_requests request
      WHERE request.run_id=run.run_id
        AND request.workspace_id=run.workspace_id
        AND request.task_id=run.task_id
        AND request.agent_id=run.agent_id
        AND run.model_provider='agent-gateway'
        AND run.model_name='enrollment-request'
    );

  UPDATE runs
  SET billing_class='historical_execution'
  WHERE billing_class IS NULL;
END
$$;

ALTER TABLE runs
ALTER COLUMN billing_class SET DEFAULT 'metered_execution',
ALTER COLUMN billing_class SET NOT NULL;

ALTER TABLE runs
DROP CONSTRAINT IF EXISTS runs_billing_class_v10_check;
ALTER TABLE runs
ADD CONSTRAINT runs_billing_class_v10_check CHECK(
  billing_class IN (
    'metered_execution',
    'historical_execution',
    'nonbillable_management'
  )
);

CREATE TABLE IF NOT EXISTS run_cost_reservations (
    reservation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    billing_class TEXT NOT NULL DEFAULT 'metered_execution',
    billing_month_utc DATE NOT NULL,
    state TEXT NOT NULL,
    estimated_cost_usd NUMERIC(18,6) NOT NULL,
    observed_cost_usd NUMERIC(18,6) NOT NULL DEFAULT 0.000000,
    settled_cost_usd NUMERIC(18,6),
    idempotency_key_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    settlement_idempotency_key_hash TEXT,
    settlement_request_hash TEXT,
    release_idempotency_key_hash TEXT,
    release_request_hash TEXT,
    release_reason TEXT,
    reserved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    settled_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT run_cost_reservations_pkey PRIMARY KEY(reservation_id),
    CONSTRAINT run_cost_reservations_workspace_run_v10_unique
      UNIQUE(workspace_id,run_id),
    CONSTRAINT run_cost_reservations_workspace_idempotency_v10_unique
      UNIQUE(workspace_id,idempotency_key_hash)
);

DROP TRIGGER IF EXISTS run_cost_reservations_guard_v10
ON run_cost_reservations;

ALTER TABLE run_cost_reservations
ADD COLUMN IF NOT EXISTS billing_class TEXT,
ADD COLUMN IF NOT EXISTS observed_cost_usd NUMERIC(18,6);

UPDATE run_cost_reservations
SET
  billing_class=COALESCE(billing_class,'metered_execution'),
  observed_cost_usd=COALESCE(
    observed_cost_usd,
    settled_cost_usd,
    0.000000
  )
WHERE billing_class IS NULL
  OR observed_cost_usd IS NULL;

ALTER TABLE run_cost_reservations
ALTER COLUMN billing_class SET DEFAULT 'metered_execution',
ALTER COLUMN billing_class SET NOT NULL,
ALTER COLUMN observed_cost_usd SET DEFAULT 0.000000,
ALTER COLUMN observed_cost_usd SET NOT NULL;

ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_workspace_id_fkey;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_workspace_id_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_run_id_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_billing_class_v10_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_state_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_estimated_cost_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_observed_cost_v10_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_settled_cost_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_idempotency_hash_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_request_hash_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_settlement_hashes_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_release_hashes_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_release_reason_v10_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_billing_month_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_expiry_order_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_update_order_check;
ALTER TABLE run_cost_reservations
DROP CONSTRAINT IF EXISTS run_cost_reservations_state_shape_check;

ALTER TABLE run_cost_reservations
ADD CONSTRAINT run_cost_reservations_workspace_id_check
  CHECK(NULLIF(btrim(workspace_id),'') IS NOT NULL),
ADD CONSTRAINT run_cost_reservations_run_id_check
  CHECK(NULLIF(btrim(run_id),'') IS NOT NULL),
ADD CONSTRAINT run_cost_reservations_billing_class_v10_check CHECK(
  billing_class IN ('metered_execution','historical_execution')
),
ADD CONSTRAINT run_cost_reservations_state_check
  CHECK(state IN ('reserved','settled','released','expired')),
ADD CONSTRAINT run_cost_reservations_estimated_cost_check CHECK(
  estimated_cost_usd > 0
  AND lower(estimated_cost_usd::TEXT)
    NOT IN ('nan','infinity','-infinity')
),
ADD CONSTRAINT run_cost_reservations_observed_cost_v10_check CHECK(
  observed_cost_usd >= 0
  AND observed_cost_usd <= estimated_cost_usd
  AND lower(observed_cost_usd::TEXT)
    NOT IN ('nan','infinity','-infinity')
),
ADD CONSTRAINT run_cost_reservations_settled_cost_check CHECK(
  settled_cost_usd IS NULL
  OR (
    settled_cost_usd >= observed_cost_usd
    AND settled_cost_usd <= estimated_cost_usd
    AND lower(settled_cost_usd::TEXT)
      NOT IN ('nan','infinity','-infinity')
  )
),
ADD CONSTRAINT run_cost_reservations_idempotency_hash_check
  CHECK(idempotency_key_hash ~ '^[a-f0-9]{64}$'),
ADD CONSTRAINT run_cost_reservations_request_hash_check
  CHECK(request_hash ~ '^[a-f0-9]{64}$'),
ADD CONSTRAINT run_cost_reservations_settlement_hashes_check CHECK(
  (
    settlement_idempotency_key_hash IS NULL
    AND settlement_request_hash IS NULL
  )
  OR (
    settlement_idempotency_key_hash ~ '^[a-f0-9]{64}$'
    AND settlement_request_hash ~ '^[a-f0-9]{64}$'
  )
),
ADD CONSTRAINT run_cost_reservations_release_hashes_check CHECK(
  (
    release_idempotency_key_hash IS NULL
    AND release_request_hash IS NULL
  )
  OR (
    release_idempotency_key_hash ~ '^[a-f0-9]{64}$'
    AND release_request_hash ~ '^[a-f0-9]{64}$'
  )
),
ADD CONSTRAINT run_cost_reservations_release_reason_v10_check CHECK(
  release_reason IS NULL
  OR release_reason IN (
    'run_insert_failed',
    'run_cancelled_before_execution',
    'run_rejected_before_execution'
  )
),
ADD CONSTRAINT run_cost_reservations_billing_month_check CHECK(
  billing_month_utc
    = date_trunc('month',billing_month_utc::TIMESTAMP)::DATE
),
ADD CONSTRAINT run_cost_reservations_expiry_order_check
  CHECK(expires_at > reserved_at),
ADD CONSTRAINT run_cost_reservations_update_order_check
  CHECK(updated_at >= reserved_at),
ADD CONSTRAINT run_cost_reservations_state_shape_check CHECK(
  (
    state='reserved'
    AND settled_cost_usd IS NULL
    AND settlement_idempotency_key_hash IS NULL
    AND settlement_request_hash IS NULL
    AND release_idempotency_key_hash IS NULL
    AND release_request_hash IS NULL
    AND release_reason IS NULL
    AND settled_at IS NULL
    AND released_at IS NULL
    AND expired_at IS NULL
  )
  OR (
    state='settled'
    AND settled_cost_usd IS NOT NULL
    AND settlement_idempotency_key_hash IS NOT NULL
    AND settlement_request_hash IS NOT NULL
    AND release_idempotency_key_hash IS NULL
    AND release_request_hash IS NULL
    AND release_reason IS NULL
    AND settled_at IS NOT NULL
    AND released_at IS NULL
  )
  OR (
    state='released'
    AND settled_cost_usd IS NULL
    AND settlement_idempotency_key_hash IS NULL
    AND settlement_request_hash IS NULL
    AND release_idempotency_key_hash IS NOT NULL
    AND release_request_hash IS NOT NULL
    AND release_reason IS NOT NULL
    AND settled_at IS NULL
    AND released_at IS NOT NULL
  )
  OR (
    state='expired'
    AND settled_cost_usd IS NULL
    AND settlement_idempotency_key_hash IS NULL
    AND settlement_request_hash IS NULL
    AND release_idempotency_key_hash IS NULL
    AND release_request_hash IS NULL
    AND release_reason IS NULL
    AND settled_at IS NULL
    AND released_at IS NULL
    AND expired_at IS NOT NULL
  )
);

-- Backfill every pre-migration execution run. Non-billable enrollment runs were
-- explicitly classified above and are intentionally absent from this ledger.
WITH migration_clock AS (
  SELECT clock_timestamp() AS now
)
INSERT INTO run_cost_reservations(
  reservation_id,workspace_id,run_id,billing_class,billing_month_utc,state,
  estimated_cost_usd,observed_cost_usd,settled_cost_usd,
  idempotency_key_hash,request_hash,
  settlement_idempotency_key_hash,settlement_request_hash,
  release_idempotency_key_hash,release_request_hash,release_reason,
  reserved_at,expires_at,settled_at,released_at,expired_at,updated_at
)
SELECT
  'rsv_hist_' || substr(
    encode(sha256(convert_to(
      'historical-reservation:' || run.workspace_id || ':' || run.run_id,
      'UTF8'
    )),'hex'),
    1,
    32
  ),
  run.workspace_id,
  run.run_id,
  'historical_execution',
  CASE
    WHEN run.started_at ~ '^[0-9]{4}-(0[1-9]|1[0-2])-'
      AND substring(run.started_at FROM 1 FOR 4)::INTEGER BETWEEN 1 AND 9999
    THEN make_date(
      substring(run.started_at FROM 1 FOR 4)::INTEGER,
      substring(run.started_at FROM 6 FOR 2)::INTEGER,
      1
    )
    WHEN run.created_at ~ '^[0-9]{4}-(0[1-9]|1[0-2])-'
      AND substring(run.created_at FROM 1 FOR 4)::INTEGER BETWEEN 1 AND 9999
    THEN make_date(
      substring(run.created_at FROM 1 FOR 4)::INTEGER,
      substring(run.created_at FROM 6 FOR 2)::INTEGER,
      1
    )
    ELSE date_trunc(
      'month',
      migration_clock.now AT TIME ZONE 'UTC'
    )::DATE
  END,
  'settled',
  greatest(run.cost_usd,0.000001)::NUMERIC(18,6),
  run.cost_usd::NUMERIC(18,6),
  run.cost_usd::NUMERIC(18,6),
  encode(sha256(convert_to(
    'historical-reserve-key:' || run.workspace_id || ':' || run.run_id,
    'UTF8'
  )),'hex'),
  encode(sha256(convert_to(
    'historical-reserve-request:' || run.workspace_id || ':' || run.run_id,
    'UTF8'
  )),'hex'),
  encode(sha256(convert_to(
    'historical-settle-key:' || run.workspace_id || ':' || run.run_id,
    'UTF8'
  )),'hex'),
  encode(sha256(convert_to(
    'historical-settle-request:' || run.workspace_id || ':' || run.run_id,
    'UTF8'
  )),'hex'),
  NULL,
  NULL,
  NULL,
  migration_clock.now,
  migration_clock.now+INTERVAL '24 hours',
  migration_clock.now,
  NULL,
  NULL,
  migration_clock.now
FROM runs run
CROSS JOIN migration_clock
WHERE run.billing_class='historical_execution'
  AND NOT EXISTS (
    SELECT 1
    FROM run_cost_reservations reservation
    WHERE reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
  );

CREATE INDEX IF NOT EXISTS idx_run_cost_reservations_concurrency_v10
ON run_cost_reservations(workspace_id,state,expires_at)
WHERE state='reserved';

DROP INDEX IF EXISTS idx_run_cost_reservations_monthly_usage_v10;
CREATE INDEX idx_run_cost_reservations_monthly_usage_v10
ON run_cost_reservations(workspace_id,billing_month_utc,state)
INCLUDE(estimated_cost_usd,observed_cost_usd,settled_cost_usd);

CREATE INDEX IF NOT EXISTS idx_run_cost_reservations_expiry_v10
ON run_cost_reservations(expires_at,workspace_id)
WHERE state='reserved';

CREATE UNIQUE INDEX IF NOT EXISTS
idx_run_cost_reservations_settlement_idempotency_v10
ON run_cost_reservations(workspace_id,settlement_idempotency_key_hash)
WHERE settlement_idempotency_key_hash IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS
idx_run_cost_reservations_release_idempotency_v10
ON run_cost_reservations(workspace_id,release_idempotency_key_hash)
WHERE release_idempotency_key_hash IS NOT NULL;

CREATE OR REPLACE FUNCTION agentops_run_cost_reservation_guard_v10()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_delete_forbidden';
  END IF;

  IF TG_OP='INSERT' THEN
    IF NEW.state<>'reserved' THEN
      RAISE EXCEPTION USING
        ERRCODE='23514',
        MESSAGE='cost_reservation_must_begin_reserved';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.reservation_id IS DISTINCT FROM NEW.reservation_id
    OR OLD.workspace_id IS DISTINCT FROM NEW.workspace_id
    OR OLD.run_id IS DISTINCT FROM NEW.run_id
    OR OLD.billing_class IS DISTINCT FROM NEW.billing_class
    OR OLD.billing_month_utc IS DISTINCT FROM NEW.billing_month_utc
    OR OLD.estimated_cost_usd IS DISTINCT FROM NEW.estimated_cost_usd
    OR OLD.idempotency_key_hash IS DISTINCT FROM NEW.idempotency_key_hash
    OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
    OR OLD.reserved_at IS DISTINCT FROM NEW.reserved_at
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_binding_immutable';
  END IF;

  IF NEW.observed_cost_usd<OLD.observed_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='observed_cost_decrease_forbidden';
  END IF;
  IF NEW.observed_cost_usd>NEW.estimated_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='observed_cost_exceeds_reservation';
  END IF;
  IF NEW.updated_at<OLD.updated_at THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_update_time_decrease_forbidden';
  END IF;

  IF OLD.state IN ('settled','released') THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_transition_forbidden';
  END IF;
  IF OLD.state='reserved'
    AND NEW.state NOT IN ('reserved','settled','released','expired')
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_transition_forbidden';
  END IF;
  IF OLD.state='expired'
    AND NEW.state NOT IN ('reserved','settled','released')
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_transition_forbidden';
  END IF;
  IF NEW.state='reserved'
    AND NEW.expires_at<=OLD.expires_at
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_lease_must_advance';
  END IF;

  RETURN NEW;
END
$$;

CREATE TRIGGER run_cost_reservations_guard_v10
BEFORE INSERT OR UPDATE OR DELETE ON run_cost_reservations
FOR EACH ROW
EXECUTE FUNCTION agentops_run_cost_reservation_guard_v10();

CREATE OR REPLACE FUNCTION agentops_run_billing_class_guard_v10()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  IF OLD.billing_class IS NOT DISTINCT FROM NEW.billing_class THEN
    RETURN NEW;
  END IF;

  IF OLD.billing_class='metered_execution'
    AND NEW.billing_class='nonbillable_management'
    AND NEW.model_provider='agent-gateway'
    AND NEW.model_name='enrollment-request'
    AND EXISTS (
      SELECT 1
      FROM agent_gateway_enrollment_requests request
      WHERE request.run_id=NEW.run_id
        AND request.workspace_id=NEW.workspace_id
        AND request.task_id=NEW.task_id
        AND request.agent_id=NEW.agent_id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM run_cost_reservations reservation
      WHERE reservation.workspace_id=NEW.workspace_id
        AND reservation.run_id=NEW.run_id
    )
  THEN
    RETURN NEW;
  END IF;

  RAISE EXCEPTION USING
    ERRCODE='55000',
    MESSAGE='run_billing_class_immutable';
END
$$;

DROP TRIGGER IF EXISTS runs_billing_class_guard_v10 ON runs;
CREATE TRIGGER runs_billing_class_guard_v10
BEFORE UPDATE OF billing_class ON runs
FOR EACH ROW
EXECUTE FUNCTION agentops_run_billing_class_guard_v10();

CREATE OR REPLACE FUNCTION
agentops_classify_enrollment_management_run_v10()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM runs run
    WHERE run.run_id=NEW.run_id
      AND run.workspace_id=NEW.workspace_id
      AND run.task_id=NEW.task_id
      AND run.agent_id=NEW.agent_id
      AND run.model_provider='agent-gateway'
      AND run.model_name='enrollment-request'
      AND run.approval_required=1
      AND run.status='waiting_approval'
      AND run.cost_usd=0.000000
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE='23514',
      MESSAGE='nonbillable_management_run_binding_invalid';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM run_cost_reservations reservation
    WHERE reservation.workspace_id=NEW.workspace_id
      AND reservation.run_id=NEW.run_id
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE='23514',
      MESSAGE='nonbillable_management_run_has_cost_ledger';
  END IF;

  UPDATE runs
  SET billing_class='nonbillable_management'
  WHERE run_id=NEW.run_id
    AND workspace_id=NEW.workspace_id
    AND billing_class='metered_execution';

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS
agent_gateway_enrollment_run_billing_class_v10
ON agent_gateway_enrollment_requests;
CREATE TRIGGER agent_gateway_enrollment_run_billing_class_v10
AFTER INSERT OR UPDATE OF run_id,workspace_id,task_id,agent_id
ON agent_gateway_enrollment_requests
FOR EACH ROW
EXECUTE FUNCTION agentops_classify_enrollment_management_run_v10();

CREATE OR REPLACE FUNCTION agentops_enforce_billable_run_ledger_v10()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
  v_run runs%ROWTYPE;
  v_reservation run_cost_reservations%ROWTYPE;
BEGIN
  SELECT *
  INTO v_run
  FROM runs
  WHERE run_id=NEW.run_id;

  IF NOT FOUND THEN
    RETURN NEW;
  END IF;

  IF v_run.billing_class='nonbillable_management' THEN
    IF v_run.model_provider<>'agent-gateway'
      OR v_run.model_name<>'enrollment-request'
      OR NOT EXISTS (
        SELECT 1
        FROM agent_gateway_enrollment_requests request
        WHERE request.run_id=v_run.run_id
          AND request.workspace_id=v_run.workspace_id
          AND request.task_id=v_run.task_id
          AND request.agent_id=v_run.agent_id
      )
      OR EXISTS (
        SELECT 1
        FROM run_cost_reservations reservation
        WHERE reservation.workspace_id=v_run.workspace_id
          AND reservation.run_id=v_run.run_id
      )
    THEN
      RAISE EXCEPTION USING
        ERRCODE='23514',
        MESSAGE='nonbillable_management_run_binding_invalid';
    END IF;
    RETURN NEW;
  END IF;

  SELECT *
  INTO v_reservation
  FROM run_cost_reservations reservation
  WHERE reservation.workspace_id=v_run.workspace_id
    AND reservation.run_id=v_run.run_id
    AND reservation.billing_class=v_run.billing_class;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE='23514',
      MESSAGE='billable_run_cost_reservation_required';
  END IF;

  IF v_run.billing_class='metered_execution'
    AND v_run.status IN ('completed','failed','blocked')
  THEN
    IF v_reservation.state='released'
      AND NOT (
        (
          v_reservation.release_reason='run_rejected_before_execution'
          AND v_run.status='blocked'
        )
        OR (
          v_reservation.release_reason='run_cancelled_before_execution'
          AND v_run.status IN ('failed','blocked')
        )
      )
    THEN
      RAISE EXCEPTION USING
        ERRCODE='23514',
        MESSAGE='terminal_run_released_state_invalid';
    END IF;
    IF v_reservation.state NOT IN ('settled','released') THEN
      RAISE EXCEPTION USING
        ERRCODE='23514',
        MESSAGE='terminal_run_cost_settlement_required';
    END IF;
  END IF;
  IF v_run.billing_class='metered_execution'
    AND v_run.status IN ('running','waiting_approval')
    AND v_reservation.state NOT IN ('reserved','expired')
  THEN
    RAISE EXCEPTION USING
      ERRCODE='23514',
      MESSAGE='active_run_cost_reservation_state_invalid';
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS runs_billable_ledger_v10 ON runs;
CREATE CONSTRAINT TRIGGER runs_billable_ledger_v10
AFTER INSERT OR UPDATE ON runs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION agentops_enforce_billable_run_ledger_v10();

CREATE OR REPLACE FUNCTION agentops_reserve_run_cost_v10(
  p_workspace_id TEXT,
  p_run_id TEXT,
  p_estimated_cost_usd NUMERIC,
  p_idempotency_key_hash TEXT,
  p_request_hash TEXT,
  p_ttl INTERVAL DEFAULT INTERVAL '1 hour'
)
RETURNS run_cost_reservations
LANGUAGE plpgsql
SECURITY INVOKER
SET lock_timeout='5s'
SET statement_timeout='30s'
AS $$
DECLARE
  v_now TIMESTAMPTZ := clock_timestamp();
  v_billing_month DATE;
  v_entitlement workspace_entitlements%ROWTYPE;
  v_existing run_cost_reservations%ROWTYPE;
  v_run runs%ROWTYPE;
  v_concurrent_runs BIGINT;
  v_monthly_runs BIGINT;
  v_monthly_cost NUMERIC(18,6);
BEGIN
  IF NULLIF(btrim(p_workspace_id),'') IS NULL
    OR NULLIF(btrim(p_run_id),'') IS NULL
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_reservation_binding_required';
  END IF;
  IF p_idempotency_key_hash IS NULL
    OR p_request_hash IS NULL
    OR p_idempotency_key_hash !~ '^[a-f0-9]{64}$'
    OR p_request_hash !~ '^[a-f0-9]{64}$'
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_reservation_hash_invalid';
  END IF;
  IF p_estimated_cost_usd IS NULL
    OR p_estimated_cost_usd <= 0
    OR lower(p_estimated_cost_usd::TEXT)
      IN ('nan','infinity','-infinity')
    OR p_estimated_cost_usd > 999999999999.999999
    OR p_estimated_cost_usd <> round(p_estimated_cost_usd,6)
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='estimated_cost_must_be_positive_numeric_18_6';
  END IF;
  IF p_ttl IS NULL
    OR p_ttl < INTERVAL '1 second'
    OR p_ttl > INTERVAL '24 hours'
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_reservation_ttl_invalid';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'agentops:run-cost-reservation:' || p_workspace_id,
      0
    )
  );
  v_now := clock_timestamp();
  v_billing_month :=
    date_trunc('month',v_now AT TIME ZONE 'UTC')::DATE;

  UPDATE run_cost_reservations
  SET
    state='expired',
    expired_at=v_now,
    updated_at=v_now
  WHERE workspace_id=p_workspace_id
    AND state='reserved'
    AND expires_at<=v_now;

  SELECT *
  INTO v_existing
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id
    AND idempotency_key_hash=p_idempotency_key_hash
  FOR UPDATE;

  IF FOUND THEN
    IF v_existing.run_id<>p_run_id
      OR v_existing.request_hash<>p_request_hash
      OR v_existing.estimated_cost_usd
        <> p_estimated_cost_usd::NUMERIC(18,6)
    THEN
      RAISE EXCEPTION USING
        ERRCODE='23505',
        MESSAGE='cost_reservation_idempotency_conflict';
    END IF;

    IF v_existing.state='expired' THEN
      RAISE EXCEPTION USING
        ERRCODE='55000',
        MESSAGE='cost_reservation_replay_expired';
    END IF;
    IF v_existing.state='released' THEN
      RAISE EXCEPTION USING
        ERRCODE='55000',
        MESSAGE='cost_reservation_replay_released';
    END IF;

    SELECT *
    INTO v_run
    FROM runs
    WHERE workspace_id=p_workspace_id
      AND run_id=p_run_id;

    IF v_existing.state='settled' THEN
      IF NOT FOUND
        OR v_run.billing_class<>v_existing.billing_class
        OR v_run.status NOT IN ('completed','failed','blocked')
      THEN
        RAISE EXCEPTION USING
          ERRCODE='55000',
          MESSAGE='cost_reservation_settled_replay_state_invalid';
      END IF;
      RETURN v_existing;
    END IF;

    IF FOUND AND (
      v_run.billing_class<>v_existing.billing_class
      OR v_run.status IN ('completed','failed','blocked')
    ) THEN
      RAISE EXCEPTION USING
        ERRCODE='55000',
        MESSAGE='cost_reservation_active_replay_state_invalid';
    END IF;
    RETURN v_existing;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM run_cost_reservations
    WHERE workspace_id=p_workspace_id
      AND run_id=p_run_id
  ) OR EXISTS (
    SELECT 1
    FROM runs
    WHERE workspace_id=p_workspace_id
      AND run_id=p_run_id
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE='23505',
      MESSAGE='cost_reservation_run_binding_conflict';
  END IF;

  SELECT *
  INTO v_entitlement
  FROM workspace_entitlements
  WHERE workspace_id=p_workspace_id
  FOR SHARE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE='42501',
      MESSAGE='cost_reservation_entitlement_missing';
  END IF;
  IF v_entitlement.status<>'active'
    OR v_entitlement.effective_at>v_now
    OR (
      v_entitlement.expires_at IS NOT NULL
      AND v_entitlement.expires_at<=v_now
    )
    OR jsonb_typeof(v_entitlement.capabilities_json)<>'object'
    OR v_entitlement.capabilities_json->'run_start'
      IS DISTINCT FROM 'true'::JSONB
    OR v_entitlement.max_concurrent_runs<=0
    OR v_entitlement.max_monthly_runs<=0
    OR v_entitlement.max_monthly_cost_usd<=0
    OR lower(v_entitlement.max_monthly_cost_usd::TEXT)
      IN ('nan','infinity','-infinity')
  THEN
    RAISE EXCEPTION USING
      ERRCODE='42501',
      MESSAGE='cost_reservation_entitlement_denied';
  END IF;

  SELECT
    COUNT(*) FILTER(
      WHERE state='reserved'
        AND expires_at>v_now
    ),
    COUNT(*) FILTER(
      WHERE billing_month_utc=v_billing_month
        AND state IN ('reserved','expired','settled')
    ),
    COALESCE(SUM(
      CASE
        WHEN billing_month_utc=v_billing_month
          AND state IN ('reserved','expired')
          THEN estimated_cost_usd
        WHEN billing_month_utc=v_billing_month
          AND state='settled'
          THEN settled_cost_usd
        ELSE 0::NUMERIC
      END
    ),0::NUMERIC)::NUMERIC(18,6)
  INTO v_concurrent_runs,v_monthly_runs,v_monthly_cost
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id;

  IF v_concurrent_runs + 1 > v_entitlement.max_concurrent_runs THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='run_concurrency_limit_exceeded';
  END IF;
  IF v_monthly_runs + 1 > v_entitlement.max_monthly_runs THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='monthly_run_limit_exceeded';
  END IF;
  IF v_monthly_cost + p_estimated_cost_usd
    > v_entitlement.max_monthly_cost_usd
  THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='monthly_cost_reservation_limit_exceeded';
  END IF;

  INSERT INTO run_cost_reservations(
    reservation_id,workspace_id,run_id,billing_class,billing_month_utc,state,
    estimated_cost_usd,observed_cost_usd,settled_cost_usd,
    idempotency_key_hash,request_hash,
    settlement_idempotency_key_hash,settlement_request_hash,
    release_idempotency_key_hash,release_request_hash,release_reason,
    reserved_at,expires_at,settled_at,released_at,expired_at,updated_at
  ) VALUES(
    'rsv_' || replace(gen_random_uuid()::TEXT,'-',''),
    p_workspace_id,p_run_id,'metered_execution',v_billing_month,'reserved',
    p_estimated_cost_usd::NUMERIC(18,6),0.000000,NULL,
    p_idempotency_key_hash,p_request_hash,NULL,NULL,NULL,NULL,NULL,
    v_now,v_now+p_ttl,NULL,NULL,NULL,v_now
  )
  RETURNING * INTO v_existing;

  RETURN v_existing;
END
$$;

CREATE OR REPLACE FUNCTION agentops_heartbeat_run_cost_v10(
  p_workspace_id TEXT,
  p_run_id TEXT,
  p_observed_cost_usd NUMERIC,
  p_ttl INTERVAL DEFAULT INTERVAL '1 hour'
)
RETURNS run_cost_reservations
LANGUAGE plpgsql
SECURITY INVOKER
SET lock_timeout='5s'
SET statement_timeout='30s'
AS $$
DECLARE
  v_now TIMESTAMPTZ := clock_timestamp();
  v_entitlement workspace_entitlements%ROWTYPE;
  v_reservation run_cost_reservations%ROWTYPE;
  v_run runs%ROWTYPE;
  v_other_concurrent_runs BIGINT;
  v_next_expiry TIMESTAMPTZ;
BEGIN
  IF NULLIF(btrim(p_workspace_id),'') IS NULL
    OR NULLIF(btrim(p_run_id),'') IS NULL
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_heartbeat_binding_required';
  END IF;
  IF p_observed_cost_usd IS NULL
    OR p_observed_cost_usd<0
    OR lower(p_observed_cost_usd::TEXT)
      IN ('nan','infinity','-infinity')
    OR p_observed_cost_usd>999999999999.999999
    OR p_observed_cost_usd<>round(p_observed_cost_usd,6)
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='observed_cost_numeric_18_6_required';
  END IF;
  IF p_ttl IS NULL
    OR p_ttl<INTERVAL '1 second'
    OR p_ttl>INTERVAL '24 hours'
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_reservation_ttl_invalid';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'agentops:run-cost-reservation:' || p_workspace_id,
      0
    )
  );
  v_now := clock_timestamp();

  UPDATE run_cost_reservations
  SET
    state='expired',
    expired_at=v_now,
    updated_at=v_now
  WHERE workspace_id=p_workspace_id
    AND state='reserved'
    AND expires_at<=v_now;

  SELECT *
  INTO v_reservation
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE='P0002',
      MESSAGE='cost_reservation_not_found';
  END IF;
  IF v_reservation.state NOT IN ('reserved','expired') THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_not_renewable';
  END IF;
  IF p_observed_cost_usd<v_reservation.observed_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='observed_cost_decrease_forbidden';
  END IF;
  IF p_observed_cost_usd>v_reservation.estimated_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='observed_cost_exceeds_reservation';
  END IF;

  SELECT *
  INTO v_run
  FROM runs
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_run.billing_class<>v_reservation.billing_class
    OR v_run.status NOT IN ('running','waiting_approval')
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_heartbeat_run_not_active';
  END IF;

  SELECT *
  INTO v_entitlement
  FROM workspace_entitlements
  WHERE workspace_id=p_workspace_id
  FOR SHARE;

  IF NOT FOUND
    OR v_entitlement.status<>'active'
    OR v_entitlement.effective_at>v_now
    OR (
      v_entitlement.expires_at IS NOT NULL
      AND v_entitlement.expires_at<=v_now
    )
    OR jsonb_typeof(v_entitlement.capabilities_json)<>'object'
    OR v_entitlement.capabilities_json->'run_start'
      IS DISTINCT FROM 'true'::JSONB
    OR v_entitlement.max_concurrent_runs<=0
  THEN
    RAISE EXCEPTION USING
      ERRCODE='42501',
      MESSAGE='cost_reservation_entitlement_denied';
  END IF;

  SELECT COUNT(*)
  INTO v_other_concurrent_runs
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id
    AND reservation_id<>v_reservation.reservation_id
    AND state='reserved'
    AND expires_at>v_now;

  IF v_other_concurrent_runs + 1
    > v_entitlement.max_concurrent_runs
  THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='run_concurrency_limit_exceeded';
  END IF;

  v_next_expiry=greatest(v_reservation.expires_at,v_now+p_ttl);
  IF v_next_expiry>v_now+INTERVAL '24 hours' THEN
    v_next_expiry=v_now+INTERVAL '24 hours';
  END IF;

  UPDATE run_cost_reservations
  SET
    state='reserved',
    observed_cost_usd=p_observed_cost_usd::NUMERIC(18,6),
    expires_at=v_next_expiry,
    expired_at=NULL,
    updated_at=v_now
  WHERE reservation_id=v_reservation.reservation_id
  RETURNING * INTO v_reservation;

  UPDATE runs
  SET cost_usd=v_reservation.observed_cost_usd
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id;

  RETURN v_reservation;
END
$$;

CREATE OR REPLACE FUNCTION agentops_settle_run_cost_v10(
  p_workspace_id TEXT,
  p_run_id TEXT,
  p_actual_cost_usd NUMERIC,
  p_idempotency_key_hash TEXT,
  p_request_hash TEXT
)
RETURNS run_cost_reservations
LANGUAGE plpgsql
SECURITY INVOKER
SET lock_timeout='5s'
SET statement_timeout='30s'
AS $$
DECLARE
  v_now TIMESTAMPTZ := clock_timestamp();
  v_reservation run_cost_reservations%ROWTYPE;
  v_run runs%ROWTYPE;
BEGIN
  IF NULLIF(btrim(p_workspace_id),'') IS NULL
    OR NULLIF(btrim(p_run_id),'') IS NULL
    OR p_idempotency_key_hash IS NULL
    OR p_request_hash IS NULL
    OR p_idempotency_key_hash !~ '^[a-f0-9]{64}$'
    OR p_request_hash !~ '^[a-f0-9]{64}$'
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_settlement_binding_invalid';
  END IF;
  IF p_actual_cost_usd IS NULL
    OR p_actual_cost_usd<0
    OR lower(p_actual_cost_usd::TEXT)
      IN ('nan','infinity','-infinity')
    OR p_actual_cost_usd>999999999999.999999
    OR p_actual_cost_usd<>round(p_actual_cost_usd,6)
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='actual_cost_numeric_18_6_required';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'agentops:run-cost-reservation:' || p_workspace_id,
      0
    )
  );
  v_now := clock_timestamp();

  SELECT *
  INTO v_reservation
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE='P0002',
      MESSAGE='cost_reservation_not_found';
  END IF;
  IF v_reservation.state='settled' THEN
    IF v_reservation.settlement_idempotency_key_hash
        = p_idempotency_key_hash
      AND v_reservation.settlement_request_hash=p_request_hash
      AND v_reservation.settled_cost_usd
        = p_actual_cost_usd::NUMERIC(18,6)
    THEN
      RETURN v_reservation;
    END IF;
    RAISE EXCEPTION USING
      ERRCODE='23505',
      MESSAGE='cost_settlement_idempotency_conflict';
  END IF;
  IF v_reservation.state NOT IN ('reserved','expired') THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_not_settleable';
  END IF;

  SELECT *
  INTO v_run
  FROM runs
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF NOT FOUND
    OR v_run.billing_class<>v_reservation.billing_class
  THEN
    RAISE EXCEPTION USING
      ERRCODE='23503',
      MESSAGE='cost_settlement_run_missing';
  END IF;
  IF p_actual_cost_usd<v_reservation.observed_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_settlement_below_observed';
  END IF;
  IF p_actual_cost_usd>v_reservation.estimated_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE='54000',
      MESSAGE='cost_settlement_exceeds_reservation';
  END IF;

  UPDATE run_cost_reservations
  SET
    state='settled',
    settled_cost_usd=p_actual_cost_usd::NUMERIC(18,6),
    settlement_idempotency_key_hash=p_idempotency_key_hash,
    settlement_request_hash=p_request_hash,
    settled_at=v_now,
    updated_at=v_now
  WHERE reservation_id=v_reservation.reservation_id
  RETURNING * INTO v_reservation;

  UPDATE runs
  SET cost_usd=v_reservation.settled_cost_usd
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id;

  RETURN v_reservation;
END
$$;

CREATE OR REPLACE FUNCTION agentops_release_run_cost_v10(
  p_workspace_id TEXT,
  p_run_id TEXT,
  p_reason TEXT,
  p_idempotency_key_hash TEXT,
  p_request_hash TEXT
)
RETURNS run_cost_reservations
LANGUAGE plpgsql
SECURITY INVOKER
SET lock_timeout='5s'
SET statement_timeout='30s'
AS $$
DECLARE
  v_now TIMESTAMPTZ := clock_timestamp();
  v_reservation run_cost_reservations%ROWTYPE;
  v_run runs%ROWTYPE;
BEGIN
  IF NULLIF(btrim(p_workspace_id),'') IS NULL
    OR NULLIF(btrim(p_run_id),'') IS NULL
    OR p_reason NOT IN (
      'run_insert_failed',
      'run_cancelled_before_execution',
      'run_rejected_before_execution'
    )
    OR p_idempotency_key_hash IS NULL
    OR p_request_hash IS NULL
    OR p_idempotency_key_hash !~ '^[a-f0-9]{64}$'
    OR p_request_hash !~ '^[a-f0-9]{64}$'
  THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_release_binding_invalid';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'agentops:run-cost-reservation:' || p_workspace_id,
      0
    )
  );
  v_now := clock_timestamp();

  SELECT *
  INTO v_reservation
  FROM run_cost_reservations
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE='P0002',
      MESSAGE='cost_reservation_not_found';
  END IF;
  IF v_reservation.state='released' THEN
    IF v_reservation.release_idempotency_key_hash=p_idempotency_key_hash
      AND v_reservation.release_request_hash=p_request_hash
      AND v_reservation.release_reason=p_reason
    THEN
      RETURN v_reservation;
    END IF;
    RAISE EXCEPTION USING
      ERRCODE='23505',
      MESSAGE='cost_release_idempotency_conflict';
  END IF;
  IF v_reservation.state NOT IN ('reserved','expired')
    OR v_reservation.observed_cost_usd<>0.000000
  THEN
    RAISE EXCEPTION USING
      ERRCODE='55000',
      MESSAGE='cost_reservation_not_releasable';
  END IF;

  SELECT *
  INTO v_run
  FROM runs
  WHERE workspace_id=p_workspace_id
    AND run_id=p_run_id
  FOR UPDATE;

  IF p_reason='run_insert_failed' THEN
    IF FOUND THEN
      RAISE EXCEPTION USING
        ERRCODE='55000',
        MESSAGE='cost_release_run_state_invalid';
    END IF;
  ELSE
    IF NOT FOUND
      OR v_run.billing_class<>v_reservation.billing_class
      OR COALESCE(v_run.input_tokens,0)<>0
      OR COALESCE(v_run.output_tokens,0)<>0
      OR COALESCE(v_run.reasoning_tokens,0)<>0
      OR v_run.cost_usd<>0.000000
      OR (
        p_reason='run_rejected_before_execution'
        AND v_run.status<>'blocked'
      )
      OR (
        p_reason='run_cancelled_before_execution'
        AND v_run.status NOT IN ('failed','blocked')
      )
    THEN
      RAISE EXCEPTION USING
        ERRCODE='55000',
        MESSAGE='cost_release_run_state_invalid';
    END IF;
  END IF;

  UPDATE run_cost_reservations
  SET
    state='released',
    release_idempotency_key_hash=p_idempotency_key_hash,
    release_request_hash=p_request_hash,
    release_reason=p_reason,
    released_at=v_now,
    updated_at=v_now
  WHERE reservation_id=v_reservation.reservation_id
  RETURNING * INTO v_reservation;

  RETURN v_reservation;
END
$$;

CREATE OR REPLACE FUNCTION agentops_expire_run_cost_reservations_v10(
  p_workspace_id TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY INVOKER
SET lock_timeout='5s'
SET statement_timeout='30s'
AS $$
DECLARE
  v_now TIMESTAMPTZ := clock_timestamp();
  v_expired BIGINT;
BEGIN
  IF NULLIF(btrim(p_workspace_id),'') IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE='22023',
      MESSAGE='cost_expiry_workspace_required';
  END IF;

  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'agentops:run-cost-reservation:' || p_workspace_id,
      0
    )
  );
  v_now := clock_timestamp();

  UPDATE run_cost_reservations
  SET
    state='expired',
    expired_at=v_now,
    updated_at=v_now
  WHERE workspace_id=p_workspace_id
    AND state='reserved'
    AND expires_at<=v_now;

  GET DIAGNOSTICS v_expired = ROW_COUNT;
  RETURN v_expired;
END
$$;

REVOKE INSERT,UPDATE,DELETE,TRUNCATE
ON run_cost_reservations
FROM PUBLIC;

REVOKE EXECUTE ON FUNCTION
agentops_run_cost_reservation_guard_v10()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_run_billing_class_guard_v10()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_classify_enrollment_management_run_v10()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_enforce_billable_run_ledger_v10()
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_reserve_run_cost_v10(TEXT,TEXT,NUMERIC,TEXT,TEXT,INTERVAL)
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_heartbeat_run_cost_v10(TEXT,TEXT,NUMERIC,INTERVAL)
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_settle_run_cost_v10(TEXT,TEXT,NUMERIC,TEXT,TEXT)
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_release_run_cost_v10(TEXT,TEXT,TEXT,TEXT,TEXT)
FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION
agentops_expire_run_cost_reservations_v10(TEXT)
FROM PUBLIC;
