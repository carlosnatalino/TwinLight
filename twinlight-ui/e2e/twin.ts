import { expect, type APIRequestContext, type APIResponse } from "@playwright/test";

// The twin's REST API as the tests use it: T-API v2.6.0 for services and
// topology, so setup and checks go through the same interface the UI uses.

const CONTEXT = "/tapi-common:context";
const CONNECTIVITY = `${CONTEXT}/tapi-connectivity:connectivity-context`;

type NameValue = { "value-name": string; value: string };

export interface Service {
  uuid: string;
  name?: NameValue[];
  "administrative-state"?: string;
  "lifecycle-state"?: string;
  "operational-state"?: string;
}

interface Sip {
  uuid: string;
  name?: NameValue[];
}

interface TopologyCounts {
  nodes: number;
  links: number;
}

export interface NewService {
  name: string;
  /** Node name of the A-end SIP, e.g. "trx A". */
  from: string;
  /** Node name of the Z-end SIP. */
  to: string;
  adminState?: "UNLOCKED" | "LOCKED";
  lifecycleState?: string;
}

async function ok(resp: APIResponse, what: string): Promise<APIResponse> {
  if (!resp.ok()) {
    throw new Error(`${what}: HTTP ${resp.status()} ${await resp.text()}`);
  }
  return resp;
}

export class TwinApi {
  private dataRoot: Promise<string> | undefined;

  constructor(
    private readonly request: APIRequestContext,
    /** The twin's base URL, e.g. http://127.0.0.1:18080. */
    readonly url: string,
  ) {}

  /** `{restconf root}/data`, the root read from host-meta (RFC 8040 §3.1). */
  private data(): Promise<string> {
    this.dataRoot ??= (async () => {
      const resp = await ok(await this.request.get("/.well-known/host-meta"), "host-meta");
      const href = /rel=['"]restconf['"][^>]*href=['"]([^'"]*)['"]/.exec(await resp.text())?.[1];
      return `${(href ?? "").replace(/\/+$/, "")}/data`;
    })();
    return this.dataRoot;
  }

  private async get<T>(resource: string): Promise<T> {
    const resp = await this.request.get(`${await this.data()}${resource}`);
    return (await ok(resp, `GET ${resource}`)).json() as Promise<T>;
  }

  /** The SIP whose name is `nodeName` (the twin names a SIP after its transceiver). */
  async sipUuid(nodeName: string): Promise<string> {
    const body = await this.get<{ "tapi-common:context": { "service-interface-point": Sip[] } }>(
      CONTEXT,
    );
    const sip = body["tapi-common:context"]["service-interface-point"].find((s) =>
      s.name?.some((n) => n.value === nodeName),
    );
    if (!sip) throw new Error(`No SIP named ${nodeName}`);
    return sip.uuid;
  }

  async services(): Promise<Service[]> {
    const body = await this.get<{ "tapi-connectivity:connectivity-service": Service[] }>(
      `${CONNECTIVITY}/connectivity-service`,
    );
    return body["tapi-connectivity:connectivity-service"];
  }

  async service(uuid: string): Promise<Service> {
    const body = await this.get<{ "tapi-connectivity:connectivity-service": Service[] }>(
      `${CONNECTIVITY}/connectivity-service=${uuid}`,
    );
    return body["tapi-connectivity:connectivity-service"][0];
  }

  /** The service's route and length (/internal/services/{uuid}, proprietary). */
  async serviceInfo(uuid: string): Promise<{ "total-fiber-km"?: number; hops?: { uid: string }[] }> {
    const resp = await this.request.get(`/internal/services/${uuid}`);
    return (await ok(resp, `GET /internal/services/${uuid}`)).json();
  }

  /** Create a service through T-API and return it as the twin admitted it. */
  async createService(svc: NewService): Promise<Service> {
    const [a, z] = await Promise.all([this.sipUuid(svc.from), this.sipUuid(svc.to)]);
    const endPoint = (localId: string, sip: string, direction: string) => ({
      "local-id": localId,
      "service-interface-point": { "service-interface-point-uuid": sip },
      direction,
    });
    const resp = await this.request.post(`${await this.data()}${CONNECTIVITY}`, {
      headers: { "Content-Type": "application/yang-data+json" },
      data: {
        "tapi-connectivity:connectivity-service": {
          name: [{ "value-name": "service-name", value: svc.name }],
          "end-point": [endPoint("a-end", a, "SOURCE"), endPoint("z-end", z, "SINK")],
          "administrative-state": svc.adminState ?? "UNLOCKED",
          "lifecycle-state": svc.lifecycleState ?? "PLANNED",
        },
      },
    });
    expect(resp.status(), await resp.text()).toBe(201);
    return (await resp.json())["tapi-connectivity:connectivity-service"][0];
  }

  /**
   * Delete every connectivity service in one request: a PUT of an empty
   * connectivity-context (RFC 8040 §4.5), which is all or nothing.
   */
  async deleteAllServices(): Promise<void> {
    const resp = await this.request.put(`${await this.data()}${CONNECTIVITY}`, {
      headers: { "Content-Type": "application/yang-data+json" },
      data: { "tapi-connectivity:connectivity-context": {} },
    });
    await ok(resp, "PUT connectivity-context");
  }

  /** Nodes and links over every topology, as the Topology page counts them. */
  async topologyCounts(): Promise<TopologyCounts> {
    const body = await this.get<{
      "tapi-topology:topology-context": { topology: { node?: unknown[]; link?: unknown[] }[] };
    }>(`${CONTEXT}/tapi-topology:topology-context`);
    const topologies = body["tapi-topology:topology-context"].topology;
    return {
      nodes: topologies.reduce((n, t) => n + (t.node?.length ?? 0), 0),
      links: topologies.reduce((n, t) => n + (t.link?.length ?? 0), 0),
    };
  }
}

/** The service's `service-name`, or undefined. */
export function serviceName(svc: Service): string | undefined {
  return svc.name?.find((n) => n["value-name"] === "service-name")?.value;
}
