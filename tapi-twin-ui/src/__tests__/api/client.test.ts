import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { TapiApiClient, ApiError, NetworkError } from "@/api/client";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = mockFetch;

function makeResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    statusText: "OK",
  };
}

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TapiApiClient", () => {
  const client = new TapiApiClient("http://localhost:8080");

  it("calls /health and returns result", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ status: "ok" }));
    const result = await client.health();
    expect(result.status).toBe("ok");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8080/health",
      expect.objectContaining({ headers: expect.any(Object) })
    );
  });

  it("throws ApiError on 4xx", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ detail: "not found" }, 404));
    await expect(client.getContext()).rejects.toBeInstanceOf(ApiError);
  });

  it("throws NetworkError on fetch failure", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(client.health()).rejects.toBeInstanceOf(NetworkError);
  });

  it("strips trailing slash from base URL", () => {
    const c = new TapiApiClient("http://localhost:8080/");
    mockFetch.mockResolvedValueOnce(makeResponse({ status: "ok" }));
    void c.health();
    expect(mockFetch.mock.calls[0][0]).toBe("http://localhost:8080/health");
  });

  it("calls /internal/opm and returns result", async () => {
    const opmResp = { services: [], timestamp: 1234567890 };
    mockFetch.mockResolvedValueOnce(makeResponse(opmResp));
    const result = await client.getAllOpm();
    expect(result.services).toEqual([]);
  });
});
