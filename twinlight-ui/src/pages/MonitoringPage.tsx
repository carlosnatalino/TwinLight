import { useState, useCallback, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useConnectionStore } from "@/store/connection";
import { useMonitoringStore, OPM_METRICS, TILE_METRICS } from "@/store/monitoring";
import { usePolling } from "@/hooks/usePolling";
import { useMonitoringHistory, TIME_RANGES, type TimeRange } from "@/hooks/useMonitoringHistory";
import { createClient } from "@/api/client";
import MetricChart from "@/components/monitoring/MetricChart";
import MetricCard from "@/components/monitoring/MetricCard";
import HistoryControls from "@/components/monitoring/HistoryControls";
import ConstellationDiagram from "@/components/monitoring/ConstellationDiagram";
import EyeDiagram from "@/components/monitoring/EyeDiagram";
import EmptyState from "@/components/common/EmptyState";
import { Activity, RefreshCw } from "lucide-react";
import { exportSeriesAsCsv } from "@/lib/time-series";
import type { TileMetric } from "@/store/monitoring";
import type { DataPoint } from "@/lib/time-series";
import type {
  ServiceInfoResponse,
  ConstellationResponse,
  EyeDiagramResponse,
} from "@/api/types";
import { cn } from "@/lib/cn";

const METRIC_META: Record<TileMetric, { label: string; unit: string; color: string }> = {
  "osnr-db": { label: "OSNR", unit: "dB", color: "#3b82f6" },
  // Same measurement as OSNR, referenced to 0.1 nm instead of the signal
  // bandwidth — what the literature and an OSA quote. Tile only: its trend
  // is the OSNR trend shifted by a constant.
  "osnr-01nm-db": { label: "OSNR @0.1nm", unit: "dB", color: "#3b82f6" },
  "gsnr-db": { label: "GSNR", unit: "dB", color: "#8b5cf6" },
  "pre-fec-ber": { label: "Pre-FEC BER", unit: "BER", color: "#ef4444" },
  "q-factor-db": { label: "Q-Factor", unit: "dB", color: "#22c55e" },
  "chromatic-dispersion-ps-per-nm": { label: "Chromatic Dispersion", unit: "ps/nm", color: "#f59e0b" },
  "pmd-ps": { label: "PMD", unit: "ps", color: "#06b6d4" },
};

