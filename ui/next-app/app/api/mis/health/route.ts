import { NextResponse } from "next/server";

import {
  controlPlaneMode,
  isProductionDeployment,
} from "@/server/controlPlane/config";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "@/server/controlPlane/schemaManifest";
import { withPostgresTransaction } from "@/server/controlPlane/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    if (!isProductionDeployment() || controlPlaneMode() !== "postgres") {
      return NextResponse.json(
        {
          ok: false,
          status: "not_ready",
          error: "commercial_postgres_owner_required",
          python_proxy_performed: false,
          sqlite_used: false,
          credentials_omitted: true,
        },
        { status: 503 },
      );
    }
    const schemaReady = await withPostgresTransaction(async (client) => {
      await client.query("SET LOCAL statement_timeout = '5s'");
      const relation = await client.query<{ relation: string | null }>(
        "SELECT to_regclass('agentops_schema_migrations')::text AS relation",
      );
      if (!relation.rows[0]?.relation) return false;
      const rows = await client.query<{
        component: string;
        version: string;
        schema_contract: string;
        checksum: string;
      }>(
        `SELECT component,version,schema_contract,checksum
        FROM agentops_schema_migrations
        WHERE component=ANY($1::text[])`,
        [POSTGRES_MIGRATION_MANIFEST.map((migration) => migration.component)],
      );
      const recorded = new Map(rows.rows.map((row) => [row.component, row]));
      return POSTGRES_MIGRATION_MANIFEST.every((migration) => {
        const row = recorded.get(migration.component);
        return row?.version === migration.version
          && row.schema_contract === migration.schemaContract
          && row.checksum === migration.checksum;
      });
    });
    if (!schemaReady) throw new Error("schema_not_ready");
    return NextResponse.json({
      ok: true,
      status: "ready",
      control_plane: "typescript_postgres",
      schema_contract: SCHEMA_CONTRACT,
      schema_ready: true,
      python_proxy_performed: false,
      sqlite_used: false,
      credentials_omitted: true,
    });
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        status: "not_ready",
        error: error instanceof Error && error.message === "schema_not_ready"
          ? "schema_not_ready"
          : "commercial_readiness_failed",
        python_proxy_performed: false,
        sqlite_used: false,
        credentials_omitted: true,
      },
      { status: 503 },
    );
  }
}
