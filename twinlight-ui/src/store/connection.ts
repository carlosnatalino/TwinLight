import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ConnectionStatus = "connected" | "disconnected" | "checking" | "stale";

declare global {
  interface Window {
    // Injected by public/config.js (regenerated from $TWIN_URL in the
    // container build); absent during tests/SSR.
    __TWIN_CONFIG__?: { baseUrl?: string };
  }
}

// Default DT URL for a fresh load: runtime config if present, else localhost.
// A value the user has saved (persisted below) always takes precedence.
const DEFAULT_BASE_URL =
  (typeof window !== "undefined" && window.__TWIN_CONFIG__?.baseUrl) ||
  "http://localhost:8080";

interface ConnectionState {
  baseUrl: string;
  pollInterval: number; // seconds
  status: ConnectionStatus;
  lastSuccessAt: number | null;
  setBaseUrl: (url: string) => void;
  setPollInterval: (seconds: number) => void;
  setStatus: (status: ConnectionStatus) => void;
  setLastSuccess: () => void;
}

export const useConnectionStore = create<ConnectionState>()(
  persist(
    (set) => ({
      baseUrl: DEFAULT_BASE_URL,
      pollInterval: 10,
      status: "disconnected" as ConnectionStatus,
      lastSuccessAt: null,

      setBaseUrl: (url) => set({ baseUrl: url, status: "disconnected" }),
      setPollInterval: (seconds) => set({ pollInterval: seconds }),
      setStatus: (status) => set({ status }),
      setLastSuccess: () => set({ lastSuccessAt: Date.now(), status: "connected" }),
    }),
    {
      name: "twinlight-ui:settings",
      partialize: (state) => ({
        baseUrl: state.baseUrl,
        pollInterval: state.pollInterval,
      }),
    }
  )
);
