import { useCallback } from "react";
import { useConnectionStore } from "@/store/connection";
import { createClient } from "@/api/client";
import { usePolling } from "./usePolling";

/**
 * Polls /health on the configured DT URL at the user-configured poll interval.
 * Updates the global connection status (connected / disconnected) accordingly.
 * Should be mounted once in RootLayout so it runs on every page.
 */
export function useConnectionCheck() {
  const { baseUrl, pollInterval, setStatus, setLastSuccess } = useConnectionStore();

  const check = useCallback(async () => {
    if (!baseUrl) {
      setStatus("disconnected");
      return;
    }
    try {
      setStatus("checking");
      const client = createClient(baseUrl);
      await client.health();
      setLastSuccess(); // sets status → "connected"
    } catch {
      setStatus("disconnected");
    }
  }, [baseUrl, setStatus, setLastSuccess]);

  usePolling({
    intervalMs: pollInterval * 1000,
    enabled: !!baseUrl,
    onTick: check,
    immediate: true,
  });
}
