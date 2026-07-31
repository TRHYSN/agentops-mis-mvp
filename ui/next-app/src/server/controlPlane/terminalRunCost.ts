import type { PoolClient } from "pg";

import { ControlPlaneHttpError } from "./http";
import { settleRunCost } from "./runCostReservations";

type TerminalRunStatus = "completed" | "failed" | "blocked";

export async function settleExistingTerminalRunCost(
  client: PoolClient,
  input: Readonly<{
    workspaceId: string;
    runId: string;
    terminalStatus: TerminalRunStatus;
  }>,
) {
  const run = (await client.query<{
    actual_cost_usd: string;
    billing_class: string;
    reservation_exists: boolean;
  }>(
    `SELECT COALESCE(
        reservation.estimated_cost_usd,
        run.cost_usd,
        0
      )::numeric(18,6)::text AS actual_cost_usd,
      run.billing_class,
      reservation.reservation_id IS NOT NULL AS reservation_exists
    FROM runs run
    LEFT JOIN run_cost_reservations reservation
      ON reservation.workspace_id=run.workspace_id
      AND reservation.run_id=run.run_id
    WHERE run.workspace_id=$1 AND run.run_id=$2
    FOR UPDATE OF run`,
    [input.workspaceId, input.runId],
  )).rows[0];
  if (!run) {
    throw new ControlPlaneHttpError(
      409,
      "terminal_run_missing",
      "Run terminal cost authority is unavailable.",
    );
  }
  if (!run.reservation_exists) {
    if (run.billing_class === "historical_execution") {
      return {
        mode: "unreserved_historical_execution" as const,
        reservation: null,
      };
    }
    if (run.billing_class === "nonbillable_management") {
      return {
        mode: "unreserved_nonbillable_management" as const,
        reservation: null,
      };
    }
    throw new ControlPlaneHttpError(
      409,
      "terminal_run_cost_reservation_required",
      "Metered run terminal transition requires an existing cost reservation.",
    );
  }
  const settlement = await settleRunCost(client, {
    workspaceId: input.workspaceId,
    runId: input.runId,
    actualCostUsd: run.actual_cost_usd,
    terminalStatus: input.terminalStatus,
  });
  return {
    mode: "settled_existing_reservation" as const,
    reservation: settlement.reservation,
  };
}
