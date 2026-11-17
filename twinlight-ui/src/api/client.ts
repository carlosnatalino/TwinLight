import type {
  ContextResponse,
  TopologyContextResponse,
  TopologyResponse,
  NodeResponse,
  LinkResponse,
  AllOpmResponse,
  ServiceOpm,
  HealthResponse,
  WhoamiResponse,
  CreateConnectivityServiceRequest,
  CreateConnectivityServiceResponse,
  ConnectivityContextResponse,
  UpdateConnectivityServiceRequest,
  UpdateConnectivityServiceResponse,
  ServiceInfoResponse,
  PhysicalContextResponse,
  TransparentElementListResponse,
  ComputePathInput,
  ComputePathResponse,
  PathInfoResponse,
  RoadmToRoadmLinksResponse,
  SpectrumContextResponse,
  SpectrumGridResponse,
  ConstellationResponse,
  EyeDiagramResponse,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string
  ) {
    super(`HTTP ${status}: ${message}`);
    this.name = "ApiError";
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

/** True for the statuses a twin answers when it rejects the credentials. */
export function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 401 || err.status === 403);
}

/**
 * The ApiError for a failed response. A rejected token gets a sentence the
 * user can act on rather than the RESTCONF error body; every other status
 * keeps the raw body, which some pages parse for its error details.
 */
async function apiErrorFrom(resp: Response): Promise<ApiError> {
  if (resp.status === 401 || resp.status === 403) {
    return new ApiError(
      resp.status,
      "The DT requires a valid API token. Set one in Settings."
    );
  }
  const text = await resp.text().catch(() => resp.statusText);
  return new ApiError(resp.status, text);
}

/**
 * Supplies the API token for clients built without one. The connection store
 * registers itself here, so every client — however it was created — sends the
 * token currently saved in Settings, and a changed token takes effect on the
 * next request without rebuilding anything.
 */
let tokenProvider: () => string | undefined = () => undefined;

export function setTokenProvider(provider: () => string | undefined): void {
  tokenProvider = provider;
}

export class TapiApiClient {
  private baseUrl: string;
  private token: string | undefined;

  /**
   * @param token API token for this client only (e.g. one being tested in
   *   Settings before it is saved). Omit it to use the saved token.
   */
  constructor(baseUrl: string, token?: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token = token;
  }

  /** Request headers, with `Authorization: Bearer` when a token is set. */
  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const token = (this.token ?? tokenProvider())?.trim();
    return {
      Accept: "application/yang-data+json, application/json",
      ...extra,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }

