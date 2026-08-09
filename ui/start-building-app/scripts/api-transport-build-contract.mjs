import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");
const temporaryRoot = await mkdtemp(
  path.join(os.tmpdir(), "agentops-ui-transport-"),
);

function cleanEnvironment(overrides = {}) {
  const environment = { ...process.env };
  for (const key of [
    "VITE_AGENTOPS_DEPLOYMENT_MODE",
    "VITE_AGENTOPS_CONTROL_PLANE_MODE",
    "VITE_AGENTOPS_API_BASE",
    "VITE_AGENTOPS_PROXY_TARGET",
  ]) {
    delete environment[key];
  }
  return { ...environment, ...overrides };
}

function build(name, environment) {
  const outDir = path.join(temporaryRoot, name);
  const result = spawnSync(
    process.execPath,
    [viteBin, "build", "--outDir", outDir, "--emptyOutDir"],
    {
      cwd: root,
      env: cleanEnvironment(environment),
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  return { ...result, outDir };
}

async function javascriptBundle(outDir) {
  const assets = path.join(outDir, "assets");
  const files = await readdir(assets);
  const javascript = files.filter((name) => name.endsWith(".js"));
  assert.ok(javascript.length > 0);
  return (
    await Promise.all(
      javascript.map((name) => readFile(path.join(assets, name), "utf8")),
    )
  ).join("\n");
}

try {
  const freeLocal = build("free-local", {});
  assert.equal(freeLocal.status, 0);
  const freeLocalBundle = await javascriptBundle(freeLocal.outDir);
  assert.equal(freeLocalBundle.includes("/mis-api"), true);
  assert.equal(freeLocalBundle.includes("/api/mis"), false);
  assert.equal(freeLocalBundle.includes("127.0.0.1:8787"), false);
  assert.equal(freeLocalBundle.includes("/human-auth/status"), true);
  for (const localAuthPath of [
    "/human-auth/bootstrap",
    "/human-auth/password-recovery/",
    "/human-auth/pair",
    "/human-auth/pairing-invitations",
    "/human-auth/devices",
    "/human-auth/sessions",
  ]) {
    assert.equal(freeLocalBundle.includes(localAuthPath), true);
  }

  const commercial = build("commercial", {
    VITE_AGENTOPS_DEPLOYMENT_MODE: "production",
    VITE_AGENTOPS_CONTROL_PLANE_MODE: "postgres",
  });
  assert.equal(commercial.status, 0);
  const commercialBundle = await javascriptBundle(commercial.outDir);
  assert.equal(commercialBundle.includes("/api/mis"), true);
  assert.equal(commercialBundle.includes("/mis-api"), false);
  assert.equal(commercialBundle.includes("127.0.0.1:8787"), false);
  assert.equal(commercialBundle.includes("/human-auth/session"), true);
  assert.equal(commercialBundle.includes("/human-auth/status"), false);
  for (const localAuthPath of [
    "/human-auth/bootstrap",
    "/human-auth/password-recovery/",
    "/human-auth/pair",
    "/human-auth/pairing-invitations",
    "/human-auth/devices",
    "/human-auth/sessions",
  ]) {
    assert.equal(commercialBundle.includes(localAuthPath), false);
  }
  assert.equal(commercialBundle.includes("X-AgentOps-CSRF"), true);
  assert.equal(commercialBundle.includes("Idempotency-Key"), true);

  const forbiddenProxy = build("forbidden-proxy", {
    VITE_AGENTOPS_DEPLOYMENT_MODE: "production",
    VITE_AGENTOPS_CONTROL_PLANE_MODE: "proxy",
  });
  assert.notEqual(forbiddenProxy.status, 0);
  assert.match(
    `${forbiddenProxy.stdout}${forbiddenProxy.stderr}`,
    /commercial_ui_python_proxy_forbidden/,
  );

  const forbiddenHttp = build("forbidden-http", {
    VITE_AGENTOPS_DEPLOYMENT_MODE: "production",
    VITE_AGENTOPS_CONTROL_PLANE_MODE: "postgres",
    VITE_AGENTOPS_API_BASE: "http://127.0.0.1:3001/api/mis",
  });
  assert.notEqual(forbiddenHttp.status, 0);
  assert.match(
    `${forbiddenHttp.stdout}${forbiddenHttp.stderr}`,
    /commercial_ui_https_api_base_required/,
  );

  process.stdout.write(`${JSON.stringify({
    ok: true,
    contract: "commercial_ui_api_transport_build_v1",
    free_local_bundle_python_compatibility_path: true,
    commercial_bundle_next_postgres_path: true,
    commercial_human_session_write_authority: true,
    free_local_human_auth_preserved: true,
    commercial_local_human_auth_omitted: true,
    python_proxy_target_omitted_from_bundles: true,
    commercial_proxy_build_rejected: true,
    commercial_insecure_http_build_rejected: true,
    credentials_omitted: true,
  })}\n`);
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}
