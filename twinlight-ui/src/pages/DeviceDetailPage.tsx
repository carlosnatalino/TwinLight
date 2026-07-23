import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Copy, CheckCheck } from "lucide-react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { useMonitoringStore } from "@/store/monitoring";
import { getNodeDisplayName, getLinksForNode, getLinkDisplayName, getNodeType } from "@/lib/tapi-helpers";
import NepTable from "@/components/device/NepTable";
import StateIndicator from "@/components/device/StateIndicator";
import OpmDashboard from "@/components/monitoring/OpmDashboard";
import JsonViewer from "@/components/common/JsonViewer";
import type { Topology } from "@/api/types";

function CopyableUuid({ uuid }: { uuid: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard.writeText(uuid);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={copy}
      className="flex items-center gap-1.5 font-mono text-xs text-gray-400 hover:text-gray-700 transition-colors"
      title="Copy UUID"
    >
      {uuid}
      {copied ? <CheckCheck className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

export default function DeviceDetailPage() {
  const { topoId, nodeId } = useParams<{ topoId: string; nodeId: string }>();
  const navigate = useNavigate();
  const { baseUrl } = useConnectionStore();
  const { data, fetchTopology } = useTopologyStore();
  const currentOpm = useMonitoringStore((s) => s.currentOpm);

  useEffect(() => {
    if (baseUrl && !data) void fetchTopology(baseUrl);
  }, [baseUrl, data, fetchTopology]);

  const topology: Topology | undefined = data?.topologies.find((t) => t.uuid === topoId);
  const node = topology?.node.find((n) => n.uuid === nodeId);

  if (!node || !topology) {
    return (
      <div className="p-6">
        <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 mb-4">
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <p className="text-gray-500">Node not found.</p>
      </div>
    );
  }

  const displayName = getNodeDisplayName(node);
  const nodeType = getNodeType(node);
  const connectedLinks = getLinksForNode(topology, node.uuid);

  // Find OPM data — look for service associated with this node's SIPs
  const sipUuids = new Set(
    node["owned-node-edge-point"]
      .flatMap((nep) => nep["mapped-service-interface-point"] ?? [])
      .map((s) => s["service-interface-point-uuid"])
  );
  const opmEntry = Object.entries(currentOpm).find(([uuid]) => sipUuids.has(uuid));

  return (
    <div className="mx-auto max-w-4xl p-6 space-y-6">
      <button
        onClick={() => navigate(-1)}
        className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 transition-colors"
      >
        <ArrowLeft className="h-4 w-4" /> Back to Topology
      </button>

      {/* Header */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-start gap-4">
          <div
            className={`rounded-lg p-3 ${nodeType === "transceiver" ? "bg-blue-100" : "bg-green-100"}`}
          >
            {nodeType === "transceiver" ? (
              <div className="h-6 w-6 rounded bg-blue-500" />
            ) : (
              <div className="h-6 w-6 rotate-45 bg-green-500" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold text-gray-900 truncate">{displayName}</h1>
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${nodeType === "transceiver" ? "bg-blue-100 text-blue-700" : "bg-green-100 text-green-700"}`}>
                {nodeType === "transceiver" ? "Transceiver" : "ROADM"}
              </span>
            </div>
            <CopyableUuid uuid={node.uuid} />
          </div>
        </div>
      </div>

      {/* State badges */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">State</h2>
        <div className="flex flex-wrap gap-6">
          <StateIndicator label="Operational" value={node["operational-state"]} />
          <StateIndicator label="Administrative" value={node["administrative-state"]} />
          <StateIndicator label="Lifecycle" value={node["lifecycle-state"]} />
        </div>
      </div>

      {/* NEP table */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">
          Node Edge Points ({node["owned-node-edge-point"].length})
        </h2>
        <NepTable neps={node["owned-node-edge-point"]} />
      </div>

      {/* Connected links */}
      {connectedLinks.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">
            Connected Links ({connectedLinks.length})
          </h2>
          <div className="space-y-2">
            {connectedLinks.map((link) => {
              const peerRef = link["node-edge-point"].find(
                (ref) => ref["node-uuid"] !== node.uuid
              );
              const peerNode = peerRef
                ? topology.node.find((n) => n.uuid === peerRef["node-uuid"])
                : null;
              return (
                <div
                  key={link.uuid}
                  className="flex items-center justify-between rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 text-sm"
                >
                  <div>
                    <p className="font-medium text-gray-800">{getLinkDisplayName(link)}</p>
                    {peerNode && (
                      <p className="text-xs text-gray-500">
                        Peer: {getNodeDisplayName(peerNode)}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => navigate(`/topology/${topoId}/link/${link.uuid}`)}
                    className="text-xs text-blue-600 hover:underline"
                  >
                    Details →
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* OPM section */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Optical Performance Monitoring</h2>
        {opmEntry ? (
          <OpmDashboard
            serviceUuid={opmEntry[0]}
            measurements={opmEntry[1].measurements}
          />
        ) : (
          <p className="text-sm text-gray-400 italic">
            No OPM data available for this node. Data appears when a connectivity service is associated.
          </p>
        )}
      </div>

      {/* Raw JSON */}
      <JsonViewer data={node} />
    </div>
  );
}
