interface NodeTooltipData {
  label: string;
  type: string;
  operState: string;
  adminState: string;
  nepCount: number;
}

interface NodeTooltipProps {
  data: NodeTooltipData;
  x: number;
  y: number;
}

export default function NodeTooltip({ data, x, y }: NodeTooltipProps) {
  return (
    <div
      className="pointer-events-none fixed z-50 rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs"
      style={{ left: x + 12, top: y - 10 }}
    >
      <p className="font-semibold text-gray-900 mb-1">{data.label}</p>
      <div className="space-y-0.5 text-gray-500">
        <p>
          Type:{" "}
          <span className="capitalize font-medium text-gray-700">{data.type}</span>
        </p>
        <p>
          Oper:{" "}
          <span
            className={
              data.operState === "ENABLED" ? "text-green-600 font-medium" : "text-red-600 font-medium"
            }
          >
            {data.operState}
          </span>
        </p>
        <p>NEPs: {data.nepCount}</p>
      </div>
    </div>
  );
}
