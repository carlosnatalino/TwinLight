import { useMemo } from "react";
import type { ElementDefinition } from "cytoscape";
import type { ConnectivityService, TopologyData } from "@/api/types";
import { getNodeDisplayName, getNodeType } from "@/lib/tapi-helpers";
import { extractName } from "@/lib/tapi-helpers";

/**
 * Builds Cytoscape element definitions for the service logical topology.
 *
 * Nodes: physical TAPI nodes that are referenced by at least one service endpoint.
 * Edges: one per service, connecting the A-end node to the Z-end node.
 *
 * The SIP → owning-node mapping is resolved by walking:
 *   topology → node → owned-node-edge-point → mapped-service-interface-point
 */
export function useServiceGraph(
  services: ConnectivityService[],
  topology: TopologyData | null
): ElementDefinition[] {
  return useMemo(() => {
    if (!topology || services.length === 0) return [];

    // Build SIP UUID → TapiNode map across all topologies
    const sipToNode = new Map<string, { nodeUuid: string; topoUuid: string }>();
    for (const topo of topology.topologies) {
      for (const node of topo.node) {
        for (const nep of node["owned-node-edge-point"]) {
          for (const sipRef of nep["mapped-service-interface-point"] ?? []) {
            sipToNode.set(sipRef["service-interface-point-uuid"], {
              nodeUuid: node.uuid,
              topoUuid: topo.uuid,
            });
          }
        }
      }
    }

    // Build node UUID → TapiNode map for quick label lookup
    const nodeMap = new Map<
      string,
      { node: (typeof topology.topologies)[0]["node"][0]; topoUuid: string }
    >();
    for (const topo of topology.topologies) {
      for (const node of topo.node) {
        nodeMap.set(node.uuid, { node, topoUuid: topo.uuid });
      }
    }

    const elements: ElementDefinition[] = [];
    const includedNodeUuids = new Set<string>();

    // First pass: collect node UUIDs referenced by services
    for (const svc of services) {
      const endpoints = svc["end-point"] ?? [];
      if (endpoints.length < 2) continue;

      const aSipUuid =
        endpoints[0]["service-interface-point"]["service-interface-point-uuid"];
      const zSipUuid =
        endpoints[1]["service-interface-point"]["service-interface-point-uuid"];

      const aRef = sipToNode.get(aSipUuid);
      const zRef = sipToNode.get(zSipUuid);

      if (!aRef || !zRef) continue;
      if (aRef.nodeUuid === zRef.nodeUuid) continue; // skip self-loop

      includedNodeUuids.add(aRef.nodeUuid);
      includedNodeUuids.add(zRef.nodeUuid);
    }

    // Add node elements
    for (const nodeUuid of includedNodeUuids) {
      const entry = nodeMap.get(nodeUuid);
      if (!entry) continue;
      const { node, topoUuid } = entry;
      const label = getNodeDisplayName(node);
      const type = getNodeType(node);
      elements.push({
        data: {
          id: node.uuid,
          label,
          type,
          topoId: topoUuid,
          adminState: node["administrative-state"],
          operState: node["operational-state"],
          lifecycleState: node["lifecycle-state"],
        },
        classes: [type],
      });
    }

    // Second pass: add service edges
    for (const svc of services) {
      const endpoints = svc["end-point"] ?? [];
      if (endpoints.length < 2) continue;

      const aEndpoint = endpoints[0];
      const zEndpoint = endpoints[1];
      const aSipUuid =
        aEndpoint["service-interface-point"]["service-interface-point-uuid"];
      const zSipUuid =
        zEndpoint["service-interface-point"]["service-interface-point-uuid"];

      const aRef = sipToNode.get(aSipUuid);
      const zRef = sipToNode.get(zSipUuid);

      if (!aRef || !zRef) continue;
      if (aRef.nodeUuid === zRef.nodeUuid) continue;

      const label =
        extractName(svc.name, "service-name") || svc.uuid.slice(0, 8);

      elements.push({
        data: {
          id: svc.uuid,
          source: aRef.nodeUuid,
          target: zRef.nodeUuid,
          label,
          adminState: svc["administrative-state"],
          operState: svc["operational-state"],
          lifecycleState: svc["lifecycle-state"],
          aEndSip: aSipUuid,
          zEndSip: zSipUuid,
          aEndDirection: aEndpoint.direction,
          zEndDirection: zEndpoint.direction,
        },
        classes: ["service-edge", svc["lifecycle-state"]?.toLowerCase().replace(/_/g, "-")],
      });
    }

    return elements;
  }, [services, topology]);
}
