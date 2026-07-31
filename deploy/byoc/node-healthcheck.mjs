import { dropPrivilegesAndAssert } from "./node-secret-entrypoint.mjs";

try {
  dropPrivilegesAndAssert();
  const response = await fetch("http://127.0.0.1:3001/api/mis/health");
  if (!response.ok) {
    throw new Error("health_not_ready");
  }
} catch {
  process.exitCode = 1;
}
