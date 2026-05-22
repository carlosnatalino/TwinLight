import type { NameAndValue, TapiNode, TapiLink, Topology } from "@/api/types";

/** Extract a named value from a NameAndValue array */
export function extractName(names: NameAndValue[], valueName: string): string {
  return names.find((n) => n["value-name"] === valueName)?.value ?? "";
}

/** Get the display name for a node (tries "node-name", then "name", then UUID) */
export function getNodeDisplayName(node: TapiNode): string {
  return (
    extractName(node.name, "node-name") ||
    extractName(node.name, "name") ||
    node.uuid.slice(0, 8)
  );
}

/** Get the display name for a link (tries "link-name", then "name", then UUID) */
export function getLinkDisplayName(link: TapiLink): string {
  return (
    extractName(link.name, "link-name") ||
    extractName(link.name, "name") ||
    link.uuid.slice(0, 8)
  );
}

/** Determine whether a node is a transceiver (has NEPs with mapped SIPs) */
export function isTransceiver(node: TapiNode): boolean {
  return node["owned-node-edge-point"].some(
    (nep) => (nep["mapped-service-interface-point"] ?? []).length > 0
  );
}

/** Get the node type label */
export function getNodeType(node: TapiNode): "transceiver" | "roadm" {
  return isTransceiver(node) ? "transceiver" : "roadm";
}

/** Parse span elements from a link's name array */
export function getSpanElements(link: TapiLink): unknown[] {
  const raw = extractName(link.name, "span-elements");
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [{ raw }];
  }
}

/** Build a lookup: NEP UUID → parent node UUID, across a topology */
export function buildNepToNodeMap(topology: Topology): Map<string, string> {
  const map = new Map<string, string>();
  for (const node of topology.node) {
    for (const nep of node["owned-node-edge-point"]) {
      map.set(nep.uuid, node.uuid);
    }
  }
  return map;
}

/** Build a lookup: node UUID → node, across a topology */
export function buildNodeMap(topology: Topology): Map<string, TapiNode> {
  const map = new Map<string, TapiNode>();
  for (const node of topology.node) {
    map.set(node.uuid, node);
  }
  return map;
}

/** Find all links that reference a given node's NEPs */
export function getLinksForNode(topology: Topology, nodeUuid: string): TapiLink[] {
  const nepUuids = new Set<string>(
    (topology.node.find((n) => n.uuid === nodeUuid)?.["owned-node-edge-point"] ?? []).map(
      (nep) => nep.uuid
    )
  );
  return topology.link.filter((link) =>
    link["node-edge-point"].some((ref) => nepUuids.has(ref["node-edge-point-uuid"]))
  );
}

/** Format a state string for display */
export function formatState(state: string): string {
  return state.replace(/_/g, " ");
}

/** Count nodes by operational state */
export function countByOperState(nodes: TapiNode[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const node of nodes) {
    const s = node["operational-state"] ?? "UNKNOWN";
    counts[s] = (counts[s] ?? 0) + 1;
  }
  return counts;
}

/** Count links by operational state */
export function countLinksByOperState(links: TapiLink[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const link of links) {
    const s = link["operational-state"] ?? "UNKNOWN";
    counts[s] = (counts[s] ?? 0) + 1;
  }
  return counts;
}
