import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ServicesPage from "@/pages/ServicesPage";
import { useConnectionStore } from "@/store/connection";
import { useServicesStore } from "@/store/services";
import { useTopologyStore } from "@/store/topology";
import type { ConnectivityService, TapiNode, TopologyData } from "@/api/types";
import { withHostMeta } from "../helpers/hostMeta";
import { collectUncaughtErrors, installCytoscapeDom, wait } from "../helpers/cytoscapeDom";

// The detail panel asks the twin for the service's path; it may stay unknown.
const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = withHostMeta(mockFetch);

function tapiNode(id: string, sips: string[]): TapiNode {
  return {
    uuid: id,
    name: [{ "value-name": "node-name", value: id }],
    "layer-protocol-name": ["PHOTONIC_MEDIA"],
    "owned-node-edge-point": sips.map((sip) => ({
      uuid: `nep-${sip}`,
      name: [],
      "layer-protocol-name": "PHOTONIC_MEDIA",
      direction: "BIDIRECTIONAL",
      "mapped-service-interface-point": [{ "service-interface-point-uuid": sip }],
      "administrative-state": "UNLOCKED",
      "operational-state": "ENABLED",
      "lifecycle-state": "INSTALLED",
    })),
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED",
  } as TapiNode;
}

const TOPOLOGY: TopologyData = {
  topologies: [
    {
      uuid: "topo-1",
      name: [],
      "layer-protocol-name": ["PHOTONIC_MEDIA"],
      node: [tapiNode("site-a", ["sip-a"]), tapiNode("site-b", ["sip-b"]), tapiNode("site-c", ["sip-c"])],
      link: [],
    },
  ],
  sips: [],
};

function service(uuid: string, name: string, a: string, z: string): ConnectivityService {
  return {
    uuid,
    name: [{ "value-name": "service-name", value: name }],
    "end-point": [
      { "local-id": "a", "service-interface-point": { "service-interface-point-uuid": a } },
      { "local-id": "z", "service-interface-point": { "service-interface-point-uuid": z } },
    ],
    "administrative-state": "UNLOCKED",
    "operational-state": "ENABLED",
    "lifecycle-state": "INSTALLED",
  };
}

const deleteService = vi.fn(async (_baseUrl: string, uuid: string) => {
  useServicesStore.setState({
    services: useServicesStore.getState().services.filter((s) => s.uuid !== uuid),
  });
});

let uninstall: () => void;
beforeAll(() => {
  uninstall = installCytoscapeDom();
});
afterAll(() => uninstall());

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({
    ok: false,
    status: 404,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(""),
  });
  deleteService.mockClear();
  useConnectionStore.setState({ baseUrl: "http://localhost:8080" });
  useTopologyStore.setState({ data: TOPOLOGY, fetchTopology: vi.fn(async () => {}) });
});

function renderPage(services: ConnectivityService[]) {
  useServicesStore.setState({
    services,
    isLoading: false,
    error: null,
    fetchServices: vi.fn(async () => {}),
    deleteService,
  });
  const view = render(
    <MemoryRouter>
      <ServicesPage />
    </MemoryRouter>
  );
  return view;
}

async function deleteFromPanel(name: string) {
  fireEvent.click(screen.getByText(name));
  fireEvent.click(await screen.findByText("Delete service"));
  await act(async () => {
    fireEvent.click(screen.getByText("Confirm"));
  });
}

describe("ServicesPage graph teardown", () => {
  it("deleting the last service while the layout runs leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage([service("svc-1", "only one", "sip-a", "sip-b")]);
    expect(view.container.querySelectorAll("canvas").length).toBeGreaterThan(0);

    await act(() => wait(30));
    await deleteFromPanel("only one");
    await screen.findByText("Create a service to see the logical topology");
    expect(view.container.querySelectorAll("canvas")).toHaveLength(0);
    expect(screen.queryByText("Service Detail")).not.toBeInTheDocument();
    await act(() => wait(300));
    watch.stop();

    expect(deleteService).toHaveBeenCalledWith("http://localhost:8080", "svc-1");
    expect(watch.errors).toEqual([]);
  });

  it("deleting one of two services keeps the graph and leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage([
      service("svc-1", "first", "sip-a", "sip-b"),
      service("svc-2", "second", "sip-b", "sip-c"),
    ]);
    await act(() => wait(30));
    await deleteFromPanel("first");
    await waitFor(() => expect(screen.queryByText("first")).not.toBeInTheDocument());
    expect(screen.getByText("second")).toBeInTheDocument();
    expect(view.container.querySelectorAll("canvas").length).toBeGreaterThan(0);
    await act(() => wait(300));
    watch.stop();

    expect(watch.errors).toEqual([]);
  });

  it("leaving the page while the layout runs leaves no page error", async () => {
    const watch = collectUncaughtErrors();
    const view = renderPage([service("svc-1", "only one", "sip-a", "sip-b")]);
    await act(() => wait(30));
    view.unmount();
    await act(() => wait(300));
    watch.stop();

    expect(watch.errors).toEqual([]);
  });
});
