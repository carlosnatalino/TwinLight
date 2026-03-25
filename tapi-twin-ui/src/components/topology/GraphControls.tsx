import { ZoomIn, ZoomOut, Maximize2, Type, ArrowRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { LAYOUT_LABELS, type LayoutName } from "@/lib/cytoscape-layout";
import RefreshButton from "@/components/common/RefreshButton";

interface GraphControlsProps {
  layoutName: LayoutName;
  onLayoutChange: (layout: LayoutName) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onRefresh: () => void;
  showLabels: boolean;
  onToggleLabels: () => void;
  showArrows: boolean;
  onToggleArrows: () => void;
  isLoading: boolean;
  /** When set, show link count and node count (links first) */
  linkCount?: number;
  nodeCount?: number;
}

const LAYOUTS = Object.entries(LAYOUT_LABELS) as [LayoutName, string][];

export default function GraphControls({
  layoutName,
  onLayoutChange,
  onZoomIn,
  onZoomOut,
  onFit,
  onRefresh,
  showLabels,
  onToggleLabels,
  showArrows,
  onToggleArrows,
  isLoading,
  linkCount,
  nodeCount,
}: GraphControlsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-white border-b border-gray-200">
      {/* Link/node summary (links first) */}
      {linkCount !== undefined && nodeCount !== undefined && (
        <span className="text-xs text-gray-500">
          {linkCount} links, {nodeCount} nodes
        </span>
      )}
      {(linkCount !== undefined || nodeCount !== undefined) && <div className="h-5 w-px bg-gray-200" />}

      {/* Layout selector */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-gray-500">Layout:</span>
        <select
          value={layoutName}
          onChange={(e) => onLayoutChange(e.target.value as LayoutName)}
          className="text-xs border border-gray-200 rounded px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {LAYOUTS.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div className="h-5 w-px bg-gray-200" />

      {/* Zoom controls */}
      <button
        onClick={onZoomIn}
        className="rounded p-1 text-gray-600 hover:bg-gray-100 transition-colors"
        title="Zoom in"
      >
        <ZoomIn className="h-4 w-4" />
      </button>
      <button
        onClick={onZoomOut}
        className="rounded p-1 text-gray-600 hover:bg-gray-100 transition-colors"
        title="Zoom out"
      >
        <ZoomOut className="h-4 w-4" />
      </button>
      <button
        onClick={onFit}
        className="rounded p-1 text-gray-600 hover:bg-gray-100 transition-colors"
        title="Fit to screen"
      >
        <Maximize2 className="h-4 w-4" />
      </button>

      <div className="h-5 w-px bg-gray-200" />

      {/* Toggle labels */}
      <button
        onClick={onToggleLabels}
        className={cn(
          "rounded p-1 transition-colors",
          showLabels ? "text-blue-600 bg-blue-50" : "text-gray-400 hover:bg-gray-100"
        )}
        title={showLabels ? "Hide labels" : "Show labels"}
      >
        <Type className="h-4 w-4" />
      </button>

      {/* Toggle arrows */}
      <button
        onClick={onToggleArrows}
        className={cn(
          "rounded p-1 transition-colors",
          showArrows ? "text-blue-600 bg-blue-50" : "text-gray-400 hover:bg-gray-100"
        )}
        title={showArrows ? "Hide arrows" : "Show arrows"}
      >
        <ArrowRight className="h-4 w-4" />
      </button>

      <div className="ml-auto">
        <RefreshButton onClick={onRefresh} isLoading={isLoading} />
      </div>
    </div>
  );
}
