import { describe, it, expect, vi, beforeEach } from "vitest";
import { withHostMeta } from "../helpers/hostMeta";
import { useServicesStore } from "@/store/services";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = withHostMeta(mockFetch);

function respond(body: unknown) {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

beforeEach(() => {
  mockFetch.mockReset();
  useServicesStore.setState({
    services: [],
    isLoading: false,
    error: null,
    lastFetchedAt: null,
  });
});

describe("fetchServices", () => {
  it("keeps the services of the connectivity-service list", async () => {
    const services = [{ uuid: "svc-1" }, { uuid: "svc-2" }];
    mockFetch.mockResolvedValueOnce(
      respond({ "tapi-connectivity:connectivity-service": services })
    );

    await useServicesStore.getState().fetchServices("http://twin");

    const state = useServicesStore.getState();
    expect(state.services).toEqual(services);
    expect(state.error).toBeNull();
    expect(mockFetch.mock.calls[0][0]).toBe(
      "http://twin/restconf/data/tapi-common:context" +
        "/tapi-connectivity:connectivity-context/connectivity-service"
    );
  });

  it("reports a body without the list as an error, not as no services", async () => {
    useServicesStore.setState({ services: [{ uuid: "svc-0" }] as never });
    mockFetch.mockResolvedValueOnce(
      respond({
        "tapi-connectivity:connectivity-context": {
          "connectivity-service": [{ uuid: "svc-1" }],
        },
      })
    );

    await useServicesStore.getState().fetchServices("http://twin");

    const state = useServicesStore.getState();
    expect(state.error).toContain("tapi-connectivity:connectivity-service");
    expect(state.services).toEqual([{ uuid: "svc-0" }]);
  });
});
