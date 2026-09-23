import { describe, it, expect } from "vitest";
import {
  MCG_CSEP_SPEC,
  spectrumOf,
  type ConnectivityService,
} from "@/api/types";

/**
 * Assigned spectrum is an end-point augment in T-API v2.6.0, with band edges
 * in Hz — there is no `frequency-slot` leaf. The Services and Spectrum pages
 * both display it, so a drifting reader shows "no spectrum" on lightpaths
 * that do have it.
 */
function serviceWithBand(lowerHz: number, upperHz: number) {
  return {
    "end-point": [
      {
        "local-id": "a",
        "service-interface-point": { "service-interface-point-uuid": "s" },
        "layer-protocol-constraint": [
          {
            "local-id": "otsi",
            [MCG_CSEP_SPEC]: {
              "number-of-mc": 1,
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
  it("converts Hz edges to a centre in THz and a width in GHz", () => {
    // 190.725 THz centre, 56.25 GHz wide — nine 6.25 GHz slots.
    const band = spectrumOf(
      serviceWithBand(190_696_875_000_000, 190_753_125_000_000),
    );
    expect(band?.centreThz).toBeCloseTo(190.725, 6);
    expect(band?.widthGhz).toBeCloseTo(56.25, 6);
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
