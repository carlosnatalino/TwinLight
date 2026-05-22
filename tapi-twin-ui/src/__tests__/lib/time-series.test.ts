import { describe, it, expect, beforeEach } from "vitest";
import {
  appendPoint,
  appendPointWithExisting,
  readSeries,
  clearSeries,
  filterByTimeRange,
  mergeSeries,
  getSeriesIndexEntries,
} from "@/lib/time-series";

beforeEach(() => {
  localStorage.clear();
});

describe("appendPoint / readSeries", () => {
  it("appends and reads data points", () => {
    const p1 = { t: 1000, v: 10 };
    const p2 = { t: 2000, v: 20 };
    appendPoint("osnr-db", "svc-1", p1);
    appendPoint("osnr-db", "svc-1", p2);
    const series = readSeries("osnr-db", "svc-1");
    expect(series).toHaveLength(2);
    expect(series[0]).toEqual(p1);
    expect(series[1]).toEqual(p2);
  });

  it("returns empty array for unknown series", () => {
    expect(readSeries("pmd-ps", "unknown")).toHaveLength(0);
  });

  it("caps at maxPoints (ring buffer)", () => {
    for (let i = 0; i < 15; i++) {
      appendPoint("osnr-db", "svc-cap", { t: i * 1000, v: i }, 10);
    }
    const series = readSeries("osnr-db", "svc-cap");
    expect(series).toHaveLength(10);
    // Should be the most recent 10 (5..14)
    expect(series[0].v).toBe(5);
    expect(series[9].v).toBe(14);
  });

  it("appendPointWithExisting accumulates from in-memory series (store-style)", () => {
    const metric = "gsnr-db";
    const serviceUuid = "svc-inmem";
    let existing: { t: number; v: number }[] = [];
    // Simulate multiple ingest calls as the store does: pass current series each time
    for (let i = 0; i < 5; i++) {
      const point = { t: 1000 * (i + 1), v: 10 + i };
      existing = appendPointWithExisting(existing, metric, serviceUuid, point, 1000);
    }
    expect(existing).toHaveLength(5);
    expect(existing[0].v).toBe(10);
    expect(existing[4].v).toBe(14);
    // Persisted so readSeries returns the same
    expect(readSeries(metric, serviceUuid)).toHaveLength(5);
  });
});

describe("mergeSeries", () => {
  it("merges two series by t and dedupes (keeps later value for same t)", () => {
    const a = [
      { t: 1000, v: 1 },
      { t: 2000, v: 2 },
    ];
    const b = [
      { t: 2000, v: 22 },
      { t: 3000, v: 3 },
    ];
    const merged = mergeSeries(a, b, 100);
    expect(merged).toHaveLength(3);
    expect(merged[0]).toEqual({ t: 1000, v: 1 });
    expect(merged[1]).toEqual({ t: 2000, v: 22 });
    expect(merged[2]).toEqual({ t: 3000, v: 3 });
  });

  it("trims to maxPoints (keeps most recent)", () => {
    const a = Array.from({ length: 5 }, (_, i) => ({ t: i * 1000, v: i }));
    const b = Array.from({ length: 5 }, (_, i) => ({ t: (i + 5) * 1000, v: i + 5 }));
    const merged = mergeSeries(a, b, 6);
    expect(merged).toHaveLength(6);
    expect(merged[0].t).toBe(4000);
    expect(merged[5].t).toBe(9000);
  });
});

describe("getSeriesIndexEntries", () => {
  it("returns empty when no series persisted", () => {
    expect(getSeriesIndexEntries()).toHaveLength(0);
  });

  it("returns entries after appending", () => {
    appendPoint("osnr-db", "svc-a", { t: 1000, v: 1 });
    appendPoint("pmd-ps", "svc-a", { t: 1000, v: 2 });
    const entries = getSeriesIndexEntries();
    expect(entries).toHaveLength(2);
    expect(entries.map((e) => e.metric).sort()).toEqual(["osnr-db", "pmd-ps"]);
    expect(entries.every((e) => e.serviceUuid === "svc-a")).toBe(true);
  });
});

describe("clearSeries", () => {
  it("removes a series", () => {
    appendPoint("osnr-db", "svc-clr", { t: 1000, v: 5 });
    clearSeries("osnr-db", "svc-clr");
    expect(readSeries("osnr-db", "svc-clr")).toHaveLength(0);
  });
});

describe("filterByTimeRange", () => {
  it("keeps points within range", () => {
    const now = Date.now();
    const points = [
      { t: now - 10_000, v: 1 },
      { t: now - 5_000, v: 2 },
      { t: now - 1_000, v: 3 },
    ];
    const filtered = filterByTimeRange(points, 7_000);
    expect(filtered).toHaveLength(2);
    expect(filtered[0].v).toBe(2);
    expect(filtered[1].v).toBe(3);
  });

  it("returns all points when rangeMs is 0", () => {
    const points = [
      { t: Date.now() - 999_999, v: 1 },
      { t: Date.now(), v: 2 },
    ];
    expect(filterByTimeRange(points, 0)).toHaveLength(2);
  });
});
