#!/usr/bin/env node

import {
  HermesAdapter,
  OpenClawAdapter,
} from "../src/worker/adapters";
import { CommercialWorker } from "../src/worker/commercialWorker";
import type { CommercialRuntime } from "../src/worker/contracts";
import { HttpGatewayClient } from "../src/worker/gatewayClient";
import { boundedInteger, redactText } from "../src/worker/redaction";

type CliOptions = {
  adapter: CommercialRuntime;
  baseUrl: string;
  workspaceId: string;
  agentId: string;
  taskId?: string;
  estimatedCostUsd: string;
  confirmRun: boolean;
  allowHighRisk: boolean;
  allowInsecureLoopback: boolean;
  requestCustomerDeliveryApproval: boolean;
  daemon: boolean;
  pollIntervalMs: number;
  maxTasks: number;
  maxAdapterAttempts: number;
  hermesGatewayUrl: string;
  hermesModel: string;
  hermesTimeoutMs: number;
  hermesMaxTokens: number;
  openClawBinary: string;
  openClawAgent: string;
  openClawTimeoutSeconds: number;
  workingDirectory: string;
};

function envBoolean(name: string, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return ["1", "true", "yes", "on"].includes(value);
}

function positiveCost(value: unknown) {
  const normalized = String(value ?? "").trim();
  if (
    !/^(?:0|[1-9]\d{0,11})(?:\.\d{1,6})?$/.test(normalized)
    || /^0(?:\.0{1,6})?$/.test(normalized)
  ) {
    throw new Error("commercial_worker_estimated_cost_required");
  }
  const [integer, fraction = ""] = normalized.split(".");
  return `${integer}.${fraction.padEnd(6, "0")}`;
}

function argumentMap(argv: string[]) {
  const values = new Map<string, string>();
  const flags = new Set<string>();
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (!item.startsWith("--")) throw new Error(`unsupported_argument:${item}`);
    if (item.startsWith("--api-key") || item.startsWith("--token")) {
      throw new Error("gateway_credentials_must_come_from_environment");
    }
    const separator = item.indexOf("=");
    if (separator > 0) {
      values.set(item.slice(0, separator), item.slice(separator + 1));
      continue;
    }
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      values.set(item, next);
      index += 1;
    } else {
      flags.add(item);
    }
  }
  return { values, flags };
}

function cliOptions(argv: string[]): CliOptions {
  const { values, flags } = argumentMap(argv);
  const known = new Set([
    "--adapter",
    "--base-url",
    "--workspace-id",
    "--agent-id",
    "--task-id",
    "--confirm-run",
    "--allow-high-risk",
    "--allow-insecure-loopback",
    "--no-customer-delivery-approval",
    "--daemon",
    "--once",
    "--poll-interval-ms",
    "--max-tasks",
    "--max-adapter-attempts",
    "--estimated-cost-usd",
    "--hermes-gateway-url",
    "--hermes-model",
    "--hermes-timeout-ms",
    "--hermes-max-tokens",
    "--openclaw-bin",
    "--openclaw-agent",
    "--openclaw-timeout-seconds",
    "--working-directory",
  ]);
  for (const name of [...values.keys(), ...flags]) {
    if (!known.has(name)) throw new Error(`unsupported_argument:${name}`);
  }
  if (flags.has("--daemon") && flags.has("--once")) {
    throw new Error("worker_mode_conflict");
  }
  const adapter = String(
    values.get("--adapter")
    || process.env.AGENTOPS_WORKER_ADAPTER
    || "",
  ) as CommercialRuntime;
  if (!["hermes", "openclaw"].includes(adapter)) {
    throw new Error("commercial_worker_adapter_required");
  }
  return {
    adapter,
    baseUrl: values.get("--base-url")
      || process.env.AGENTOPS_BASE_URL
      || "http://127.0.0.1:3001",
    workspaceId: values.get("--workspace-id")
      || process.env.AGENTOPS_WORKSPACE_ID
      || "",
    agentId: values.get("--agent-id")
      || process.env.AGENTOPS_AGENT_ID
      || "",
    taskId: values.get("--task-id") || process.env.AGENTOPS_TASK_ID || undefined,
    estimatedCostUsd: positiveCost(
      values.get("--estimated-cost-usd")
      || process.env.AGENTOPS_RUN_ESTIMATED_COST_USD,
    ),
    confirmRun: flags.has("--confirm-run")
      || envBoolean("AGENTOPS_CONFIRM_RUN"),
    allowHighRisk: flags.has("--allow-high-risk")
      || envBoolean("AGENTOPS_ALLOW_HIGH_RISK"),
    allowInsecureLoopback: flags.has("--allow-insecure-loopback")
      || envBoolean("AGENTOPS_ALLOW_INSECURE_LOOPBACK"),
    requestCustomerDeliveryApproval:
      !flags.has("--no-customer-delivery-approval")
      && !envBoolean("AGENTOPS_DISABLE_CUSTOMER_DELIVERY_APPROVAL"),
    daemon: flags.has("--daemon"),
    pollIntervalMs: boundedInteger(
      values.get("--poll-interval-ms")
      || process.env.AGENTOPS_POLL_INTERVAL_MS,
      5_000,
      250,
      300_000,
    ),
    maxTasks: boundedInteger(
      values.get("--max-tasks") || process.env.AGENTOPS_MAX_TASKS,
      0,
      0,
      1_000_000,
    ),
    maxAdapterAttempts: boundedInteger(
      values.get("--max-adapter-attempts")
      || process.env.AGENTOPS_ADAPTER_MAX_ATTEMPTS,
      2,
      1,
      5,
    ),
    hermesGatewayUrl: values.get("--hermes-gateway-url")
      || process.env.HERMES_GATEWAY_URL
      || "http://127.0.0.1:8642",
    hermesModel: values.get("--hermes-model")
      || process.env.HERMES_MODEL
      || "hermes-agent",
    hermesTimeoutMs: boundedInteger(
      values.get("--hermes-timeout-ms") || process.env.HERMES_TIMEOUT_MS,
      180_000,
      1_000,
      300_000,
    ),
    hermesMaxTokens: boundedInteger(
      values.get("--hermes-max-tokens") || process.env.HERMES_MAX_TOKENS,
      512,
      64,
      4096,
    ),
    openClawBinary: values.get("--openclaw-bin")
      || process.env.OPENCLAW_BIN
      || "/opt/homebrew/bin/openclaw",
    openClawAgent: values.get("--openclaw-agent")
      || process.env.OPENCLAW_AGENT
      || "main",
    openClawTimeoutSeconds: boundedInteger(
      values.get("--openclaw-timeout-seconds")
      || process.env.OPENCLAW_TIMEOUT_SECONDS,
      180,
      1,
      600,
    ),
    workingDirectory: values.get("--working-directory")
      || process.env.AGENTOPS_WORKER_CWD
      || process.cwd(),
  };
}

