import type { PoolClient } from "pg";

import { ControlPlaneHttpError } from "./http";
import { stableHash } from "./ledger";

export type RunCostReservationRow = Readonly<{
  reservation_id: string;
  workspace_id: string;
  run_id: string;
  billing_class: "metered_execution" | "historical_execution";
  billing_month_utc: string | Date;
  state: "reserved" | "settled" | "released" | "expired";
  estimated_cost_usd: string;
  observed_cost_usd: string;
  settled_cost_usd: string | null;
  reserved_at: string | Date;
  expires_at: string | Date;
  settled_at: string | Date | null;
  released_at: string | Date | null;
  expired_at: string | Date | null;
}>;

export type RunCostReservationDenialReason =
  | "entitlement_missing"
  | "entitlement_denied"
  | "run_concurrency_limit_exceeded"
  | "monthly_run_limit_exceeded"
  | "monthly_cost_reservation_limit_exceeded";

const DENIAL_REASONS = new Set<RunCostReservationDenialReason>([
  "entitlement_missing",
  "entitlement_denied",
  "run_concurrency_limit_exceeded",
  "monthly_run_limit_exceeded",
  "monthly_cost_reservation_limit_exceeded",
]);

function databaseMessage(error: unknown) {
  return String((error as { message?: unknown })?.message || "");
}

function normalizedCost(value: unknown) {
  const raw = String(value ?? "").trim();
  if (!/^(?:0|[1-9]\d{0,11})(?:\.\d{1,6})?$/.test(raw)) return null;
  const [integer, fraction = ""] = raw.split(".");
  return `${integer}.${fraction.padEnd(6, "0")}`;
}

function costMicros(value: string) {
  return BigInt(value.replace(".", ""));
}

export function positiveCostEstimate(value: unknown) {
  const normalized = normalizedCost(value);
  if (!normalized || costMicros(normalized) <= 0n) {
    throw new ControlPlaneHttpError(
      400,
      "estimated_cost_usd_invalid",
      "estimated_cost_usd must be a positive NUMERIC(18,6) value.",
    );
  }
  return normalized;
}

export function nonNegativeActualCost(value: unknown) {
  const normalized = normalizedCost(value);
  if (!normalized) {
    throw new ControlPlaneHttpError(
      400,
      "cost_usd_invalid",
      "cost_usd must be a non-negative NUMERIC(18,6) value.",
    );
  }
  return normalized;
}

function publicReservation(row: RunCostReservationRow) {
  return {
    reservation_ref: `reservation_ref_${stableHash({
      workspace_id: row.workspace_id,
      reservation_id: row.reservation_id,
    }).slice(0, 24)}`,
    run_id: row.run_id,
    workspace_id: row.workspace_id,
    billing_class: row.billing_class,
    billing_month_utc: row.billing_month_utc,
    state: row.state,
    estimated_cost_usd: row.estimated_cost_usd,
    observed_cost_usd: row.observed_cost_usd,
    settled_cost_usd: row.settled_cost_usd,
    reserved_at: row.reserved_at,
    expires_at: row.expires_at,
    settled_at: row.settled_at,
    released_at: row.released_at,
    expired_at: row.expired_at,
    idempotency_hash_omitted: true,
    request_hash_omitted: true,
  };
}

export type PublicRunCostReservation = ReturnType<typeof publicReservation>;

function reservationIdentity(
  workspaceId: string,
  runId: string,
  requestBinding: unknown,
) {
  return {
    idempotencyKeyHash: stableHash({
      contract: "agentops_run_cost_reservation_idempotency_v1",
      workspace_id: workspaceId,
      run_id: runId,
    }),
    requestHash: stableHash({
      contract: "agentops_run_cost_reservation_request_v1",
      workspace_id: workspaceId,
      run_id: runId,
      request_binding: requestBinding,
    }),
  };
}

