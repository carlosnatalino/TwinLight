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
  direction: string;
}

export type ModulationFormat = "DP-QPSK" | "DP-16QAM" | "DP-64QAM";

/** T-API photonic media frequency-slot (assigned spectrum, read-only). */
export interface FrequencySlot {
  "nominal-central-frequency": number; // THz
  "slot-width": number; // GHz
}

export interface ConnectivityService {
  uuid: string;
  name: NameAndValue[];
  "end-point": ConnectivityServiceEndPoint[];
  "administrative-state": string;
  "operational-state": string;
  "lifecycle-state": string;
  "modulation-format"?: ModulationFormat;
  /** Present when service has spectrum allocation (T-API L0). */
  "frequency-slot"?: FrequencySlot;
}

export interface CreateConnectivityServiceRequest {
  "tapi-connectivity:connectivity-service": {
    name?: NameAndValue[];
    "end-point": ConnectivityServiceEndPoint[];
    "administrative-state": string;
    "lifecycle-state": string;
    "modulation-format"?: ModulationFormat;
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
export interface SpectrumContextResponse {
  "tapi-photonic-media:spectrum-context": {
    "num-slots": number;
    "slot-width-ghz": number;
    "nominal-central-frequency-thz": number;
  };
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
