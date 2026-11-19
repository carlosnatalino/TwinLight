import { describe, expect, it } from "vitest";
import {
  formatDecimal64Integer,
  formatUint64,
  parseDecimal64,
  parseUint64,
  TapiValueError,
} from "@/lib/yangNumbers";

describe("RFC 7951 §6.1 64-bit values", () => {
  it("parses a uint64 string exactly", () => {
    expect(parseUint64("191278125000000", "f")).toBe(191_278_125_000_000);
    expect(parseUint64("0", "f")).toBe(0);
    expect(parseUint64("+42", "f")).toBe(42); // RFC 7950 §9.2.1 allows the sign
    expect(parseUint64(42, "f")).toBe(42); // an earlier twin's JSON number
  });

  it.each(["", "1e14", "1.0", "0x10", " 1", "-1", "abc", "9007199254740993"])(
    "refuses the uint64 %j",
    (bad) => {
      expect(() => parseUint64(bad, "f")).toThrow(TapiValueError);
    },
  );

  it.each([1.5, Number.NaN, true, null, undefined, {}])("refuses the uint64 value %j", (bad) => {
    expect(() => parseUint64(bad, "f")).toThrow(TapiValueError);
  });

  it("names the leaf in the error", () => {
    expect(() => parseUint64("x", "spectrum/lower-frequency")).toThrow(
      'spectrum/lower-frequency: expected a uint64 string, got "x"',
    );
  });

  it("parses a decimal64 string", () => {
    expect(parseDecimal64("-234.5", "p")).toBe(-234.5);
    expect(parseDecimal64("150", "p")).toBe(150);
    expect(parseDecimal64(150.5, "p")).toBe(150.5);
    for (const bad of ["", "1e2", "NaN", ".5", "5.", true, null]) {
      expect(() => parseDecimal64(bad, "p")).toThrow(TapiValueError);
    }
  });

  it("formats the canonical forms (RFC 7950 §9.2.2, §9.3.2)", () => {
    expect(formatUint64(193_100_000_000_000)).toBe("193100000000000");
    expect(formatDecimal64Integer(-235)).toBe("-235.0");
    expect(formatDecimal64Integer(0)).toBe("0.0");
    expect(formatDecimal64Integer(-0)).toBe("0.0");
    expect(() => formatUint64(2 ** 53)).toThrow(TapiValueError);
    expect(() => formatUint64(-1)).toThrow(TapiValueError);
    expect(() => formatDecimal64Integer(1.5)).toThrow(TapiValueError);
  });
});
