import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildServiceRequest,
  serviceFormError,
  topologyConstraint,
  type ServiceForm,
} from "@/lib/serviceRequest";

// The backend suite (tests/test_api/test_ui_requests.py) posts these same
// fixtures to the API, placeholders replaced by real UUIDs.
function fixture(name: string): unknown {
  // Vitest runs from the UI project root.
  const path = resolve(process.cwd(), `src/__tests__/fixtures/add-service-request.${name}.json`);
  return JSON.parse(readFileSync(path, "utf-8"));
}

const node = (uid: string) => ({ "topology-uuid": "<TOPOLOGY>", "node-uuid": `<NODE ${uid}>` });

const defaults: ServiceForm = {
  name: "",
  aEndSip: "<SIP trx A>",
  zEndSip: "<SIP trx C>",
  modulation: null,
  centralFrequencyThz: null,
  launchPowerDbm: null,
  adminState: "UNLOCKED",
  lifecycleState: "PLANNED",
  constraints: { include: [], exclude: [] },
};

const full: ServiceForm = {
  ...defaults,
  name: "  A to C via B ",
  modulation: "DP-16QAM",
  centralFrequencyThz: 191.30625,
  launchPowerDbm: 1.5,
  constraints: { include: [node("roadm B")], exclude: [node("roadm D")] },
};

describe("buildServiceRequest", () => {
  it("sends only the end-points and states by default (automatic format)", () => {
    expect(buildServiceRequest(defaults)).toEqual(fixture("defaults"));
  });

  it("sends every option the form offers", () => {
    expect(buildServiceRequest(full)).toEqual(fixture("full"));
  });

  it("never sends BIDIRECTIONAL: the A-end is the SOURCE, the Z-end the SINK", () => {
    const body = buildServiceRequest(full)["tapi-connectivity:connectivity-service"];
    expect(body["end-point"].map((ep) => ep.direction)).toEqual(["SOURCE", "SINK"]);
  });

  it("rounds to integer Hz and hundredths of a dBm, sent as RFC 7951 strings", () => {
    const body = JSON.stringify(
      buildServiceRequest({ ...defaults, centralFrequencyThz: 193.1, launchPowerDbm: -2.345 }),
    );
    expect(body).toContain('"central-frequency":"193100000000000"');
    expect(body).toContain('"channel-output-power":"-235.0"');
  });

  it("sends zero power as the canonical decimal64 zero", () => {
    const body = JSON.stringify(buildServiceRequest({ ...defaults, launchPowerDbm: -0.001 }));
    expect(body).toContain('"channel-output-power":"0.0"');
  });
});

describe("serviceFormError", () => {
  it("accepts the full form", () => {
    expect(serviceFormError(full)).toBeNull();
  });

  it.each([
    [{ zEndSip: "" }, "Select both end-points"],
    [{ zEndSip: "<SIP trx A>" }, "must differ"],
    [{ launchPowerDbm: 25 }, "between -20 and 10 dBm"],
    [{ launchPowerDbm: Number.NaN }, "between -20 and 10 dBm"],
    [{ centralFrequencyThz: -1 }, "positive"],
    [{ centralFrequencyThz: 1e4 }, "too large"],
    [{ constraints: { include: [node("roadm B")], exclude: [node("roadm B")] } }, "both included and excluded"],
  ])("refuses %j", (change, message) => {
    expect(serviceFormError({ ...full, ...change } as ServiceForm)).toContain(message);
  });
});

describe("topologyConstraint", () => {
  it("is omitted without constraints", () => {
    expect(topologyConstraint({ include: [], exclude: [] })).toBeUndefined();
  });
});

describe("routableNodes", () => {
  it("offers the ROADMs, not the transceivers carrying SIPs, by name", async () => {
    const { routableNodes } = await import("@/lib/routableNodes");
    const nep = (sips: string[]) => ({
      uuid: "n",
      name: [],
      "layer-protocol-name": "PHOTONIC_MEDIA",
      direction: "BIDIRECTIONAL",
      "mapped-service-interface-point": sips.map((s) => ({ "service-interface-point-uuid": s })),
      "administrative-state": "UNLOCKED",
      "operational-state": "ENABLED",
      "lifecycle-state": "INSTALLED",
    });
    const node = (uuid: string, name: string, sips: string[]) => ({
      uuid,
      name: [{ "value-name": "node-name", value: name }],
      "layer-protocol-name": ["PHOTONIC_MEDIA"],
      "owned-node-edge-point": [nep(sips)],
      "administrative-state": "UNLOCKED",
      "operational-state": "ENABLED",
      "lifecycle-state": "INSTALLED",
    });
    const topology = {
      uuid: "T",
      node: [node("2", "roadm B", []), node("1", "trx A", ["sip"]), node("3", "roadm A", [])],
      link: [],
    };
    expect(routableNodes([topology] as never).map((n) => n.label)).toEqual(["roadm A", "roadm B"]);
  });
});
