import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Network, Link2, Cpu, Radio, CheckCircle, XCircle, Clock } from "lucide-react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { createClient } from "@/api/client";
import type { RoadmToRoadmLink } from "@/api/types";
import { countByOperState } from "@/lib/tapi-helpers";
import EmptyState from "@/components/common/EmptyState";
import RefreshButton from "@/components/common/RefreshButton";

function countRoadmLinksByOperState(links: RoadmToRoadmLink[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const link of links) {
    const s = link["operational-state"] ?? "UNKNOWN";
    counts[s] = (counts[s] ?? 0) + 1;
  }
  return counts;
}

interface SummaryCardProps {
  label: string;
  value: number;
  icon: React.ElementType;
  color: string;
  onClick?: () => void;
}

function SummaryCard({ label, value, icon: Icon, color, onClick }: SummaryCardProps) {
  return (
    <div
      onClick={onClick}
      className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm ${onClick ? "cursor-pointer hover:shadow-md transition-shadow" : ""}`}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-500">{label}</p>
          <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
        </div>
        <div className={`rounded-full p-3 ${color}`}>
          <Icon className="h-6 w-6 text-white" />
        </div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { baseUrl, status, lastSuccessAt } = useConnectionStore();
  const { data, isLoading, error, fetchTopology, isStale } = useTopologyStore();
  const [roadmLinks, setRoadmLinks] = useState<RoadmToRoadmLink[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    if (baseUrl) void fetchTopology(baseUrl);
  }, [baseUrl, fetchTopology]);

  useEffect(() => {
    if (!baseUrl || !data) {
      setRoadmLinks([]);
      return;
    }
    createClient(baseUrl)
      .getRoadmToRoadmLinks()
      .then((res) => setRoadmLinks(res.links))
      .catch(() => setRoadmLinks([]));
  }, [baseUrl, data]);

  const topologies = data?.topologies ?? [];
  const allNodes = topologies.flatMap((t) => t.node);
  const allSips = data?.sips ?? [];

  const linkStates = countRoadmLinksByOperState(roadmLinks);
  const enabledLinks = linkStates["ENABLED"] ?? 0;
  const disabledLinks = linkStates["DISABLED"] ?? 0;

  const nodeStates = countByOperState(allNodes);
  const enabledNodes = nodeStates["ENABLED"] ?? 0;
  const disabledNodes = nodeStates["DISABLED"] ?? 0;

  const statusConfig = {
    connected: { label: "Connected", color: "text-green-600", bg: "bg-green-50 border-green-200", icon: CheckCircle },
    disconnected: { label: "Disconnected", color: "text-red-600", bg: "bg-red-50 border-red-200", icon: XCircle },
    checking: { label: "Connecting…", color: "text-blue-600", bg: "bg-blue-50 border-blue-200", icon: Clock },
    stale: { label: "Stale Data", color: "text-amber-600", bg: "bg-amber-50 border-amber-200", icon: Clock },
  }[status];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <RefreshButton
          onClick={() => void fetchTopology(baseUrl)}
          isLoading={isLoading}
        />
      </div>

      {error && !data && (
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {isStale && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-700">
          Showing cached data. Click Refresh to fetch live data.
        </div>
      )}

      {/* Connection status */}
      <div className={`rounded-xl border p-5 ${statusConfig.bg}`}>
        <div className="flex items-center gap-3">
          <statusConfig.icon className={`h-6 w-6 ${statusConfig.color}`} />
          <div>
            <p className={`font-semibold ${statusConfig.color}`}>{statusConfig.label}</p>
            <p className="text-sm text-gray-600">{baseUrl || "No URL configured"}</p>
          </div>
          {lastSuccessAt && (
            <p className="ml-auto text-xs text-gray-400">
              Last sync: {new Date(lastSuccessAt).toLocaleTimeString()}
            </p>
          )}
        </div>
      </div>

      {!data && !isLoading && (
        <EmptyState
          icon={<Network className="h-8 w-8" />}
          title="No topology loaded"
          description="Configure the DT URL in settings and click Refresh to load the network topology."
          action={
            <button
              onClick={() => navigate("/settings")}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              Open Settings
            </button>
          }
        />
      )}

      {data && (
        <>
          {/* Topology summary — links first */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <SummaryCard
              label="Links (ROADM–ROADM)"
              value={roadmLinks.length}
              icon={Link2}
              color="bg-purple-500"
              onClick={() => navigate("/topology")}
            />
            <SummaryCard
              label="Nodes"
              value={allNodes.length}
              icon={Cpu}
              color="bg-green-500"
              onClick={() => navigate("/topology")}
            />
            <SummaryCard
              label="Topologies"
              value={topologies.length}
              icon={Network}
              color="bg-blue-500"
              onClick={() => navigate("/topology")}
            />
            <SummaryCard
              label="SIPs"
              value={allSips.length}
              icon={Radio}
              color="bg-orange-500"
            />
          </div>

          {/* Links and nodes breakdown — links first */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Links (ROADM–ROADM)</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Link2 className="h-4 w-4 text-purple-500" />
                    <span className="text-sm text-gray-600">Total</span>
                  </div>
                  <span className="font-bold text-gray-900">{roadmLinks.length}</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-4 w-4 text-green-500" />
                    <span className="text-sm text-gray-600">Enabled</span>
                  </div>
                  <span className="font-bold text-green-700">{enabledLinks}</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="h-4 w-4 text-red-500" />
                    <span className="text-sm text-gray-600">Disabled</span>
                  </div>
                  <span className="font-bold text-red-700">{disabledLinks}</span>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-700 mb-4">Nodes (operational state)</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Cpu className="h-4 w-4 text-green-500" />
                    <span className="text-sm text-gray-600">Total</span>
                  </div>
                  <span className="font-bold text-gray-900">{allNodes.length}</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-4 w-4 text-green-500" />
                    <span className="text-sm text-gray-600">Enabled</span>
                  </div>
                  <span className="font-bold text-green-700">{enabledNodes}</span>
                </div>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <XCircle className="h-4 w-4 text-red-500" />
                    <span className="text-sm text-gray-600">Disabled</span>
                  </div>
                  <span className="font-bold text-red-700">{disabledNodes}</span>
                </div>
              </div>
            </div>
          </div>

          {/* Quick actions */}
          <div className="flex flex-wrap gap-3">
            <button
              onClick={() => navigate("/topology")}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
            >
              View Topology
            </button>
            <button
              onClick={() => navigate("/monitoring")}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            >
              Open Monitoring
            </button>
          </div>
        </>
      )}
    </div>
  );
}
