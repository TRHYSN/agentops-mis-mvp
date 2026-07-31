import { spawn } from "node:child_process";
import {
  chmodSync,
  chownSync,
  closeSync,
  constants,
  fchmodSync,
  fchownSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdtempSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { fileURLToPath } from "node:url";

const NODE_UID = 1000;
const NODE_GID = 1000;
const MAX_SECRET_BYTES = 64 * 1024;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function requiredCommandOption(command, option) {
  const indexes = command.flatMap((value, index) =>
    value === option ? [index] : []
  );
  if (indexes.length !== 1 || indexes[0] === command.length - 1) {
    fail(`${option.slice(2).replaceAll("-", "_")}_required`);
  }
  const value = command[indexes[0] + 1].trim();
  if (!value || value.length > 256 || /[\u0000-\u001f\u007f]/.test(value)) {
    fail(`${option.slice(2).replaceAll("-", "_")}_invalid`);
  }
  return value;
}

function isLoopbackHostname(hostname) {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized === "::1") {
    return true;
  }
  const octets = normalized.split(".");
  return (
    octets.length === 4
    && octets.every((octet) => /^\d{1,3}$/.test(octet))
    && Number(octets[0]) === 127
    && octets.every((octet) => Number(octet) <= 255)
  );
}

export function entitlementChallengeEndpoint({
  controlPlaneUrl,
  controlPlaneOrigin,
  command,
  operatorUsername,
}) {
  let baseUrl;
  try {
    baseUrl = new URL(controlPlaneUrl);
  } catch {
    fail("entitlement_control_plane_url_invalid");
  }
  if (
    !["http:", "https:"].includes(baseUrl.protocol)
    || baseUrl.username
    || baseUrl.password
    || baseUrl.search
    || baseUrl.hash
    || (baseUrl.pathname !== "/" && baseUrl.pathname !== "")
  ) {
    fail("entitlement_control_plane_url_invalid");
  }
  if (baseUrl.protocol !== "https:" && !isLoopbackHostname(baseUrl.hostname)) {
    fail("entitlement_control_plane_https_required");
  }
  let requestOrigin = "";
  if (controlPlaneOrigin?.trim()) {
    let originUrl;
    try {
      originUrl = new URL(controlPlaneOrigin);
    } catch {
      fail("entitlement_control_plane_origin_invalid");
    }
    if (
      !["http:", "https:"].includes(originUrl.protocol)
      || originUrl.username
      || originUrl.password
      || originUrl.search
      || originUrl.hash
      || (originUrl.pathname !== "/" && originUrl.pathname !== "")
    ) {
      fail("entitlement_control_plane_origin_invalid");
    }
    if (
      originUrl.protocol !== "https:"
      && !isLoopbackHostname(originUrl.hostname)
    ) {
      fail("entitlement_control_plane_origin_https_required");
    }
    requestOrigin = originUrl.origin;
  }

  const username = operatorUsername.trim();
  if (
    !/^[a-z0-9][a-z0-9._-]{2,63}$/i.test(username)
  ) {
    fail("entitlement_operator_username_invalid");
  }
  const workspaceId = requiredCommandOption(command, "--workspace-id");
  requiredCommandOption(command, "--operator-user-id");
  return {
    endpoint: new URL(
      `api/mis/workspaces/${encodeURIComponent(workspaceId)}/entitlement-admin/challenges`,
      baseUrl,
    ),
    requestOrigin,
  };
}

export async function assertEntitlementChallengeApi({
  controlPlaneUrl,
  controlPlaneOrigin,
  command,
  operatorUsername,
  fetchImplementation = fetch,
  timeoutMs = 5_000,
}) {
  const { endpoint, requestOrigin } = entitlementChallengeEndpoint({
    controlPlaneUrl,
    controlPlaneOrigin,
    command,
    operatorUsername,
  });
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  timeout.unref?.();
  try {
    const response = await fetchImplementation(endpoint, {
      headers: requestOrigin ? { origin: requestOrigin } : undefined,
      method: "OPTIONS",
      redirect: "manual",
      signal: controller.signal,
    });
    const allowedMethods = (response.headers.get("allow") || "")
      .split(",")
      .map((method) => method.trim().toUpperCase());
    if (!response.ok || !allowedMethods.includes("POST")) {
      fail("entitlement_challenge_api_unavailable");
    }
  } catch (error) {
    if (error?.code === "entitlement_challenge_api_unavailable") {
      throw error;
    }
    fail("entitlement_challenge_api_unavailable");
  } finally {
    clearTimeout(timeout);
  }
  return endpoint;
}

