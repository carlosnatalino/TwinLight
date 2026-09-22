import { describe, it, expect } from "vitest";
import {
  OTSIA_CSEP_SPEC,
  modulationOf,
  tapiEndPoint,
  type ConnectivityService,
} from "@/api/types";

/**
 * T-API v2.6.0 has no modulation leaf on connectivity-service; the photonic
 * module augments the end-point. The twin refuses a top-level
 * `modulation-format`, so if these helpers drift the Add Service form
 * silently stops being able to create anything.
 */
describe("modulation as a T-API end-point augment", () => {
  it("builds the augment the twin expects", () => {
    const ep = tapiEndPoint("a-end", "sip-1", "DP-16QAM", "BIDIRECTIONAL");

    expect(ep["local-id"]).toBe("a-end");
    expect(ep["service-interface-point"]).toEqual({
      "service-interface-point-uuid": "sip-1",
    });
    expect(ep.direction).toBe("BIDIRECTIONAL");

    const spec = ep["layer-protocol-constraint"]![0][OTSIA_CSEP_SPEC]!;
    // ONF spells 16QAM as MT_DP-QAM16, not MT_DP-16QAM.
    expect(spec["otsi-config"][0].modulation).toEqual({
      "standard-modulation-technique": "MT_DP-QAM16",
    });
  });

  it("omits direction when none is given", () => {
    expect(tapiEndPoint("a", "sip-1", "DP-QPSK")).not.toHaveProperty(
      "direction",
    );
  });

  it("round-trips every supported format", () => {
    for (const fmt of ["DP-QPSK", "DP-16QAM", "DP-64QAM"] as const) {
      const svc = {
        "end-point": [tapiEndPoint("a", "sip-1", fmt)],
      } as unknown as ConnectivityService;
      expect(modulationOf(svc)).toBe(fmt);
    }
  });

  it("returns undefined when a service carries no augment", () => {
    const svc = {
      "end-point": [
        { "local-id": "a", "service-interface-point": { "service-interface-point-uuid": "s" } },
      ],
    } as unknown as ConnectivityService;
    expect(modulationOf(svc)).toBeUndefined();
    expect(modulationOf({} as ConnectivityService)).toBeUndefined();
  });
});
