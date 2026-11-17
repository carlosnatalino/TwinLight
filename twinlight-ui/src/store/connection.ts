import { create } from "zustand";
import { persist } from "zustand/middleware";
import { setTokenProvider } from "@/api/client";

// "unauthorized": the twin is reachable but refuses this UI's API token
// (or the UI has none and the twin requires one).
export type ConnectionStatus =
  | "connected"
  | "disconnected"
  | "checking"
  | "stale"
  | "unauthorized";

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
  // Sent as `Authorization: Bearer <token>`; empty for a twin without
  // authentication. Deliberately never seeded from the runtime config.js:
  // anything there is readable by whoever can load the UI.
  apiToken: string;
  pollInterval: number; // seconds
  status: ConnectionStatus;
  lastSuccessAt: number | null;
  setBaseUrl: (url: string) => void;
  setApiToken: (token: string) => void;
  setPollInterval: (seconds: number) => void;
  setStatus: (status: ConnectionStatus) => void;
  setLastSuccess: () => void;
}

export const useConnectionStore = create<ConnectionState>()(
  persist(
    (set) => ({
      baseUrl: DEFAULT_BASE_URL,
      apiToken: "",
      pollInterval: 10,
      status: "disconnected" as ConnectionStatus,
      lastSuccessAt: null,

      setBaseUrl: (url) => set({ baseUrl: url, status: "disconnected" }),
      setApiToken: (token) => set({ apiToken: token.trim() }),
      setPollInterval: (seconds) => set({ pollInterval: seconds }),
      setStatus: (status) => set({ status }),
      setLastSuccess: () => set({ lastSuccessAt: Date.now(), status: "connected" }),
    }),
    {
      name: "twinlight-ui:settings",
      // The token is persisted with the other settings, in this browser's
      // localStorage, so a reload does not log the UI out of the twin.
      partialize: (state) => ({
        baseUrl: state.baseUrl,
        apiToken: state.apiToken,
        pollInterval: state.pollInterval,
      }),
    }
  )
);

setTokenProvider(() => useConnectionStore.getState().apiToken || undefined);
