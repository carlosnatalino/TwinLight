import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useConnectionStore } from "@/store/connection";
import { createClient } from "@/api/client";
import type { SpectrumGridResponse } from "@/api/types";
import { cn } from "@/lib/cn";

const SLOT_WIDTH_PX = 6;
const SLOT_HEIGHT_PX = 22;
const LINK_LABEL_WIDTH_PX = 340;

/**
 * Colormap: one distinct color per service by index.
 * Uses HSL with hue spread over 360° so N services get N distinguishable colors.
 */
function colorForIndex(index: number, total: number): string {
  if (total <= 0) return "rgb(148 163 184)";
  const hue = (index * (360 / Math.max(total, 1))) % 360;
  const s = 72;
  const l = 48;
  const c = (1 - Math.abs((2 * l) / 100 - 1)) * (s / 100);
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1));
  const m = l / 100 - c / 2;
  let r = 0, g = 0, b = 0;
  if (hue < 60) { r = c; g = x; b = 0; } else if (hue < 120) { r = x; g = c; b = 0; } else if (hue < 180) { r = 0; g = c; b = x; } else if (hue < 240) { r = 0; g = x; b = c; } else if (hue < 300) { r = x; g = 0; b = c; } else { r = c; g = 0; b = x; }
  return `rgb(${Math.round((r + m) * 255)} ${Math.round((g + m) * 255)} ${Math.round((b + m) * 255)})`;
}

/** One run of consecutive slots with the same occupant (or free). */
interface SlotRun {
  value: string | null;
  start: number;
  count: number;
}

function slotRunsForRow(row: (string | null)[]): SlotRun[] {
  const runs: SlotRun[] = [];
  let i = 0;
  while (i < row.length) {
    const value = row[i];
    let count = 1;
    while (i + count < row.length && row[i + count] === value) count += 1;
    runs.push({ value, start: i, count });
    i += count;
  }
  return runs;
}