function computeStats(points: DataPoint[]): { min: number; max: number; avg: number } | null {
  if (points.length === 0) return null;
  const values = points.map((p) => p.v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const avg = values.reduce((s, v) => s + v, 0) / values.length;
  return { min, max, avg };
}

// ─── Service Info Panel ───────────────────────────────────────────────────────

function ServiceInfoPanel({ info }: { info: ServiceInfoResponse }) {
  const { hops } = info;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm space-y-3">
      {/* Metadata row */}
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {info.name && (
          <span className="text-sm text-gray-700">
            <span className="font-medium text-gray-500">Name</span>
            <span className="mx-1.5 text-gray-300">·</span>
            {info.name}
          </span>
        )}
        <span className="text-sm text-gray-700">
          <span className="font-medium text-gray-500">Modulation</span>
          <span className="mx-1.5 text-gray-300">·</span>
          <span className="font-mono">{info["modulation-format"]}</span>
        </span>
        {info["total-fiber-km"] !== null && (
          <span className="text-sm text-gray-700">
            <span className="font-medium text-gray-500">Total distance</span>
            <span className="mx-1.5 text-gray-300">·</span>
            {info["total-fiber-km"].toFixed(1)} km
          </span>
        )}
        <span className="text-sm text-gray-700">
          <span className="font-medium text-gray-500">UUID</span>
          <span className="mx-1.5 text-gray-300">·</span>
          <span className="font-mono text-xs text-gray-500">{info["service-uuid"]}</span>
        </span>
      </div>

      {/* Path visualization */}
      {hops && hops.length > 0 ? (
        <div className="overflow-x-auto pb-1">
          <div className="flex items-center min-w-max gap-0">
            {hops.map((hop) => (
              <span key={hop.uid} className="flex items-center">
                {/* Node chip */}
                <span
                  className={cn(
                    "inline-flex flex-col items-center rounded-lg border px-3 py-1.5 leading-tight",
                    hop.type === "Transceiver"
                      ? "bg-blue-50 border-blue-200 text-blue-800"
                      : "bg-violet-50 border-violet-200 text-violet-800"
                  )}
                >
                  <span className="text-[9px] font-semibold uppercase tracking-wider opacity-60">
                    {hop.type === "Transceiver" ? "TRX" : "ROADM"}
                  </span>
                  <span className="text-xs font-medium whitespace-nowrap">{hop.uid}</span>
                </span>

                {/* Distance connector */}
                {hop.distance_km_to_next !== null && (
                  <span className="flex items-center text-gray-400 px-1">
                    <span className="block h-px w-5 bg-gray-300" />
                    <span className="text-[11px] px-1.5 whitespace-nowrap text-gray-500">
                      {hop.distance_km_to_next.toFixed(1)} km
                    </span>
                    <span className="block h-px w-5 bg-gray-300" />
                    <span className="text-gray-400 text-[10px]">▶</span>
                  </span>
                )}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-xs text-gray-400 italic">
          Path topology unavailable — GNPy equipment file not configured or path not yet computed.
        </p>
      )}
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function MonitoringPage() {
  const [searchParams] = useSearchParams();
  const { baseUrl, pollInterval } = useConnectionStore();
  const {
    currentOpm,
    seriesCache,
    isPaused,
    setPaused,
    ingestOpm,
    hydrateSeriesForService,
    hydrateAllFromStorage,
    clearHistory,
  } = useMonitoringStore();
  const [selectedRange, setSelectedRange] = useState<TimeRange>(TIME_RANGES[1]); // 15 min default
  const [selectedServiceUuid, setSelectedServiceUuid] = useState<string | null>(() =>
    searchParams.get("service")
  );
  const [serviceInfo, setServiceInfo] = useState<ServiceInfoResponse | null>(null);
  // Cache service names so the dropdown can display them progressively
  const [serviceNames, setServiceNames] = useState<Record<string, string>>({});
  // Constellation and eye diagram state
  const [constellationData, setConstellationData] = useState<ConstellationResponse | null>(null);
  const [eyeDiagramData, setEyeDiagramData] = useState<EyeDiagramResponse | null>(null);
  const [diagramsLoading, setDiagramsLoading] = useState(false);
  const [diagramsError, setDiagramsError] = useState<string | null>(null);

  // When navigating with ?service=uuid, select that service (e.g. from Services "View monitoring")
  useEffect(() => {
    const serviceFromUrl = searchParams.get("service");
    if (serviceFromUrl) setSelectedServiceUuid(serviceFromUrl);
  }, [searchParams]);

  // Include both live services (currentOpm) and any service we have persisted history for
  const serviceUuids = [
    ...new Set([...Object.keys(currentOpm), ...Object.keys(seriesCache)]),
  ];

  // If the selected service is gone (deleted), fall back to first available
  const effectiveServiceUuid =
    selectedServiceUuid && serviceUuids.includes(selectedServiceUuid)
      ? selectedServiceUuid
      : serviceUuids[0] ?? null;

  const { getHistory } = useMonitoringHistory(effectiveServiceUuid);

  // Restore all persisted series from localStorage on mount so reload keeps history
  useEffect(() => {
    hydrateAllFromStorage();
  }, [hydrateAllFromStorage]);

  // Merge in localStorage for the selected service when it changes (e.g. after first poll)
  useEffect(() => {
    if (effectiveServiceUuid) hydrateSeriesForService(effectiveServiceUuid);
  }, [effectiveServiceUuid, hydrateSeriesForService]);

  // Fetch service info whenever the selected service changes
  useEffect(() => {
    if (!effectiveServiceUuid || !baseUrl) {
      setServiceInfo(null);
      return;
    }
    const client = createClient(baseUrl);
    client
      .getServiceInfo(effectiveServiceUuid)
      .then((info) => {
        setServiceInfo(info);
        // Cache the name for dropdown display
        if (info.name) {
          setServiceNames((prev) => ({ ...prev, [effectiveServiceUuid]: info.name! }));
        }
      })
      .catch(() => setServiceInfo(null));
  }, [effectiveServiceUuid, baseUrl]);

  // Fetch constellation and eye diagrams
  const fetchDiagrams = useCallback(async () => {
    if (!effectiveServiceUuid || !baseUrl) {
      setConstellationData(null);
      setEyeDiagramData(null);
      return;
    }
    setDiagramsLoading(true);
    setDiagramsError(null);
    const client = createClient(baseUrl);
    try {
      const [constellation, eye] = await Promise.all([
        client.getConstellation(effectiveServiceUuid, 5000),
        client.getEyeDiagram(effectiveServiceUuid, 150, 64),
      ]);
      setConstellationData(constellation);
      setEyeDiagramData(eye);
    } catch (err) {
      setDiagramsError(
        err instanceof Error ? err.message : "Failed to load diagrams"
      );
      setConstellationData(null);
      setEyeDiagramData(null);
    } finally {
      setDiagramsLoading(false);
    }
  }, [effectiveServiceUuid, baseUrl]);

  // Load diagrams when service changes
  useEffect(() => {
    fetchDiagrams();
  }, [fetchDiagrams]);

  const pollOnce = useCallback(async () => {
    if (!baseUrl || isPaused) return;
    try {
      const client = createClient(baseUrl);
      const resp = await client.getAllOpm();
      for (const svc of resp.services) {
        ingestOpm(svc);
      }
    } catch {
      // Silently ignore poll errors; connection banner handles UI feedback
    }
  }, [baseUrl, isPaused, ingestOpm]);

  usePolling({
    intervalMs: pollInterval * 1000,
    enabled: !!baseUrl,
    onTick: pollOnce,
    immediate: true,
  });

  function handleExportCsv() {
    if (!effectiveServiceUuid) return;
    const csvParts: string[] = [];
    for (const metric of OPM_METRICS) {
      csvParts.push(exportSeriesAsCsv(metric, effectiveServiceUuid, METRIC_META[metric].label));
      csvParts.push("");
    }
    const blob = new Blob([csvParts.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `opm-${effectiveServiceUuid.slice(0, 8)}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const hasData =
    serviceUuids.length > 0 ||
    OPM_METRICS.some(
      (m) => (seriesCache[effectiveServiceUuid ?? ""]?.[m]?.length ?? 0) > 0
    );

  const activeOpm = effectiveServiceUuid ? currentOpm[effectiveServiceUuid] : null;
  // Show charts when we have a selected service and either live OPM or persisted series
  const hasSeriesForService =
    effectiveServiceUuid &&
    OPM_METRICS.some(
      (m) => (seriesCache[effectiveServiceUuid]?.[m]?.length ?? 0) > 0
    );

  return (
    <div className="p-6 space-y-6">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-gray-900">Monitoring</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Optical Performance Monitoring — polling every {pollInterval}s
          </p>
        </div>
        <HistoryControls
          selectedRange={selectedRange}
          onRangeChange={setSelectedRange}
          isPaused={isPaused}
          onTogglePause={() => setPaused(!isPaused)}
          onExportCsv={handleExportCsv}
          onClearHistory={() => clearHistory()}
        />
      </div>

      {/* ── Service selector ────────────────────────────────────────────────── */}
      {serviceUuids.length > 0 && (
        <div className="flex items-center gap-3">
          <label
            htmlFor="service-select"
            className="text-sm font-medium text-gray-700 whitespace-nowrap"
          >
            Service
          </label>
          <select
            id="service-select"
            value={effectiveServiceUuid ?? ""}
            onChange={(e) => setSelectedServiceUuid(e.target.value || null)}
            className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {serviceUuids.map((uuid) => (
              <option key={uuid} value={uuid}>
                {serviceNames[uuid]
                  ? `${serviceNames[uuid]}  (${uuid.slice(0, 8)}…)`
                  : uuid}
              </option>
            ))}
          </select>
          <span className="text-xs text-gray-400">
            {serviceUuids.length} service{serviceUuids.length !== 1 ? "s" : ""}
          </span>
        </div>
      )}

      {!hasData && (
        <EmptyState
          icon={<Activity className="h-8 w-8" />}
          title="No monitoring data"
          description="Polling the DT for OPM data. Data will appear once connectivity services are provisioned."
        />
      )}

      {(activeOpm || hasSeriesForService) && effectiveServiceUuid && (
        <>
          {/* ── Service info ─────────────────────────────────────────────────── */}
          {serviceInfo && <ServiceInfoPanel info={serviceInfo} />}

          {/* ── Current values ───────────────────────────────────────────────── */}
          <div>
            <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">
              Current Values
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
              {TILE_METRICS.map((metric) => {
                const meta = METRIC_META[metric];
                const value = activeOpm?.measurements?.[metric];
                const series = seriesCache[effectiveServiceUuid]?.[metric] ?? [];
                const sparkline = series.slice(-20);
                const history = getHistory(metric, selectedRange.ms);
                const stats = computeStats(history);
                return (
                  <MetricCard
                    key={metric}
                    label={meta.label}
                    unit={meta.unit}
                    value={value}
                    sparklineData={sparkline}
                    stats={stats}
                  />
                );
              })}
            </div>
          </div>

          {/* ── Time-series charts ───────────────────────────────────────────── */}
          <div>
            <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-3">
              Historical Trends
            </h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {OPM_METRICS.map((metric) => {
                const meta = METRIC_META[metric];
                const history = getHistory(metric, selectedRange.ms);
                const value = activeOpm?.measurements?.[metric];
                const fallbackPoint =
                  activeOpm &&
                  value !== undefined &&
                  !Number.isNaN(value)
                    ? {
                        t: (activeOpm.timestamp ?? Date.now() / 1000) * 1000,
                        v: value,
                      }
                    : null;
                return (
                  <MetricChart
                    key={metric}
                    label={meta.label}
                    unit={meta.unit}
                    data={history}
                    fallbackPoint={fallbackPoint}
                    color={meta.color}
                  />
                );
              })}
            </div>
          </div>

          {/* ── Signal Quality Diagrams ─────────────────────────────────────────── */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide">
                Signal Quality Diagrams
              </h2>
              <button
                onClick={fetchDiagrams}
                disabled={diagramsLoading}
                className={cn(
                  "inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md",
                  "border border-gray-300 bg-white text-gray-700 hover:bg-gray-50",
                  "disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                )}
              >
                <RefreshCw
                  className={cn("h-3.5 w-3.5", diagramsLoading && "animate-spin")}
                />
                Refresh
              </button>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <ConstellationDiagram
                data={constellationData}
                isLoading={diagramsLoading}
                error={diagramsError}
              />
              <EyeDiagram
                data={eyeDiagramData}
                isLoading={diagramsLoading}
                error={diagramsError}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
