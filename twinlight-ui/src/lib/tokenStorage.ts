/**
 * Where the UI keeps the API token: this browser tab's sessionStorage.
 *
 * A reload in the same tab keeps it; closing the tab or the browser forgets
 * it. It is never written to localStorage, never read from config.js or any
 * other file, and never put in a URL. A browser that blocks sessionStorage
 * (some privacy modes) degrades to keeping the token in memory for the life
 * of the page, rather than failing.
 */

export const SESSION_TOKEN_KEY = "twinlight-ui:api-token";

/** The persisted settings entry (zustand persist) that held the token before. */
export const SETTINGS_KEY = "twinlight-ui:settings";

let memoryToken = "";

function sessionStore(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null; // access itself throws when storage is blocked
  }
}

export function readSessionToken(): string {
  try {
    const stored = sessionStore()?.getItem(SESSION_TOKEN_KEY);
    if (stored) return stored;
  } catch {
    // fall through to the in-memory copy
  }
  return memoryToken;
}

/** Keep `token` for this session; an empty token forgets it. */
export function writeSessionToken(token: string): void {
  memoryToken = token;
  try {
    const store = sessionStore();
    if (!store) return;
    if (token) store.setItem(SESSION_TOKEN_KEY, token);
    else store.removeItem(SESSION_TOKEN_KEY);
  } catch {
    // quota or blocked: the in-memory copy above still serves this page
  }
}

/**
 * Earlier builds persisted the token in localStorage with the other
 * settings. Move it into the session once (unless the session already has
 * one) and delete it from localStorage, so an upgrade does not leave a
 * secret on disk. Runs before the settings store rehydrates, so the token
 * never reaches the store from localStorage.
 */
export function migrateLegacyToken(): void {
  try {
    const raw = window.localStorage.getItem(SETTINGS_KEY);
    if (!raw) return;
    const persisted = JSON.parse(raw) as { state?: Record<string, unknown> };
    const state = persisted?.state;
    if (!state || !("apiToken" in state)) return;
    const legacy = typeof state.apiToken === "string" ? state.apiToken.trim() : "";
    if (legacy && !readSessionToken()) writeSessionToken(legacy);
    delete state.apiToken;
    window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(persisted));
  } catch {
    // unreadable entry or blocked storage: nothing to migrate
  }
}

/** Tests only: forget the in-memory copy. */
export function resetMemoryToken(): void {
  memoryToken = "";
}
