// TypeScript interfaces mirroring TAPI JSON wire format (hyphenated keys)

export interface NameAndValue {
  "value-name": string;
  value: string;
}

export interface ServiceInterfacePointRef {
  "service-interface-point-uuid": string;
}

export interface ServiceInterfacePoint {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string;
  direction: string;
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

export interface NodeEdgePointRef {
  "topology-uuid": string;
  "node-uuid": string;
  "node-edge-point-uuid": string;
}

export interface NodeEdgePoint {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string;
  direction: string;
  "mapped-service-interface-point": ServiceInterfacePointRef[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

export interface TapiNode {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string[];
  "owned-node-edge-point": NodeEdgePoint[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

export interface TapiLink {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string[];
  "node-edge-point": NodeEdgePointRef[];
  direction: string;
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

export interface Topology {
  uuid: string;
  name: NameAndValue[];
  "layer-protocol-name": string[];
  node: TapiNode[];
  link: TapiLink[];
}

export interface TopologyContext {
  topology: Topology[];
}

export interface OpmMeasurements {
  "osnr-db": number;
  "gsnr-db": number;
  "pre-fec-ber": number;
  "q-factor-db": number;
  "chromatic-dispersion-ps-per-nm": number;
  "pmd-ps": number;
}

export interface ServiceOpm {
  "service-uuid": string;
  timestamp: number;
  measurements: OpmMeasurements;
}

export interface AllOpmResponse {
  services: ServiceOpm[];
  timestamp: number;
}

// Parsed API response envelopes
export interface ContextResponse {
  "tapi-common:context": {
    uuid: string;
    "service-interface-point": ServiceInterfacePoint[];
    "tapi-topology:topology-context": TopologyContext;
  };
}

export interface TopologyContextResponse {
  "tapi-topology:topology-context": TopologyContext;
}

export interface TopologyResponse {
  "tapi-topology:topology": Topology[];
}

export interface NodeResponse {
  "tapi-topology:node": TapiNode[];
}

export interface LinkResponse {
  "tapi-topology:link": TapiLink[];
}

export interface SipResponse {
  "tapi-common:context": {
    "service-interface-point": ServiceInterfacePoint[];
  };
}

export interface HealthResponse {
  status: string;
}

// Connectivity service types
export interface ConnectivityServiceEndPoint {
  "local-id": string;
  "service-interface-point": { "service-interface-point-uuid": string };
  direction?: string;
  /** Where T-API v2.6.0 carries modulation — see LayerProtocolConstraint. */
  "layer-protocol-constraint"?: LayerProtocolConstraint[];
}

export type ModulationFormat = "DP-QPSK" | "DP-16QAM" | "DP-64QAM";

/**
 * ONF `tapi-photonic-media` modulation identities.
 *
 * `tapi-connectivity` has no modulation leaf, so T-API expresses it as an
 * augment on the end-point. Note ONF spells 16QAM as `MT_DP-QAM16`.
 */
export type ModulationTechnique =
  | "MT_DP-QPSK"
  | "MT_DP-QAM16"
  | "MT_DP-QAM64";

export const MODULATION_TO_MT: Record<ModulationFormat, ModulationTechnique> = {
  "DP-QPSK": "MT_DP-QPSK",
  "DP-16QAM": "MT_DP-QAM16",
  "DP-64QAM": "MT_DP-QAM64",
};

export const MT_TO_MODULATION: Record<ModulationTechnique, ModulationFormat> = {
  "MT_DP-QPSK": "DP-QPSK",
  "MT_DP-QAM16": "DP-16QAM",
  "MT_DP-QAM64": "DP-64QAM",
};

/** The augment's JSON member name, module-qualified per RFC 7951 §4. */
export const OTSIA_CSEP_SPEC =
  "tapi-photonic-media:otsia-connectivity-service-end-point-spec";

export interface LayerProtocolConstraint {
  "local-id": string;
  "layer-protocol-name"?: string;
  [OTSIA_CSEP_SPEC]?: {
    "otsi-config": {
      "local-id": string;
      modulation: { "standard-modulation-technique": ModulationTechnique };
    }[];
    "number-of-otsi"?: number;
  };
  /** Present only once the service holds an allocation; frequencies in Hz. */
  "tapi-photonic-media:mcg-connectivity-service-end-point-spec"?: {
    "number-of-mc"?: number;
    "mc-spectrum-config-pac": {
      "local-id": string;
      spectrum: { "lower-frequency": number; "upper-frequency": number };
      "edge-frequency-constraint"?: {
        "grid-type"?: string;
        "adjustment-granularity"?: string;
      };
    }[];
  };
}

/** Build an end-point carrying the T-API modulation augment. */
export function tapiEndPoint(
  localId: string,
  sipUuid: string,
  modulation: ModulationFormat,
  direction?: string,
): ConnectivityServiceEndPoint {
  return {
    "local-id": localId,
    "service-interface-point": { "service-interface-point-uuid": sipUuid },
    ...(direction ? { direction } : {}),
    "layer-protocol-constraint": [
      {
        "local-id": "otsi",
        [OTSIA_CSEP_SPEC]: {
          "otsi-config": [
            {
              "local-id": "1",
              modulation: {
                "standard-modulation-technique": MODULATION_TO_MT[modulation],
              },
            },
          ],
        },
      },
    ],
  };
}

/** Read the modulation back off a service, or undefined if absent. */
export function modulationOf(
  service: ConnectivityService,
): ModulationFormat | undefined {
  const spec = service["end-point"]?.[0]?.["layer-protocol-constraint"]?.[0]?.[
    OTSIA_CSEP_SPEC
  ];
  const mt = spec?.["otsi-config"]?.[0]?.modulation?.[
    "standard-modulation-technique"
  ];
  return mt ? MT_TO_MODULATION[mt] : undefined;
}

/** The augment carrying assigned spectrum, on the same constraint entry. */
export const MCG_CSEP_SPEC =
  "tapi-photonic-media:mcg-connectivity-service-end-point-spec";

/** A service's assigned spectrum, in the units the UI displays. */
export interface AssignedSpectrum {
  centreThz: number;
  widthGhz: number;
}

/**
 * Read a service's assigned spectrum off its end-point.
 *
 * T-API v2.6.0 has no `frequency-slot` leaf on connectivity-service; the
 * photonic module augments the end-point's layer-protocol-constraint, with
 * band edges in Hz. Returns undefined when the service holds no spectrum.
 */
export function spectrumOf(
  service: ConnectivityService,
): AssignedSpectrum | undefined {
  const spec = service["end-point"]?.[0]?.["layer-protocol-constraint"]?.[0]?.[
    MCG_CSEP_SPEC
  ];
  const band = spec?.["mc-spectrum-config-pac"]?.[0]?.spectrum;
  if (!band) return undefined;
  const lower = band["lower-frequency"];
  const upper = band["upper-frequency"];
  if (typeof lower !== "number" || typeof upper !== "number") return undefined;
  return {
    centreThz: (lower + upper) / 2 / 1e12,
    widthGhz: (upper - lower) / 1e9,
  };
}

export interface ConnectivityService {
  uuid: string;
  name: NameAndValue[];
  "end-point": ConnectivityServiceEndPoint[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
}

export interface CreateConnectivityServiceRequest {
  "tapi-connectivity:connectivity-service": {
    name?: NameAndValue[];
    "end-point": ConnectivityServiceEndPoint[];
    "administrative-state": string;
    "lifecycle-state": string;
  };
}

export interface CreateConnectivityServiceResponse {
  "tapi-connectivity:connectivity-service": ConnectivityService;
}

export interface ConnectivityContextResponse {
  "tapi-connectivity:connectivity-context": {
    "connectivity-service": ConnectivityService[];
  };
}

export interface UpdateConnectivityServiceRequest {
  "tapi-connectivity:connectivity-service": {
    name?: NameAndValue[];
    "administrative-state"?: string;
    "lifecycle-state"?: string;
  };
}

export type UpdateConnectivityServiceResponse = CreateConnectivityServiceResponse;

/** T-API photonic media spectrum context (grid parameters). */
/**
 * Grid parameters from `/internal/spectrum-context`.
 *
 * Not T-API: these three names are TwinLight's own, and T-API v2.6.0 has no
 * `spectrum-context` container, so they are served unwrapped from the
 * internal surface rather than under the ONF module prefix.
 */
export interface SpectrumContextResponse {
  "num-slots": number;
  "slot-width-ghz": number;
  "nominal-central-frequency-thz": number;
}

/** Per-service spectrum block (internal). */
export interface SpectrumServiceAllocation {
  "service-uuid": string;
  "start-slot": number;
  "num-slots": number;
  /** ROADM UIDs traversed by this service (for overlay). */
  roadms?: string[];
}

/** Internal: ROADM–ROADM links only (excludes TRX–ROADM access links). */
export interface RoadmToRoadmLink {
  "link-uuid": string;
  label: string;
  "topology-uuid": string;
  "operational-state"?: string;
}

export interface RoadmToRoadmLinksResponse {
  links: RoadmToRoadmLink[];
}

/** Internal: link×slot grid for spectrum visualization (path not in T-API). */
export interface SpectrumGridResponse {
  "spectrum-context": {
    "num-slots": number;
    "slot-width-ghz": number;
    "nominal-central-frequency-thz": number;
  };
  links: { "link-uuid": string; label: string }[];
  /** occupancy[link_idx][slot_idx] = service_uuid or null */
  occupancy: (string | null)[][];
  /** Per-service slot range for popup (start, count) and bandwidth derivation. */
  "service-allocation": SpectrumServiceAllocation[];
}

// Service info / path types
export interface ServicePathHop {
  uid: string;
  type: "Transceiver" | "Roadm";
  distance_km_to_next: number | null;
}

// Path info (internal API: hops + GSNR estimate for path between two SIPs)
export interface PathInfoResponse {
  "sip-a": string;
  "sip-z": string;
  "modulation-format": string;
  hops: ServicePathHop[];
  "total-fiber-km": number | null;
  measurements: OpmMeasurements | null;
}

export interface ServiceInfoResponse {
  "service-uuid": string;
  name: string | null;
  "modulation-format": string;
  hops: ServicePathHop[] | null;
  "total-fiber-km": number | null;
}

// Equipment (TAPI equipment-context)
export interface Equipment {
  uuid: string;
  name: NameAndValue[];
  "equipment-type": string;
  "length-km"?: number;
}

export interface EquipmentContextResponse {
  "tapi-equipment:equipment-context": {
    uuid: string;
    equipment: Equipment[];
  };
}

export interface EquipmentListResponse {
  "tapi-equipment:equipment-context": {
    equipment: Equipment[];
  };
}

// Path computation
export interface PathLinkRef {
  "topology-uuid": string;
  "link-uuid": string;
}

export interface PathNodeRef {
  "topology-uuid": string;
  "node-uuid": string;
}

export interface PathCandidate {
  link: PathLinkRef[];
  node: PathNodeRef[];
}

export interface ComputePathInput {
  "end-point": Array<{
    "service-interface-point": { "service-interface-point-uuid": string };
  }>;
  "max-candidates"?: number;
}

export interface ComputePathResponse {
  "tapi-path-computation:output": {
    path: PathCandidate[];
  };
}

// Derived / enriched types used by the UI
export interface TopologyData {
  context: ContextResponse["tapi-common:context"];
  topologies: Topology[];
  sips: ServiceInterfacePoint[];
}

// Constellation diagram API response
export interface ConstellationResponse {
  "service-uuid": string;
  "modulation-format": string;
  n_symbols: number;
  i: number[];
  q: number[];
  measurements: {
    "gsnr-db": number | null;
    "linewidth-hz": number;
  };
}

// Eye diagram API response
export interface EyeDiagramResponse {
  "service-uuid": string;
  "modulation-format": string;
  time_ns: number[];
  traces: number[][];
  symbol_period_ns: number;
  n_traces: number;
  measurements: {
    "gsnr-db": number | null;
    "pmd-ps": number | null;
  };
}
