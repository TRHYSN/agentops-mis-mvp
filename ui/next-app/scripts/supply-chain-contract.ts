import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const EXPECTED_ACTION_REFS = new Set([
  "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
  "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
  "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
  "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
]);
const POSTGRES_IMAGE =
  "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777";

async function run() {
  const workflowUrls = [
    new URL("../../../.github/workflows/ci.yml", import.meta.url),
    new URL(
      "../../../.github/workflows/research-lab-incubator.yml",
      import.meta.url,
    ),
  ];
  const workflows = await Promise.all(
    workflowUrls.map(async (url) => ({
      path: url.pathname,
      source: await readFile(url, "utf8"),
    })),
  );
  let actionReferenceCount = 0;
  for (const workflow of workflows) {
    const references = [
      ...workflow.source.matchAll(
        /^\s*uses:\s*([^\s#]+)(?:\s+#.*)?$/gm,
      ),
    ].map((match) => String(match[1] || ""));
    for (const reference of references) {
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

  const ci = workflows.find((workflow) =>
    workflow.path.endsWith("/ci.yml"))?.source || "";
  assert(ci.includes(`image: ${POSTGRES_IMAGE}`));
  assert.doesNotMatch(ci, /^\s*image:\s*postgres:[^\s@]+\s*$/m);
  assert.match(ci, /^permissions:\n\s+contents:\s+read$/m);
  assert.match(ci, /npm ci --ignore-scripts/);
  assert.match(ci, /npm prune --omit=dev --ignore-scripts/);
  assert.match(ci, /npm audit --omit=dev --audit-level=high/);
  assert.match(ci, /npm sbom --omit=dev --sbom-format cyclonedx/);
  assert.match(ci, /name: agentops-commercial-sbom/);
  assert.match(ci, /test:schema-fingerprint-postgres-contract/);
  assert.match(ci, /test:commercial-health-postgres-contract/);
  assert.match(ci, /test:byoc-backup-restore-behavior-contract/);

  console.log(JSON.stringify({
    ok: true,
    contract: "agentops_supply_chain_contract_v1",
    workflow_count: workflows.length,
    action_reference_count: actionReferenceCount,
    actions_commit_pinned: true,
    actions_allowlisted: true,
    postgres_image_digest_pinned: true,
    workflow_permissions_read_only: true,
    locked_install: true,
    production_prune_ignores_scripts: true,
    production_audit: true,
    cyclonedx_sbom_artifact: true,
    commercial_contracts_in_ci: true,
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
