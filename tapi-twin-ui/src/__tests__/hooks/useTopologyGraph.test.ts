import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useTopologyGraph } from "@/hooks/useTopologyGraph";
import type { Topology } from "@/api/types";

function makeTopology(overrides: Partial<Topology> = {}): Topology {
  return {
    uuid: "topo-1",
    name: [{ "value-name": "topology-name", value: "Test Topology" }],
    "layer-protocol-name": ["PHOTONIC_MEDIA"],
    node: [
      {
        uuid: "node-1",
        name: [{ "value-name": "node-name", value: "Transceiver A" }],
        "layer-protocol-name": ["PHOTONIC_MEDIA"],
        "owned-node-edge-point": [
          {
            uuid: "nep-1",
            name: [],
            "layer-protocol-name": "PHOTONIC_MEDIA",
            direction: "BIDIRECTIONAL",
            "mapped-service-interface-point": [{ "service-interface-point-uuid": "sip-1" }],
            "administrative-state": "UNLOCKED",
            "operational-state": "ENABLED",
            "lifecycle-state": "INSTALLED",
          },
        ],
        "administrative-state": "UNLOCKED",
        "operational-state": "ENABLED",
        "lifecycle-state": "INSTALLED",
      },
      {
        uuid: "node-2",
        name: [{ "value-name": "node-name", value: "ROADM B" }],
        "layer-protocol-name": ["PHOTONIC_MEDIA"],
        "owned-node-edge-point": [
          {
            uuid: "nep-2",
            name: [],
            "layer-protocol-name": "PHOTONIC_MEDIA",
            direction: "BIDIRECTIONAL",
            "mapped-service-interface-point": [],
            "administrative-state": "UNLOCKED",
            "operational-state": "ENABLED",
            "lifecycle-state": "INSTALLED",
          },
        ],
        "administrative-state": "UNLOCKED",
        "operational-state": "ENABLED",
        "lifecycle-state": "INSTALLED",
      },
    ],
    link: [
      {
        uuid: "link-1",
        name: [{ "value-name": "link-name", value: "A→B" }],
        "layer-protocol-name": ["PHOTONIC_MEDIA"],
        "node-edge-point": [
          { "topology-uuid": "topo-1", "node-uuid": "node-1", "node-edge-point-uuid": "nep-1" },
          { "topology-uuid": "topo-1", "node-uuid": "node-2", "node-edge-point-uuid": "nep-2" },
        ],
        direction: "UNIDIRECTIONAL",
        "administrative-state": "UNLOCKED",
        "operational-state": "ENABLED",
        "lifecycle-state": "INSTALLED",
      },
    ],
    ...overrides,
  };
}

describe("useTopologyGraph", () => {
  it("returns nodes and edges from topology", () => {
    const { result } = renderHook(() => useTopologyGraph([makeTopology()], []));
    const elements = result.current;

    const nodes = elements.filter((e) => !e.data.source);
    const edges = elements.filter((e) => e.data.source);

    expect(nodes).toHaveLength(2);
    expect(edges).toHaveLength(1);
  });

  it("classifies transceiver vs roadm correctly", () => {
    const { result } = renderHook(() => useTopologyGraph([makeTopology()], []));
    const elements = result.current;

    const transceiver = elements.find((e) => e.data.id === "node-1");
    const roadm = elements.find((e) => e.data.id === "node-2");

    expect(transceiver?.data.type).toBe("transceiver");
    expect(roadm?.data.type).toBe("roadm");
  });

  it("maps edge source/target to node UUIDs", () => {
    const { result } = renderHook(() => useTopologyGraph([makeTopology()], []));
    const edge = result.current.find((e) => e.data.source);

    expect(edge?.data.source).toBe("node-1");
    expect(edge?.data.target).toBe("node-2");
  });

  it("returns empty array for empty topologies", () => {
    const { result } = renderHook(() => useTopologyGraph([], []));
    expect(result.current).toHaveLength(0);
  });

  it("skips links with fewer than 2 refs", () => {
    const topo = makeTopology({
      link: [
        {
          uuid: "link-bad",
          name: [],
          "layer-protocol-name": ["PHOTONIC_MEDIA"],
          "node-edge-point": [
            { "topology-uuid": "topo-1", "node-uuid": "node-1", "node-edge-point-uuid": "nep-1" },
          ],
          direction: "UNIDIRECTIONAL",
          "administrative-state": "UNLOCKED",
          "operational-state": "ENABLED",
          "lifecycle-state": "INSTALLED",
        },
      ],
    });
    const { result } = renderHook(() => useTopologyGraph([topo], []));
    const edges = result.current.filter((e) => e.data.source);
    expect(edges).toHaveLength(0);
  });
});
