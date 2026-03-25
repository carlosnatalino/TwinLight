import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Copy, CheckCheck } from "lucide-react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { getLinkDisplayName, getNodeDisplayName, getSpanElements } from "@/lib/tapi-helpers";
import StateIndicator from "@/components/device/StateIndicator";
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
      className="flex items-center gap-1.5 font-mono text-xs text-gray-400 hover:text-gray-700"
    >
      {uuid}
      {copied ? <CheckCheck className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

export default function LinkDetailPage() {
  const { topoId, linkId } = useParams<{ topoId: string; linkId: string }>();
  const navigate = useNavigate();
  const { baseUrl } = useConnectionStore();
  const { data, fetchTopology } = useTopologyStore();

  useEffect(() => {
    if (baseUrl && !data) void fetchTopology(baseUrl);
  }, [baseUrl, data, fetchTopology]);

  const topology: Topology | undefined = data?.topologies.find((t) => t.uuid === topoId);
  const link = topology?.link.find((l) => l.uuid === linkId);

  if (!link || !topology) {
    return (
      <div className="p-6">
        <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-gray-500 mb-4">
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <p className="text-gray-500">Link not found.</p>
      </div>
    );
  }

  const displayName = getLinkDisplayName(link);
  const refs = link["node-edge-point"];
  const spanElements = getSpanElements(link);

  const getNodeForRef = (nodeUuid: string) =>
    topology.node.find((n) => n.uuid === nodeUuid);

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
        <h1 className="text-xl font-bold text-gray-900 mb-1">{displayName}</h1>
        <CopyableUuid uuid={link.uuid} />
        <div className="mt-2 flex flex-wrap gap-2">
          <span className="rounded-full bg-purple-100 px-2.5 py-0.5 text-xs font-medium text-purple-700">
            {link.direction}
          </span>
          {link["layer-protocol-name"]?.map((lp) => (
            <span key={lp} className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-600">
              {lp}
            </span>
          ))}
        </div>
      </div>

      {/* State */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">State</h2>
        <div className="flex flex-wrap gap-6">
          <StateIndicator label="Operational" value={link["operational-state"]} />
          <StateIndicator label="Administrative" value={link["administrative-state"]} />
          <StateIndicator label="Lifecycle" value={link["lifecycle-state"]} />
        </div>
      </div>

      {/* Endpoints */}
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-700 mb-4">Endpoints</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {refs.map((ref, i) => {
            const peerNode = getNodeForRef(ref["node-uuid"]);
            return (
              <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 p-4">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                  {i === 0 ? "Source" : "Destination"}
                </p>
                {peerNode ? (
                  <>
                    <p className="font-semibold text-gray-800">{getNodeDisplayName(peerNode)}</p>
                    <p className="font-mono text-xs text-gray-400 mt-0.5">{ref["node-uuid"]}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      NEP: <span className="font-mono">{ref["node-edge-point-uuid"].slice(0, 16)}…</span>
                    </p>
                    <button
                      onClick={() => navigate(`/topology/${topoId}/node/${ref["node-uuid"]}`)}
                      className="mt-2 text-xs text-blue-600 hover:underline"
                    >
                      View Device →
                    </button>
                  </>
                ) : (
                  <p className="font-mono text-xs text-gray-500">{ref["node-uuid"]}</p>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Span elements */}
      {spanElements.length > 0 && (
        <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">
            Span Elements ({spanElements.length})
          </h2>
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-3 py-2 text-left font-semibold text-gray-600 uppercase">Type</th>
                  <th className="px-3 py-2 text-left font-semibold text-gray-600 uppercase">UID</th>
                  <th className="px-3 py-2 text-left font-semibold text-gray-600 uppercase">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {spanElements.map((el, i) => {
                  const elem = el as Record<string, unknown>;
                  return (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-700">{String(elem.type ?? "—")}</td>
                      <td className="px-3 py-2 font-mono text-gray-500">{String(elem.uid ?? String(i))}</td>
                      <td className="px-3 py-2 text-gray-500">
                        {Object.entries(elem)
                          .filter(([k]) => !["type", "uid"].includes(k))
                          .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                          .join(", ")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Raw JSON */}
      <JsonViewer data={link} />
    </div>
  );
}
