import { useConnectionStore } from "@/store/connection";
import { ApiError, createClient, isAuthError } from "@/api/client";
import { usePolling } from "./usePolling";

/**
 * Check the saved DT URL and token once, and record the result as the global
 * connection status (connected / unauthorized / disconnected).
 *
 * /health is public even on a twin that requires a token, so reachability
 * alone says nothing about the token. Each check therefore also asks
 * /internal/whoami, which only a request with an accepted token gets past.
 *
 * Reads the store when called rather than closing over it, so Settings can
 * run a check the moment a new token is saved instead of waiting for the
 * next poll.
 */
export async function checkConnection(): Promise<void> {
  const { baseUrl, setStatus, setLastSuccess } = useConnectionStore.getState();
  if (!baseUrl) {
    setStatus("disconnected");
    return;
  }
  try {
    setStatus("checking");
    const client = createClient(baseUrl);
    await client.health();
    try {
      await client.whoami();
    } catch (err) {
      if (isAuthError(err)) {
        setStatus("unauthorized");
        return;
      }
      // 404: a twin from before authentication existed — open by definition.
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    setLastSuccess(); // sets status → "connected"
  } catch {
    setStatus("disconnected");
  }
}

/**
 * Polls the configured DT at the user-configured poll interval (see
 * checkConnection). Should be mounted once in RootLayout so it runs on every
 * page.
 */
export function useConnectionCheck() {
  const { baseUrl, pollInterval } = useConnectionStore();

  usePolling({
    intervalMs: pollInterval * 1000,
    enabled: !!baseUrl,
    onTick: checkConnection,
    immediate: true,
  });
}