function stableSecretBytes(sourcePath) {
  const before = lstatSync(sourcePath);
  if (!before.isFile() || before.isSymbolicLink()) {
    fail("source_not_regular");
  }
  if (before.size < 1 || before.size > MAX_SECRET_BYTES) {
    fail("source_size_invalid");
  }

  const noFollow = constants.O_NOFOLLOW;
  if (typeof noFollow !== "number") {
    fail("nofollow_unavailable");
  }

  const descriptor = openSync(sourcePath, constants.O_RDONLY | noFollow);
  try {
    const opened = fstatSync(descriptor);
    if (
      !opened.isFile() ||
      opened.dev !== before.dev ||
      opened.ino !== before.ino ||
      opened.size !== before.size
    ) {
      fail("source_identity_changed");
    }

    const value = readFileSync(descriptor);
    const after = fstatSync(descriptor);
    if (
      after.dev !== opened.dev ||
      after.ino !== opened.ino ||
      after.size !== opened.size ||
      after.mtimeMs !== opened.mtimeMs ||
      after.ctimeMs !== opened.ctimeMs
    ) {
      fail("source_changed_during_read");
    }
    return value;
  } finally {
    closeSync(descriptor);
  }
}

export function stageSecretFile({
  sourcePath,
  targetDirectory,
  targetName,
  targetUid,
  targetGid,
}) {
  const value = stableSecretBytes(sourcePath);
  const temporaryPath = `${targetDirectory}/.${targetName}.${process.pid}`;
  const finalPath = `${targetDirectory}/${targetName}`;
  const descriptor = openSync(
    temporaryPath,
    constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
    0o400,
  );
  try {
    writeFileSync(descriptor, value);
    fchmodSync(descriptor, 0o400);
    fchownSync(descriptor, targetUid, targetGid);
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
    value.fill(0);
  }
  renameSync(temporaryPath, finalPath);
  return finalPath;
}

export function prepareRuntimeSecrets({
  definitions,
  runtimePrefix,
  targetUid,
  targetGid,
}) {
  const runtimeDirectory = mkdtempSync(runtimePrefix);
  chmodSync(runtimeDirectory, 0o700);
  try {
    const prepared = {};
    for (const definition of definitions) {
      prepared[definition.environmentName] = stageSecretFile({
        sourcePath: definition.sourcePath,
        targetDirectory: runtimeDirectory,
        targetName: definition.targetName,
        targetUid,
        targetGid,
      });
    }
    chownSync(runtimeDirectory, targetUid, targetGid);
    return { prepared, runtimeDirectory };
  } catch (error) {
    rmSync(runtimeDirectory, { force: true, recursive: true });
    throw error;
  }
}

function parseInvocation(arguments_) {
  const separator = arguments_.indexOf("--");
  if (separator < 0 || separator === arguments_.length - 1) {
    fail("command_missing");
  }
  const flags = new Set(arguments_.slice(0, separator));
  if (
    [...flags].some(
      (flag) => ![
        "--postgres",
        "--postgres-runtime",
        "--postgres-migrator",
        "--postgres-entitlement-admin",
        "--runtime-role-password",
        "--entitlement-admin-password",
        "--entitlement-operator",
        "--human-session",
      ].includes(flag),
    )
  ) {
    fail("flag_invalid");
  }
  const explicitProfiles = [
    flags.has("--postgres-runtime"),
    flags.has("--postgres-migrator"),
    flags.has("--postgres-entitlement-admin"),
  ].filter(Boolean).length;
  if (explicitProfiles > 1) {
    fail("postgres_profile_ambiguous");
  }
  if (!flags.has("--postgres") && explicitProfiles === 0) {
    fail("postgres_secret_required");
  }
  const postgresProfile = flags.has("--postgres-migrator")
    ? "migrator"
    : flags.has("--postgres-entitlement-admin")
      ? "entitlement-admin"
      : "runtime";
  if (
    flags.has("--runtime-role-password")
    && postgresProfile !== "migrator"
  ) {
    fail("runtime_role_password_profile_invalid");
  }
  if (
    flags.has("--entitlement-admin-password")
    && postgresProfile !== "migrator"
  ) {
    fail("entitlement_admin_password_profile_invalid");
  }
  if (
    flags.has("--entitlement-operator")
    && postgresProfile !== "entitlement-admin"
  ) {
    fail("entitlement_operator_profile_invalid");
  }
  if (
    postgresProfile === "entitlement-admin"
    && !flags.has("--entitlement-operator")
  ) {
    fail("entitlement_operator_secret_required");
  }
  return {
    command: arguments_.slice(separator + 1),
    includeHumanSession: flags.has("--human-session"),
    includeRuntimeRolePassword: flags.has("--runtime-role-password"),
    includeEntitlementAdminPassword:
      flags.has("--entitlement-admin-password"),
    includeEntitlementOperator: flags.has("--entitlement-operator"),
    postgresProfile,
  };
}

