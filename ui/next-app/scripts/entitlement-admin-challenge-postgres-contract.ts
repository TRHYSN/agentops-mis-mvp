import assert from "node:assert/strict";
import {
  createHash,
  randomBytes,
  randomUUID,
} from "node:crypto";
import { readFile } from "node:fs/promises";

import { Client } from "pg";

import { stableHash } from "../src/server/controlPlane/ledger";
import {
  POSTGRES_MIGRATION_MANIFEST,
  SCHEMA_CONTRACT,
} from "../src/server/controlPlane/schemaManifest";

const baseDsn = String(
  process.env.AGENTOPS_POSTGRES_DSN
  || "postgresql://127.0.0.1:55439/postgres",
).trim();
const suffix = randomUUID().replaceAll("-", "").slice(0, 12);
const applicationSchema = `ent_v11_${suffix}`;
const runtimeApiSchema = `ent_v11_api_${suffix}`;
const runtimeRole = `ent_v11_runtime_${suffix}`;
const adminRole = `ent_v11_admin_${suffix}`;
const otherRole = `ent_v11_other_${suffix}`;
const runtimePassword = `${randomBytes(24).toString("base64url")}R1!`;
const adminPassword = `${randomBytes(24).toString("base64url")}A1!`;
const otherPassword = `${randomBytes(24).toString("base64url")}O1!`;
const operatorId = `usr_ent_v11_${suffix}`;
const workspaceId = `ws_ent_v11_${suffix}`;
const contract =
  "agentops_workspace_entitlement_administration_v2" as const;

type ChallengeMode = "plan" | "confirm";
type CanonicalRequest = Readonly<{
  contract: typeof contract;
  workspace_id: string;
  operator_user_id: string;
  mode: ChallengeMode;
  guard:
    | Readonly<{ kind: "none" }>
    | Readonly<{ kind: "expect_absent" }>
    | Readonly<{ kind: "expected_revision"; revision: string }>;
  configuration: Readonly<{
    edition: string;
    status: string;
    capabilities: Readonly<{
      enrollment_issue: boolean;
      session_issue: boolean;
      run_start: boolean;
    }>;
    max_agents: number;
    max_active_enrollments: number;
    max_active_sessions_per_agent: number;
    max_concurrent_runs: number;
    max_monthly_runs: number;
    max_monthly_cost_usd: string;
    effective_at: string;
    expires_at: string | null;
  }>;
}>;

function quotedIdentifier(value: string) {
  assert.match(value, /^[A-Za-z_][A-Za-z0-9_]{0,62}$/);
  return `"${value}"`;
}

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function roleDsn(role: string, password: string) {
  const parsed = new URL(baseDsn);
  parsed.username = role;
  parsed.password = password;
  parsed.searchParams.set(
    "options",
    `-csearch_path=pg_catalog,${runtimeApiSchema},pg_temp`,
  );
  return parsed.toString();
}

function request(
  mode: ChallengeMode,
  overrides: Partial<CanonicalRequest["configuration"]> = {},
  guard: CanonicalRequest["guard"] = mode === "plan"
    ? { kind: "none" }
    : { kind: "expect_absent" },
): CanonicalRequest {
  const now = Date.now();
  return Object.freeze({
    contract,
    workspace_id: workspaceId,
    operator_user_id: operatorId,
    mode,
    guard: Object.freeze(guard),
    configuration: Object.freeze({
      edition: "enterprise_byoc",
      status: "active",
      capabilities: Object.freeze({
        enrollment_issue: true,
        session_issue: true,
        run_start: true,
      }),
      max_agents: 20,
      max_active_enrollments: 40,
      max_active_sessions_per_agent: 3,
      max_concurrent_runs: 5,
      max_monthly_runs: 1000,
      max_monthly_cost_usd: "5000.250000",
      effective_at: new Date(now - 60_000).toISOString(),
      expires_at: new Date(now + 86_400_000).toISOString(),
      ...overrides,
    }),
  });
}

async function expectCode(
  code: string,
  work: () => Promise<unknown>,
) {
  await assert.rejects(work, (error: unknown) => (
    error !== null
    && typeof error === "object"
    && "code" in error
    && error.code === code
  ));
}

const STRUCTURED_REJECTION_CODES = new Set([
  "entitlement_absent",
  "entitlement_already_exists",
  "entitlement_revision_stale",
]);

function assertStructuredRejection(
  receipt: Record<string, unknown>,
  mode: "plan" | "confirmed",
  errorCode: string,
) {
  assert(STRUCTURED_REJECTION_CODES.has(errorCode));
  assert.deepEqual(
    Object.keys(receipt).sort(),
    [
      "audit_appended",
      "challenge_consumed",
      "contract",
      "credentials_omitted",
      "error_code",
      "mode",
      "ok",
      "raw_config_omitted",
      "token_omitted",
      "workspace_id",
    ],
  );
  assert.deepEqual(receipt, {
    contract,
    ok: false,
    mode,
    workspace_id: workspaceId,
    error_code: errorCode,
    challenge_consumed: true,
    audit_appended: false,
    raw_config_omitted: true,
    credentials_omitted: true,
    token_omitted: true,
  });
  assert.doesNotMatch(
    JSON.stringify(receipt),
    /challenge_token|configuration|capabilities|max_monthly|operator_user/i,
  );
}

