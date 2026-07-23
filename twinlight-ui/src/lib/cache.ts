// localStorage read/write with versioning and timestamps

const SCHEMA_VERSION = 1;

interface CacheEntry<T> {
  v: number;
  data: T;
  fetchedAt: number;
  etag?: string;
}

export function cacheRead<T>(key: string): CacheEntry<T> | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const entry = JSON.parse(raw) as CacheEntry<T>;
    if (entry.v !== SCHEMA_VERSION) {
      localStorage.removeItem(key);
      return null;
    }
    return entry;
  } catch {
    return null;
  }
}

export function cacheWrite<T>(key: string, data: T, etag?: string): void {
  const entry: CacheEntry<T> = {
    v: SCHEMA_VERSION,
    data,
    fetchedAt: Date.now(),
    etag,
  };
  try {
    localStorage.setItem(key, JSON.stringify(entry));
  } catch {
    // Ignore quota exceeded errors
  }
}

export function cacheDelete(key: string): void {
  localStorage.removeItem(key);
}

/** Purge all keys with a given prefix */
export function cachePurgeByPrefix(prefix: string): void {
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) keys.push(k);
  }
  keys.forEach((k) => localStorage.removeItem(k));
}

/** Return approximate bytes used by keys with a given prefix */
export function cacheUsageBytes(prefix: string): number {
  let bytes = 0;
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith(prefix)) {
      bytes += (localStorage.getItem(k) ?? "").length * 2; // UTF-16
    }
  }
  return bytes;
}

export const CACHE_KEYS = {
  SETTINGS: "twinlight-ui:settings",
  TOPOLOGY: "twinlight-ui:topology",
  SERIES_INDEX: "twinlight-ui:series:index",
  series: (metric: string, serviceUuid: string) =>
    `twinlight-ui:series:${metric}:${serviceUuid}`,
} as const;

export const TOPOLOGY_MAX_AGE_MS = 5 * 60 * 1000; // 5 minutes
