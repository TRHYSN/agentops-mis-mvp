import { Pool, type PoolClient } from "pg";

import {
  postgresApplicationName,
  postgresDsn,
  postgresSslEnabled,
} from "./config";
import { ControlPlaneHttpError } from "./http";

declare global {
  var __agentOpsControlPlanePool: Pool | undefined;
}

function controlPlanePool() {
  if (!globalThis.__agentOpsControlPlanePool) {
    const configuredMax = Number(process.env.AGENTOPS_POSTGRES_POOL_MAX || 10);
    const max = Number.isFinite(configuredMax)
      ? Math.max(1, Math.min(Math.trunc(configuredMax), 30))
      : 10;
    globalThis.__agentOpsControlPlanePool = new Pool({
      connectionString: postgresDsn(),
      max,
      ssl: postgresSslEnabled() ? { rejectUnauthorized: true } : undefined,
      application_name: postgresApplicationName(),
    });
  }
  return globalThis.__agentOpsControlPlanePool;
}

export async function withPostgresTransaction<T>(
  work: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await controlPlanePool().connect();
  try {
    await client.query("BEGIN");
    const result = await work(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    if (error instanceof ControlPlaneHttpError && error.commitTransaction) {
      await client.query("COMMIT");
    } else {
      await client.query("ROLLBACK");
    }
    throw error;
  } finally {
    client.release();
  }
}

export async function closeControlPlanePoolForTests() {
  const active = globalThis.__agentOpsControlPlanePool;
  globalThis.__agentOpsControlPlanePool = undefined;
  if (active) await active.end();
}
