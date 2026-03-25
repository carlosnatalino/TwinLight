import { Pause, Play, Download, Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { TIME_RANGES, type TimeRange } from "@/hooks/useMonitoringHistory";

interface HistoryControlsProps {
  selectedRange: TimeRange;
  onRangeChange: (range: TimeRange) => void;
  isPaused: boolean;
  onTogglePause: () => void;
  onExportCsv: () => void;
  onClearHistory: () => void;
}

export default function HistoryControls({
  selectedRange,
  onRangeChange,
  isPaused,
  onTogglePause,
  onExportCsv,
  onClearHistory,
}: HistoryControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {/* Time range */}
      <div className="flex rounded-md border border-gray-200 overflow-hidden">
        {TIME_RANGES.map((range) => (
          <button
            key={range.label}
            onClick={() => onRangeChange(range)}
            className={cn(
              "px-3 py-1.5 text-xs font-medium transition-colors",
              selectedRange.label === range.label
                ? "bg-blue-600 text-white"
                : "bg-white text-gray-600 hover:bg-gray-50"
            )}
          >
            {range.label}
          </button>
        ))}
      </div>

      {/* Pause/Resume */}
      <button
        onClick={onTogglePause}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium border transition-colors",
          isPaused
            ? "bg-green-50 text-green-700 border-green-300 hover:bg-green-100"
            : "bg-white text-gray-600 border-gray-200 hover:bg-gray-50"
        )}
      >
        {isPaused ? <Play className="h-3 w-3" /> : <Pause className="h-3 w-3" />}
        {isPaused ? "Resume" : "Pause"}
      </button>

      {/* Export CSV */}
      <button
        onClick={onExportCsv}
        className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium bg-white text-gray-600 border border-gray-200 hover:bg-gray-50 transition-colors"
      >
        <Download className="h-3 w-3" />
        Export CSV
      </button>

      {/* Clear history */}
      <button
        onClick={onClearHistory}
        className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium bg-white text-red-600 border border-red-200 hover:bg-red-50 transition-colors"
      >
        <Trash2 className="h-3 w-3" />
        Clear
      </button>
    </div>
  );
}
