import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { hostMetaResponse, withHostMeta, xrd } from "../helpers/hostMeta";
import {
  TapiApiClient,
  ApiError,
  NetworkError,
  isAuthError,
  setTokenProvider,
  createClient,
  parseRestconfRoot,
  resetRestconfRoots,
  errorDetail,
} from "@/api/client";

const mockFetch = vi.fn();
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).fetch = withHostMeta(mockFetch);

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

  it("reads the connectivity-service list from its standard body", async () => {
    const services = [{ uuid: "svc-1" }, { uuid: "svc-2" }];
    mockFetch.mockResolvedValueOnce(
      makeResponse({ "tapi-connectivity:connectivity-service": services })
    );
    expect(await client.getServices()).toEqual(services);
    expect(mockFetch.mock.calls[0][0]).toBe(
      "http://localhost:8080/restconf/data/tapi-common:context" +
        "/tapi-connectivity:connectivity-context/connectivity-service"
    );
  });

  it("reads an empty connectivity-service list as no services", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({ "tapi-connectivity:connectivity-service": [] })
    );
    expect(await client.getServices()).toEqual([]);
  });

  it("rejects a list body without the list rather than reporting no services", async () => {
    // The container's envelope, which the list GET answered with before.
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        "tapi-connectivity:connectivity-context": {
          "connectivity-service": [{ uuid: "svc-1" }],
        },
      })
    );
    await expect(client.getServices()).rejects.toThrow(
      "tapi-connectivity:connectivity-service"
    );
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
    mockFetch.mockResolvedValueOnce(
      makeResponse({ "tapi-connectivity:connectivity-service": [{ uuid: "svc-1" }] }, 201)
    );
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

describe("connectivity-service entry bodies (RFC 7951 §5.4)", () => {
  const entry = (body: unknown, status = 200) => {
    mockFetch.mockResolvedValueOnce(makeResponse(body, status));
  };

  it("returns the created service from its array of one", async () => {
    entry({ "tapi-connectivity:connectivity-service": [{ uuid: "svc-1" }] }, 201);
    const created = await new TapiApiClient("http://dt").createConnectivityService({} as never);
    expect(created.uuid).toBe("svc-1");
  });

  it("returns the patched service from its array of one", async () => {
    entry({ "tapi-connectivity:connectivity-service": [{ uuid: "svc-1", "lifecycle-state": "PLANNED" }] });
    const patched = await new TapiApiClient("http://dt").updateService("svc-1", {
      "tapi-connectivity:connectivity-service": { "lifecycle-state": "PLANNED" },
    });
    expect(patched["lifecycle-state"]).toBe("PLANNED");
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe(
      "http://dt/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=svc-1"
    );
    expect(init.method).toBe("PATCH");
  });

  it.each([
    ["an object, the earlier body", { "tapi-connectivity:connectivity-service": { uuid: "svc-1" } }],
    ["an empty list", { "tapi-connectivity:connectivity-service": [] }],
    ["no member at all", {}],
  ])("refuses %s rather than guessing", async (_what, body) => {
    entry(body, 201);
    await expect(
      new TapiApiClient("http://dt").createConnectivityService({} as never)
    ).rejects.toThrow("tapi-connectivity:connectivity-service");
  });
});

