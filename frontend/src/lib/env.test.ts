import { describe, expect, it } from "vitest";
import { emptyRow, rowsFromEnv, toEnvPayload, validateRows } from "./env";
import type { EnvRow } from "./env";

function row(overrides: Partial<EnvRow>): EnvRow {
  return { ...emptyRow(), ...overrides };
}

describe("rowsFromEnv", () => {
  it("shows secrets as blank fields that are marked as stored", () => {
    const rows = rowsFromEnv([
      { key: "LOG_LEVEL", value: "debug", is_secret: false },
      { key: "DB_PASSWORD", value: null, is_secret: true },
    ]);
    expect(rows).toEqual([
      { key: "LOG_LEVEL", value: "debug", isSecret: false, hasStoredSecret: false },
      { key: "DB_PASSWORD", value: "", isSecret: true, hasStoredSecret: true },
    ]);
  });
});

describe("toEnvPayload", () => {
  it("sends null for an untouched stored secret so the server keeps it", () => {
    const payload = toEnvPayload([row({ key: "DB_PASSWORD", isSecret: true, hasStoredSecret: true })]);
    expect(payload).toEqual([{ key: "DB_PASSWORD", value: null, is_secret: true }]);
  });

  it("sends the new value when a stored secret is replaced", () => {
    const payload = toEnvPayload([
      row({ key: "DB_PASSWORD", value: "rotated", isSecret: true, hasStoredSecret: true }),
    ]);
    expect(payload[0]?.value).toBe("rotated");
  });

  it("sends an empty string for a new secret or a plain variable left blank", () => {
    const payload = toEnvPayload([
      row({ key: "NEW_SECRET", isSecret: true }),
      row({ key: "EMPTY_PLAIN" }),
    ]);
    expect(payload.map((item) => item.value)).toEqual(["", ""]);
  });

  it("trims whitespace around names", () => {
    expect(toEnvPayload([row({ key: "  API_URL ", value: "x" })])[0]?.key).toBe("API_URL");
  });
});

describe("validateRows", () => {
  it("accepts a valid set", () => {
    expect(validateRows([row({ key: "A", value: "1" }), row({ key: "_B2", value: "2" })])).toBeNull();
  });

  it.each(["", "1ABC", "HAS-DASH", "HAS SPACE"])("rejects the name %j", (key) => {
    expect(validateRows([row({ key })])).toMatch(/not a valid name/);
  });

  it("rejects duplicates", () => {
    expect(validateRows([row({ key: "A" }), row({ key: "A" })])).toMatch(/defined twice/);
  });

  it("rejects names the platform sets itself", () => {
    expect(validateRows([row({ key: "PORT", value: "1" })])).toMatch(/set by the platform/);
  });

  it("requires the value again when a stored secret is made readable", () => {
    const rows = [row({ key: "DB_PASSWORD", isSecret: false, hasStoredSecret: true })];
    expect(validateRows(rows)).toMatch(/enter the value again/);
  });
});
