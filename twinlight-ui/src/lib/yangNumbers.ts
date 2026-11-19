/**
 * YANG 64-bit numbers in T-API bodies (RFC 7951 §6.1).
 *
 * "A value of the "int64", "uint64", or "decimal64" type is represented as
 * a JSON string whose content is the lexical representation of the
 * corresponding YANG type". T-API v2.6.0 types every numeric leaf that way,
 * so the frequencies, counts and powers the twin returns are strings —
 * `"191278125000000"` — and the requests the UI builds send them as strings.
 *
 * Reading them into a JavaScript `number` is exact only below 2^53
 * (`Number.MAX_SAFE_INTEGER`, about 9.007e15). The frequencies are Hz in the
 * C band, about 1.9e14, well inside that; a value beyond it is refused here
 * rather than silently rounded. Anything that is not a well-formed value
 * throws `TapiValueError`: never `NaN`, `0`, a concatenated string, or a
 * missing spectrum.
 */

export class TapiValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TapiValueError";
  }
}

// RFC 7950 §9.2.1 and §9.3.1: an optional sign, decimal digits and, for
// decimal64, a period followed by decimal digits. [0-9], not \d.
const INTEGER = /^[+-]?[0-9]+$/;
const DECIMAL = /^[+-]?[0-9]+(\.[0-9]+)?$/;

function describe(value: unknown): string {
  return typeof value === "string" ? JSON.stringify(value) : String(value);
}

/**
 * A uint64 leaf as a number: the standard string, or the JSON number an
 * earlier twin sent. Throws for anything else, or for a value too large to
 * hold exactly.
 */
export function parseUint64(value: unknown, where: string): number {
  let n: number;
  if (typeof value === "string" && INTEGER.test(value)) {
    n = Number(value);
  } else if (typeof value === "number" && Number.isInteger(value)) {
    n = value;
  } else {
    throw new TapiValueError(`${where}: expected a uint64 string, got ${describe(value)}`);
  }
  if (n < 0) {
    throw new TapiValueError(`${where}: a uint64 cannot be negative, got ${describe(value)}`);
  }
  if (!Number.isSafeInteger(n)) {
    throw new TapiValueError(
      `${where}: ${describe(value)} is beyond 2^53 and cannot be held exactly`,
    );
  }
  return n;
}

/** A decimal64 leaf as a number: the standard string, or a JSON number. */
export function parseDecimal64(value: unknown, where: string): number {
  if (typeof value === "string" && DECIMAL.test(value)) return Number(value);
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new TapiValueError(`${where}: expected a decimal64 string, got ${describe(value)}`);
}

/** The canonical uint64 string (RFC 7950 §9.2.2) of a safe integer. */
export function formatUint64(n: number): string {
  if (!Number.isSafeInteger(n) || n < 0) {
    throw new TapiValueError(`not a uint64 the UI can send exactly: ${n}`);
  }
  // String() of a safe integer is plain decimal digits: no exponent below 1e21.
  return String(n);
}

/**
 * The canonical decimal64 string (RFC 7950 §9.3.2) of an integer, e.g. a
 * launch power in 0.01 dBm: "-235.0". "The decimal point is required ...
 * The value zero is represented as "0.0"."
 */
export function formatDecimal64Integer(n: number): string {
  if (!Number.isSafeInteger(n)) {
    throw new TapiValueError(`not an integer the UI can send exactly: ${n}`);
  }
  return `${n === 0 ? 0 : n}.0`;
}
