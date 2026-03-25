import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ConnectionStatus = "connected" | "disconnected" | "checking" | "stale";

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
      baseUrl: "http://localhost:8080",
      pollInterval: 10,
      status: "disconnected" as ConnectionStatus,
      lastSuccessAt: null,

      setBaseUrl: (url) => set({ baseUrl: url, status: "disconnected" }),
      setPollInterval: (seconds) => set({ pollInterval: seconds }),
      setStatus: (status) => set({ status }),
      setLastSuccess: () => set({ lastSuccessAt: Date.now(), status: "connected" }),
    }),
    {
      name: "tapi-twin-ui:settings",
      partialize: (state) => ({
        baseUrl: state.baseUrl,
        pollInterval: state.pollInterval,
      }),
    }
  )
);
