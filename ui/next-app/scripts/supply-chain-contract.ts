import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const EXPECTED_ACTION_REFS = new Set([
  "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
  "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
  "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
  "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
]);
const EXPECTED_LOCAL_WORKFLOW_REFS = new Set([
  "./.github/workflows/byoc-compose-acceptance.yml",
  "./.github/workflows/byoc-cross-schema-v9-v11-acceptance.yml",
]);
const POSTGRES_IMAGE =
  "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777";
const REGISTRY_IMAGE =
  "registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373";

async function run() {
  const workflowUrls = [
    new URL("../../../.github/workflows/ci.yml", import.meta.url),
    new URL(
      "../../../.github/workflows/research-lab-incubator.yml",
      import.meta.url,
    ),
    new URL(
      "../../../.github/workflows/byoc-compose-acceptance.yml",
      import.meta.url,
    ),
    new URL(
      "../../../.github/workflows/byoc-cross-schema-v9-v11-acceptance.yml",
      import.meta.url,
    ),
  ];
  const workflows = await Promise.all(
    workflowUrls.map(async (url) => ({
      path: url.pathname,
      source: await readFile(url, "utf8"),
    })),
  );
  const byocDockerfile = await readFile(
    new URL("../../../deploy/byoc/Dockerfile", import.meta.url),
    "utf8",
  );
  const historicalByocDockerfile = await readFile(
    new URL("../../../deploy/byoc/historical-v9.Dockerfile", import.meta.url),
    "utf8",
  );
  let actionReferenceCount = 0;
  let localWorkflowReferenceCount = 0;
  for (const workflow of workflows) {
    assert.match(
      workflow.source,
      /^permissions:\n\s+contents:\s+read$/m,
      `workflow permissions are not read-only: ${workflow.path}`,
    );
    assert.doesNotMatch(
      workflow.source,
      /^\s*pull_request_target:\s*$/m,
      `workflow uses pull_request_target: ${workflow.path}`,
    );
    const references = [
      ...workflow.source.matchAll(
        /^\s*uses:\s*([^\s#]+)(?:\s+#.*)?$/gm,
      ),
    ].map((match) => String(match[1] || ""));
    for (const reference of references) {
      if (reference.startsWith("./")) {
        localWorkflowReferenceCount += 1;
        assert(
          EXPECTED_LOCAL_WORKFLOW_REFS.has(reference),
          `local reusable workflow ref is not reviewed: ${workflow.path}`,
        );
        continue;
      }
      actionReferenceCount += 1;
      assert.match(
        reference,
        /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+@[a-f0-9]{40}$/,
        `workflow action is not commit-pinned: ${workflow.path}`,
      );
      assert(
        EXPECTED_ACTION_REFS.has(reference),
        `workflow action ref is not reviewed: ${workflow.path}`,
      );
    }
  }
  assert(actionReferenceCount > 0);
  assert.equal(localWorkflowReferenceCount, EXPECTED_LOCAL_WORKFLOW_REFS.size);

  const ci = workflows.find((workflow) =>
    workflow.path.endsWith("/ci.yml"))?.source || "";
  assert(ci.includes(`image: ${POSTGRES_IMAGE}`));
  assert.doesNotMatch(ci, /^\s*image:\s*postgres:[^\s@]+\s*$/m);
  assert.match(ci, /npm ci --ignore-scripts/);
  assert.match(
    ci,
    /node-version: "22"\n\s+cache: npm\n\s+cache-dependency-path: ui\/start-building-app\/package-lock\.json/,
  );
  assert.match(ci, /npm prune --omit=dev --ignore-scripts/);
  assert.match(ci, /npm audit --omit=dev --audit-level=high/);
  assert.match(ci, /npm sbom --omit=dev --sbom-format cyclonedx/);
  assert.match(ci, /name: agentops-commercial-sbom/);
  assert.match(ci, /test:schema-fingerprint-postgres-contract/);
  assert.match(ci, /test:commercial-health-postgres-contract/);
  assert.match(ci, /test:byoc-backup-restore-behavior-contract/);
  assert.match(ci, /byoc_restore_guardian_real_postgres_smoke\.py/);
  assert.match(
    ci,
    /test:byoc-retained-data-lifecycle-packaging-contract/,
  );
  assert.match(
    ci,
    /test:byoc-retained-data-lifecycle-behavior-contract/,
  );
  assert.match(
    ci,
    /uses:\s+\.\/\.github\/workflows\/byoc-compose-acceptance\.yml/,
  );
  assert.match(
    ci,
    /uses:\s+\.\/\.github\/workflows\/byoc-cross-schema-v9-v11-acceptance\.yml/,
  );
  assert.match(ci, /name:\s+Commercial promotion gate/);
  assert.match(
    ci,
    /needs:[\s\S]*backend-deterministic[\s\S]*ui-build[\s\S]*commercial-next-boundary[\s\S]*byoc-compose-acceptance[\s\S]*byoc-cross-schema-acceptance/,
  );
  assert.match(
    ci,
    /test "\$BYOC_COMPOSE_RESULT" = success[\s\S]*test "\$BYOC_CROSS_SCHEMA_RESULT" = success/,
  );

  const byoc = workflows.find((workflow) =>
    workflow.path.endsWith("/byoc-compose-acceptance.yml"))?.source || "";
  assert.match(byoc, /^\s+workflow_call:\s*$/m);
  assert.match(byoc, /persist-credentials:\s+false/);
  assert.match(
    byoc,
    /test "\$\(git rev-parse HEAD\)" = "\$\{GITHUB_SHA\}"/,
  );
  assert.match(byoc, /timeout-minutes:\s+45/);
  assert(byoc.includes(REGISTRY_IMAGE));
  assert.doesNotMatch(byoc, /^\s+registry:2\s*$/m);
  assert.match(byoc, /docker compose[\s\S]+build --pull migrate/);
  assert.match(byoc, /up --detach --no-build --wait --wait-timeout 300 control-plane/);
  assert.match(byoc, /deploy\/byoc\/backup\.sh/);
  assert.match(byoc, /deploy\/byoc\/restore-drill\.sh/);
  assert.match(byoc, /retained-data-lifecycle\.mjs plan/);
  assert.match(byoc, /retained-data-lifecycle\.mjs apply/);
  assert.match(byoc, /retained-data-lifecycle\.mjs rollback/);
  assert.match(byoc, /docker-content-digest/i);
  assert.match(byoc, /BYOC image publication failed/);
  assert.doesNotMatch(byoc, /RepoDigests/);
  assert.match(byoc, /from_schema_contract == \.to_schema_contract/);
  assert.match(byoc, /authority_database_bound == true/);
  assert.match(byoc, /backup_restore_authoritative == true/);
  assert.match(byoc, /quarantine_cleanup_pending == false/);
  assert.match(byoc, /audit_log_count\(\)[\s\S]*<<'SQL'[\s\S]*audit_id=:'audit_id'/);
  assert.doesNotMatch(
    byoc,
    /--command\s+["\\]+SELECT count\(\*\) FROM audit_logs/,
  );
  assert.match(byoc, /authority_after_rollback[^]*= "1"/);
  assert.match(byoc, /probe_after_rollback[^]*= "0"/);
  assert.match(
    byoc,
    /agentops_byoc_retained_data_lifecycle_postconditions_v1/,
  );
  assert.match(byoc, /authority_retained:[^]*post_apply_probe_removed:/);
  assert.match(byoc, /source_image_restored:[^]*volume_identity_retained:/);
  assert.match(byoc, /cluster_identity_retained:[^]*final_health_verified:/);
  assert.match(
    byoc,
    /\$final_health_verified\s+and \$final_schema_readiness_verified/,
  );
  assert.match(
    byoc,
    /jq -e '\.ok == true' "\$\{postcondition_receipt\}"/,
  );
  assert.match(
    byoc,
    /node-secret-entrypoint\.mjs[\s\S]*--postgres-runtime[\s\S]*check:postgres-schema/,
  );
  assert.doesNotMatch(
    byoc,
    /api\/mis\/health[\s\S]{0,600}database_role_boundary_verified/,
  );
  assert.match(byoc, /image_identifiers_omitted: true/);
  assert.match(byoc, /database_identifiers_omitted: true/);
  assert.match(
    byoc,
    /agentops-byoc-lifecycle-final-schema-readiness\.json/,
  );
  assert.match(byoc, /agentops-byoc-lifecycle-postconditions\.json/);
  assert.match(byoc, /volume_identity_before/);
  assert.match(byoc, /cluster_identifier_before/);
  assert.match(byoc, /down --volumes --remove-orphans/);
  assert.match(
    byoc,
    /does not validate image upgrade or rollback across (?:schema|Schema) versions/,
  );
  const crossSchema = workflows.find((workflow) =>
    workflow.path.endsWith(
      "/byoc-cross-schema-v9-v11-acceptance.yml",
    ))?.source || "";
  assert.match(crossSchema, /^\s+workflow_dispatch:\s*$/m);
  assert.match(crossSchema, /^\s+workflow_call:\s*$/m);
  assert.match(crossSchema, /^\s+pull_request:\s*$/m);
  assert.match(crossSchema, /persist-credentials:\s+false/g);
  assert.match(
    crossSchema,
    /f55def1233403a503a39d9af92371a71770c23f7/,
  );
  assert.match(
    crossSchema,
    /test "\$\(git rev-parse HEAD\)" = "\$\{GITHUB_SHA\}"/,
  );
  assert.match(crossSchema, /timeout-minutes:\s+60/);
  assert(crossSchema.includes(REGISTRY_IMAGE));
  assert.match(crossSchema, /historical-v9\.Dockerfile/);
  assert.match(crossSchema, /deploy\/byoc\/Dockerfile/);
  assert.match(crossSchema, /cross-schema-v9-v11-acceptance\.sh/);
  assert.match(crossSchema, /backup_restore_authoritative == true/);
  assert.match(crossSchema, /down_migration_performed == false/);
  assert.doesNotMatch(crossSchema, /\bpython(?:3)?\s+/i);
  assert.doesNotMatch(crossSchema, /\bsqlite3?\s+/i);
  assert(historicalByocDockerfile.includes(
    "FROM node:22-bookworm-slim@sha256:",
  ));
  assert.match(
    historicalByocDockerfile,
    /LABEL org\.opencontainers\.image\.revision=/,
  );
  assert.match(
    historicalByocDockerfile,
    /io\.agentops\.byoc\.schema-contract="agentops_commercial_postgres_v9"/,
  );
  const buildIdentityOffset = byocDockerfile.indexOf(
    "ARG AGENTOPS_BUILD_IDENTITY=",
  );
  const sourceRevisionOffset = byocDockerfile.indexOf(
    "ARG AGENTOPS_SOURCE_REVISION=",
  );
  const identityLabelOffset = byocDockerfile.indexOf(
    "io.agentops.byoc.build-identity=",
  );
  const lastRootfsMutationOffset = Math.max(
    byocDockerfile.lastIndexOf("\nCOPY "),
    byocDockerfile.lastIndexOf("\nRUN "),
  );
  assert(lastRootfsMutationOffset >= 0);
  assert(buildIdentityOffset > lastRootfsMutationOffset);
  assert(sourceRevisionOffset > lastRootfsMutationOffset);
  assert(identityLabelOffset > lastRootfsMutationOffset);

  console.log(JSON.stringify({
    ok: true,
    contract: "agentops_supply_chain_contract_v1",
    workflow_count: workflows.length,
    action_reference_count: actionReferenceCount,
    local_workflow_reference_count: localWorkflowReferenceCount,
    actions_commit_pinned: true,
    actions_allowlisted: true,
    local_reusable_workflows_allowlisted: true,
    byoc_exact_caller_commit_verified: true,
    postgres_image_digest_pinned: true,
    registry_image_digest_pinned: true,
    registry_manifest_digest_resolved: true,
    build_identity_outside_rootfs_layers: true,
    workflow_permissions_read_only: true,
    locked_install: true,
    production_prune_ignores_scripts: true,
    production_audit: true,
    cyclonedx_sbom_artifact: true,
    commercial_contracts_in_ci: true,
    real_byoc_compose_acceptance_in_ci: true,
    real_byoc_same_schema_lifecycle_in_ci: true,
    retained_postgres_volume_verified: true,
    rollback_data_authority_verified: true,
    real_byoc_cross_schema_upgrade_gate_in_ci: true,
    aggregate_commercial_promotion_gate_in_ci: true,
    historical_byoc_image_inputs_pinned: true,
    byoc_upgrade_rollback_claimed: false,
    credentials_omitted: true,
  }));
}

run().catch(() => {
  console.log(JSON.stringify({
    ok: false,
    contract: "agentops_supply_chain_contract_v1",
    error_code: "supply_chain_contract_failed",
    credentials_omitted: true,
  }));
  process.exitCode = 1;
});
