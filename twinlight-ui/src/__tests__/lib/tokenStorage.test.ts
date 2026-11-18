import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  SESSION_TOKEN_KEY,
  SETTINGS_KEY,
  migrateLegacyToken,
  readSessionToken,
  resetMemoryToken,
  writeSessionToken,
} from "@/lib/tokenStorage";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetMemoryToken();
});

afterEach(() => vi.restoreAllMocks());

describe("session token storage", () => {
  it("keeps the token in sessionStorage, never in localStorage", () => {
    writeSessionToken("tok-1");
    expect(sessionStorage.getItem(SESSION_TOKEN_KEY)).toBe("tok-1");
    expect(readSessionToken()).toBe("tok-1");
    expect(JSON.stringify({ ...localStorage })).not.toContain("tok-1");
    for (let i = 0; i < localStorage.length; i++) {
      expect(localStorage.getItem(localStorage.key(i)!)).not.toContain("tok-1");
    }
  });

  it("forgets the token when given an empty one", () => {
    writeSessionToken("tok-1");
    writeSessionToken("");
    expect(sessionStorage.getItem(SESSION_TOKEN_KEY)).toBeNull();
    expect(readSessionToken()).toBe("");
  });

  it("keeps the token in memory when sessionStorage is blocked", () => {
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(() => writeSessionToken("tok-mem")).not.toThrow();
    expect(readSessionToken()).toBe("tok-mem");
  });

  it("keeps the token in memory when writing to sessionStorage fails", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    writeSessionToken("tok-mem");
    expect(readSessionToken()).toBe("tok-mem");
  });
});

describe("migrating a token an earlier build left in localStorage", () => {
  function legacy(state: Record<string, unknown>) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ state, version: 0 }));
  }

  it("moves it into the session and deletes it from disk", () => {
    legacy({ baseUrl: "http://dt", apiToken: "old-tok", pollInterval: 15 });
    migrateLegacyToken();
    expect(readSessionToken()).toBe("old-tok");
    const kept = JSON.parse(localStorage.getItem(SETTINGS_KEY)!);
    expect(kept.state).toEqual({ baseUrl: "http://dt", pollInterval: 15 });
    expect(localStorage.getItem(SETTINGS_KEY)).not.toContain("old-tok");
  });

  it("does not overwrite a token already in the session", () => {
    writeSessionToken("current");
    legacy({ baseUrl: "http://dt", apiToken: "old-tok" });
    migrateLegacyToken();
    expect(readSessionToken()).toBe("current");
    expect(localStorage.getItem(SETTINGS_KEY)).not.toContain("old-tok");
  });

  it("removes an empty legacy field and leaves other entries alone", () => {
    legacy({ baseUrl: "http://dt", apiToken: "" });
    migrateLegacyToken();
    expect(readSessionToken()).toBe("");
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!).state).toEqual({ baseUrl: "http://dt" });
  });

  it("ignores an unreadable entry", () => {
    localStorage.setItem(SETTINGS_KEY, "{not json");
    expect(() => migrateLegacyToken()).not.toThrow();
  });
});
