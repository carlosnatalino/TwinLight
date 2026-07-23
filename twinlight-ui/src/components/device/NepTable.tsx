import type { NodeEdgePoint } from "@/api/types";
import { extractName } from "@/lib/tapi-helpers";
import SipBadge from "./SipBadge";
import StateIndicator from "./StateIndicator";

interface NepTableProps {
  neps: NodeEdgePoint[];
}

export default function NepTable({ neps }: NepTableProps) {
  if (neps.length === 0) {
    return <p className="text-sm text-gray-500 italic">No node edge points.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-gray-50 border-b border-gray-200">
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              UUID
            </th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              Name
            </th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              Layer
            </th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              Dir
            </th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              Oper
            </th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-600 uppercase tracking-wide">
              SIPs
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {neps.map((nep) => {
            const name =
              extractName(nep.name, "nep-name") ||
              extractName(nep.name, "name") ||
              nep.uuid.slice(0, 12);
            const sips = nep["mapped-service-interface-point"] ?? [];

            return (
              <tr key={nep.uuid} className="hover:bg-gray-50">
                <td className="px-3 py-2 font-mono text-xs text-gray-500">
                  {nep.uuid.slice(0, 12)}…
                </td>
                <td className="px-3 py-2 text-gray-700">{name}</td>
                <td className="px-3 py-2 text-xs text-gray-500">{nep["layer-protocol-name"]}</td>
                <td className="px-3 py-2 text-xs text-gray-500">{nep.direction}</td>
                <td className="px-3 py-2">
                  <StateIndicator label="" value={nep["operational-state"]} />
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    {sips.map((s) => (
                      <SipBadge key={s["service-interface-point-uuid"]} sipUuid={s["service-interface-point-uuid"]} />
                    ))}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
