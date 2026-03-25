import { ResponsiveContainer, LineChart, Line } from "recharts";
import type { DataPoint } from "@/lib/time-series";
import { cn } from "@/lib/cn";

interface MetricCardProps {
  label: string;
  /** Current value; undefined when only persisted history (no live OPM) */
  value: number | null | undefined;
  unit: string;
  sparklineData: DataPoint[];
  stats?: { min: number; max: number; avg: number } | null;
  formatValue?: (v: number) => string;
  className?: string;
}

function defaultFormat(v: number, unit: string): string {
  if (unit === "BER") {
    if (v === 0) return "0";
    return v.toExponential(2);
  }
  return v.toFixed(2);
}

export default function MetricCard({
  label,
  value,
  unit,
  sparklineData,
  stats,
  formatValue,
  className,
}: MetricCardProps) {
  const fmt = (v: number) =>
    formatValue ? formatValue(v) : defaultFormat(v, unit);

  const hasValue = value != null && typeof value === "number" && !Number.isNaN(value);
  const displayValue = hasValue ? fmt(value) : "—";

  return (
    <div className={cn("rounded-lg border border-gray-200 bg-white p-4 shadow-sm", className)}>
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className="mt-1 text-2xl font-bold text-gray-900">
        {displayValue}
        {hasValue && (
          <span className="ml-1 text-sm font-normal text-gray-400">{unit}</span>
        )}
      </p>

      {/* Min / Avg / Max row */}
      {stats && (
        <div className="mt-1.5 grid grid-cols-3 gap-1 text-center">
          <div>
            <p className="text-[10px] text-gray-400 leading-none">min</p>
            <p className="text-xs font-medium text-gray-600 tabular-nums">{fmt(stats.min)}</p>
          </div>
          <div>
            <p className="text-[10px] text-gray-400 leading-none">avg</p>
            <p className="text-xs font-medium text-gray-600 tabular-nums">{fmt(stats.avg)}</p>
          </div>
          <div>
            <p className="text-[10px] text-gray-400 leading-none">max</p>
            <p className="text-xs font-medium text-gray-600 tabular-nums">{fmt(stats.max)}</p>
          </div>
        </div>
      )}

      {sparklineData.length > 1 && (
        <div className="mt-2 h-10">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparklineData.map((p) => ({ t: p.t, v: p.v }))}>
              <Line
                type="monotone"
                dataKey="v"
                dot={false}
                strokeWidth={1.5}
                stroke="#3b82f6"
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
