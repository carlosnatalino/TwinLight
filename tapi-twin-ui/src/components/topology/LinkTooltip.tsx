interface LinkTooltipData {
  label: string;
  operState: string;
  direction: string;
}

interface LinkTooltipProps {
  data: LinkTooltipData;
  x: number;
  y: number;
}

export default function LinkTooltip({ data, x, y }: LinkTooltipProps) {
  return (
    <div
      className="pointer-events-none fixed z-50 rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs"
      style={{ left: x + 12, top: y - 10 }}
    >
      <p className="font-semibold text-gray-900 mb-1 max-w-[200px] truncate">{data.label || "Link"}</p>
      <div className="space-y-0.5 text-gray-500">
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
        <p>Direction: {data.direction}</p>
      </div>
    </div>
  );
}
