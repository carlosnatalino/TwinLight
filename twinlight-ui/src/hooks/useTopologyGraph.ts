import { useMemo } from "react";
import type { ElementDefinition } from "cytoscape";
import type { Topology, ServiceInterfacePoint } from "@/api/types";
import { getNodeDisplayName, getLinkDisplayName, getNodeType } from "@/lib/tapi-helpers";

export function useTopologyGraph(
  topologies: Topology[],
  _sips: ServiceInterfacePoint[]
): ElementDefinition[] {
  return useMemo(() => {
    const elements: ElementDefinition[] = [];

    for (const topology of topologies) {
      // Add nodes
      for (const node of topology.node) {
        const label = getNodeDisplayName(node);
        const type = getNodeType(node);

        elements.push({
          data: {
            id: node.uuid,
            label,
            type,
            topoId: topology.uuid,
            adminState: node["administrative-state"],
            operState: node["operational-state"],
            lifecycleState: node["lifecycle-state"],
            nepCount: node["owned-node-edge-point"].length,
            layerProtocol: node["layer-protocol-name"]?.join(", ") ?? "",
          },
          classes: [type],
        });
      }

      // Build NEP → node-UUID map for resolving link endpoints
      const nepToNode = new Map<string, string>();
      for (const node of topology.node) {
        for (const nep of node["owned-node-edge-point"]) {
          nepToNode.set(nep.uuid, node.uuid);
        }
      }

      // Add edges
      for (const link of topology.link) {
        const refs = link["node-edge-point"];
        if (!refs || refs.length < 2) continue;

        // Use node-uuid from refs directly (preferred) or fall back to NEP map
        const sourceNode = refs[0]["node-uuid"] || nepToNode.get(refs[0]["node-edge-point-uuid"]);
        const targetNode = refs[1]["node-uuid"] || nepToNode.get(refs[1]["node-edge-point-uuid"]);

        if (!sourceNode || !targetNode) continue;
        if (sourceNode === targetNode) continue; // skip self-loops

        const label = getLinkDisplayName(link);

        elements.push({
          data: {
            id: link.uuid,
            source: sourceNode,
            target: targetNode,
            label,
            topoId: topology.uuid,
            operState: link["operational-state"],
            adminState: link["administrative-state"],
            direction: link.direction,
          },
        });
      }
    }

    return elements;
  }, [topologies, _sips]);
}
