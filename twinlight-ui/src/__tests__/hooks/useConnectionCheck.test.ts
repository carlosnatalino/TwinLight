import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useConnectionCheck } from "@/hooks/useConnectionCheck";
import { useConnectionStore } from "@/store/connection";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = mockFetch;

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

beforeEach(() => {
  localStorage.clear();
  mockFetch.mockReset();
  useConnectionStore.setState({
    baseUrl: "http://dt",
    apiToken: "",
    pollInterval: 60,
    status: "disconnected",
  });
});

describe("useConnectionCheck", () => {
  it("is connected when /health and /internal/whoami both answer", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({ principal: null, authentication: "disabled" }));
    renderHook(() => useConnectionCheck());
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("connected"));
  });

  it("is unauthorized when the DT is up but refuses the token", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({}, 401));
    renderHook(() => useConnectionCheck());
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("unauthorized"));
  });

  it("sends the saved token on the probe", async () => {
    useConnectionStore.setState({ apiToken: "saved" });
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({ principal: "ui", authentication: "bearer-token" }));
    renderHook(() => useConnectionCheck());
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("connected"));
    expect(mockFetch.mock.calls[1][1].headers.Authorization).toBe("Bearer saved");
  });

  it("treats a DT without /internal/whoami as connected", async () => {
    mockFetch
      .mockResolvedValueOnce(respond({ status: "ok" }))
      .mockResolvedValueOnce(respond({}, 404));
    renderHook(() => useConnectionCheck());
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("connected"));
  });

  it("is disconnected when /health fails", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderHook(() => useConnectionCheck());
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    await waitFor(() => expect(useConnectionStore.getState().status).toBe("disconnected"));
  });
});
