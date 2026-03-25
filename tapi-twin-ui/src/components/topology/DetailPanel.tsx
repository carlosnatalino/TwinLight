import { X, ExternalLink } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTopologyStore } from "@/store/topology";
import { useSelectionStore } from "@/store/selection";
import StateIndicator from "@/components/device/StateIndicator";
import SipBadge from "@/components/device/SipBadge";
import { getNodeDisplayName, getLinkDisplayName, getSpanElements } from "@/lib/tapi-helpers";

export default function DetailPanel() {
  const { selectedNodeId, selectedLinkId, selectedTopologyId, clearSelection } =
    useSelectionStore();
  const topologies = useTopologyStore((s) => s.getTopologies());
  const navigate = useNavigate();

  const topology = topologies.find((t) => t.uuid === selectedTopologyId);
  const selectedNode = topology?.node.find((n) => n.uuid === selectedNodeId) ?? null;
  const selectedLink = topology?.link.find((l) => l.uuid === selectedLinkId) ?? null;

  if (!selectedNode && !selectedLink) return null;

  return (
    <div className="w-80 shrink-0 flex flex-col border-l border-gray-200 bg-white overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-800">
          {selectedNode ? "Node Details" : "Link Details"}
        </h3>
        <button
          onClick={clearSelection}
          className="rounded p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-200 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 p-4 space-y-4">
        {selectedNode && (
          <>
            <div>
              <p className="text-base font-semibold text-gray-900">
                {getNodeDisplayName(selectedNode)}
              </p>
              <p className="mt-0.5 font-mono text-xs text-gray-400">{selectedNode.uuid}</p>
            </div>

            <div className="flex flex-wrap gap-3">
              <StateIndicator label="Operational" value={selectedNode["operational-state"]} />
              <StateIndicator label="Admin" value={selectedNode["administrative-state"]} />
            </div>

            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Node Edge Points ({selectedNode["owned-node-edge-point"].length})
              </p>
              <div className="space-y-2">
                {selectedNode["owned-node-edge-point"].slice(0, 5).map((nep) => {
                  const sips = nep["mapped-service-interface-point"] ?? [];
                  return (
                    <div
                      key={nep.uuid}
                      className="rounded border border-gray-100 bg-gray-50 p-2 text-xs"
                    >
                      <p className="font-mono text-gray-500">{nep.uuid.slice(0, 12)}…</p>
                      <p className="text-gray-600">{nep["layer-protocol-name"]} · {nep.direction}</p>
                      {sips.map((s) => (
                        <SipBadge key={s["service-interface-point-uuid"]} sipUuid={s["service-interface-point-uuid"]} />
                      ))}
                    </div>
                  );
                })}
                {selectedNode["owned-node-edge-point"].length > 5 && (
                  <p className="text-xs text-gray-400">
                    +{selectedNode["owned-node-edge-point"].length - 5} more…
                  </p>
                )}
              </div>
            </div>

            {selectedTopologyId && (
              <button
                onClick={() => navigate(`/topology/${selectedTopologyId}/node/${selectedNode.uuid}`)}
                className="flex w-full items-center justify-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Full Device Details
              </button>
            )}
          </>
        )}

        {selectedLink && !selectedNode && (
          <>
            <div>
              <p className="text-base font-semibold text-gray-900">
                {getLinkDisplayName(selectedLink)}
              </p>
              <p className="mt-0.5 font-mono text-xs text-gray-400">{selectedLink.uuid}</p>
            </div>

            <div className="flex flex-wrap gap-3">
              <StateIndicator label="Operational" value={selectedLink["operational-state"]} />
              <StateIndicator label="Admin" value={selectedLink["administrative-state"]} />
            </div>

            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Endpoints
              </p>
              {selectedLink["node-edge-point"].map((ref, i) => (
                <div key={i} className="text-xs bg-gray-50 rounded p-2 mb-1">
                  <p className="text-gray-500">{i === 0 ? "Source" : "Destination"}</p>
                  <p className="font-mono text-gray-600">{ref["node-uuid"].slice(0, 12)}…</p>
                  <p className="text-gray-400">NEP: {ref["node-edge-point-uuid"].slice(0, 12)}…</p>
                </div>
              ))}
            </div>

            {getSpanElements(selectedLink).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  Span Elements ({getSpanElements(selectedLink).length})
                </p>
                <pre className="text-xs bg-gray-50 p-2 rounded overflow-x-auto">
                  {JSON.stringify(getSpanElements(selectedLink), null, 2)}
                </pre>
              </div>
            )}

            {selectedTopologyId && (
              <button
                onClick={() => navigate(`/topology/${selectedTopologyId}/link/${selectedLink.uuid}`)}
                className="flex w-full items-center justify-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Full Link Details
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