export default function SpectrumGridPage() {
  const { baseUrl } = useConnectionStore();
  const [data, setData] = useState<SpectrumGridResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoverCell, setHoverCell] = useState<{
    linkIdx: number;
    slotIdx: number;
    serviceUuid: string;
    x: number;
    y: number;
  } | null>(null);

  // Hiding the popup is deferred so the cursor can travel from the slot cell
  // to the popup (which sits a few px below) without it disappearing.
  const hideTimer = useRef<number | null>(null);
  const cancelHide = () => {
    if (hideTimer.current != null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  };
  const scheduleHide = () => {
    cancelHide();
    hideTimer.current = window.setTimeout(() => setHoverCell(null), 120);
  };
  useEffect(() => cancelHide, []);

  useEffect(() => {
    const log = (msg: string, detail?: unknown) => {
      console.info("[SpectrumGrid]", msg, detail !== undefined ? detail : "");
    };
    if (!baseUrl) {
      log("No baseUrl; skipping fetch");
      setLoading(false);
      return;
    }
    log("Fetching spectrum grid", { baseUrl });
    const client = createClient(baseUrl);
    client
      .getSpectrumGrid()
      .then((res) => {
        const numLinks = res.links?.length ?? 0;
        const numSlots = res["spectrum-context"]?.["num-slots"] ?? 0;
        const allocation = res["service-allocation"] ?? [];
        const occupancy = res.occupancy ?? [];
        const servicesInOccupancy = new Set<string>();
        for (const row of occupancy) {
          for (const cell of row) {
            if (cell) servicesInOccupancy.add(cell);
          }
        }
        const allocatedUuids = new Set(allocation.map((a) => a["service-uuid"]));
        const inAllocationOnly = [...allocatedUuids].filter((u) => !servicesInOccupancy.has(u));
        log("Spectrum grid loaded", {
          links: numLinks,
          slots: numSlots,
          "service-allocation count": allocation.length,
          "distinct services with slots in grid": servicesInOccupancy.size,
          "services in allocation but not in grid": inAllocationOnly.length ? inAllocationOnly : undefined,
        });
        setData(res);
      })
      .catch((e) => {
        log("Spectrum grid fetch failed", {
          error: e instanceof Error ? e.message : String(e),
          stack: e instanceof Error ? e.stack : undefined,
        });
        setError(e instanceof Error ? e.message : "Failed to load");
      })
      .finally(() => setLoading(false));
  }, [baseUrl]);

  const occupancy = data?.occupancy ?? [];
  const serviceAllocation = data?.["service-allocation"] ?? [];

  const serviceToColor = useMemo(() => {
    const seen = new Set<string>();
    for (const a of serviceAllocation) seen.add(a["service-uuid"]);
    for (const row of occupancy) {
      for (const cell of row) {
        if (cell) seen.add(cell);
      }
    }
    const sorted = Array.from(seen).sort();
    const map = new Map<string, string>();
    const n = sorted.length;
    sorted.forEach((uuid, index) => {
      map.set(uuid, colorForIndex(index, n));
    });
    if (n > 0) {
      console.info("[SpectrumGrid] Colormap", { serviceCount: n, serviceUuids: sorted });
    }
    return map;
  }, [occupancy, serviceAllocation]);

  const allocationByUuid = useMemo(() => {
    const map = new Map<
      string,
      { startSlot: number; numSlots: number; roadms: string[] }
    >();
    for (const a of serviceAllocation) {
      map.set(a["service-uuid"], {
        startSlot: a["start-slot"],
        numSlots: a["num-slots"],
        roadms: a.roadms ?? [],
      });
    }
    return map;
  }, [serviceAllocation]);

  /** Per-row runs of consecutive slots (same service or free) for efficient rendering. */
  const runsPerRow = useMemo(
    () => occupancy.map((row) => slotRunsForRow(row)),
    [occupancy]
  );

  const hoverServiceUuid = hoverCell?.serviceUuid ?? null;

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-slate-500">Loading spectrum grid…</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6">
        <p className="text-red-600">{error}</p>
      </div>
    );
  }
  if (!data || data.links.length === 0) {
    if (data && data["service-allocation"]?.length) {
      console.info("[SpectrumGrid] Has services but no links in grid", {
        "service-allocation count": data["service-allocation"].length,
      });
    }
    return (
      <div className="p-6 space-y-4">
        <h1 className="text-2xl font-bold text-gray-900">Spectrum grid</h1>
        <p className="text-slate-500">
          {data
            ? "No ROADM–ROADM links in this topology. The grid shows only backbone links between ROADMs; use a topology with multiple ROADMs (e.g. CORONET) to see the spectrum grid."
            : "No data. Load the topology and ensure the backend has ROADM–ROADM links."}
        </p>
      </div>
    );
  }

  const numSlots = data["spectrum-context"]["num-slots"];
  const slotWidthGhz = data["spectrum-context"]["slot-width-ghz"];
  const links = data.links;

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Spectrum grid</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          Rows = links, columns = spectrum slots. One color per service. Hover a
          used slot to highlight that service across all links and see spectrum
          range and bandwidth.
        </p>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
        <div
          className="overflow-x-auto overflow-y-auto"
          style={{ maxHeight: "70vh" }}
        >
          <table
            className="border-collapse"
            style={{ minWidth: numSlots * SLOT_WIDTH_PX + LINK_LABEL_WIDTH_PX }}
          >
            <thead>
              <tr>
                <th
                  className="sticky left-0 z-10 bg-gray-100 border-b border-r border-gray-200 px-2 py-2 text-left text-xs font-medium text-gray-600"
                  style={{ width: LINK_LABEL_WIDTH_PX, minWidth: LINK_LABEL_WIDTH_PX }}
                >
                  Link
                </th>
                <th
                  className="border-b border-gray-200 px-1 py-2 text-center text-xs font-medium text-gray-500 whitespace-nowrap"
                  colSpan={numSlots}
                  style={{ minWidth: numSlots * SLOT_WIDTH_PX }}
                >
                  Slot index 0 → {numSlots - 1}
                </th>
              </tr>
            </thead>
            <tbody>
              {links.map((link, linkIdx) => (
                <tr key={link["link-uuid"]}>
                  <td
                    className="sticky left-0 z-10 bg-white border-b border-r border-gray-200 px-2 py-0.5 text-sm font-mono text-gray-700 truncate"
                    style={{
                      width: LINK_LABEL_WIDTH_PX,
                      maxWidth: LINK_LABEL_WIDTH_PX,
                    }}
                    title={link["link-uuid"]}
                  >
                    {link.label}
                  </td>
                  {runsPerRow[linkIdx]?.map((run, runIdx) => {
                    const used = run.value != null;
                    const color = used && run.value ? serviceToColor.get(run.value) ?? "rgb(37 99 235)" : undefined;
                    const isHighlighted = used && hoverServiceUuid === run.value;
                    return (
                      <td
                        key={`${run.start}-${run.count}-${runIdx}`}
                        className={cn(
                          "border-b border-gray-100 p-0 align-top transition-shadow",
                          used && "cursor-pointer",
                          isHighlighted && "ring-2 ring-offset-0 ring-gray-900 ring-inset shadow-md"
                        )}
                        style={{
                          width: run.count * SLOT_WIDTH_PX,
                          minWidth: run.count * SLOT_WIDTH_PX,
                          height: SLOT_HEIGHT_PX,
                          backgroundColor: color ?? "rgb(243 244 246)",
                        }}
                        colSpan={run.count}
                        onMouseEnter={(e) => {
                          if (run.value) {
                            cancelHide();
                            const rect = (e.target as HTMLElement).getBoundingClientRect();
                            setHoverCell({
                              linkIdx,
                              slotIdx: run.start,
                              serviceUuid: run.value,
                              x: rect.left,
                              y: rect.bottom,
                            });
                          }
                        }}
                        onMouseLeave={scheduleHide}
                      />
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {hoverCell && (() => {
          const alloc = allocationByUuid.get(hoverCell.serviceUuid);
          const startSlot = alloc?.startSlot ?? 0;
          const numSlots = alloc?.numSlots ?? 0;
          const endSlot = startSlot + numSlots - 1;
          const bandwidthGhz = numSlots * slotWidthGhz;
          const roadms = alloc?.roadms ?? [];
          return (
            <div
              className="fixed z-50 rounded-lg border border-gray-200 bg-white shadow-lg p-3 min-w-[220px] max-w-[360px]"
              style={{
                left: hoverCell.x,
                top: hoverCell.y + 4,
              }}
              onMouseEnter={cancelHide}
              onMouseLeave={scheduleHide}
            >
              <p className="text-xs text-gray-500 mb-1">Service</p>
              <p className="font-mono text-xs text-gray-800 break-all mb-2">
                {hoverCell.serviceUuid}
              </p>
              {roadms.length > 0 && (
                <div className="text-xs text-gray-600 mb-2">
                  <p className="font-medium text-gray-500 mb-0.5">
                    ROADMs traversed
                  </p>
                  <p className="text-gray-700 break-words">
                    {roadms.join(" → ")}
                  </p>
                </div>
              )}
              {alloc && numSlots > 0 && (
                <div className="text-xs text-gray-600 mb-3 space-y-0.5">
                  <p>
                    Spectrum: slots {startSlot}–{endSlot}
                  </p>
                  <p>Bandwidth: {bandwidthGhz.toFixed(1)} GHz</p>
                </div>
              )}
              {alloc && numSlots === 0 && (
                <p className="text-xs text-amber-600 mb-3">No spectrum allocated</p>
              )}
              <Link
                to={`/monitoring?service=${encodeURIComponent(hoverCell.serviceUuid)}`}
                className="inline-flex items-center gap-1.5 rounded bg-blue-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
              >
                View monitoring
              </Link>
            </div>
          );
        })()}
      </div>

      <p className="text-xs text-gray-500">
        Grid: {links.length} links × {numSlots} slots. Color = per service; gray
        = free.
      </p>
    </div>
  );
}
