import { create } from "zustand";
import { persist } from "zustand/middleware";
import { setTokenProvider } from "@/api/client";
import {
  migrateLegacyToken,
  readSessionToken,
  writeSessionToken,
} from "@/lib/tokenStorage";

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
  // authentication. The user enters it when the twin asks for one (the
  // token prompt) or in Settings, and it is kept for this browser session
  // only (lib/tokenStorage.ts), never in localStorage. Deliberately never
  // seeded from the runtime config.js: anything there is readable by
  // whoever can load the UI.
  apiToken: string;
  pollInterval: number; // seconds
  status: ConnectionStatus;
  lastSuccessAt: number | null;
  // True once the first connection check has settled, so pages load only
  // after the UI knows whether the twin needs a token (and has one).
  ready: boolean;
  // The token prompt. Opened when the twin answers 401 to /internal/whoami
  // (a token is required, or the one sent is rejected). A dismissed prompt
  // is not reopened for the same twin and token; reaching the twin with a
  // token, or a changed URL or token, makes it eligible again.
  tokenPromptOpen: boolean;
  tokenPromptDismissedFor: string | null;
  // Moves on whenever a token is accepted, so pages interrupted by the
  // prompt mount again and reload their data.
  authEpoch: number;
  setBaseUrl: (url: string) => void;
  setApiToken: (token: string) => void;
  setPollInterval: (seconds: number) => void;
  setStatus: (status: ConnectionStatus) => void;
  setLastSuccess: () => void;
  setReady: () => void;
  requestToken: (opts?: { force?: boolean }) => void;
  dismissTokenPrompt: () => void;
  acceptToken: (token: string) => void;
}

/** What a dismissal applies to: this twin, with this token (or none). */
export function promptKey(baseUrl: string, token: string): string {
  return `${baseUrl}\n${token}`;
}

// Before the store rehydrates: a token an earlier build kept in
// localStorage moves into the session and is deleted from disk.
migrateLegacyToken();

export const useConnectionStore = create<ConnectionState>()(
  persist(
    (set) => ({
      baseUrl: DEFAULT_BASE_URL,
      apiToken: readSessionToken(),
      pollInterval: 10,
      status: "disconnected" as ConnectionStatus,
      lastSuccessAt: null,
      ready: false,
      tokenPromptOpen: false,
      tokenPromptDismissedFor: null,
      authEpoch: 0,

      setBaseUrl: (url) => set({ baseUrl: url, status: "disconnected" }),
      setApiToken: (token) => {
        const trimmed = token.trim();
        writeSessionToken(trimmed);
        set({ apiToken: trimmed });
      },
      setPollInterval: (seconds) => set({ pollInterval: seconds }),
      setStatus: (status) => set({ status }),
      setLastSuccess: () =>
        set({ lastSuccessAt: Date.now(), status: "connected", tokenPromptDismissedFor: null }),
      setReady: () => set({ ready: true }),
      requestToken: (opts) =>
        set((state) => {
          const key = promptKey(state.baseUrl, state.apiToken);
          if (!opts?.force && state.tokenPromptDismissedFor === key) return {};
          return { tokenPromptOpen: true };
        }),
      dismissTokenPrompt: () =>
        set((state) => ({
          tokenPromptOpen: false,
          tokenPromptDismissedFor: promptKey(state.baseUrl, state.apiToken),
        })),
      acceptToken: (token) => {
        const trimmed = token.trim();
        writeSessionToken(trimmed);
        set((state) => ({
          apiToken: trimmed,
          tokenPromptOpen: false,
          tokenPromptDismissedFor: null,
          authEpoch: state.authEpoch + 1,
        }));
      },
    }),
    {
      name: "twinlight-ui:settings",
      // Only these reach localStorage. The token is kept for the session
      // only (lib/tokenStorage.ts), so it is not on disk after the browser
      // closes; nor are the prompt and connection states, which describe
      // this page's view of the twin.
      partialize: (state) => ({
        baseUrl: state.baseUrl,
        pollInterval: state.pollInterval,
      }),
      // Never take a token from localStorage, even one written there by a
      // build older than migrateLegacyToken.
      merge: (persisted, current) => {
        const { apiToken: _ignored, ...rest } = (persisted ?? {}) as Record<string, unknown>;
        void _ignored;
        return { ...current, ...rest };
      },
    }
  )
);

setTokenProvider(() => useConnectionStore.getState().apiToken || undefined);
