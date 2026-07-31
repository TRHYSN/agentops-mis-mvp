const NUMERIC_18_6 = /^([0-9]{1,12})(?:\.([0-9]{1,6}))?$/;

export function costUsdExact(value: string | null | undefined) {
  if (value !== null && value !== undefined && typeof value !== "string") {
    throw new Error("authoritative_cost_usd_string_required");
  }
  const normalized = (value ?? "0").trim();
  const match = NUMERIC_18_6.exec(normalized);
  if (!match) throw new Error("authoritative_cost_usd_invalid");
  return `${match[1]}.${(match[2] || "").padEnd(6, "0")}`;
}
