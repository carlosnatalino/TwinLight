import type { Topology } from "@/api/types";
import { extractName } from "@/lib/tapi-helpers";
import type { NodeRef } from "@/lib/serviceRequest";

export interface RoutableNode {
  ref: NodeRef;
  label: string;
}

/**
 * Nodes a route can be constrained through: those with no service-interface
 * point (ROADMs), not the transceivers the end-points sit on.
 */
export function routableNodes(topologies: Topology[]): RoutableNode[] {
  const nodes: RoutableNode[] = [];
  for (const topology of topologies) {
    for (const node of topology.node) {
      const hasSip = node["owned-node-edge-point"].some(
        (nep) => (nep["mapped-service-interface-point"] ?? []).length > 0,
      );
      if (hasSip) continue;
      nodes.push({
        ref: { "topology-uuid": topology.uuid, "node-uuid": node.uuid },
        label: extractName(node.name, "node-name") || node.uuid.slice(0, 8),
      });
    }
  }
  return nodes.sort((a, b) => a.label.localeCompare(b.label));
}
