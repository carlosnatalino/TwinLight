import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Core } from "cytoscape";
import TopologyPage from "@/pages/TopologyPage";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import type { TapiLink, TapiNode, TopologyData } from "@/api/types";
import { geographicPositions } from "@/lib/geo";
import { collectUncaughtErrors, installCytoscapeDom, wait } from "../helpers/cytoscapeDom";

function tapiNode(id: string, sip?: string): TapiNode {
  return {
    uuid: id,
    name: [{ "value-name": "node-name", value: id }],
    "layer-protocol-name": ["PHOTONIC_MEDIA"],
    "owned-node-edge-point": [
      {
        uuid: `nep-${id}`,
        name: [],
        "layer-protocol-name": "PHOTONIC_MEDIA",
        direction: "BIDIRECTIONAL",
        "mapped-service-interface-point": sip ? [{ "service-interface-point-uuid": sip }] : [],
        "administrative-state": "UNLOCKED",
        "operational-state": "ENABLED",
        "lifecycle-state": "INSTALLED",
      },
    ],
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED",
  };
}

function link(a: string, b: string): TapiLink {
  return {
    uuid: `${a}->${b}`,
    name: [],
    "layer-protocol-name": ["PHOTONIC_MEDIA"],
    "node-edge-point": [
      { "topology-uuid": "t", "node-uuid": a, "node-edge-point-uuid": `nep-${a}` },
      { "topology-uuid": "t", "node-uuid": b, "node-edge-point-uuid": `nep-${b}` },
    ],
    direction: "UNIDIRECTIONAL",
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED",
  };
}

const NODES = [
  tapiNode("roadm Seattle"),
  tapiNode("trx Seattle", "sip-sea"),
  tapiNode("roadm New_York"),
  tapiNode("roadm Miami"),
];
const SEATTLE = { latitude: 47.60621, longitude: -122.332071 };
const POSITIONS = {
  "roadm Seattle": SEATTLE,
  "trx Seattle": SEATTLE,
  "roadm New_York": { latitude: 40.712784, longitude: -74.005941 },
  "roadm Miami": { latitude: 25.76168, longitude: -80.19179 },
};

function data(extra: Partial<TopologyData> = {}): TopologyData {
  return {
    topologies: [
      {
        uuid: "t",
        name: [],
        "layer-protocol-name": ["PHOTONIC_MEDIA"],
        node: NODES,
        link: [
          link("roadm Seattle", "roadm New_York"),
          link("roadm New_York", "roadm Miami"),
          link("trx Seattle", "roadm Seattle"),
        ],
      },
    ],
    sips: [],
    ...extra,
  };
}

let uninstall: () => void;
beforeAll(() => {
  uninstall = installCytoscapeDom();
});
afterAll(() => uninstall());

beforeEach(() => {
  useConnectionStore.setState({ baseUrl: "http://localhost:8080" });
});

function renderPage(topology: TopologyData) {
  useTopologyStore.setState({ data: topology, isLoading: false, fetchTopology: vi.fn(async () => {}) });
  return render(
    <MemoryRouter>
      <TopologyPage />
    </MemoryRouter>,
  );
}

/** The page's Cytoscape core: Cytoscape registers it on its container. */
function coreOf(container: HTMLElement): Core {
  const host = [...container.querySelectorAll("div")].find(
    (d) => (d as unknown as { _cyreg?: { cy?: Core } })._cyreg?.cy,
  ) as unknown as { _cyreg: { cy: Core } };
  return host._cyreg.cy;
}

function layoutSelect(): HTMLSelectElement {
  return screen.getByRole("combobox") as HTMLSelectElement;
}

function optionValues(): string[] {
  return [...layoutSelect().options].map((o) => o.value);
}

describe("TopologyPage layouts", () => {
  it("draws the twin's positions by default, Seattle west of New York and north of Miami", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage(data({ positions: POSITIONS }));
    expect(layoutSelect().value).toBe("geographic");
    expect(optionValues()).toEqual(["geographic", "cose", "dagre", "grid", "breadthfirst", "circle"]);

    const cy = coreOf(view.container);
    const at = (id: string) => cy.getElementById(id).position();
    expect(at("roadm Seattle").x).toBeLessThan(at("roadm New_York").x);
    expect(at("roadm Seattle").y).toBeLessThan(at("roadm Miami").y);
    // The transceiver at the ROADM's coordinates is drawn apart from it, due north.
    expect(at("trx Seattle").x).toBeCloseTo(at("roadm Seattle").x, 9);
    expect(at("trx Seattle").y).toBeLessThan(at("roadm Seattle").y);

    const expected = geographicPositions(
      NODES.map((n) => ({
        id: n.uuid,
        type: n.uuid.startsWith("trx") ? "transceiver" : "roadm",
        label: n.uuid,
      })),
      POSITIONS,
    )!;
    for (const node of NODES) expect(at(node.uuid)).toEqual(expected[node.uuid]);

    // Another layout and back: the map comes back as it was.
    fireEvent.change(layoutSelect(), { target: { value: "grid" } });
    expect(layoutSelect().value).toBe("grid");
    await act(() => wait(450));
    expect(at("roadm Seattle")).not.toEqual(expected["roadm Seattle"]);
    fireEvent.change(layoutSelect(), { target: { value: "geographic" } });
    for (const node of NODES) expect(at(node.uuid)).toEqual(expected[node.uuid]);

    // The label and arrow toggles leave the nodes where they are.
    fireEvent.click(screen.getByTitle("Hide labels"));
    fireEvent.click(screen.getByTitle("Hide arrows"));
    for (const node of NODES) expect(at(node.uuid)).toEqual(expected[node.uuid]);

    view.unmount();
    await act(() => wait(100));
    watch.stop();
    expect(watch.errors).toEqual([]);
  });

  it("behaves as before when the twin gives no positions", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage(data());
    expect(layoutSelect().value).toBe("cose");
    expect(optionValues()).not.toContain("geographic");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    view.unmount();
    await act(() => wait(300));
    watch.stop();
    expect(watch.errors).toEqual([]);
  });

  it("says so when the positions could not be read, and draws the graph without them", () => {
    const message = 'device=d/equipment=e/geolocation/latitude: expected a decimal64 string, got "N"';
    renderPage(data({ positionsError: message }));
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(layoutSelect().value).toBe("cose");
  });

  it("leaving the page while switching away from the map leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage(data({ positions: POSITIONS }));
    fireEvent.change(layoutSelect(), { target: { value: "cose" } });
    await act(() => wait(30));
    view.unmount();
    await act(() => wait(300));
    watch.stop();
    expect(watch.errors).toEqual([]);
  });
});
