import { useCallback } from "react";
import { filterByTimeRange, type DataPoint } from "@/lib/time-series";
import { useMonitoringStore } from "@/store/monitoring";
import type { TileMetric } from "@/store/monitoring";

export const TIME_RANGES = [
  { label: "5 min", ms: 5 * 60 * 1000 },
  { label: "15 min", ms: 15 * 60 * 1000 },
  { label: "1 hour", ms: 60 * 60 * 1000 },
  { label: "All", ms: 0 },
] as const;

export type TimeRange = (typeof TIME_RANGES)[number];

/** History comes from store (seriesCache) so charts re-render when new OPM is ingested. */
export function useMonitoringHistory(serviceUuid: string | null) {
  const seriesCache = useMonitoringStore((s) => s.seriesCache);

  const getHistory = useCallback(
    (metric: TileMetric, rangeMs: number): DataPoint[] => {
      if (!serviceUuid) return [];
      const all = seriesCache[serviceUuid]?.[metric] ?? [];
      return filterByTimeRange(all, rangeMs);
    },
    [serviceUuid, seriesCache]
  );

  return { getHistory };
}
