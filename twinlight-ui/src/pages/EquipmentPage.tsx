import { useState, useEffect } from "react";
import { useConnectionStore } from "@/store/connection";
import { createClient, ApiError } from "@/api/client";
import type { Equipment } from "@/api/types";
import { Loader2, Cpu, Cable, Radio, Box } from "lucide-react";
import { cn } from "@/lib/cn";

const TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  Transceiver: Radio,
  Roadm: Box,
  Fiber: Cable,
  Edfa: Cpu,
};

function getIcon(type: string) {
  return TYPE_ICONS[type] ?? Cpu;
}

export default function EquipmentPage() {
  const { baseUrl } = useConnectionStore();
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    createClient(baseUrl)
      .getEquipmentList()
      .then((res) => {
        if (!cancelled) {
          setEquipment(res["tapi-equipment:equipment-context"].equipment ?? []);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [baseUrl]);

  const filtered = filter
    ? equipment.filter(
        (e) =>
          e.uuid.toLowerCase().includes(filter.toLowerCase()) ||
          (e["equipment-type"] ?? "").toLowerCase().includes(filter.toLowerCase())
      )
    : equipment;

  const byType = filtered.reduce<Record<string, number>>((acc, e) => {
    const t = e["equipment-type"] || "other";
    acc[t] = (acc[t] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="mx-auto max-w-5xl p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Equipment</h1>
        <p className="mt-1 text-sm text-gray-500">
          Inventory from the T-API equipment context (GNPy elements).
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-slate-600">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading equipment…</span>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="flex flex-wrap items-center gap-4">
            <input
              type="text"
              placeholder="Filter by UUID or type…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm w-64 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <span className="text-sm text-gray-500">
              {filtered.length} of {equipment.length} equipment
            </span>
          </div>

          {Object.keys(byType).length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <h2 className="text-sm font-semibold text-gray-800 mb-3">By type</h2>
              <div className="flex flex-wrap gap-3">
                {Object.entries(byType).map(([type, count]) => {
                  const Icon = getIcon(type);
                  return (
                    <span
                      key={type}
                      className="inline-flex items-center gap-2 rounded-lg bg-slate-100 px-3 py-1.5 text-sm text-slate-700"
                    >
                      <Icon className="h-4 w-4 text-slate-500" />
                      {type}: {count}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-800">Equipment list</h2>
            </div>
            <ul className="divide-y divide-gray-200 max-h-[50vh] overflow-y-auto">
              {filtered.map((eq) => {
                const Icon = getIcon(eq["equipment-type"] ?? "");
                return (
                  <li
                    key={eq.uuid}
                    className="flex items-center gap-4 px-4 py-3 hover:bg-gray-50"
                  >
                    <div
                      className={cn(
                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                        eq["equipment-type"] === "Fiber"
                          ? "bg-amber-100 text-amber-700"
                          : eq["equipment-type"] === "Transceiver"
                            ? "bg-blue-100 text-blue-700"
                            : eq["equipment-type"] === "Roadm"
                              ? "bg-violet-100 text-violet-700"
                              : "bg-slate-100 text-slate-600"
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-mono text-sm text-gray-900 truncate" title={eq.uuid}>
                        {eq.uuid}
                      </p>
                      <p className="text-xs text-gray-500">{eq["equipment-type"]}</p>
                    </div>
                    {eq["length-km"] != null && (
                      <span className="text-sm text-gray-600 shrink-0">
                        {eq["length-km"].toFixed(2)} km
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            {filtered.length === 0 && (
              <p className="px-4 py-8 text-center text-sm text-gray-500">
                No equipment matching filter.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
