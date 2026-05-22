import { describe, it, expect, beforeEach } from "vitest";
import { cacheRead, cacheWrite, cacheDelete, cachePurgeByPrefix } from "@/lib/cache";

beforeEach(() => {
  localStorage.clear();
});

describe("cacheWrite / cacheRead", () => {
  it("stores and retrieves data", () => {
    cacheWrite("test:key", { foo: "bar" });
    const entry = cacheRead<{ foo: string }>("test:key");
    expect(entry).not.toBeNull();
    expect(entry!.data).toEqual({ foo: "bar" });
    expect(entry!.fetchedAt).toBeGreaterThan(0);
  });

  it("returns null for missing key", () => {
    expect(cacheRead("missing:key")).toBeNull();
  });

  it("returns null for malformed JSON", () => {
    localStorage.setItem("bad:key", "not json{{");
    expect(cacheRead("bad:key")).toBeNull();
  });

  it("returns null for wrong schema version", () => {
    localStorage.setItem("old:key", JSON.stringify({ v: 0, data: {}, fetchedAt: Date.now() }));
    expect(cacheRead("old:key")).toBeNull();
    // Should have been removed
    expect(localStorage.getItem("old:key")).toBeNull();
  });
});

describe("cacheDelete", () => {
  it("removes a key", () => {
    cacheWrite("del:key", 42);
    cacheDelete("del:key");
    expect(cacheRead("del:key")).toBeNull();
  });
});

describe("cachePurgeByPrefix", () => {
  it("removes all matching keys", () => {
    cacheWrite("myapp:a", 1);
    cacheWrite("myapp:b", 2);
    cacheWrite("other:c", 3);
    cachePurgeByPrefix("myapp:");
    expect(cacheRead("myapp:a")).toBeNull();
    expect(cacheRead("myapp:b")).toBeNull();
    expect(cacheRead<number>("other:c")).not.toBeNull();
  });
});