  private async get<T>(path: string): Promise<T> {
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        headers: this.headers(),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) throw await apiErrorFrom(resp);
      return resp.json() as Promise<T>;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (err instanceof Error && err.name === "AbortError") {
        throw new NetworkError("Request timed out");
      }
      throw new NetworkError(err instanceof Error ? err.message : "Network error");
    }
  }

  private async patch<T>(path: string, body: unknown): Promise<T> {
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method: "PATCH",
        headers: this.headers({ "Content-Type": "application/yang-data+json" }),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) throw await apiErrorFrom(resp);
      return resp.json() as Promise<T>;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (err instanceof Error && err.name === "AbortError") {
        throw new NetworkError("Request timed out");
      }
      throw new NetworkError(err instanceof Error ? err.message : "Network error");
    }
  }

  private async delete(path: string): Promise<void> {
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method: "DELETE",
        headers: this.headers(),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) throw await apiErrorFrom(resp);
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (err instanceof Error && err.name === "AbortError") {
        throw new NetworkError("Request timed out");
      }
      throw new NetworkError(err instanceof Error ? err.message : "Network error");
    }
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: this.headers({ "Content-Type": "application/yang-data+json" }),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) throw await apiErrorFrom(resp);
      return resp.json() as Promise<T>;
    } catch (err) {
      if (err instanceof ApiError) throw err;
      if (err instanceof Error && err.name === "AbortError") {
        throw new NetworkError("Request timed out");
      }
      throw new NetworkError(err instanceof Error ? err.message : "Network error");
    }
  }

  health(): Promise<HealthResponse> {
    return this.get<HealthResponse>("/health");
  }

  /**
   * Which token the twin accepted the request as. Unlike /health, which is
   * public, this needs a valid token when the twin has authentication on, so
   * a 401 here means the token is missing or rejected.
   */
  whoami(): Promise<WhoamiResponse> {
    return this.get<WhoamiResponse>("/internal/whoami");
  }

  getContext(): Promise<ContextResponse> {
    return this.get<ContextResponse>("/data/tapi-common:context");
  }

  getTopologyContext(): Promise<TopologyContextResponse> {
    return this.get<TopologyContextResponse>(
      "/data/tapi-common:context/tapi-topology:topology-context"
    );
  }

  getTopology(uuid: string): Promise<TopologyResponse> {
    return this.get<TopologyResponse>(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${uuid}`
    );
  }

  getNode(topoUuid: string, nodeUuid: string): Promise<NodeResponse> {
    return this.get<NodeResponse>(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${topoUuid}/node=${nodeUuid}`
    );
  }

  getLink(topoUuid: string, linkUuid: string): Promise<LinkResponse> {
    return this.get<LinkResponse>(
      `/data/tapi-common:context/tapi-topology:topology-context/topology=${topoUuid}/link=${linkUuid}`
    );
  }

  getAllOpm(): Promise<AllOpmResponse> {
    return this.get<AllOpmResponse>("/internal/opm");
  }

  getServiceOpm(serviceUuid: string): Promise<ServiceOpm> {
    return this.get<ServiceOpm>(`/internal/opm/${serviceUuid}`);
  }

  getServiceInfo(serviceUuid: string): Promise<ServiceInfoResponse> {
    return this.get<ServiceInfoResponse>(`/internal/services/${serviceUuid}`);
  }

  createConnectivityService(
    body: CreateConnectivityServiceRequest
  ): Promise<CreateConnectivityServiceResponse> {
    return this.post<CreateConnectivityServiceResponse>(
      "/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service",
      body
    );
  }

  getServices(): Promise<ConnectivityContextResponse> {
    return this.get<ConnectivityContextResponse>(
      "/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
    );
  }

  updateService(
    uuid: string,
    body: UpdateConnectivityServiceRequest
  ): Promise<UpdateConnectivityServiceResponse> {
    return this.patch<UpdateConnectivityServiceResponse>(
      `/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=${uuid}`,
      body
    );
  }

  deleteService(uuid: string): Promise<void> {
    return this.delete(
      `/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service=${uuid}`
    );
  }

  getPhysicalContext(): Promise<PhysicalContextResponse> {
    return this.get<PhysicalContextResponse>(
      "/data/tapi-common:context/tapi-equipment:physical-context"
    );
  }

  // Every element with its attributes, from the transparent controller.
  getElementList(): Promise<TransparentElementListResponse> {
    return this.get<TransparentElementListResponse>(
      "/transparent/data/ElementList"
    );
  }

  // Ranked candidates with a count: not expressible in T-API v2.6.0, whose
  // path computation is the path-comp-service resource, so it is internal.
  computePath(input: ComputePathInput): Promise<ComputePathResponse> {
    return this.post<ComputePathResponse>("/internal/compute-path", input);
  }

  getPathInfo(
    sipA: string,
    sipZ: string,
    modulation?: string
  ): Promise<PathInfoResponse> {
    const params = new URLSearchParams({ sip_a: sipA, sip_z: sipZ });
    if (modulation) params.set("modulation", modulation);
    return this.get<PathInfoResponse>(
      `/internal/path-info?${params.toString()}`
    );
  }

  getSpectrumContext(): Promise<SpectrumContextResponse> {
    return this.get<SpectrumContextResponse>("/internal/spectrum-context");
  }

  getRoadmToRoadmLinks(): Promise<RoadmToRoadmLinksResponse> {
    return this.get<RoadmToRoadmLinksResponse>("/internal/links");
  }

  getSpectrumGrid(): Promise<SpectrumGridResponse> {
    return this.get<SpectrumGridResponse>("/internal/spectrum-grid");
  }

  getConstellation(
    serviceUuid: string,
    nSymbols = 5000
  ): Promise<ConstellationResponse> {
    return this.get<ConstellationResponse>(
      `/internal/services/${serviceUuid}/constellation?n_symbols=${nSymbols}`
    );
  }

  getEyeDiagram(
    serviceUuid: string,
    nTraces = 150,
    samplesPerSymbol = 64
  ): Promise<EyeDiagramResponse> {
    const params = new URLSearchParams({
      n_traces: String(nTraces),
      samples_per_symbol: String(samplesPerSymbol),
    });
    return this.get<EyeDiagramResponse>(
      `/internal/services/${serviceUuid}/eye-diagram?${params}`
    );
  }
}

let _client: TapiApiClient | null = null;

export function getClient(baseUrl: string): TapiApiClient {
  if (!_client || (_client as unknown as { baseUrl: string }).baseUrl !== baseUrl) {
    _client = new TapiApiClient(baseUrl);
  }
  return _client;
}

export function createClient(baseUrl: string, token?: string): TapiApiClient {
  return new TapiApiClient(baseUrl, token);
}