function assertDroppedPrivilegeState() {
  if (process.platform !== "linux") {
    fail("linux_runtime_required");
  }
  const status = readFileSync("/proc/self/status", "utf8");
  for (const field of ["CapInh", "CapPrm", "CapEff", "CapAmb"]) {
    const capabilities = status.match(
      new RegExp(`^${field}:\\s+([0-9A-Fa-f]+)$`, "m"),
    );
    if (!capabilities || !/^0+$/.test(capabilities[1])) {
      fail("privilege_state_invalid");
    }
  }
  if (!/^NoNewPrivs:\s+1$/m.test(status)) {
    fail("privilege_state_invalid");
  }
}

export function dropPrivilegesAndAssert() {
  if (process.getuid?.() !== 0 || process.getgid?.() !== 0) {
    fail("root_initialization_required");
  }
  process.setgroups([]);
  process.setgid(NODE_GID);
  process.setuid(NODE_UID);
  if (process.getuid() !== NODE_UID || process.getgid() !== NODE_GID) {
    fail("privilege_drop_failed");
  }
  assertDroppedPrivilegeState();
}

async function main() {
  if (process.getuid?.() !== 0 || process.getgid?.() !== 0) {
    fail("root_initialization_required");
  }
  if (
    process.env.AGENTOPS_POSTGRES_PASSWORD ||
    process.env.AGENTOPS_POSTGRES_DSN ||
    process.env.AGENTOPS_POSTGRES_MIGRATOR_PASSWORD ||
    process.env.AGENTOPS_POSTGRES_MIGRATOR_DSN ||
    process.env.AGENTOPS_POSTGRES_RUNTIME_PASSWORD ||
    process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD ||
    process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_DSN ||
    process.env.AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD ||
    process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY
  ) {
    fail("direct_secret_forbidden");
  }

  const invocation = parseInvocation(process.argv.slice(2));
  const definitions = [];
  const postgresPrefix = invocation.postgresProfile === "migrator"
    ? "AGENTOPS_POSTGRES_MIGRATOR"
    : invocation.postgresProfile === "entitlement-admin"
      ? "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN"
      : "AGENTOPS_POSTGRES";
  const dsnFile =
    process.env[`${postgresPrefix}_DSN_SOURCE_FILE`]?.trim() ||
    process.env[`${postgresPrefix}_DSN_FILE`]?.trim() ||
    "";
  const passwordFile =
    process.env[`${postgresPrefix}_PASSWORD_SOURCE_FILE`]?.trim() ||
    process.env[`${postgresPrefix}_PASSWORD_FILE`]?.trim() ||
    "";
  if (dsnFile && passwordFile) {
    fail("postgres_secret_family_ambiguous");
  }
  if (dsnFile) {
    definitions.push({
      environmentName: `${postgresPrefix}_DSN_FILE`,
      sourcePath: dsnFile,
      targetName: invocation.postgresProfile === "migrator"
        ? "postgres_migrator_dsn"
        : invocation.postgresProfile === "entitlement-admin"
          ? "postgres_entitlement_admin_dsn"
          : "postgres_runtime_dsn",
    });
  } else {
    definitions.push({
      environmentName: `${postgresPrefix}_PASSWORD_FILE`,
      sourcePath: passwordFile || (
        invocation.postgresProfile === "migrator"
          ? "/run/secrets/postgres_migrator_password"
          : invocation.postgresProfile === "entitlement-admin"
            ? "/run/secrets/postgres_entitlement_admin_password"
            : "/run/secrets/postgres_runtime_password"
      ),
      targetName: invocation.postgresProfile === "migrator"
        ? "postgres_migrator_password"
        : invocation.postgresProfile === "entitlement-admin"
          ? "postgres_entitlement_admin_password"
          : "postgres_runtime_password",
    });
  }
  if (invocation.includeRuntimeRolePassword) {
    definitions.push({
      environmentName: "AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE",
      sourcePath:
        process.env.AGENTOPS_POSTGRES_RUNTIME_PASSWORD_SOURCE_FILE?.trim()
        || process.env.AGENTOPS_POSTGRES_RUNTIME_PASSWORD_FILE?.trim()
        || "/run/secrets/postgres_runtime_password",
      targetName: "postgres_runtime_role_password",
    });
  }
  if (invocation.includeEntitlementAdminPassword) {
    definitions.push({
      environmentName: "AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE",
      sourcePath:
        process.env
          .AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_SOURCE_FILE?.trim()
        || process.env.AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_FILE?.trim()
        || "/run/secrets/postgres_entitlement_admin_password",
      targetName: "postgres_entitlement_admin_role_password",
    });
  }
  if (invocation.includeEntitlementOperator) {
    definitions.push({
      environmentName: "AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD_FILE",
      sourcePath:
        process.env.AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD_SOURCE_FILE?.trim()
        || process.env.AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD_FILE?.trim()
        || "/run/secrets/entitlement_operator_password",
      targetName: "entitlement_operator_password",
    });
  }
  if (invocation.includeHumanSession) {
    definitions.push({
      environmentName: "AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE",
      sourcePath:
        process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE?.trim() ||
        process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_FILE?.trim() ||
        "/run/secrets/human_session_hmac_key",
      targetName: "human_session_hmac_key",
    });
  }

  const { prepared, runtimeDirectory } = prepareRuntimeSecrets({
    definitions,
    runtimePrefix: "/run/agentops-runtime-secrets/instance-",
    targetUid: NODE_UID,
    targetGid: NODE_GID,
  });
  Object.assign(process.env, prepared);
  delete process.env.AGENTOPS_POSTGRES_PASSWORD_SOURCE_FILE;
  delete process.env.AGENTOPS_POSTGRES_DSN_SOURCE_FILE;
  delete process.env.AGENTOPS_POSTGRES_MIGRATOR_PASSWORD_SOURCE_FILE;
  delete process.env.AGENTOPS_POSTGRES_MIGRATOR_DSN_SOURCE_FILE;
  delete process.env.AGENTOPS_POSTGRES_RUNTIME_PASSWORD_SOURCE_FILE;
  delete process.env
    .AGENTOPS_POSTGRES_ENTITLEMENT_ADMIN_PASSWORD_SOURCE_FILE;
  delete process.env.AGENTOPS_ENTITLEMENT_OPERATOR_PASSWORD_SOURCE_FILE;
  delete process.env.AGENTOPS_HUMAN_SESSION_HMAC_KEY_SOURCE_FILE;

  try {
    dropPrivilegesAndAssert();
    if (invocation.postgresProfile === "entitlement-admin") {
      await assertEntitlementChallengeApi({
        controlPlaneUrl:
          process.env.AGENTOPS_ENTITLEMENT_CONTROL_PLANE_URL?.trim() || "",
        controlPlaneOrigin:
          process.env.AGENTOPS_ENTITLEMENT_CONTROL_PLANE_ORIGIN?.trim() || "",
        command: invocation.command,
        operatorUsername:
          process.env.AGENTOPS_ENTITLEMENT_OPERATOR_USERNAME?.trim() || "",
      });
    }

    const child = spawn(invocation.command[0], invocation.command.slice(1), {
      env: process.env,
      stdio: "inherit",
    });
    const signalHandlers = new Map();
    const forwardedSignals = ["SIGINT", "SIGTERM", "SIGHUP"];
    for (const signal of forwardedSignals) {
      const handler = () => {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill(signal);
        }
      };
      signalHandlers.set(signal, handler);
      process.on(signal, handler);
    }
    const result = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    for (const [signal, handler] of signalHandlers) {
      process.removeListener(signal, handler);
    }
    rmSync(runtimeDirectory, { force: true, recursive: true });
    if (result.signal) {
      process.kill(process.pid, result.signal);
      return;
    }
    process.exitCode = result.code ?? 1;
  } catch (error) {
    rmSync(runtimeDirectory, { force: true, recursive: true });
    throw error;
  }
}

function isMain() {
  return process.argv[1] === fileURLToPath(import.meta.url);
}

if (isMain()) {
  main().catch((error) => {
    const code =
      typeof error?.code === "string" ? error.code : "initialization_failed";
    process.stderr.write(`byoc_secret_preflight_failed:${code}\n`);
    process.exitCode = 78;
  });
}
