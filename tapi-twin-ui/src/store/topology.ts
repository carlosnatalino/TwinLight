import { create } from "zustand";
import type { TopologyData, Topology, TapiNode, TapiLink, ServiceInterfacePoint } from "@/api/types";
import { createClient } from "@/api/client";
import { cacheRead, cacheWrite, CACHE_KEYS, TOPOLOGY_MAX_AGE_MS } from "@/lib/cache";

interface TopologyState {
  data: TopologyData | null;
  fetchedAt: number | null;
  isLoading: boolean;
  error: string | null;
  isStale: boolean;

  // Actions
  fetchTopology: (baseUrl: string) => Promise<void>;
  hydrateFromCache: () => boolean;
  clearCache: () => void;

  // Selectors (derived)
  getTopologies: () => Topology[];
  getAllNodes: () => TapiNode[];
  getAllLinks: () => TapiLink[];
  getSips: () => ServiceInterfacePoint[];
}

// Synchronously read cache at module load time so the store starts pre-populated.
// This avoids calling hydrateFromCache() inside useEffect (which changes `data`,
// triggers re-runs of effects that depend on `data`, and causes infinite loops).
function loadInitialFromCache(): { data: TopologyData; fetchedAt: number; isStale: boolean } | null {
  try {
    const entry = cacheRead<TopologyData>(CACHE_KEYS.TOPOLOGY);
    if (!entry) return null;
    return {
      data: entry.data,
      fetchedAt: entry.fetchedAt,
      isStale: Date.now() - entry.fetchedAt > TOPOLOGY_MAX_AGE_MS,
    };
  } catch {
    return null;
  }
}

const _initial = loadInitialFromCache();

export const useTopologyStore = create<TopologyState>()((set, get) => ({
  data: _initial?.data ?? null,
  fetchedAt: _initial?.fetchedAt ?? null,
  isLoading: false,
  error: null,
  isStale: _initial?.isStale ?? false,

  fetchTopology: async (baseUrl: string) => {
    if (!baseUrl) {
      set({ error: "No DT URL configured", isLoading: false });
      return;
    }
    set({ isLoading: true, error: null });
    try {
      const client = createClient(baseUrl);
      const response = await client.getContext();
      const ctx = response["tapi-common:context"];
      const topologies = ctx["tapi-topology:topology-context"]?.topology ?? [];
      const sips = ctx["service-interface-point"] ?? [];

      const topologyData: TopologyData = {
        context: ctx,
        topologies,
        sips,
      };

      set({
        data: topologyData,
        fetchedAt: Date.now(),
        isLoading: false,
        error: null,
        isStale: false,
      });

      cacheWrite(CACHE_KEYS.TOPOLOGY, topologyData);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to fetch topology";
      // Try to use cache as fallback
      const cached = get().data;
      set({
        isLoading: false,
        error: message,
        isStale: !!cached,
      });
    }
  },

  hydrateFromCache: () => {
    const entry = cacheRead<TopologyData>(CACHE_KEYS.TOPOLOGY);
    if (!entry) return false;
    const age = Date.now() - entry.fetchedAt;
    set({
      data: entry.data,
      fetchedAt: entry.fetchedAt,
      isStale: age > TOPOLOGY_MAX_AGE_MS,
      error: null,
    });
    return true;
  },

  clearCache: () => {
    localStorage.removeItem(CACHE_KEYS.TOPOLOGY);
    set({ data: null, fetchedAt: null, isStale: false, error: null });
  },

  getTopologies: () => get().data?.topologies ?? [],
  getAllNodes: () => get().data?.topologies.flatMap((t) => t.node) ?? [],
  getAllLinks: () => get().data?.topologies.flatMap((t) => t.link) ?? [],
  getSips: () => get().data?.sips ?? [],
}));