export async function reserveRunCost(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    runId: string;
    estimatedCostUsd: string;
    requestBinding: unknown;
  }>,
) {
  const identity = reservationIdentity(
    input.workspaceId,
    input.runId,
    input.requestBinding,
  );
  await client.query("SAVEPOINT run_cost_reserve");
  try {
    const row = (await client.query<RunCostReservationRow>(
      `SELECT *
      FROM agentops_reserve_run_cost_v10(
        $1,$2,$3::numeric,$4,$5,interval '24 hours'
      )`,
      [
        input.workspaceId,
        input.runId,
        input.estimatedCostUsd,
        identity.idempotencyKeyHash,
        identity.requestHash,
      ],
    )).rows[0];
    if (!row) throw new Error("run_cost_reservation_missing");
    await client.query("RELEASE SAVEPOINT run_cost_reserve");
    return {
      allowed: true as const,
      row,
      reservation: publicReservation(row),
      requestHash: identity.requestHash,
      idempotencyKeyHash: identity.idempotencyKeyHash,
    };
  } catch (error) {
    await client.query("ROLLBACK TO SAVEPOINT run_cost_reserve");
    await client.query("RELEASE SAVEPOINT run_cost_reserve");
    const message = databaseMessage(error);
    const reason = (
      message === "cost_reservation_entitlement_missing"
        ? "entitlement_missing"
        : message === "cost_reservation_entitlement_denied"
          ? "entitlement_denied"
          : message
    ) as RunCostReservationDenialReason;
    if (DENIAL_REASONS.has(reason)) {
      return {
        allowed: false as const,
        reason,
        requestHash: identity.requestHash,
        idempotencyKeyHash: identity.idempotencyKeyHash,
      };
    }
    if (
      message === "cost_reservation_idempotency_conflict"
      || message === "cost_reservation_run_binding_conflict"
      || message === "cost_reservation_replay_expired"
      || message === "cost_reservation_replay_released"
      || message === "cost_reservation_settled_replay_state_invalid"
      || message === "cost_reservation_active_replay_state_invalid"
    ) {
      throw new ControlPlaneHttpError(
        409,
        message,
        "Run cost reservation replay does not match the original request.",
      );
    }
    throw error;
  }
}

export async function heartbeatRunCost(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    runId: string;
    actualCostUsd: string;
  }>,
) {
  try {
    const row = (await client.query<RunCostReservationRow>(
      `SELECT *
      FROM agentops_heartbeat_run_cost_v10(
        $1,$2,$3::numeric,interval '24 hours'
      )`,
      [input.workspaceId, input.runId, input.actualCostUsd],
    )).rows[0];
    if (!row) throw new Error("run_cost_heartbeat_missing");
    return {
      row,
      reservation: publicReservation(row),
    };
  } catch (error) {
    const message = databaseMessage(error);
    if (message === "observed_cost_decrease_forbidden") {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_decrease_forbidden",
        "Reported run cost cannot decrease.",
      );
    }
    if (message === "observed_cost_exceeds_reservation") {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_reservation_exceeded",
        "Reported run cost exceeds its approved reservation.",
      );
    }
    if (message === "cost_reservation_entitlement_denied") {
      throw new ControlPlaneHttpError(
        403,
        "workspace_entitlement_denied",
        "Workspace entitlement does not allow run cost renewal.",
      );
    }
    if (message === "run_concurrency_limit_exceeded") {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_concurrency_renewal_denied",
        "Run cost renewal exceeds the workspace concurrency limit.",
      );
    }
    if (
      message === "cost_reservation_not_found"
      || message === "cost_reservation_not_renewable"
      || message === "cost_heartbeat_run_not_active"
    ) {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_reservation_not_active",
        "Run heartbeat requires a renewable cost reservation.",
      );
    }
    throw error;
  }
}

export async function settleRunCost(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    runId: string;
    actualCostUsd: string;
    terminalStatus: string;
  }>,
) {
  const idempotencyKeyHash = stableHash({
    contract: "agentops_run_cost_settlement_idempotency_v1",
    workspace_id: input.workspaceId,
    run_id: input.runId,
  });
  const requestHash = stableHash({
    contract: "agentops_run_cost_settlement_request_v1",
    workspace_id: input.workspaceId,
    run_id: input.runId,
    actual_cost_usd: input.actualCostUsd,
    terminal_status: input.terminalStatus,
  });
  try {
    const row = (await client.query<RunCostReservationRow>(
      `SELECT *
      FROM agentops_settle_run_cost_v10($1,$2,$3::numeric,$4,$5)`,
      [
        input.workspaceId,
        input.runId,
        input.actualCostUsd,
        idempotencyKeyHash,
        requestHash,
      ],
    )).rows[0];
    if (!row) throw new Error("run_cost_settlement_missing");
    return {
      row,
      reservation: publicReservation(row),
      requestHash,
      idempotencyKeyHash,
    };
  } catch (error) {
    const message = databaseMessage(error);
    if (message === "cost_settlement_exceeds_reservation") {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_reservation_exceeded",
        "Reported terminal cost exceeds its approved reservation.",
      );
    }
    if (message === "cost_settlement_below_observed") {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_decrease_forbidden",
        "Reported terminal cost cannot be below observed run cost.",
      );
    }
    if (
      message === "cost_reservation_not_found"
      || message === "cost_reservation_not_settleable"
    ) {
      throw new ControlPlaneHttpError(
        409,
        "run_cost_reservation_not_active",
        "Run terminal transition requires an active cost reservation.",
      );
    }
    if (message === "cost_settlement_idempotency_conflict") {
      throw new ControlPlaneHttpError(
        409,
        message,
        "Run cost settlement replay does not match the terminal receipt.",
      );
    }
    throw error;
  }
}
