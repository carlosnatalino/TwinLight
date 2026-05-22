// Ring buffer implementation for monitoring time-series data

import { cacheRead, cacheWrite, CACHE_KEYS } from "./cache";

export interface DataPoint {
  t: number; // Unix ms timestamp
  v: number; // value
}

export interface SeriesIndex {
  metric: string;
  serviceUuid: string;
  count: number;
}

const DEFAULT_MAX_POINTS = 1000;
export const MAX_SERIES_POINTS = DEFAULT_MAX_POINTS;

export function readSeries(metric: string, serviceUuid: string): DataPoint[] {
  const key = CACHE_KEYS.series(metric, serviceUuid);
  const entry = cacheRead<DataPoint[]>(key);
  return entry?.data ?? [];
}

/**
 * Appends a point to an existing series (in-memory or from cache), trims to maxPoints,
 * persists to localStorage when possible, and returns the new array.
 * Prefer passing existing from the store so history accumulates even when localStorage
 * fails (e.g. private mode, quota).
 */
export function appendPoint(
  metric: string,
  serviceUuid: string,
  point: DataPoint,
  maxPoints = DEFAULT_MAX_POINTS
): DataPoint[] {
  const key = CACHE_KEYS.series(metric, serviceUuid);
  const existing = cacheRead<DataPoint[]>(key)?.data ?? [];
  return appendPointWithExisting(existing, metric, serviceUuid, point, maxPoints);
}

/**
 * Same as appendPoint but takes the current series as first argument so the store
 * can pass state.seriesCache[serviceUuid]?.[metric], ensuring in-session accumulation
 * even if localStorage is unavailable.
 */
export function appendPointWithExisting(
  existing: DataPoint[],
  metric: string,
  serviceUuid: string,
  point: DataPoint,
  maxPoints = DEFAULT_MAX_POINTS
): DataPoint[] {
  const updated = [...existing, point];
  const trimmed =
    updated.length > maxPoints ? updated.slice(updated.length - maxPoints) : updated;
  const key = CACHE_KEYS.series(metric, serviceUuid);
  try {
    cacheWrite(key, trimmed);
    updateSeriesIndex(metric, serviceUuid, trimmed.length);
  } catch {
    // localStorage may be full or disabled; in-memory series is still updated
  }
  return trimmed;
}

export function clearSeries(metric: string, serviceUuid: string): void {
  const key = CACHE_KEYS.series(metric, serviceUuid);
  localStorage.removeItem(key);
  removeFromSeriesIndex(metric, serviceUuid);
}

export function clearAllSeries(): void {
  const index = readSeriesIndex();
  for (const entry of index) {
    const key = CACHE_KEYS.series(entry.metric, entry.serviceUuid);
    localStorage.removeItem(key);
  }
  localStorage.removeItem(CACHE_KEYS.SERIES_INDEX);
}

export function filterByTimeRange(points: DataPoint[], rangeMs: number): DataPoint[] {
  if (rangeMs <= 0) return points;
  const cutoff = Date.now() - rangeMs;
  return points.filter((p) => p.t >= cutoff);
}

/**
 * Merges two series by timestamp: concatenates, sorts by t, dedupes (same t keeps later value),
 * then trims to maxPoints (keeps most recent). Use when combining in-memory and localStorage
 * so reloads don't lose data and we never overwrite newer in-memory points with stale storage.
 */
export function mergeSeries(
  a: DataPoint[],
  b: DataPoint[],
  maxPoints = DEFAULT_MAX_POINTS
): DataPoint[] {
  const byT = new Map<number, number>();
  for (const p of a) byT.set(p.t, p.v);
  for (const p of b) byT.set(p.t, p.v);
  const merged = Array.from(byT.entries(), ([t, v]) => ({ t, v })).sort((x, y) => x.t - y.t);
  return merged.length > maxPoints ? merged.slice(merged.length - maxPoints) : merged;
}

/** Returns all (metric, serviceUuid) entries from the persisted series index for bulk hydrate. */
export function getSeriesIndexEntries(): { metric: string; serviceUuid: string }[] {
  return readSeriesIndex().map((e) => ({ metric: e.metric, serviceUuid: e.serviceUuid }));
}

function readSeriesIndex(): SeriesIndex[] {
  const entry = cacheRead<SeriesIndex[]>(CACHE_KEYS.SERIES_INDEX);
  return entry?.data ?? [];
}

function updateSeriesIndex(metric: string, serviceUuid: string, count: number): void {
  const index = readSeriesIndex();
  const existing = index.find((e) => e.metric === metric && e.serviceUuid === serviceUuid);
  if (existing) {
    existing.count = count;
  } else {
    index.push({ metric, serviceUuid, count });
  }
  cacheWrite(CACHE_KEYS.SERIES_INDEX, index);
}

function removeFromSeriesIndex(metric: string, serviceUuid: string): void {
  const index = readSeriesIndex().filter(
    (e) => !(e.metric === metric && e.serviceUuid === serviceUuid)
  );
  cacheWrite(CACHE_KEYS.SERIES_INDEX, index);
}

export function exportSeriesAsCsv(
  metric: string,
  serviceUuid: string,
  label: string
): string {
  const points = readSeries(metric, serviceUuid);
  const header = "timestamp,datetime," + label;
  const rows = points.map((p) => {
    const dt = new Date(p.t).toISOString();
    return `${p.t},${dt},${p.v}`;
  });
  return [header, ...rows].join("\n");
}
