import { useEffect, useState } from "react";
import { useConnectionStore } from "@/store/connection";
import { createClient } from "@/api/client";
import { spectrumOf } from "@/api/types";
import type { SpectrumContextResponse } from "@/api/types";
import type { ConnectivityService } from "@/api/types";
import { extractName } from "@/lib/tapi-helpers";
export default function SpectrumPage() {
  const { baseUrl } = useConnectionStore();
  const [spectrumContext, setSpectrumContext] =
    useState<SpectrumContextResponse | null>(null);
  const [services, setServices] = useState<ConnectivityService[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!baseUrl) {
      setLoading(false);
      return;
    }
    const client = createClient(baseUrl);
    Promise.all([
      client.getSpectrumContext().catch(() => null),
      client.getServices().then((r) => r["tapi-connectivity:connectivity-context"]["connectivity-service"]),
    ])
      .then(([ctx, svcs]) => {
        setSpectrumContext(ctx ?? null);
        setServices(svcs ?? []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [baseUrl]);

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-slate-500">Loading spectrum data…</p>
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

  const grid = spectrumContext;
  const withSlot = services.filter((s) => spectrumOf(s));

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Spectrum (L0)</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          T-API photonic media: grid and per-service frequency slots
        </p>
      </div>

      {grid && (
        <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Grid (spectrum context)</h2>
          <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-sm">
            <div>
              <dt className="text-gray-500">Slots</dt>
              <dd className="font-mono font-medium">{grid["num-slots"]}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Slot width</dt>
              <dd className="font-mono font-medium">{grid["slot-width-ghz"]} GHz</dd>
            </div>
            <div>
              <dt className="text-gray-500">Nominal central frequency</dt>
              <dd className="font-mono font-medium">{grid["nominal-central-frequency-thz"]} THz</dd>
            </div>
          </dl>
        </section>
      )}

      <section className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
        <h2 className="text-sm font-semibold text-gray-700 px-4 py-3 border-b border-gray-200">
          Services with spectrum allocation
        </h2>
        {withSlot.length === 0 ? (
          <p className="p-4 text-gray-500 text-sm">No services with assigned spectrum.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-4 py-2 font-medium text-gray-700">Service</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-700">Center (THz)</th>
                  <th className="text-left px-4 py-2 font-medium text-gray-700">Width (GHz)</th>
                </tr>
              </thead>
              <tbody>
                {withSlot.map((s) => (
                  <tr key={s.uuid} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-2">
                      <span className="font-mono text-xs text-gray-500">{s.uuid.slice(0, 8)}</span>
                      {extractName(s.name, "service-name") && (
                        <span className="ml-2">{extractName(s.name, "service-name")}</span>
                      )}
                    </td>
                    <td className="px-4 py-2 font-mono">
                      {spectrumOf(s)!.centreThz.toFixed(4)}
                    </td>
                    <td className="px-4 py-2 font-mono">
                      {spectrumOf(s)!.widthGhz.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
