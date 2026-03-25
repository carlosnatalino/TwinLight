import type {
  ContextResponse,
  TopologyContextResponse,
  TopologyResponse,
  NodeResponse,
  LinkResponse,
  AllOpmResponse,
  ServiceOpm,
  HealthResponse,
  CreateConnectivityServiceRequest,
  CreateConnectivityServiceResponse,
  ConnectivityContextResponse,
  UpdateConnectivityServiceRequest,
  UpdateConnectivityServiceResponse,
  ServiceInfoResponse,
  Equipment,
  EquipmentContextResponse,
  EquipmentListResponse,
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

export class TapiApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  private async get<T>(path: string): Promise<T> {
    try {
      const resp = await fetch(`${this.baseUrl}${path}`, {
        headers: { Accept: "application/yang-data+json, application/json" },
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new ApiError(resp.status, text);
      }
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
        headers: {
          Accept: "application/yang-data+json, application/json",
          "Content-Type": "application/yang-data+json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new ApiError(resp.status, text);
      }
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
        headers: { Accept: "application/yang-data+json, application/json" },
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new ApiError(resp.status, text);
      }
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
        headers: {
          Accept: "application/yang-data+json, application/json",
          "Content-Type": "application/yang-data+json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15_000),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => resp.statusText);
        throw new ApiError(resp.status, text);
      }
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
      "/data/tapi-connectivity:connectivity-context/connectivity-service",
      body
    );
  }

  getServices(): Promise<ConnectivityContextResponse> {
    return this.get<ConnectivityContextResponse>(
      "/data/tapi-connectivity:connectivity-context/connectivity-service"
    );
  }

  updateService(
    uuid: string,
    body: UpdateConnectivityServiceRequest
  ): Promise<UpdateConnectivityServiceResponse> {
    return this.patch<UpdateConnectivityServiceResponse>(
      `/data/tapi-connectivity:connectivity-context/connectivity-service=${uuid}`,
      body
    );
  }

  deleteService(uuid: string): Promise<void> {
    return this.delete(
      `/data/tapi-connectivity:connectivity-context/connectivity-service=${uuid}`
    );
  }

  getEquipmentContext(): Promise<EquipmentContextResponse> {
    return this.get<EquipmentContextResponse>(
      "/data/tapi-equipment:equipment-context"
    );
  }

  getEquipmentList(): Promise<EquipmentListResponse> {
    return this.get<EquipmentListResponse>(
      "/data/tapi-equipment:equipment-context/equipment"
    );
  }

  getEquipment(uuid: string): Promise<{ "tapi-equipment:equipment": Equipment[] }> {
    return this.get(
      `/data/tapi-equipment:equipment-context/equipment=${encodeURIComponent(uuid)}`
    );
  }

  computePath(input: ComputePathInput): Promise<ComputePathResponse> {
    return this.post<ComputePathResponse>(
      "/data/tapi-path-computation:path-computation-context/path-computation-service/compute-path",
      { "tapi-path-computation:input": input }
    );
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
    return this.get<SpectrumContextResponse>(
      "/data/tapi-photonic-media%3Aspectrum-context"
    );
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

export function createClient(baseUrl: string): TapiApiClient {
  return new TapiApiClient(baseUrl);
}