function publicFailure(error: unknown) {
  return {
    ok: false,
    operation: "commercial_typescript_worker",
    error_type: error instanceof Error ? error.name : "WorkerError",
    error_message: redactText(
      error instanceof Error ? error.message : String(error),
      240,
    ),
    provider_call_performed: false,
    token_omitted: true,
    raw_prompt_omitted: true,
    raw_response_omitted: true,
  };
}

async function main() {
  const options = cliOptions(process.argv.slice(2));
  if (!options.confirmRun) {
    throw new Error("commercial_worker_requires_explicit_confirm_run");
  }
  const token = String(
    process.env.AGENTOPS_API_KEY
    || process.env.AGENTOPS_AGENT_TOKEN
    || "",
  );
  const gateway = new HttpGatewayClient({
    baseUrl: options.baseUrl,
    workspaceId: options.workspaceId,
    agentId: options.agentId,
    token,
    allowInsecureLoopback: options.allowInsecureLoopback,
  });
  const adapter = options.adapter === "hermes"
    ? new HermesAdapter({
      gatewayUrl: options.hermesGatewayUrl,
      model: options.hermesModel,
      timeoutMs: options.hermesTimeoutMs,
      maxTokens: options.hermesMaxTokens,
    })
    : new OpenClawAdapter({
      binaryPath: options.openClawBinary,
      agentName: options.openClawAgent,
      timeoutSeconds: options.openClawTimeoutSeconds,
      workingDirectory: options.workingDirectory,
    });
  const worker = new CommercialWorker(gateway, adapter, {
    workspaceId: options.workspaceId,
    agentId: options.agentId,
    runtime: options.adapter,
    estimatedCostUsd: options.estimatedCostUsd,
    taskId: options.taskId,
    confirmRun: options.confirmRun,
    allowHighRisk: options.allowHighRisk,
    requestCustomerDeliveryApproval:
      options.requestCustomerDeliveryApproval,
    maxAdapterAttempts: options.maxAdapterAttempts,
  });
  let stopping = false;
  let processed = 0;
  process.once("SIGINT", () => {
    stopping = true;
  });
  process.once("SIGTERM", () => {
    stopping = true;
  });
  do {
    const receipt = await worker.runOnce();
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (receipt.processed) processed += 1;
    if (!options.daemon || stopping) {
      if (!receipt.ok) process.exitCode = 1;
      break;
    }
    if (options.maxTasks > 0 && processed >= options.maxTasks) break;
    await new Promise((resolve) => setTimeout(resolve, options.pollIntervalMs));
  } while (!stopping);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify(publicFailure(error))}\n`);
  process.exitCode = 1;
});
