import { execFile } from "node:child_process";
import { access } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { promisify } from "node:util";

import type {
  PromptBundle,
  RuntimeAdapter,
  RuntimeAdapterResult,
} from "./contracts";
import {
  boundedInteger,
  redactText,
  sha256,
  stableHash,
} from "./redaction";

const execFileAsync = promisify(execFile);
const MAX_RUNTIME_RESPONSE_BYTES = 1024 * 1024;
const PROVIDER_RESPONSE_OMITTED =
  "Provider response omitted; execution metadata and payload hash recorded.";
const PROVIDER_EMPTY_RESPONSE =
  "Provider response omitted; no visible assistant content was returned.";

function hasVisibleProviderContent(value: unknown) {
  return typeof value === "string" && value.trim().length > 0;
}

function loopbackHost(hostname: string) {
  return ["127.0.0.1", "::1", "[::1]", "localhost"].includes(
    hostname.toLowerCase(),
  ) || hostname.toLowerCase().endsWith(".localhost");
}

function runtimeUrl(value: string) {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("runtime_url_invalid");
  }
  if (
    url.username
    || url.password
    || url.search
    || url.hash
    || !["http:", "https:"].includes(url.protocol)
    || (url.protocol === "http:" && !loopbackHost(url.hostname))
  ) {
    throw new Error("runtime_url_invalid");
  }
  url.pathname = url.pathname.replace(/\/+$/, "");
  return url;
}

async function boundedRuntimeText(response: Response) {
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > MAX_RUNTIME_RESPONSE_BYTES) {
    throw new Error("runtime_response_too_large");
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_RUNTIME_RESPONSE_BYTES) {
    throw new Error("runtime_response_too_large");
  }
  return new TextDecoder().decode(bytes);
}

export class HermesAdapter implements RuntimeAdapter {
  readonly runtime = "hermes" as const;
  readonly modelName: string;
  readonly #gatewayUrl: URL;
  readonly #timeoutMs: number;
  readonly #maxTokens: number;

  constructor(options: {
    gatewayUrl: string;
    model?: string;
    timeoutMs?: number;
    maxTokens?: number;
  }) {
    this.#gatewayUrl = runtimeUrl(options.gatewayUrl);
    this.modelName = redactText(options.model || "hermes-agent", 120);
    this.#timeoutMs = boundedInteger(options.timeoutMs, 180_000, 1_000, 300_000);
    this.#maxTokens = boundedInteger(options.maxTokens, 512, 64, 4096);
  }

  async execute(bundle: PromptBundle): Promise<RuntimeAdapterResult> {
    const started = Date.now();
    const endpoint = new URL(this.#gatewayUrl);
    const basePath = this.#gatewayUrl.pathname.replace(/\/+$/, "");
    endpoint.pathname = `${basePath}/v1/chat/completions`;
    const targetResource = `hermes://gateway/${
      sha256(endpoint.origin).slice(0, 20)
    }/v1/chat/completions`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.#timeoutMs);
    let providerCallPerformed = false;
    try {
      providerCallPerformed = true;
      const response = await fetch(endpoint, {
        method: "POST",
        redirect: "manual",
        signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: this.modelName,
          messages: [{ role: "user", content: bundle.prompt }],
          temperature: 0,
          max_tokens: this.#maxTokens,
        }),
      });
      const raw = await boundedRuntimeText(response);
      const rawPayloadHash = sha256(raw);
      if (!response.ok) {
        return {
          ok: false,
          runtime: this.runtime,
          modelName: this.modelName,
          outputSummary: `Hermes returned HTTP ${response.status}.`,
          rawPayloadHash,
          targetResource,
          durationMs: Date.now() - started,
          outputTokens: 0,
          providerCallPerformed,
          dryRun: false,
          retryable: [408, 409, 425, 429].includes(response.status)
            || response.status >= 500,
          errorType: `HermesHTTP${response.status}`,
          errorMessage: `Hermes returned HTTP ${response.status}; body omitted.`,
        };
      }
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        throw new Error("hermes_response_invalid_json");
      }
      const choices = Array.isArray(payload.choices) ? payload.choices : [];
      const choice = choices[0] && typeof choices[0] === "object"
        ? choices[0] as Record<string, unknown>
        : {};
      const message = choice.message && typeof choice.message === "object"
        ? choice.message as Record<string, unknown>
        : {};
      const hasVisibleContent = hasVisibleProviderContent(message.content);
      const usage = payload.usage && typeof payload.usage === "object"
        ? payload.usage as Record<string, unknown>
        : {};
      return {
        ok: hasVisibleContent,
        runtime: this.runtime,
        modelName: this.modelName,
        outputSummary: hasVisibleContent
          ? PROVIDER_RESPONSE_OMITTED
          : PROVIDER_EMPTY_RESPONSE,
        rawPayloadHash,
        targetResource,
        durationMs: Date.now() - started,
        outputTokens: boundedInteger(
          usage.completion_tokens ?? usage.output_tokens,
          0,
          0,
          10_000_000,
        ),
        providerCallPerformed,
        dryRun: false,
        retryable: !hasVisibleContent,
        errorType: hasVisibleContent ? null : "HermesEmptyResponse",
        errorMessage: hasVisibleContent
          ? null
          : "Provider error detail omitted; Hermes returned no visible content.",
      };
    } catch (error) {
      const timeoutError = error instanceof Error && error.name === "AbortError";
      return {
        ok: false,
        runtime: this.runtime,
        modelName: this.modelName,
        outputSummary: "Hermes execution failed.",
        rawPayloadHash: stableHash({
          runtime: this.runtime,
          error_type: timeoutError ? "HermesTimeout" : "HermesExecutionFailed",
        }),
        targetResource,
        durationMs: Date.now() - started,
        outputTokens: 0,
        providerCallPerformed,
        dryRun: false,
        retryable: true,
        errorType: timeoutError ? "HermesTimeout" : "HermesExecutionFailed",
        errorMessage: timeoutError
          ? "Hermes execution timed out."
          : "Hermes transport failed; detail omitted.",
      };
    } finally {
      clearTimeout(timeout);
    }
  }
}