describe("RESTCONF root discovery", () => {
  // Every request, host-meta included, with the answer the test sets per URL.
  const calls: { url: string; init?: RequestInit }[] = [];
  let hostMeta: () => unknown;
  let dataBody: unknown;

  beforeEach(() => {
    calls.length = 0;
    resetRestconfRoots();
    hostMeta = () => hostMetaResponse("/restconf");
    dataBody = {};
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).fetch = (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(
        url.endsWith("/.well-known/host-meta") ? hostMeta() : makeResponse(dataBody)
      );
    };
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).fetch = withHostMeta(mockFetch);
    setTokenProvider(() => undefined);
  });

  const urls = () => calls.map((c) => c.url);
  const CONTEXT = "/data/tapi-common:context";

  it("addresses T-API under the advertised root", async () => {
    await new TapiApiClient("http://dt").getContext();
    expect(urls()).toEqual([
      "http://dt/.well-known/host-meta",
      `http://dt/restconf${CONTEXT}`,
    ]);
  });

  it("follows a root other than /restconf", async () => {
    hostMeta = () => hostMetaResponse("/api");
    dataBody = { "tapi-connectivity:connectivity-service": [] };
    await new TapiApiClient("http://dt").getServices();
    expect(urls()[1]).toBe(
      "http://dt/api/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
    );
  });

  it("creates a service by POST on the connectivity-context (RFC 8040 §4.4.1)", async () => {
    const body = { "tapi-connectivity:connectivity-service": { "end-point": [] } };
    dataBody = { "tapi-connectivity:connectivity-service": [{ uuid: "svc-1" }] };
    await new TapiApiClient("http://dt").createConnectivityService(body as never);
    expect(calls[1].url).toBe(
      `http://dt/restconf${CONTEXT}/tapi-connectivity:connectivity-context`
    );
    expect(calls[1].init?.method).toBe("POST");
    expect(JSON.parse(calls[1].init?.body as string)).toEqual(body);
  });

  it("creates on the container under a root other than /restconf", async () => {
    hostMeta = () => hostMetaResponse("/api");
    dataBody = { "tapi-connectivity:connectivity-service": [{ uuid: "svc-1" }] };
    await new TapiApiClient("http://dt").createConnectivityService({} as never);
    expect(urls()[1]).toBe(
      "http://dt/api/data/tapi-common:context/tapi-connectivity:connectivity-context"
    );
  });

  it("asks once per base URL, across calls and clients", async () => {
    await createClient("http://dt").getContext();
    await createClient("http://dt").getPhysicalContext();
    await createClient("http://dt").deleteService("svc-1");
    await createClient("http://other").getContext();
    expect(urls().filter((u) => u.endsWith("/.well-known/host-meta"))).toEqual([
      "http://dt/.well-known/host-meta",
      "http://other/.well-known/host-meta",
    ]);
    expect(urls()).toContain(
      "http://dt/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=svc-1"
    );
  });

  it("sends the token to host-meta, which needs one on a secured twin", async () => {
    await new TapiApiClient("http://dt", "tok-123").getContext();
    const headers = calls[0].init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");
  });

  it("discovers an empty root from host-meta", async () => {
    hostMeta = () => hostMetaResponse("http://dt");
    await new TapiApiClient("http://dt").getContext();
    expect(urls()[1]).toBe(`http://dt${CONTEXT}`);
  });

  it("uses /data when an older twin serves no host-meta", async () => {
    hostMeta = () => makeResponse({}, 404);
    await new TapiApiClient("http://dt").getContext();
    expect(urls()[1]).toBe(`http://dt${CONTEXT}`);
  });

  it("reports a rejected token and retries discovery on the next call", async () => {
    hostMeta = () => makeResponse({}, 401);
    const err = await new TapiApiClient("http://dt").getContext().catch((e) => e);
    expect(isAuthError(err)).toBe(true);
    expect(urls()).toEqual(["http://dt/.well-known/host-meta"]);

    hostMeta = () => hostMetaResponse("/restconf");
    await new TapiApiClient("http://dt", "tok").getContext();
    expect(urls().slice(1)).toEqual([
      "http://dt/.well-known/host-meta",
      `http://dt/restconf${CONTEXT}`,
    ]);
  });

  it("leaves the non-T-API endpoints where they are", async () => {
    const c = new TapiApiClient("http://dt");
    await c.health();
    await c.whoami();
    await c.getAllOpm();
    await c.getElementList();
    expect(urls()).toEqual([
      "http://dt/health",
      "http://dt/internal/whoami",
      "http://dt/internal/opm",
      "http://dt/transparent/data/ElementList",
    ]);
  });
});

describe("parseRestconfRoot", () => {
  it("reads the restconf link", () => {
    expect(parseRestconfRoot(xrd("/restconf"))).toBe("/restconf");
    expect(parseRestconfRoot(xrd("/api/"))).toBe("/api");
  });

  it("reduces an absolute href to its path", () => {
    expect(parseRestconfRoot(xrd("http://twin:8080/top/restconf"))).toBe("/top/restconf");
  });

  it("reads an absolute href with an empty path as the empty root", () => {
    // What a twin with restconf_root "" advertises (RFC 3986 §3.3).
    expect(parseRestconfRoot(xrd("http://twin:8080"))).toBe("");
    expect(parseRestconfRoot(xrd("https://twin.example"))).toBe("");
  });

  it("accepts either attribute order and quote style", () => {
    expect(parseRestconfRoot('<XRD><Link href="/rc" rel="restconf"/></XRD>')).toBe("/rc");
  });

  it("ignores other links and treats no restconf link as the server root", () => {
    expect(parseRestconfRoot("<XRD><Link rel='lrdd' href='/x'/></XRD>")).toBe("");
  });
});

describe("errorDetail", () => {
  it("shows the RESTCONF error-message of a T-API refusal (RFC 8040 §7)", () => {
    const body = {
      "ietf-restconf:errors": {
        error: [{ "error-type": "application", "error-tag": "resource-denied",
                  "error-message": "No path with sufficient GSNR" }],
      },
    };
    expect(errorDetail(new ApiError(409, JSON.stringify(body)))).toBe("No path with sufficient GSNR");
  });

  it("falls back to FastAPI's detail, then to the raw message", () => {
    expect(errorDetail(new ApiError(422, JSON.stringify({ detail: "bad" })))).toBe("bad");
    expect(errorDetail(new ApiError(502, "Bad Gateway"))).toBe("HTTP 502: Bad Gateway");
  });
});

