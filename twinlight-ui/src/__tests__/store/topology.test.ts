import { describe, it, expect, vi, beforeEach } from "vitest";
import { withHostMeta } from "../helpers/hostMeta";
import { useTopologyStore } from "@/store/topology";
import { cacheRead, CACHE_KEYS } from "@/lib/cache";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = withHostMeta(mockFetch);

const topology = { uuid: "topo-1", node: [{ uuid: "node-1" }], link: [] };
const sip = { uuid: "sip-1" };

// A full-context response as the twin serves it: besides the SIPs and the
// topology context it carries every other augment of /tapi-common:context.
const fullContext = {
  "tapi-common:context": {
    uuid: "tapi-context",
    "service-interface-point": [sip],
    "tapi-topology:topology-context": { topology: [topology] },
    "tapi-connectivity:connectivity-context": {
      "connectivity-service": [{ uuid: "svc-1" }],
    },
    "tapi-path-computation:path-computation-context": {
      "path-comp-service": [],
      path: [],
    },
    "tapi-equipment:physical-context": {
      device: [{ uuid: "device-1" }],
      "physical-span": [{ uuid: "span-1" }],
    },
    "tapi-oam:oam-context": { "oam-job": [{ uuid: "job-1" }] },
    "tapi-streaming:stream-context": { "available-stream": [] },
  },
};

function respond(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

beforeEach(() => {
  localStorage.clear();
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(respond(fullContext));
  useTopologyStore.setState({ data: null, fetchedAt: null, error: null, isStale: false });
});

describe("fetchTopology", () => {
  it("keeps only the topologies and SIPs of the full context", async () => {
    await useTopologyStore.getState().fetchTopology("http://twin");

    expect(useTopologyStore.getState().data).toEqual({
      topologies: [topology],
      sips: [sip],
    });
  });

  it("caches only the topologies and SIPs", async () => {
    await useTopologyStore.getState().fetchTopology("http://twin");

    expect(cacheRead(CACHE_KEYS.TOPOLOGY)?.data).toEqual({
      topologies: [topology],
      sips: [sip],
    });
    const raw = localStorage.getItem(CACHE_KEYS.TOPOLOGY) ?? "";
    for (const section of [
      "tapi-common:context",
      "tapi-connectivity:connectivity-context",
      "tapi-equipment:physical-context",
      "tapi-oam:oam-context",
    ]) {
      expect(raw).not.toContain(section);
    }
  });

  it("still loads an entry cached with the old context field, then replaces it", async () => {
    localStorage.setItem(
      CACHE_KEYS.TOPOLOGY,
      JSON.stringify({
        v: 1,
        fetchedAt: Date.now(),
        data: {
          context: fullContext["tapi-common:context"],
          topologies: [topology],
          sips: [sip],
        },
      })
    );
    expect(useTopologyStore.getState().hydrateFromCache()).toBe(true);
    expect(useTopologyStore.getState().getTopologies()).toEqual([topology]);
    expect(useTopologyStore.getState().getSips()).toEqual([sip]);

    await useTopologyStore.getState().fetchTopology("http://twin");
    expect(cacheRead(CACHE_KEYS.TOPOLOGY)?.data).not.toHaveProperty("context");
  });
});

describe("fetchTopology: node positions", () => {
  const located = {
    ...topology,
    node: [
      {
        uuid: "node-1",
        "tapi-equipment:device-supports-node": { "supporting-device": [{ "device-uuid": "device-1" }] },
      },
    ],
  };
  function contextWith(geolocation: unknown) {
    const ctx = fullContext["tapi-common:context"];
    return {
      "tapi-common:context": {
        ...ctx,
        "tapi-topology:topology-context": { topology: [located] },
        "tapi-equipment:physical-context": {
          device: [{ uuid: "device-1", equipment: [{ uuid: "eq-1", geolocation }] }],
          "physical-span": [],
        },
      },
    };
  }

  it("keeps the position of each node, and only that, of the physical context", async () => {
    mockFetch.mockResolvedValue(respond(contextWith({ latitude: "47.60621", longitude: "-122.332071" })));
    await useTopologyStore.getState().fetchTopology("http://twin");

    const data = useTopologyStore.getState().data;
    expect(data?.positions).toEqual({ "node-1": { latitude: 47.60621, longitude: -122.332071 } });
    expect(data?.positionsError).toBeUndefined();
    expect(cacheRead(CACHE_KEYS.TOPOLOGY)?.data).toEqual(data);
    expect(localStorage.getItem(CACHE_KEYS.TOPOLOGY)).not.toContain("tapi-equipment:physical-context");
  });

  it("loads the topology and reports a position that does not parse", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mockFetch.mockResolvedValue(respond(contextWith({ latitude: "47.6", longitude: "far west" })));
    await useTopologyStore.getState().fetchTopology("http://twin");

    const state = useTopologyStore.getState();
    expect(state.error).toBeNull();
    expect(state.data?.topologies).toEqual([located]);
    expect(state.data?.positions).toBeUndefined();
    expect(state.data?.positionsError).toContain('geolocation/longitude: expected a decimal64 string, got "far west"');
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("has no positions for a twin that serves none", async () => {
    await useTopologyStore.getState().fetchTopology("http://twin");
    expect(useTopologyStore.getState().data).not.toHaveProperty("positions");
    expect(useTopologyStore.getState().data).not.toHaveProperty("positionsError");
  });
});
