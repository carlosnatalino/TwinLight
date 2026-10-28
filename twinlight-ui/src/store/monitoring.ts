import { create } from "zustand";
import type { ServiceOpm } from "@/api/types";
import {
  appendPointWithExisting,
  clearAllSeries,
  getSeriesIndexEntries,
  mergeSeries,
  readSeries,
  MAX_SERIES_POINTS,
  type DataPoint,
} from "@/lib/time-series";

/** serviceUuid -> metric -> DataPoint[] so charts re-render when new points arrive */
export type SeriesCache = Record<string, Record<string, DataPoint[]>>;

interface MonitoringState {
  currentOpm: Record<string, ServiceOpm>; // serviceUuid → latest OPM
  /** In-memory series so UI always sees latest; also written to localStorage by appendPoint */
  seriesCache: SeriesCache;
  isPolling: boolean;
  isPaused: boolean;
  lastPollAt: number | null;
  pollError: string | null;

  // Actions
  ingestOpm: (opm: ServiceOpm) => void;
  /** Load series for one service from localStorage into seriesCache (e.g. on mount). Merges with in-memory. */
  hydrateSeriesForService: (serviceUuid: string) => void;
  /** Restore all persisted series from localStorage into seriesCache (e.g. on Monitoring mount after reload). */
  hydrateAllFromStorage: () => void;
  setIsPolling: (polling: boolean) => void;
  setPaused: (paused: boolean) => void;
  setLastPollAt: (ts: number) => void;
  setPollError: (err: string | null) => void;
  clearHistory: () => void;
}

/**
 * Metrics that get a historical trend chart. Six, laid out 2x3.
 *
 * `osnr-01nm-db` is deliberately not here: it is the same measurement as
 * `osnr-db` on a different reference bandwidth, so its trend line would be
 * an exact copy of the OSNR one shifted by a constant. It appears as a
 * current-value tile instead — see TILE_METRICS.
 */
const OPM_METRICS = [
  "osnr-db",
  "gsnr-db",
  "pre-fec-ber",
  "q-factor-db",
  "chromatic-dispersion-ps-per-nm",
  "pmd-ps",
] as const;

/** Signal-quality tiles: the charted six, plus OSNR@0.1nm. */
const SIGNAL_TILE_METRICS = [...OPM_METRICS, "osnr-01nm-db"] as const;

/**
 * Coding-layer tiles, shown as their own group: two post-FEC BER estimates
 * from different models, and the GMI. Tiles only — on a healthy link the
 * post-FEC BERs sit at their floor, which makes a flat, uninformative chart.
 */
const CODING_TILE_METRICS = [
  "post-fec-ber-threshold",
  "post-fec-ber-hd",
  "gmi-bits-per-symbol",
  "ngmi",
] as const;

/** Every metric with a current-value tile; each keeps a sparkline series. */
const TILE_METRICS = [...SIGNAL_TILE_METRICS, ...CODING_TILE_METRICS] as const;

export type OpmMetric = (typeof OPM_METRICS)[number];
export type TileMetric = (typeof TILE_METRICS)[number];

export { OPM_METRICS, SIGNAL_TILE_METRICS, CODING_TILE_METRICS, TILE_METRICS };

export const useMonitoringStore = create<MonitoringState>()((set) => ({
  currentOpm: {},
  seriesCache: {},
  isPolling: false,
  isPaused: false,
  lastPollAt: null,
  pollError: null,

  // Ingest one OPM snapshot: append to in-memory series first so history accumulates
  // even when localStorage fails (private mode, quota). Then persist when possible.
  ingestOpm: (opm: ServiceOpm) => {
    const serviceUuid = opm["service-uuid"];
    const ts = opm.timestamp * 1000; // convert to ms if needed
    const measurements = opm.measurements;

    set((state) => {
      const updates: Record<string, DataPoint[]> = {};
      for (const metric of TILE_METRICS) {
        const value = measurements[metric];
        if (value !== undefined && !isNaN(value)) {
          const existing =
            state.seriesCache[serviceUuid]?.[metric] ?? readSeries(metric, serviceUuid);
          const trimmed = appendPointWithExisting(
            existing,
            metric,
            serviceUuid,
            { t: ts, v: value }
          );
          updates[metric] = trimmed;
        }
      }
      return {
        currentOpm: {
          ...state.currentOpm,
          [serviceUuid]: opm,
        },
        seriesCache: {
          ...state.seriesCache,
          [serviceUuid]: {
            ...state.seriesCache[serviceUuid],
            ...updates,
          },
        },
        lastPollAt: Date.now(),
        pollError: null,
      };
    });
  },

  // Merge persisted series with in-memory so we never overwrite newer points with stale storage.
  hydrateSeriesForService: (serviceUuid: string) => {
    set((state) => {
      const updates: Record<string, DataPoint[]> = {};
      for (const metric of TILE_METRICS) {
        const persisted = readSeries(metric, serviceUuid);
        const inMemory = state.seriesCache[serviceUuid]?.[metric] ?? [];
        const merged = mergeSeries(inMemory, persisted, MAX_SERIES_POINTS);
        if (merged.length > 0) updates[metric] = merged;
      }
      if (Object.keys(updates).length === 0) return state;
      return {
        seriesCache: {
          ...state.seriesCache,
          [serviceUuid]: {
            ...state.seriesCache[serviceUuid],
            ...updates,
          },
        },
      };
    });
  },

  // On reload, seriesCache is empty; restore all persisted series from localStorage using the index.
  hydrateAllFromStorage: () => {
    const entries = getSeriesIndexEntries();
    if (entries.length === 0) return;
    set((state) => {
      const nextCache = { ...state.seriesCache };
      for (const { metric, serviceUuid } of entries) {
        const persisted = readSeries(metric, serviceUuid);
        if (persisted.length === 0) continue;
        const inMemory = nextCache[serviceUuid]?.[metric] ?? [];
        const merged = mergeSeries(inMemory, persisted, MAX_SERIES_POINTS);
        if (!nextCache[serviceUuid]) nextCache[serviceUuid] = {};
        nextCache[serviceUuid][metric] = merged;
      }
      return { seriesCache: nextCache };
    });
  },

  setIsPolling: (isPolling) => set({ isPolling }),
  setPaused: (isPaused) => set({ isPaused }),
  setLastPollAt: (ts) => set({ lastPollAt: ts }),
  setPollError: (err) => set({ pollError: err }),

  clearHistory: () => {
    clearAllSeries();
    set({ currentOpm: {}, seriesCache: {} });
  },
}));
