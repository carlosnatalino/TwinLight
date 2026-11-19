import { describe, it, expect } from "vitest";
import {
  MCG_CSEP_SPEC,
  spectrumOf,
  type ConnectivityService,
} from "@/api/types";
import { TapiValueError } from "@/lib/yangNumbers";

/**
 * Assigned spectrum is an end-point augment in T-API v2.6.0, with band edges
 * in Hz — there is no `frequency-slot` leaf. The Services and Spectrum pages
 * both display it, so a drifting reader shows "no spectrum" on lightpaths
 * that do have it.
 */
function serviceWithBand(lowerHz: unknown, upperHz: unknown) {
  return {
    "end-point": [
      {
        "local-id": "a",
        "service-interface-point": { "service-interface-point-uuid": "s" },
        "layer-protocol-constraint": [
          {
            "local-id": "otsi",
            [MCG_CSEP_SPEC]: {
              "number-of-mc": "1",
              "mc-spectrum-config-pac": [
                {
                  "local-id": "1",
                  spectrum: {
                    "lower-frequency": lowerHz,
                    "upper-frequency": upperHz,
                  },
                },
              ],
            },
          },
        ],
      },
    ],
  } as unknown as ConnectivityService;
}

describe("assigned spectrum from the end-point augment", () => {
  it("reads the uint64 strings RFC 7951 §6.1 encodes the Hz edges as", () => {
    // 190.725 THz centre, 56.25 GHz wide — nine 6.25 GHz slots.
    const band = spectrumOf(
      serviceWithBand("190696875000000", "190753125000000"),
    );
    // Exact: the edges are far below 2^53, so the arithmetic is on integers.
    expect(band).toEqual({ centreThz: 190.725, widthGhz: 56.25 });
  });

  it("still reads the JSON numbers an earlier twin sent", () => {
    const band = spectrumOf(serviceWithBand(190_696_875_000_000, 190_753_125_000_000));
    expect(band).toEqual({ centreThz: 190.725, widthGhz: 56.25 });
  });

  it("does arithmetic, not string concatenation", () => {
    // "191…" + "191…" would be a 30-digit string; / 2 of it a wrong number.
    const band = spectrumOf(serviceWithBand("191278125000000", "191334375000000"));
    expect(band?.centreThz).toBeCloseTo(191.30625, 9);
    expect(band?.widthGhz).toBeCloseTo(56.25, 9);
  });

  it.each([
    ["an exponent", "1.9e14"],
    ["a fraction", "190696875000000.0"],
    ["an empty string", ""],
    ["a blank", " 190696875000000"],
    ["a word", "abc"],
    ["a negative", "-1"],
    ["a boolean", true],
    ["null", null],
    ["a fractional number", 190696875000000.5],
    ["a value beyond 2^53", "9007199254740993"],
  ])("throws on %s rather than showing no spectrum or NaN", (_what, bad) => {
    expect(() => spectrumOf(serviceWithBand(bad, "190753125000000"))).toThrow(TapiValueError);
    expect(() => spectrumOf(serviceWithBand("190696875000000", bad))).toThrow(TapiValueError);
  });

  it("throws on an empty band", () => {
    expect(() => spectrumOf(serviceWithBand("190753125000000", "190696875000000"))).toThrow(
      "not above",
    );
  });

  it("returns undefined when the service holds no spectrum", () => {
    const unallocated = {
      "end-point": [
        {
          "local-id": "a",
          "service-interface-point": { "service-interface-point-uuid": "s" },
          "layer-protocol-constraint": [{ "local-id": "otsi" }],
        },
      ],
    } as unknown as ConnectivityService;
    expect(spectrumOf(unallocated)).toBeUndefined();
    expect(spectrumOf({} as ConnectivityService)).toBeUndefined();
  });
});
