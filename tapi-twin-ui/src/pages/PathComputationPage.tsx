import { useState, useEffect } from "react";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { createClient, ApiError } from "@/api/client";
import { extractName } from "@/lib/tapi-helpers";
import type {
  ModulationFormat,
  PathCandidate,
  PathInfoResponse,
  ServiceInterfacePoint,
} from "@/api/types";
import { Loader2, Route, ArrowRight } from "lucide-react";
import { cn } from "@/lib/cn";

const MODULATION_OPTIONS: ModulationFormat[] = [
  "DP-QPSK",
  "DP-16QAM",
  "DP-64QAM",
];

function getSipLabel(sip: ServiceInterfacePoint): string {
  return (
    extractName(sip.name, "node-name") ||
    extractName(sip.name, "sip-name") ||
    sip.uuid.slice(0, 8)
  );
}

export default function PathComputationPage() {
  const { baseUrl } = useConnectionStore();
  const sips = useTopologyStore((s) => s.getSips());
  const fetchTopology = useTopologyStore((s) => s.fetchTopology);
  const [sipA, setSipA] = useState<string>("");
  const [sipZ, setSipZ] = useState<string>("");
  const [maxCandidates, setMaxCandidates] = useState(1);
  const [modulation, setModulation] = useState<ModulationFormat>("DP-QPSK");
  const [paths, setPaths] = useState<PathCandidate[] | null>(null);
  const [pathInfo, setPathInfo] = useState<PathInfoResponse | null>(null);
  const [pathInfoLoading, setPathInfoLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (baseUrl) void fetchTopology(baseUrl);
  }, [baseUrl, fetchTopology]);

  useEffect(() => {
    if (sips.length >= 1 && !sipA) setSipA(sips[0].uuid);
    if (sips.length >= 2 && !sipZ) setSipZ(sips[1].uuid);
  }, [sips, sipA, sipZ]);

  async function handleCompute() {
    if (!sipA || !sipZ) return;
    setLoading(true);
    setError(null);
    setPaths(null);
    setPathInfo(null);
    try {
      const res = await createClient(baseUrl).computePath({
        "end-point": [
          { "service-interface-point": { "service-interface-point-uuid": sipA } },
          { "service-interface-point": { "service-interface-point-uuid": sipZ } },
        ],
        "max-candidates": maxCandidates,
      });
      const pathList = res["tapi-path-computation:output"].path ?? [];
      setPaths(pathList);
      if (pathList.length > 0) {
        setPathInfoLoading(true);
        try {
          const info = await createClient(baseUrl).getPathInfo(
            sipA,
            sipZ,
            modulation
          );
          setPathInfo(info);
        } catch {
          setPathInfo(null);
        } finally {
          setPathInfoLoading(false);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }


  return (
    <div className="mx-auto max-w-4xl p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Path Computation</h1>
        <p className="mt-1 text-sm text-gray-500">
          Compute path(s) between two service interface points (T-API path computation API).
        </p>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm space-y-5">
        <h2 className="text-base font-semibold text-gray-800">Compute path</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              A-End (source) SIP
            </label>
            <select
              value={sipA}
              onChange={(e) => setSipA(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm bg-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select SIP…</option>
              {sips.map((s) => (
                <option key={s.uuid} value={s.uuid}>
                  {getSipLabel(s)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Z-End (destination) SIP
            </label>
            <select
              value={sipZ}
              onChange={(e) => setSipZ(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm bg-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select SIP…</option>
              {sips.map((s) => (
                <option key={s.uuid} value={s.uuid}>
                  {getSipLabel(s)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Max path candidates (k-shortest)
            </label>
            <input
              type="number"
              min={1}
              max={10}
              value={maxCandidates}
              onChange={(e) =>
                setMaxCandidates(Math.max(1, Math.min(10, Number(e.target.value))))
              }
              className="w-24 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Modulation (for GSNR estimate)
            </label>
            <select
              value={modulation}
              onChange={(e) => setModulation(e.target.value as ModulationFormat)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm bg-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {MODULATION_OPTIONS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          onClick={handleCompute}
          disabled={loading || !sipA || !sipZ || sipA === sipZ || !baseUrl}
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-white transition-colors",
            loading || !sipA || !sipZ || sipA === sipZ
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700"
          )}
        >
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          <Route className="h-4 w-4" />
          Compute path
        </button>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
      </div>

      {paths !== null && paths.length > 0 && (
        <>
          {/* Path & distance (same as Services detail panel) */}
          <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-800">
                Path & distance
              </h2>
            </div>
            <div className="p-4 space-y-4">
              {pathInfoLoading ? (
                <p className="text-sm text-slate-500 flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading path details…
                </p>
              ) : pathInfo?.hops && pathInfo.hops.length > 0 ? (
                <div className="space-y-2">
                  {pathInfo["total-fiber-km"] != null && (
                    <p className="text-sm text-slate-700">
                      Total fiber:{" "}
                      <span className="font-medium">
                        {pathInfo["total-fiber-km"].toFixed(1)} km
                      </span>
                    </p>
                  )}
                  <div className="overflow-x-auto pb-1">
                    <div className="flex items-center min-w-max gap-0 flex-wrap">
                      {pathInfo.hops.map((hop) => (
                        <span key={hop.uid} className="flex items-center">
                          <span
                            className={cn(
                              "inline-flex flex-col items-center rounded border px-2 py-1 text-xs leading-tight",
                              hop.type === "Transceiver"
                                ? "bg-blue-50 border-blue-200 text-blue-800"
                                : "bg-violet-50 border-violet-200 text-violet-800"
                            )}
                          >
                            <span className="text-[9px] font-semibold uppercase opacity-70">
                              {hop.type === "Transceiver" ? "TRX" : "ROADM"}
                            </span>
                            <span className="font-mono whitespace-nowrap">
                              {hop.uid}
                            </span>
                          </span>
                          {hop.distance_km_to_next != null && (
                            <span className="flex items-center text-slate-400 px-1">
                              <span className="block h-px w-3 bg-slate-300" />
                              <span className="text-[10px] px-1 whitespace-nowrap text-slate-500">
                                {hop.distance_km_to_next.toFixed(1)} km
                              </span>
                              <span className="block h-px w-3 bg-slate-300" />
                              <span className="text-slate-400 text-[9px]">→</span>
                            </span>
                          )}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              ) : pathInfo && !pathInfoLoading ? (
                <p className="text-xs text-slate-400 italic">
                  Path details unavailable (GNPy not configured or path not computed).
                </p>
              ) : null}
            </div>
          </div>

          {/* Estimated QoT (GSNR, OSNR, etc.) */}
          {pathInfo?.measurements && (
            <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
                <h2 className="text-sm font-semibold text-gray-800">
                  Estimated QoT ({pathInfo["modulation-format"]})
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">
                  From GNPy propagation + transients
                </p>
              </div>
              <div className="p-4 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    GSNR
                  </p>
                  <p className="text-lg font-semibold text-slate-800 font-mono">
                    {pathInfo.measurements["gsnr-db"].toFixed(2)} dB
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    OSNR
                  </p>
                  <p className="text-lg font-semibold text-slate-800 font-mono">
                    {pathInfo.measurements["osnr-db"].toFixed(2)} dB
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    Q-factor
                  </p>
                  <p className="text-lg font-semibold text-slate-800 font-mono">
                    {pathInfo.measurements["q-factor-db"].toFixed(2)} dB
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    Pre-FEC BER
                  </p>
                  <p className="text-sm font-semibold text-slate-800 font-mono break-all">
                    {pathInfo.measurements["pre-fec-ber"].toExponential(2)}
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    CD
                  </p>
                  <p className="text-sm font-semibold text-slate-800 font-mono">
                    {pathInfo.measurements["chromatic-dispersion-ps-per-nm"].toFixed(1)} ps/nm
                  </p>
                </div>
                <div className="rounded-lg bg-slate-50 p-3">
                  <p className="text-[10px] uppercase tracking-wide text-slate-500">
                    PMD
                  </p>
                  <p className="text-sm font-semibold text-slate-800 font-mono">
                    {pathInfo.measurements["pmd-ps"].toFixed(2)} ps
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Path result candidates (node list) */}
          <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-800">
                Path result{paths.length > 1 ? "s" : ""}
              </h2>
            </div>
            <div className="p-4 space-y-6">
              {paths.map((path, idx) => (
                <div
                  key={idx}
                  className="border border-gray-200 rounded-lg p-4 bg-gray-50/50"
                >
                  <p className="text-xs font-medium text-gray-500 mb-2">
                    Candidate {idx + 1}
                  </p>
                  <div className="flex flex-wrap items-center gap-1">
                    {(path.node ?? []).map((ref, i) => (
                      <span key={`n-${i}`} className="flex items-center gap-1">
                        <span className="rounded bg-blue-100 px-2 py-0.5 font-mono text-xs text-blue-800">
                          {ref["node-uuid"].slice(0, 8)}…
                        </span>
                        {i < (path.node?.length ?? 0) - 1 && (
                          <ArrowRight className="h-3 w-3 text-gray-400" />
                        )}
                      </span>
                    ))}
                  </div>
                  <p className="text-xs text-gray-500 mt-2">
                    Links: {(path.link ?? []).length} · Nodes:{" "}
                    {(path.node ?? []).length}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {paths !== null && paths.length === 0 && (
        <p className="text-sm text-gray-500">
          No path found between the selected SIPs.
        </p>
      )}
    </div>
  );
}
