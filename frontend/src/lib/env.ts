import type { EnvVar, EnvVarInput } from "../api/types";

export interface EnvRow {
  key: string;
  value: string;
  isSecret: boolean;
  /** True when the server already holds a secret value for this key. */
  hasStoredSecret: boolean;
}

const KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const RESERVED_KEYS = new Set(["PORT"]);

export function rowsFromEnv(variables: EnvVar[]): EnvRow[] {
  return variables.map((variable) => ({
    key: variable.key,
    value: variable.value ?? "",
    isSecret: variable.is_secret,
    hasStoredSecret: variable.is_secret,
  }));
}

export function emptyRow(): EnvRow {
  return { key: "", value: "", isSecret: false, hasStoredSecret: false };
}

/**
 * A stored secret whose field was left blank is sent as null, which tells the API to
 * keep the existing value. The browser never has that value to send back.
 */
export function toEnvPayload(rows: EnvRow[]): EnvVarInput[] {
  return rows.map((row) => ({
    key: row.key.trim(),
    value: row.isSecret && row.hasStoredSecret && row.value === "" ? null : row.value,
    is_secret: row.isSecret,
  }));
}

export function validateRows(rows: EnvRow[]): string | null {
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    if (!KEY_PATTERN.test(key)) {
      return `"${key}" is not a valid name. Use letters, digits and underscores, not starting with a digit.`;
    }
    if (RESERVED_KEYS.has(key)) {
      return `${key} is set by the platform.`;
    }
    if (seen.has(key)) {
      return `${key} is defined twice.`;
    }
    if (row.hasStoredSecret && !row.isSecret && row.value === "") {
      return `${key}: enter the value again to turn a secret into a plain variable.`;
    }
    seen.add(key);
  }
  return null;
}
