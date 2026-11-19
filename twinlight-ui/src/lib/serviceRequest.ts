/**
 * The T-API v2.6.0 request bodies the UI sends for a lightpath.
 *
 * Built here, not in the pages, so the exact JSON is unit-tested and pinned
 * by fixtures (`src/__tests__/fixtures/*.json`) that the backend test suite
 * posts to the real API: a change on either side that breaks the other fails
 * a test instead of a user's request.
 */

import {
  MODULATION_TO_MT,
  OTSIA_CSEP_SPEC,
  type ConnectivityServiceEndPoint,
  type CreateConnectivityServiceRequest,
  type LayerProtocolConstraint,
  type ModulationFormat,
} from "@/api/types";
import { formatDecimal64Integer, formatUint64 } from "@/lib/yangNumbers";

/** A T-API node reference, as topology-constraint lists carry them. */
export interface NodeRef {
  "topology-uuid": string;
  "node-uuid": string;
}

/** Nodes the route must visit (in order) and nodes it must avoid. */
export interface RouteConstraints {
  include: NodeRef[];
  exclude: NodeRef[];
}

export interface ServiceForm {
  name: string;
  aEndSip: string;
  zEndSip: string;
  /** null: admission picks the densest format whose QoT closes. */
  modulation: ModulationFormat | null;
  /** THz; null: first-fit spectrum inside the amplifier band. */
  centralFrequencyThz: number | null;
  /** dBm; null: the equipment library's default (SI power_dbm). */
  launchPowerDbm: number | null;
  adminState: string;
  lifecycleState: string;
  constraints: RouteConstraints;
}

/** Per-channel launch powers the twin models (backend: LAUNCH_POWER_RANGE_DBM). */
export const LAUNCH_POWER_RANGE_DBM = [-20, 10] as const;

/** Why a form cannot be sent, or null when it can. */
export function serviceFormError(form: ServiceForm): string | null {
  if (!form.aEndSip || !form.zEndSip) return "Select both end-points";
  if (form.aEndSip === form.zEndSip) return "The end-points must differ";
  const [low, high] = LAUNCH_POWER_RANGE_DBM;
  if (form.launchPowerDbm !== null) {
    if (!Number.isFinite(form.launchPowerDbm) || form.launchPowerDbm < low || form.launchPowerDbm > high) {
      return `Launch power must be between ${low} and ${high} dBm`;
    }
  }
  if (form.centralFrequencyThz !== null) {
    if (!Number.isFinite(form.centralFrequencyThz) || form.centralFrequencyThz <= 0) {
      return "Central frequency must be a positive number of THz";
    }
    // Sent in Hz as a uint64 string, exact only below 2^53 Hz (9007 THz).
    if (!Number.isSafeInteger(Math.round(form.centralFrequencyThz * 1e12))) {
      return "Central frequency is too large";
    }
  }
  const included = new Set(form.constraints.include.map((n) => n["node-uuid"]));
  if (form.constraints.exclude.some((n) => included.has(n["node-uuid"]))) {
    return "A node cannot be both included and excluded";
  }
  return null;
}

/** The topology-constraint list for include/exclude nodes, or undefined. */
export function topologyConstraint(
  constraints: RouteConstraints,
): Array<Record<string, unknown>> | undefined {
  const { include, exclude } = constraints;
  if (include.length === 0 && exclude.length === 0) return undefined;
  return [
    {
      "local-id": "1",
      ...(include.length ? { "include-node": include } : {}),
      ...(exclude.length ? { "exclude-node": exclude } : {}),
    },
  ];
}

/**
 * One lightpath end-point. The A-end is the SOURCE (it transmits into the
 * network), the Z-end the SINK: the twin provisions one direction per
 * service. The photonic augment is only sent when it says something.
 */
function endPoint(
  localId: string,
  sipUuid: string,
  direction: "SOURCE" | "SINK",
  form: ServiceForm,
): ConnectivityServiceEndPoint {
  type OtsiConfig = NonNullable<LayerProtocolConstraint[typeof OTSIA_CSEP_SPEC]>["otsi-config"][number];
  const otsiConfig: OtsiConfig = { "local-id": "1" };
  if (form.modulation !== null) {
    otsiConfig.modulation = {
      "standard-modulation-technique": MODULATION_TO_MT[form.modulation],
    };
  }
  // Both are 64-bit leaves, sent as strings (RFC 7951 §6.1; lib/yangNumbers.ts).
  if (form.centralFrequencyThz !== null) {
    // uint64 Hz; rounded to the Hz so float noise never makes it off-grid.
    otsiConfig["central-frequency"] = formatUint64(Math.round(form.centralFrequencyThz * 1e12));
  }
  if (form.launchPowerDbm !== null) {
    // decimal64 in 0.01 dBm, e.g. "-235.0".
    otsiConfig["channel-output-power"] = formatDecimal64Integer(Math.round(form.launchPowerDbm * 100));
  }
  const says = Object.keys(otsiConfig).length > 1;
  return {
    "local-id": localId,
    "service-interface-point": { "service-interface-point-uuid": sipUuid },
    direction,
    ...(says
      ? {
          "layer-protocol-constraint": [
            { "local-id": "otsi", [OTSIA_CSEP_SPEC]: { "otsi-config": [otsiConfig] } },
          ],
        }
      : {}),
  };
}

/** The POST body creating the lightpath the form describes. */
export function buildServiceRequest(form: ServiceForm): CreateConnectivityServiceRequest {
  const name = form.name.trim();
  const topology = topologyConstraint(form.constraints);
  return {
    "tapi-connectivity:connectivity-service": {
      ...(name ? { name: [{ "value-name": "service-name", value: name }] } : {}),
      "end-point": [
        endPoint("a-end", form.aEndSip, "SOURCE", form),
        endPoint("z-end", form.zEndSip, "SINK", form),
      ],
      ...(topology ? { "topology-constraint": topology } : {}),
      "administrative-state": form.adminState,
      "lifecycle-state": form.lifecycleState,
    },
  };
}
