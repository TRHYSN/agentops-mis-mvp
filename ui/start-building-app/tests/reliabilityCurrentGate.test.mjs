import assert from "node:assert/strict";
import test, { after } from "node:test";

import { createServer } from "vite";

const server = await createServer({
  configFile: false,
  root: process.cwd(),
  appType: "custom",
  optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true },
});
const { selectReliabilityCurrentGate } = await server.ssrLoadModule(
  "/src/app/data/reliabilityApi.ts",
);

after(async () => {
  await server.close();
});

const gate = (gate_id, is_current) => ({ gate_id, is_current });

test("explicit gate head is authoritative over list order and flags", () => {
  const selected = selectReliabilityCurrentGate(
    { current_gate_id: "gate-a" },
    [gate("gate-b", true), gate("gate-a", false)],
  );
  assert.equal(selected?.gate_id, "gate-a");
});

test("missing explicit gate head fails neutral", () => {
  const selected = selectReliabilityCurrentGate(
    { current_gate_id: "gate-missing" },
    [gate("gate-b", true)],
  );
  assert.equal(selected, null);
});

test("one explicit current marker is accepted without a gate head", () => {
  const selected = selectReliabilityCurrentGate(
    { current_gate_id: null },
    [gate("gate-a", false), gate("gate-b", true)],
  );
  assert.equal(selected?.gate_id, "gate-b");
});

test("zero or ambiguous current markers fail neutral", () => {
  assert.equal(
    selectReliabilityCurrentGate(null, [gate("gate-a", false)]),
    null,
  );
  assert.equal(
    selectReliabilityCurrentGate(null, [gate("gate-a", true), gate("gate-b", true)]),
    null,
  );
});
