import { create } from "zustand";
import type {
  ConnectivityService,
  UpdateConnectivityServiceRequest,
} from "@/api/types";
import { createClient } from "@/api/client";

interface ServicesState {
  services: ConnectivityService[];
  isLoading: boolean;
  error: string | null;
  lastFetchedAt: number | null;

  fetchServices: (baseUrl: string) => Promise<void>;
  deleteService: (baseUrl: string, uuid: string) => Promise<void>;
  updateService: (
    baseUrl: string,
    uuid: string,
    body: UpdateConnectivityServiceRequest
  ) => Promise<void>;
}

export const useServicesStore = create<ServicesState>()((set, get) => ({
  services: [],
  isLoading: false,
  error: null,
  lastFetchedAt: null,

  fetchServices: async (baseUrl: string) => {
    if (!baseUrl) {
      set({ error: "No DT URL configured", isLoading: false });
      return;
    }
    set({ isLoading: true, error: null });
    try {
      const client = createClient(baseUrl);
      const response = await client.getServices();
      const services =
        response["tapi-connectivity:connectivity-context"]?.[
          "connectivity-service"
        ] ?? [];
      set({ services, isLoading: false, error: null, lastFetchedAt: Date.now() });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to fetch services";
      set({ isLoading: false, error: message });
    }
  },

  deleteService: async (baseUrl: string, uuid: string) => {
    const client = createClient(baseUrl);
    await client.deleteService(uuid);
    set({ services: get().services.filter((s) => s.uuid !== uuid) });
  },

  updateService: async (
    baseUrl: string,
    uuid: string,
    body: UpdateConnectivityServiceRequest
  ) => {
    const client = createClient(baseUrl);
    const response = await client.updateService(uuid, body);
    const updated = response["tapi-connectivity:connectivity-service"];
    set({
      services: get().services.map((s) => (s.uuid === uuid ? updated : s)),
    });
  },
}));