function openClawEnvironment() {
  const allowed = [
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SHELL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "OPENCLAW_HOME",
    "OPENCLAW_STATE_DIR",
    "OPENCLAW_CONFIG_PATH",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
  ];
  return {
    NODE_ENV: process.env.NODE_ENV || "production",
    ...Object.fromEntries(
    allowed
      .filter((key) => process.env[key])
      .map((key) => [key, process.env[key] as string]),
    ),
  };
}

export class OpenClawAdapter implements RuntimeAdapter {
  readonly runtime = "openclaw" as const;
  readonly modelName: string;
  readonly #binaryPath: string;
  readonly #agentName: string;
  readonly #timeoutSeconds: number;
  readonly #workingDirectory: string;

  constructor(options: {
    binaryPath: string;
    agentName?: string;
    timeoutSeconds?: number;
    workingDirectory?: string;
  }) {
    if (!isAbsolute(options.binaryPath)) {
      throw new Error("openclaw_binary_absolute_path_required");
    }
    this.#binaryPath = options.binaryPath;
    this.#agentName = redactText(options.agentName || "main", 80);
    this.modelName = this.#agentName;
    this.#timeoutSeconds = boundedInteger(
      options.timeoutSeconds,
      180,
      1,
      600,
    );
    this.#workingDirectory = options.workingDirectory || process.cwd();
  }

  async execute(bundle: PromptBundle): Promise<RuntimeAdapterResult> {
    await access(this.#binaryPath);
    const started = Date.now();
    const targetResource = `local://openclaw/${this.#agentName}`;
    let providerCallPerformed = false;
    try {
      providerCallPerformed = true;
      const { stdout, stderr } = await execFileAsync(
        this.#binaryPath,
        [
          "agent",
          "--agent",
          this.#agentName,
          "--message",
          bundle.prompt,
          "--timeout",
          String(this.#timeoutSeconds),
          "--json",
        ],
        {
          cwd: this.#workingDirectory,
          env: openClawEnvironment(),
          encoding: "utf8",
          timeout: (this.#timeoutSeconds + 30) * 1000,
          maxBuffer: MAX_RUNTIME_RESPONSE_BYTES,
          windowsHide: true,
        },
      );
      const rawPayloadHash = stableHash({ stdout, stderr });
      let payload: Record<string, unknown>;
      try {
        payload = stdout ? JSON.parse(stdout) as Record<string, unknown> : {};
      } catch {
        throw new Error("openclaw_response_invalid_json");
      }
      const result = payload.result && typeof payload.result === "object"
        ? payload.result as Record<string, unknown>
        : {};
      const meta = result.meta && typeof result.meta === "object"
        ? result.meta as Record<string, unknown>
        : {};
      const payloads = Array.isArray(result.payloads) ? result.payloads : [];
      const firstPayload = payloads[0] && typeof payloads[0] === "object"
        ? payloads[0] as Record<string, unknown>
        : {};
      const hasVisibleContent = hasVisibleProviderContent(
        meta.finalAssistantVisibleText ?? firstPayload.text,
      );
      return {
        ok: hasVisibleContent,
        runtime: this.runtime,
        modelName: this.modelName,
        outputSummary: hasVisibleContent
          ? PROVIDER_RESPONSE_OMITTED
          : PROVIDER_EMPTY_RESPONSE,
        rawPayloadHash,
        targetResource,
        durationMs: boundedInteger(
          meta.durationMs,
          Date.now() - started,
          0,
          86_400_000,
        ),
        outputTokens: 0,
        providerCallPerformed,
        dryRun: false,
        retryable: !hasVisibleContent,
        errorType: hasVisibleContent ? null : "OpenClawEmptyResponse",
        errorMessage: hasVisibleContent
          ? null
          : "Provider error detail omitted; OpenClaw returned no visible content.",
      };
    } catch {
      return {
        ok: false,
        runtime: this.runtime,
        modelName: this.modelName,
        outputSummary: "OpenClaw execution failed.",
        rawPayloadHash: stableHash({
          runtime: this.runtime,
          error_type: "OpenClawExecutionFailed",
        }),
        targetResource,
        durationMs: Date.now() - started,
        outputTokens: 0,
        providerCallPerformed,
        dryRun: false,
        retryable: true,
        errorType: "OpenClawExecutionFailed",
        errorMessage: "OpenClaw process failed; detail omitted.",
      };
    }
  }
}
