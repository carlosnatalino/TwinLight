import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import type { DataPoint } from "@/lib/time-series";

interface MetricChartProps {
  label: string;
  unit: string;
  data: DataPoint[];
  /** When data is empty, show this as a single point so the chart is not blank */
  fallbackPoint?: { t: number; v: number } | null;
  color?: string;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatTooltipValue(value: number, unit: string): string {
  if (unit === "BER") return value.toExponential(3);
  return `${value.toFixed(3)} ${unit}`;
}

/** Compute Y-axis [min, max] with 10 % padding on each side. */
function computeDomain(values: number[], unit: string): [number, number] {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const range = hi - lo;
  // Flat line: pad by ±10 % of the value itself, or ±1 if value is zero.
  const pad = range === 0 ? (Math.abs(hi) * 0.1 || 1) : range * 0.1;
  const domainMin = lo - pad;
  const domainMax = hi + pad;
  // BER is always positive — clamp lower bound to zero.
  return [unit === "BER" ? Math.max(0, domainMin) : domainMin, domainMax];
}

export default function MetricChart({
  label,
  unit,
  data,
  fallbackPoint,
  color = "#3b82f6",
}: MetricChartProps) {
  const effectiveData =
    data.length > 0 ? data : fallbackPoint && !isNaN(fallbackPoint.v) ? [fallbackPoint] : [];

  if (effectiveData.length === 0) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-2">{label}</p>
        <div className="flex h-32 items-center justify-center text-sm text-gray-400">
          No data yet
        </div>
      </div>
    );
  }

  const chartData = effectiveData.map((p) => ({ t: p.t, v: p.v }));
  const [domainMin, domainMax] = computeDomain(effectiveData.map((p) => p.v), unit);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-3">
        {label}
        <span className="ml-1 font-normal text-gray-400">({unit})</span>
      </p>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="t"
              tickFormatter={formatTime}
              tick={{ fontSize: 10, fill: "#9ca3af" }}
              tickLine={false}
              axisLine={false}
              minTickGap={60}
            />
            <YAxis
              domain={[domainMin, domainMax]}
              tick={{ fontSize: 10, fill: "#9ca3af" }}
              tickLine={false}
              axisLine={false}
              width={50}
              tickFormatter={(v: number) => (unit === "BER" ? v.toExponential(1) : v.toFixed(2))}
            />
            <Tooltip
              labelFormatter={(v) => formatTime(v as number)}
              formatter={(v) => [formatTooltipValue(v as number, unit), label]}
              contentStyle={{
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid #e5e7eb",
              }}
            />
            <Line
              type="monotone"
              dataKey="v"
              stroke={color}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
