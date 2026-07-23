/**
 * Eye diagram visualization component.
 *
 * Renders overlaid time-vs-amplitude traces to form the characteristic "eye".
 * Uses canvas for efficient rendering of many traces.
 *
 * Traces come from the backend's statistical synthesis: raised-cosine pulses
 * with GSNR-derived noise and PMD-derived timing jitter (see docs/PHYSICS.md).
 */
import { useRef, useEffect, useMemo } from "react";
import type { EyeDiagramResponse } from "@/api/types";

interface Props {
  data: EyeDiagramResponse | null;
  isLoading?: boolean;
  error?: string | null;
}

const CANVAS_WIDTH = 350;
const CANVAS_HEIGHT = 220;
const TRACE_COLOR = "rgba(34, 197, 94, 0.15)";
const GRID_COLOR = "#e5e7eb";
const AXIS_COLOR = "#9ca3af";

export default function EyeDiagram({ data, isLoading, error }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const bounds = useMemo(() => {
    if (!data || data.traces.length === 0) {
      return { yMin: -2, yMax: 2, tMin: 0, tMax: 1 };
    }
    const allAmps = data.traces.flat();
    const yMin = Math.min(...allAmps) * 1.1;
    const yMax = Math.max(...allAmps) * 1.1;
    const tMin = Math.min(...data.time_ns);
    const tMax = Math.max(...data.time_ns);
    return { yMin, yMax, tMin, tMax };
  }, [data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || data.traces.length === 0) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = CANVAS_WIDTH * dpr;
    canvas.height = CANVAS_HEIGHT * dpr;
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT);

    const { yMin, yMax, tMin, tMax } = bounds;
    const xScale = CANVAS_WIDTH / (tMax - tMin);
    const yScale = CANVAS_HEIGHT / (yMax - yMin);
    const toX = (t: number) => (t - tMin) * xScale;
    const toY = (a: number) => CANVAS_HEIGHT - (a - yMin) * yScale;

    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 0.5;
    const yRange = yMax - yMin;
    const yStep = yRange / 6;
    for (let v = yMin; v <= yMax; v += yStep) {
      ctx.beginPath();
      ctx.moveTo(0, toY(v));
      ctx.lineTo(CANVAS_WIDTH, toY(v));
      ctx.stroke();
    }
    const tRange = tMax - tMin;
    const tStep = tRange / 8;
    for (let t = tMin; t <= tMax; t += tStep) {
      ctx.beginPath();
      ctx.moveTo(toX(t), 0);
      ctx.lineTo(toX(t), CANVAS_HEIGHT);
      ctx.stroke();
    }

    ctx.strokeStyle = AXIS_COLOR;
    ctx.lineWidth = 1;
    const zeroY = toY(0);
    if (zeroY > 0 && zeroY < CANVAS_HEIGHT) {
      ctx.beginPath();
      ctx.moveTo(0, zeroY);
      ctx.lineTo(CANVAS_WIDTH, zeroY);
      ctx.stroke();
    }
    const centerT = toX((tMin + tMax) / 2);
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(centerT, 0);
    ctx.lineTo(centerT, CANVAS_HEIGHT);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.strokeStyle = TRACE_COLOR;
    ctx.lineWidth = 1;
    for (const trace of data.traces) {
      ctx.beginPath();
      for (let i = 0; i < trace.length; i++) {
        const x = toX(data.time_ns[i]);
        const y = toY(trace[i]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  }, [data, bounds]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-gray-700 mb-2">Eye Diagram</h3>
      {isLoading && (
        <div className="flex items-center justify-center h-[220px]">
          <div className="animate-spin h-6 w-6 border-2 border-green-500 border-t-transparent rounded-full" />
        </div>
      )}
      {error && (
        <div className="flex items-center justify-center h-[220px] text-sm text-red-500">
          {error}
        </div>
      )}
      {!isLoading && !error && data && data.traces.length > 0 && (
        <div className="space-y-2">
          <canvas
            ref={canvasRef}
            style={{ width: CANVAS_WIDTH, height: CANVAS_HEIGHT }}
            className="mx-auto block"
          />
          <div className="flex justify-between text-xs text-gray-500 px-1">
            <span>
              {data["modulation-format"]} · {data.n_traces} traces
            </span>
            <span>
              T<sub>sym</sub> = {data.symbol_period_ns.toFixed(3)} ns
            </span>
          </div>
          <div className="flex justify-between text-xs text-gray-500 px-1">
            {data.measurements["gsnr-db"] != null && (
              <span>GSNR: {data.measurements["gsnr-db"].toFixed(1)} dB</span>
            )}
            {data.measurements["pmd-ps"] != null && (
              <span>PMD: {data.measurements["pmd-ps"].toFixed(2)} ps</span>
            )}
          </div>
        </div>
      )}
      {!isLoading && !error && (!data || data.traces.length === 0) && (
        <div className="flex items-center justify-center h-[220px] text-sm text-gray-400">
          No data available
        </div>
      )}
    </div>
  );
}
