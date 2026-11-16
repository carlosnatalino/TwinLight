import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  TapiApiClient,
  ApiError,
  NetworkError,
  isAuthError,
  setTokenProvider,
} from "@/api/client";

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

  it("computes ranked paths through the internal endpoint", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ path: [] }));
    const input = {
      "end-point": [
        { "service-interface-point": { "service-interface-point-uuid": "a" } },
        { "service-interface-point": { "service-interface-point-uuid": "z" } },
      ],
      "max-candidates": 2,
    };
    const result = await client.computePath(input);
    expect(result.path).toEqual([]);
    const [url, init] = mockFetch.mock.calls[0];
    // T-API v2.6.0 has no compute-path RPC; the ranked view is internal.
    expect(url).toBe("http://localhost:8080/internal/compute-path");
    expect(JSON.parse(init.body)).toEqual(input);
  });
});

describe("TapiApiClient authentication", () => {
  afterEach(() => setTokenProvider(() => undefined));

  function sentHeaders(call = 0): Record<string, string> {
    return mockFetch.mock.calls[call][1].headers as Record<string, string>;
  }

  it("sends no Authorization header without a token", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ status: "ok" }));
    await new TapiApiClient("http://dt").health();
    expect(sentHeaders()).not.toHaveProperty("Authorization");
  });

  it("sends the constructor token as a bearer token", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({}));
    await new TapiApiClient("http://dt", "tok-123").getContext();
    expect(sentHeaders().Authorization).toBe("Bearer tok-123");
  });

  it("falls back to the registered token provider", async () => {
    setTokenProvider(() => "saved-token");
    mockFetch.mockResolvedValueOnce(makeResponse({}));
    await new TapiApiClient("http://dt").getContext();
    expect(sentHeaders().Authorization).toBe("Bearer saved-token");
  });

  it("lets an explicit empty token override the saved one", async () => {
    setTokenProvider(() => "saved-token");
    mockFetch.mockResolvedValueOnce(makeResponse({ status: "ok" }));
    await new TapiApiClient("http://dt", "").health();
    expect(sentHeaders()).not.toHaveProperty("Authorization");
  });

  it("keeps Content-Type alongside Authorization on writes", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({}));
    await new TapiApiClient("http://dt", "tok-123").createConnectivityService(
      {} as never
    );
    expect(sentHeaders()).toMatchObject({
      Authorization: "Bearer tok-123",
      "Content-Type": "application/yang-data+json",
    });
  });

  it("calls /internal/whoami", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({ principal: "ui", authentication: "bearer-token" })
    );
    const who = await new TapiApiClient("http://dt", "tok").whoami();
    expect(mockFetch.mock.calls[0][0]).toBe("http://dt/internal/whoami");
    expect(who.principal).toBe("ui");
  });

  it("turns a 401 into a message the user can act on", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({ "ietf-restconf:errors": { error: [] } }, 401)
    );
    const err = await new TapiApiClient("http://dt").getContext().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toBe("HTTP 401: The DT requires a valid API token. Set one in Settings.");
  });

  it("keeps the raw body for other errors, which pages parse", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse({ detail: "busy" }, 409));
    const err = await new TapiApiClient("http://dt").getContext().catch((e) => e);
    expect(err.message).toBe('HTTP 409: {"detail":"busy"}');
  });

  it("classifies 401 and 403 as authentication errors", () => {
    expect(isAuthError(new ApiError(401, "x"))).toBe(true);
    expect(isAuthError(new ApiError(403, "x"))).toBe(true);
    expect(isAuthError(new ApiError(404, "x"))).toBe(false);
    expect(isAuthError(new NetworkError("x"))).toBe(false);
  });
});
