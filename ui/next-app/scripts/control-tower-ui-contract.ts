import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { createControlTowerClient } from "../src/client/controlTowerApi";

type RecordedRequest = {
  path: string;
  init?: RequestInit;
};

const requests: RecordedRequest[] = [];
const session = {
  ok: true as const,
  authenticated: true as const,
  user: { user_id: "usr_owner", name: "Workspace Owner" },
  memberships: [{ workspace_id: "ws_commercial", role: "owner" }],
  csrf_token: "a".repeat(64),
  session_expires_at: "2026-08-11T00:00:00.000Z",
  idle_ttl_seconds: 1800,
};

const fetchStub = (async (input: string | URL | Request, init?: RequestInit) => {
  const path = String(input);
  requests.push({ path, init });
  if (path.includes("/dashboard/metrics")) {
    return Response.json({
      workspace_id: "ws_commercial",
      agents_total: 1,
      agents_running: 1,
      tasks_completed_total: 1,
      total_cost_usd: 0.25,
      pending_approvals: 0,
      failure_rate: 0,
      task_status_distribution: [{ status: "completed", count: 1 }],
      control_plane: "typescript_postgres",
    });
  }
  if (path.includes("/tasks?")) return Response.json([]);
  if (path.includes("/runs?")) return Response.json([]);
  if (path.includes("/approvals?")) return Response.json([]);
  if (path.endsWith("/logout")) {
    return Response.json({ ok: true, authenticated: false });
  }
  return Response.json(session);
}) as typeof fetch;

async function main() {
  const pageSource = await readFile(
    new URL("../app/page.tsx", import.meta.url),
    "utf8",
  );
  const componentSource = await readFile(
    new URL("../src/client/ControlTower.tsx", import.meta.url),
    "utf8",
  );
  const clientSource = await readFile(
    new URL("../src/client/controlTowerApi.ts", import.meta.url),
    "utf8",
  );
  const cssSource = await readFile(
    new URL("../app/control-tower.module.css", import.meta.url),
    "utf8",
  );

  assert.match(pageSource, /<ControlTower\s*\/>/);
  assert.match(componentSource, /Human Session/);
  assert.match(componentSource, /session\.user\.name/);
  assert.match(componentSource, /session\.memberships/);
  assert.match(componentSource, /handleLogout/);
  assert.match(componentSource, /snapshot\?\.metrics\.workspace_id === workspaceId/);
  assert.match(componentSource, /error\.status === 401[\s\S]*clearSession\(\)/);
  assert.match(componentSource, /Workspace ledger/);
  assert.doesNotMatch(componentSource, /\/approve|\/reject|prepared-actions/);
  assert.match(cssSource, /@media \(max-width: 640px\)/);
  assert.doesNotMatch(cssSource, /linear-gradient|radial-gradient/);

  for (const endpoint of [
    "/api/mis/human-auth/login",
    "/api/mis/human-auth/session",
    "/api/mis/human-auth/logout",
    "/api/mis/dashboard/metrics",
    "/api/mis/tasks",
    "/api/mis/runs",
    "/api/mis/approvals",
  ]) {
    assert.match(clientSource, new RegExp(endpoint.replaceAll("/", "\\/")));
  }

  const client = createControlTowerClient(fetchStub);
  const loggedIn = await client.login("owner", "bounded-password");
  assert.equal(loggedIn.user.user_id, "usr_owner");
  assert.equal((await client.session()).authenticated, true);
  const snapshot = await client.workspaceSnapshot("ws_commercial");
  assert.equal(snapshot.metrics.workspace_id, "ws_commercial");
  assert.deepEqual(snapshot.tasks, []);
  await client.logout(session.csrf_token);

  assert.equal(requests.length, 7);
  assert.ok(
    requests.every((request) => request.init?.credentials === "same-origin"),
  );
  assert.ok(requests.every((request) => request.init?.cache === "no-store"));
  assert.equal(requests[0].init?.method, "POST");
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), {
    username: "owner",
    password: "bounded-password",
  });
  assert.equal(requests[6].init?.method, "POST");
  assert.equal(
    new Headers(requests[6].init?.headers).get("x-agentops-csrf"),
    session.csrf_token,
  );
  const writePaths = requests
    .filter((request) => request.init?.method === "POST")
    .map((request) => request.path);
  assert.deepEqual(writePaths, [
    "/api/mis/human-auth/login",
    "/api/mis/human-auth/logout",
  ]);

  process.stdout.write(`${JSON.stringify({
    ok: true,
    authenticated_workspace_reads: true,
    login_logout_contract: true,
    approval_mutations_present: false,
    responsive_contract: true,
    workspace_snapshot_identity_bound: true,
    expired_session_cleared: true,
  })}\n`);
}

await main();