async function main() {
  assert.ok(baseDsn, "AGENTOPS_POSTGRES_DSN is required");
  const owner = new Client({ connectionString: baseDsn });
  let runtime: Client | undefined;
  let admin: Client | undefined;
  let other: Client | undefined;
  await owner.connect();
  try {
    await owner.query(
      `CREATE ROLE ${quotedIdentifier(runtimeRole)}
      LOGIN PASSWORD '${runtimePassword.replaceAll("'", "''")}'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`,
    );
    await owner.query(
      `CREATE ROLE ${quotedIdentifier(adminRole)}
      LOGIN PASSWORD '${adminPassword.replaceAll("'", "''")}'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`,
    );
    await owner.query(
      `CREATE ROLE ${quotedIdentifier(otherRole)}
      LOGIN PASSWORD '${otherPassword.replaceAll("'", "''")}'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`,
    );
    await owner.query(
      `CREATE SCHEMA ${quotedIdentifier(applicationSchema)}`,
    );
    await owner.query(
      `CREATE SCHEMA ${quotedIdentifier(runtimeApiSchema)}`,
    );
    const databaseName = (
      await owner.query<{ database_name: string }>(
        "SELECT current_database() AS database_name",
      )
    ).rows[0].database_name;
    await owner.query(
      `GRANT CONNECT ON DATABASE ${quotedIdentifier(databaseName)}
      TO ${quotedIdentifier(runtimeRole)},${quotedIdentifier(adminRole)},
        ${quotedIdentifier(otherRole)}`,
    );
    await owner.query(
      `REVOKE ALL ON SCHEMA ${quotedIdentifier(applicationSchema)}
      FROM PUBLIC,${quotedIdentifier(runtimeRole)},
        ${quotedIdentifier(adminRole)},${quotedIdentifier(otherRole)}`,
    );

    const ownerUrl = new URL(baseDsn);
    ownerUrl.searchParams.set(
      "options",
      `-csearch_path=${applicationSchema}`,
    );
    const migrationClient = new Client({
      connectionString: ownerUrl.toString(),
    });
    await migrationClient.connect();
    try {
      for (const migration of POSTGRES_MIGRATION_MANIFEST) {
        const sql = await readFile(
          `../../migrations/postgres/${migration.filename}`,
          "utf8",
        );
        await migrationClient.query("BEGIN");
        try {
          await migrationClient.query(sql);
          await migrationClient.query("COMMIT");
        } catch (error) {
          await migrationClient.query("ROLLBACK");
          throw error;
        }
      }
      const v11Sql = await readFile(
        "../../migrations/postgres/20260731_entitlement_admin_challenges_v11.sql",
        "utf8",
      );
      await migrationClient.query("BEGIN");
      try {
        await migrationClient.query(v11Sql);
        await migrationClient.query("COMMIT");
      } catch (error) {
        await migrationClient.query("ROLLBACK");
        throw error;
      }

      await migrationClient.query(
        `CREATE OR REPLACE FUNCTION ${quotedIdentifier(runtimeApiSchema)}
          .agentops_issue_workspace_entitlement_admin_challenge_v11(
            p_human_session_id TEXT,p_workspace_id TEXT,
            p_operator_user_id TEXT,p_mode TEXT,p_request JSONB,
            p_token_sha256 TEXT,p_ttl INTERVAL
          )
          RETURNS JSONB
          LANGUAGE sql VOLATILE STRICT SECURITY DEFINER
          SET search_path=pg_catalog,${quotedIdentifier(applicationSchema)},pg_temp
          AS $function$
            SELECT ${quotedIdentifier(applicationSchema)}
              .agentops_issue_entitlement_admin_challenge_core_v11(
                $1,$2,$3,$4,$5,$6,$7,
                '${adminRole.replaceAll("'", "''")}'::text
              )
          $function$`,
      );
      await migrationClient.query(
        `CREATE OR REPLACE FUNCTION ${quotedIdentifier(runtimeApiSchema)}
          .agentops_plan_workspace_entitlement_v11(
            p_challenge_id TEXT,p_challenge_token TEXT,p_request JSONB
          )
          RETURNS JSONB
          LANGUAGE sql VOLATILE STRICT SECURITY DEFINER
          SET search_path=pg_catalog,${quotedIdentifier(applicationSchema)},pg_temp
          AS $function$
            SELECT ${quotedIdentifier(applicationSchema)}
              .agentops_plan_workspace_entitlement_core_v11($1,$2,$3)
          $function$`,
      );
      await migrationClient.query(
        `CREATE OR REPLACE FUNCTION ${quotedIdentifier(runtimeApiSchema)}
          .agentops_apply_workspace_entitlement_v11(
            p_challenge_id TEXT,p_challenge_token TEXT,p_request JSONB
          )
          RETURNS JSONB
          LANGUAGE sql VOLATILE STRICT SECURITY DEFINER
          SET search_path=pg_catalog,${quotedIdentifier(applicationSchema)},pg_temp
          AS $function$
            SELECT ${quotedIdentifier(applicationSchema)}
              .agentops_apply_workspace_entitlement_core_v11($1,$2,$3)
          $function$`,
      );
      await migrationClient.query(
        `REVOKE ALL ON ALL FUNCTIONS IN SCHEMA
          ${quotedIdentifier(runtimeApiSchema)} FROM PUBLIC`,
      );
      await migrationClient.query(
        `GRANT USAGE ON SCHEMA ${quotedIdentifier(runtimeApiSchema)}
          TO ${quotedIdentifier(runtimeRole)},${quotedIdentifier(adminRole)},
            ${quotedIdentifier(otherRole)}`,
      );
      await migrationClient.query(
        `GRANT EXECUTE ON FUNCTION ${quotedIdentifier(runtimeApiSchema)}
          .agentops_issue_workspace_entitlement_admin_challenge_v11(
            text,text,text,text,jsonb,text,interval
          ) TO ${quotedIdentifier(runtimeRole)}`,
      );
      await migrationClient.query(
        `GRANT EXECUTE ON FUNCTION
          ${quotedIdentifier(runtimeApiSchema)}
            .agentops_plan_workspace_entitlement_v11(text,text,jsonb),
          ${quotedIdentifier(runtimeApiSchema)}
            .agentops_apply_workspace_entitlement_v11(text,text,jsonb)
          TO ${quotedIdentifier(adminRole)}`,
      );
      await migrationClient.query(
        `INSERT INTO users(user_id,name,email,role,created_at)
        VALUES($1,'Entitlement V11 Operator',$2,'operator',$3)`,
        [
          operatorId,
          `${operatorId}@example.invalid`,
          new Date().toISOString(),
        ],
      );
      await migrationClient.query(
        `INSERT INTO workspace_memberships(
          workspace_id,user_id,role,status,created_at,updated_at
        ) VALUES($1,$2,'operator','active',$3,$3)`,
        [workspaceId, operatorId, new Date().toISOString()],
      );
      await migrationClient.query(
        `INSERT INTO human_login_credentials(
          credential_id,user_id,username,password_hash,password_salt,
          password_params_json,status,created_at,updated_at
        ) VALUES($1,$2,$3,$4,$5,'{}','active',$6,$6)`,
        [
          `cred_${suffix}`,
          operatorId,
          `operator_${suffix}`,
          "a".repeat(64),
          "b".repeat(32),
          new Date().toISOString(),
        ],
      );
    } finally {
      await migrationClient.end();
    }

    runtime = new Client({
      connectionString: roleDsn(runtimeRole, runtimePassword),
    });
    admin = new Client({
      connectionString: roleDsn(adminRole, adminPassword),
    });
    other = new Client({
      connectionString: roleDsn(otherRole, otherPassword),
    });
    await Promise.all([runtime.connect(), admin.connect(), other.connect()]);

    const createSession = async (label: string) => {
      const sessionId = `hsess_${label}_${randomUUID().replaceAll("-", "")}`;
      const now = new Date();
      await owner.query(
        `INSERT INTO ${quotedIdentifier(applicationSchema)}.human_sessions(
          session_id,user_id,session_hash,status,created_at,expires_at
        ) VALUES($1,$2,$3,'active',$4,$5)`,
        [
          sessionId,
          operatorId,
          sha256(sessionId),
          now.toISOString(),
          new Date(now.getTime() + 300_000).toISOString(),
        ],
      );
      return sessionId;
    };

    const issue = async (
      value: CanonicalRequest | Record<string, unknown>,
      ttl = "75 seconds",
      token = randomBytes(32).toString("base64url"),
    ) => {
      const sessionId = await createSession("issue");
      const result = await runtime!.query<{ receipt: Record<string, unknown> }>(
        `SELECT ${quotedIdentifier(runtimeApiSchema)}
          .agentops_issue_workspace_entitlement_admin_challenge_v11(
            $1,$2,$3,$4,$5::jsonb,$6,$7::interval
          ) AS receipt`,
        [
          sessionId,
          workspaceId,
          operatorId,
          value.mode,
          JSON.stringify(value),
          sha256(token),
          ttl,
        ],
      );
      return {
        sessionId,
        token,
        receipt: result.rows[0].receipt,
      };
    };
    const commitStructuredRejection = async (
      operation: "plan" | "apply",
      challenge: Awaited<ReturnType<typeof issue>>,
      value: CanonicalRequest,
      errorCode: string,
    ) => {
      const functionName = operation === "plan"
        ? "agentops_plan_workspace_entitlement_v11"
        : "agentops_apply_workspace_entitlement_v11";
      await admin!.query("BEGIN");
      let receipt: Record<string, unknown>;
      try {
        receipt = (
          await admin!.query<{ receipt: Record<string, unknown> }>(
            `SELECT ${quotedIdentifier(runtimeApiSchema)}
              .${functionName}($1,$2,$3::jsonb) AS receipt`,
            [
              challenge.receipt.challenge_id,
              challenge.token,
              JSON.stringify(value),
            ],
          )
        ).rows[0].receipt;
        assertStructuredRejection(
          receipt,
          operation === "plan" ? "plan" : "confirmed",
          errorCode,
        );
        await admin!.query("COMMIT");
      } catch (error) {
        await admin!.query("ROLLBACK");
        throw error;
      }
      const consumed = (
        await owner.query<{
          consumed: boolean;
          consumed_action: string | null;
        }>(
          `SELECT consumed_at IS NOT NULL AS consumed,consumed_action
          FROM ${quotedIdentifier(applicationSchema)}
            .entitlement_admin_challenges
          WHERE challenge_id=$1`,
          [challenge.receipt.challenge_id],
        )
      ).rows[0];
      assert.deepEqual(consumed, {
        consumed: true,
        consumed_action: operation,
      });
      return receipt;
    };

    const goldenValues = [
      null,
      true,
      false,
      "quoted \" value\n",
      0,
      -12.5,
      0.0000001,
      1e21,
      [3, "two", null, { z: false, a: true }],
      { z: 1, a: ["x", { b: 2, a: 1 }] },
      { __agentops_python_float__: 3 },
      request("plan"),
    ];
    for (const value of goldenValues) {
      const result = await owner.query<{ hash: string }>(
        `SELECT ${quotedIdentifier(applicationSchema)}
          .agentops_stable_hash_v1($1::jsonb) AS hash`,
        [JSON.stringify(value)],
      );
      assert.equal(result.rows[0].hash, stableHash(value));
    }

    const privilege = (
      await owner.query<{
        admin_app_usage: boolean;
        admin_challenge_select: boolean;
        admin_entitlement_select: boolean;
        admin_entitlement_insert: boolean;
        admin_audit_insert: boolean;
        public_challenge_select: boolean;
      }>(
        `SELECT
          has_schema_privilege($1,$2,'USAGE') AS admin_app_usage,
          has_table_privilege($1,$3,'SELECT') AS admin_challenge_select,
          has_table_privilege($1,$4,'SELECT') AS admin_entitlement_select,
          has_table_privilege($1,$4,'INSERT') AS admin_entitlement_insert,
          has_table_privilege($1,$5,'INSERT') AS admin_audit_insert,
          has_table_privilege('public',$3,'SELECT')
            AS public_challenge_select`,
        [
          adminRole,
          applicationSchema,
          `${applicationSchema}.entitlement_admin_challenges`,
          `${applicationSchema}.workspace_entitlements`,
          `${applicationSchema}.audit_logs`,
        ],
      )
    ).rows[0];
    assert.deepEqual(privilege, {
      admin_app_usage: false,
      admin_challenge_select: false,
      admin_entitlement_select: false,
      admin_entitlement_insert: false,
      admin_audit_insert: false,
      public_challenge_select: false,
    });
    const appFunctions = (
      await owner.query<{
        proname: string;
        identity_arguments: string;
        security_definer: boolean;
        configuration: string[];
        public_execute: boolean;
      }>(
        `SELECT procedure_row.proname,
          pg_get_function_identity_arguments(procedure_row.oid)
            AS identity_arguments,
          procedure_row.prosecdef AS security_definer,
          COALESCE(procedure_row.proconfig,ARRAY[]::TEXT[])
            AS configuration,
          has_function_privilege(
            'public',procedure_row.oid,'EXECUTE'
          ) AS public_execute
        FROM pg_proc AS procedure_row
        JOIN pg_namespace AS namespace_row
          ON namespace_row.oid=procedure_row.pronamespace
        WHERE namespace_row.nspname=$1
          AND procedure_row.proname IN (
            'agentops_issue_workspace_entitlement_admin_challenge_v11',
            'agentops_plan_workspace_entitlement_v11',
            'agentops_apply_workspace_entitlement_v11'
          )
        ORDER BY procedure_row.proname`,
        [applicationSchema],
      )
    ).rows;
    assert.equal(appFunctions.length, 3);
    for (const functionRow of appFunctions) {
      assert.equal(functionRow.security_definer, true);
      assert.equal(functionRow.public_execute, false);
      assert(
        functionRow.configuration.includes(
          `search_path=pg_catalog, ${applicationSchema}, pg_temp`,
        ),
      );
    }
    assert.equal(
      appFunctions.find((row) => row.proname.includes("_issue_"))
        ?.identity_arguments,
      "p_human_session_id text, p_workspace_id text, p_operator_user_id text, p_mode text, p_request jsonb, p_token_sha256 text, p_ttl interval",
    );
    for (const functionName of [
      "agentops_plan_workspace_entitlement_v11",
      "agentops_apply_workspace_entitlement_v11",
    ]) {
      assert.equal(
        appFunctions.find((row) => row.proname === functionName)
          ?.identity_arguments,
        "p_challenge_id text, p_challenge_token text, p_request jsonb",
      );
    }

    const invalidBase = request("plan");
    const invalidRequests: Array<Record<string, unknown>> = [
      {
        contract: "agentops_workspace_entitlement_admin_challenge_v11",
        workspace_id: invalidBase.workspace_id,
        operator_user_id: invalidBase.operator_user_id,
        mode: invalidBase.mode,
        request: {
          guard: invalidBase.guard,
          configuration: invalidBase.configuration,
        },
      },
      { ...invalidBase, unknown: true },
      {
        ...invalidBase,
        contract: "agentops_workspace_entitlement_admin_challenge_v11",
      },
      request("plan", { edition: "free_local" }),
      request("plan", {
        capabilities: {
          enrollment_issue: false,
          session_issue: false,
          run_start: false,
        },
        max_agents: 0,
        max_active_enrollments: 0,
        max_active_sessions_per_agent: 0,
        max_concurrent_runs: 0,
        max_monthly_runs: 0,
        max_monthly_cost_usd: "0.000000",
      }),
      request("plan", {
        capabilities: {
          enrollment_issue: false,
          session_issue: true,
          run_start: false,
        },
        max_agents: 0,
        max_active_enrollments: 0,
        max_active_sessions_per_agent: 1,
        max_concurrent_runs: 0,
        max_monthly_runs: 0,
        max_monthly_cost_usd: "0.000000",
      }),
      request("plan", {
        capabilities: {
          enrollment_issue: true,
          session_issue: false,
          run_start: false,
        },
        max_active_sessions_per_agent: 1,
        max_concurrent_runs: 0,
        max_monthly_runs: 0,
        max_monthly_cost_usd: "0.000000",
      }),
      request("plan", {
        capabilities: {
          enrollment_issue: true,
          session_issue: true,
          run_start: true,
        },
        max_concurrent_runs: 0,
      }),
      request("plan", {
        capabilities: {
          enrollment_issue: true,
          session_issue: true,
          run_start: false,
        },
        max_concurrent_runs: 1,
      }),
      request("plan", {
        effective_at: new Date(Date.now() + 60_000).toISOString(),
      }),
      request("plan", {
        status: "expired",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
      }),
    ];
    for (const invalid of invalidRequests) {
      const code = invalid.configuration
        && typeof invalid.configuration === "object"
        && "edition" in invalid.configuration
        && invalid.configuration.edition === "free_local"
        ? "42501"
        : "22023";
      await expectCode(code, () => issue(invalid));
    }

    const liveGuardRequest = request("plan");
    const roleAttributes = [
      ["SUPERUSER", "NOSUPERUSER"],
      ["CREATEROLE", "NOCREATEROLE"],
      ["CREATEDB", "NOCREATEDB"],
      ["REPLICATION", "NOREPLICATION"],
      ["BYPASSRLS", "NOBYPASSRLS"],
      ["INHERIT", "NOINHERIT"],
    ] as const;
    const claimPlan = async (
      challenge: Awaited<ReturnType<typeof issue>>,
    ) => (
      await admin!.query<{ receipt: Record<string, unknown> }>(
        `SELECT ${quotedIdentifier(runtimeApiSchema)}
          .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)
          AS receipt`,
        [
          challenge.receipt.challenge_id,
          challenge.token,
          JSON.stringify(liveGuardRequest),
        ],
      )
    ).rows[0].receipt;
    const assertIssueDriftRejected = async (
      drift: () => Promise<unknown>,
      restore: () => Promise<unknown>,
    ) => {
      await drift();
      try {
        await expectCode("42501", () => issue(liveGuardRequest));
      } finally {
        await restore();
      }
    };
    const assertClaimDriftRejected = async (
      drift: () => Promise<unknown>,
      restore: () => Promise<unknown>,
    ) => {
      const challenge = await issue(liveGuardRequest);
      await drift();
      try {
        await expectCode("42501", () => claimPlan(challenge));
      } finally {
        await restore();
      }
      assert.equal((await claimPlan(challenge)).ok, true);
    };

    for (const roleName of [runtimeRole, adminRole]) {
      for (const [elevate, restrict] of roleAttributes) {
        await assertIssueDriftRejected(
          () => owner.query(
            `ALTER ROLE ${quotedIdentifier(roleName)} ${elevate}`,
          ),
          () => owner.query(
            `ALTER ROLE ${quotedIdentifier(roleName)} ${restrict}`,
          ),
        );
      }
    }
    for (const [grantedRole, memberRole] of [
      [otherRole, runtimeRole],
      [runtimeRole, otherRole],
      [otherRole, adminRole],
      [adminRole, otherRole],
    ]) {
      await assertIssueDriftRejected(
        () => owner.query(
          `GRANT ${quotedIdentifier(grantedRole)}
           TO ${quotedIdentifier(memberRole)}`,
        ),
        () => owner.query(
          `REVOKE ${quotedIdentifier(grantedRole)}
           FROM ${quotedIdentifier(memberRole)}`,
        ),
      );
    }
    for (const [elevate, restrict] of roleAttributes) {
      await assertClaimDriftRejected(
        () => owner.query(
          `ALTER ROLE ${quotedIdentifier(adminRole)} ${elevate}`,
        ),
        () => owner.query(
          `ALTER ROLE ${quotedIdentifier(adminRole)} ${restrict}`,
        ),
      );
    }
    for (const [grantedRole, memberRole] of [
      [otherRole, adminRole],
      [adminRole, otherRole],
    ]) {
      await assertClaimDriftRejected(
        () => owner.query(
          `GRANT ${quotedIdentifier(grantedRole)}
           TO ${quotedIdentifier(memberRole)}`,
        ),
        () => owner.query(
          `REVOKE ${quotedIdentifier(grantedRole)}
           FROM ${quotedIdentifier(memberRole)}`,
        ),
      );
    }

    await assertClaimDriftRejected(
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.users
         SET role='reviewer'
         WHERE user_id=$1`,
        [operatorId],
      ),
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.users
         SET role='operator'
         WHERE user_id=$1`,
        [operatorId],
      ),
    );
    await assertClaimDriftRejected(
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.workspace_memberships
         SET status='disabled'
         WHERE workspace_id=$1 AND user_id=$2`,
        [workspaceId, operatorId],
      ),
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.workspace_memberships
         SET status='active'
         WHERE workspace_id=$1 AND user_id=$2`,
        [workspaceId, operatorId],
      ),
    );
    await assertClaimDriftRejected(
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.human_login_credentials
         SET status='disabled'
         WHERE user_id=$1`,
        [operatorId],
      ),
      () => owner.query(
        `UPDATE ${quotedIdentifier(applicationSchema)}.human_login_credentials
         SET status='active'
         WHERE user_id=$1`,
        [operatorId],
      ),
    );

    const absentPlanRequest = request(
      "plan",
      {},
      { kind: "expected_revision", revision: "c".repeat(64) },
    );
    await commitStructuredRejection(
      "plan",
      await issue(absentPlanRequest),
      absentPlanRequest,
      "entitlement_absent",
    );
    const absentApplyRequest = request(
      "confirm",
      {},
      { kind: "expected_revision", revision: "d".repeat(64) },
    );
    await commitStructuredRejection(
      "apply",
      await issue(absentApplyRequest),
      absentApplyRequest,
      "entitlement_absent",
    );

    const planRequest = request("plan");
    const plannedChallenge = await issue(planRequest);
    assert.equal(
      plannedChallenge.receipt.contract,
      "agentops_workspace_entitlement_admin_challenge_v11",
    );
    assert.equal(plannedChallenge.receipt.request_sha256, stableHash(planRequest));
    assert.equal(plannedChallenge.receipt.session_revoked, true);
    assert.equal(plannedChallenge.receipt.single_use, true);
    const beforePlan = (
      await owner.query<{ entitlements: string; audits: string }>(
        `SELECT
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.workspace_entitlements)
              AS entitlements,
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.audit_logs) AS audits`,
      )
    ).rows[0];
    const planned = (
      await admin.query<{ receipt: Record<string, unknown> }>(
        `SELECT ${quotedIdentifier(runtimeApiSchema)}
          .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)
          AS receipt`,
        [
          plannedChallenge.receipt.challenge_id,
          plannedChallenge.token,
          JSON.stringify(planRequest),
        ],
      )
    ).rows[0].receipt;
    assert.equal(planned.ok, true);
    assert.equal(planned.contract, contract);
    assert.equal(planned.outcome, "would_create");
    const afterPlan = (
      await owner.query<{ entitlements: string; audits: string }>(
        `SELECT
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.workspace_entitlements)
              AS entitlements,
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.audit_logs) AS audits`,
      )
    ).rows[0];
    assert.deepEqual(afterPlan, beforePlan);
    await expectCode("55000", () => admin!.query(
      `SELECT ${quotedIdentifier(runtimeApiSchema)}
        .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)`,
      [
        plannedChallenge.receipt.challenge_id,
        plannedChallenge.token,
        JSON.stringify(planRequest),
      ],
    ));

    const tamperRequest = request("plan");
    const tamperChallenge = await issue(tamperRequest);
    await expectCode("42501", () => admin!.query(
      `SELECT ${quotedIdentifier(runtimeApiSchema)}
        .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)`,
      [
        tamperChallenge.receipt.challenge_id,
        tamperChallenge.token,
        JSON.stringify({
          ...tamperRequest,
          configuration: {
            ...tamperRequest.configuration,
            max_agents: 21,
          },
        }),
      ],
    ));
    await admin.query(
      `SELECT ${quotedIdentifier(runtimeApiSchema)}
        .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)`,
      [
        tamperChallenge.receipt.challenge_id,
        tamperChallenge.token,
        JSON.stringify(tamperRequest),
      ],
    );

    const wrongRoleRequest = request("plan");
    const wrongRoleChallenge = await issue(wrongRoleRequest);
    await expectCode("42501", () => other!.query(
      `SELECT ${quotedIdentifier(runtimeApiSchema)}
        .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)`,
      [
        wrongRoleChallenge.receipt.challenge_id,
        wrongRoleChallenge.token,
        JSON.stringify(wrongRoleRequest),
      ],
    ));

    const expiringRequest = request("plan");
    const expiringChallenge = await issue(expiringRequest, "1 second");
    await owner.query("SELECT pg_sleep(1.05)");
    await expectCode("55000", () => admin!.query(
      `SELECT ${quotedIdentifier(runtimeApiSchema)}
        .agentops_plan_workspace_entitlement_v11($1,$2,$3::jsonb)`,
      [
        expiringChallenge.receipt.challenge_id,
        expiringChallenge.token,
        JSON.stringify(expiringRequest),
      ],
    ));

    const createRequest = request("confirm");
    const createChallenge = await issue(createRequest);
    const created = (
      await admin.query<{ receipt: Record<string, unknown> }>(
        `SELECT ${quotedIdentifier(runtimeApiSchema)}
          .agentops_apply_workspace_entitlement_v11($1,$2,$3::jsonb)
          AS receipt`,
        [
          createChallenge.receipt.challenge_id,
          createChallenge.token,
          JSON.stringify(createRequest),
        ],
      )
    ).rows[0].receipt;
    assert.equal(created.ok, true);
    assert.equal(created.contract, contract);
    assert.equal(created.outcome, "created");
    assert.equal(created.audit_appended, true);
    const desired = {
      workspace_id: workspaceId,
      edition: createRequest.configuration.edition,
      status: createRequest.configuration.status,
      capabilities_json: createRequest.configuration.capabilities,
      max_agents: createRequest.configuration.max_agents,
      max_active_enrollments:
        createRequest.configuration.max_active_enrollments,
      max_active_sessions_per_agent:
        createRequest.configuration.max_active_sessions_per_agent,
      max_concurrent_runs: createRequest.configuration.max_concurrent_runs,
      max_monthly_runs: createRequest.configuration.max_monthly_runs,
      max_monthly_cost_usd:
        createRequest.configuration.max_monthly_cost_usd.replace(
          /(?:\.0+|(\.\d*?[1-9])0+)$/,
          "$1",
        ),
      effective_at: createRequest.configuration.effective_at,
      expires_at: createRequest.configuration.expires_at,
    };
    const firstAudit = (
      await owner.query<{
        actor_type: string;
        actor_id: string;
        action: string;
        entity_type: string;
        entity_id: string;
        before_hash: string | null;
        after_hash: string;
        metadata_json: string;
        tamper_chain_hash: string;
      }>(
        `SELECT actor_type,actor_id,action,entity_type,entity_id,before_hash,
          after_hash,metadata_json,tamper_chain_hash
        FROM ${quotedIdentifier(applicationSchema)}.audit_logs
        ORDER BY created_at,audit_id LIMIT 1`,
      )
    ).rows[0];
    const metadata = JSON.parse(firstAudit.metadata_json);
    assert.equal(firstAudit.before_hash, null);
    assert.equal(firstAudit.after_hash, stableHash(desired));
    assert.equal(
      firstAudit.tamper_chain_hash,
      stableHash({
        actor_type: firstAudit.actor_type,
        actor_id: firstAudit.actor_id,
        action: firstAudit.action,
        entity_type: firstAudit.entity_type,
        entity_id: firstAudit.entity_id,
        before_hash: firstAudit.before_hash,
        after_hash: firstAudit.after_hash,
        metadata_json: metadata,
        previous: "genesis",
      }),
    );

    const firstRevision = String(created.revision);
    assert.match(firstRevision, /^[a-f0-9]{64}$/);
    const existingPlanRequest = request(
      "plan",
      { max_monthly_cost_usd: "5001.000000" },
      { kind: "expect_absent" },
    );
    await commitStructuredRejection(
      "plan",
      await issue(existingPlanRequest),
      existingPlanRequest,
      "entitlement_already_exists",
    );
    const stalePlanRequest = request(
      "plan",
      { max_monthly_cost_usd: "5002.000000" },
      { kind: "expected_revision", revision: "e".repeat(64) },
    );
    await commitStructuredRejection(
      "plan",
      await issue(stalePlanRequest),
      stalePlanRequest,
      "entitlement_revision_stale",
    );
    const existingApplyRequest = request(
      "confirm",
      { max_monthly_cost_usd: "5003.000000" },
      { kind: "expect_absent" },
    );
    await commitStructuredRejection(
      "apply",
      await issue(existingApplyRequest),
      existingApplyRequest,
      "entitlement_already_exists",
    );
    const firstUpdate = request(
      "confirm",
      { max_monthly_cost_usd: "5100.000000" },
      { kind: "expected_revision", revision: firstRevision },
    );
    const staleUpdate = request(
      "confirm",
      { max_monthly_cost_usd: "5200.000000" },
      { kind: "expected_revision", revision: firstRevision },
    );
    const firstUpdateChallenge = await issue(firstUpdate);
    const staleUpdateChallenge = await issue(staleUpdate);
    const updated = (
      await admin.query<{ receipt: Record<string, unknown> }>(
        `SELECT ${quotedIdentifier(runtimeApiSchema)}
          .agentops_apply_workspace_entitlement_v11($1,$2,$3::jsonb)
          AS receipt`,
        [
          firstUpdateChallenge.receipt.challenge_id,
          firstUpdateChallenge.token,
          JSON.stringify(firstUpdate),
        ],
      )
    ).rows[0].receipt;
    assert.equal(updated.ok, true);
    assert.equal(updated.contract, contract);
    assert.equal(updated.outcome, "updated");
    await commitStructuredRejection(
      "apply",
      staleUpdateChallenge,
      staleUpdate,
      "entitlement_revision_stale",
    );
    const finalCounts = (
      await owner.query<{ entitlements: string; audits: string }>(
        `SELECT
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.workspace_entitlements)
              AS entitlements,
          (SELECT count(*)::text FROM
            ${quotedIdentifier(applicationSchema)}.audit_logs) AS audits`,
      )
    ).rows[0];
    assert.deepEqual(finalCounts, { entitlements: "1", audits: "2" });

    console.log(JSON.stringify({
      ok: true,
      contract: "agentops_entitlement_admin_challenge_postgres_contract_v1",
      schema_contract: SCHEMA_CONTRACT,
      postgres_major: 16,
      canonical_request_contract: contract,
      app_schema_direct_access_forbidden: true,
      migration_idempotent: true,
      app_function_integrity_verified: true,
      runtime_api_role_split_verified: true,
      database_role_issue_attribute_matrix_rejected: true,
      database_role_issue_membership_matrix_rejected: true,
      database_role_claim_attribute_matrix_rejected: true,
      database_role_claim_membership_matrix_rejected: true,
      operator_authority_drift_matrix_rejected: true,
      stable_hash_golden_vectors_verified: true,
      request_hash_complete_binding_verified: true,
      old_nested_contract_rejected: true,
      commercial_invariants_database_authoritative: true,
      challenge_replay_rejected: true,
      challenge_expiry_rejected: true,
      challenge_tamper_rejected: true,
      wrong_role_rejected: true,
      plan_entitlement_audit_zero_write: true,
      apply_atomic_audit_verified: true,
      concurrent_revision_conflict_consumed: true,
      structured_rejections_committed_and_minimized: true,
      credentials_omitted: true,
      raw_config_omitted: true,
      python_used: false,
      sqlite_used: false,
    }));
  } finally {
    await Promise.all([
      runtime?.end().catch(() => undefined),
      admin?.end().catch(() => undefined),
      other?.end().catch(() => undefined),
    ]);
    await owner.query(
      `DROP SCHEMA IF EXISTS ${quotedIdentifier(runtimeApiSchema)} CASCADE`,
    ).catch(() => undefined);
    await owner.query(
      `DROP SCHEMA IF EXISTS ${quotedIdentifier(applicationSchema)} CASCADE`,
    ).catch(() => undefined);
    for (const role of [runtimeRole, adminRole, otherRole]) {
      await owner.query(
        `DROP OWNED BY ${quotedIdentifier(role)}`,
      ).catch(() => undefined);
      await owner.query(
        `DROP ROLE IF EXISTS ${quotedIdentifier(role)}`,
      ).catch(() => undefined);
    }
    await owner.end().catch(() => undefined);
  }
}

main().catch((error) => {
  console.log(JSON.stringify({
    ok: false,
    contract: "agentops_entitlement_admin_challenge_postgres_contract_v1",
    error_code: "entitlement_admin_challenge_contract_failed",
    credentials_omitted: true,
    raw_config_omitted: true,
    python_used: false,
    sqlite_used: false,
  }));
  process.exitCode = 1;
});
