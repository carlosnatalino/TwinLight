/**
 * Constellation diagram visualization component.
 *
 * Renders I/Q scatter plot from synthesized constellation data.
 * Uses canvas for efficient rendering of many points.
 *
 * Reference: OCATA (Sequeira et al., ECOC 2023) - GMM constellation features.
 */
import { useRef, useEffect, useMemo } from "react";
import type { ConstellationResponse } from "@/api/types";

interface Props {
  data: ConstellationResponse | null;
  isLoading?: boolean;
  error?: string | null;
}

const CANVAS_SIZE = 300;
const POINT_RADIUS = 1.5;
const POINT_COLOR = "rgba(59, 130, 246, 0.5)";
const GRID_COLOR = "#e5e7eb";
const AXIS_COLOR = "#9ca3af";

export default function ConstellationDiagram({ data, isLoading, error }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const bounds = useMemo(() => {
    if (!data) return { min: -2, max: 2 };
    const allVals = [...data.i, ...data.q];
    const absMax = Math.max(...allVals.map(Math.abs), 1.5);
    const padded = absMax * 1.15;
    return { min: -padded, max: padded };
  }, [data]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = CANVAS_SIZE * dpr;
    canvas.height = CANVAS_SIZE * dpr;
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);

    const range = bounds.max - bounds.min;
    const scale = CANVAS_SIZE / range;
    const toX = (i: number) => (i - bounds.min) * scale;
    const toY = (q: number) => CANVAS_SIZE - (q - bounds.min) * scale;

    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 0.5;
    const gridStep = range / 8;
    for (let v = bounds.min; v <= bounds.max; v += gridStep) {
      ctx.beginPath();
      ctx.moveTo(toX(v), 0);
      ctx.lineTo(toX(v), CANVAS_SIZE);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, toY(v));
      ctx.lineTo(CANVAS_SIZE, toY(v));
      ctx.stroke();
    }

    ctx.strokeStyle = AXIS_COLOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(toX(0), 0);
    ctx.lineTo(toX(0), CANVAS_SIZE);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(0, toY(0));
    ctx.lineTo(CANVAS_SIZE, toY(0));
    ctx.stroke();

    ctx.fillStyle = POINT_COLOR;
    for (let k = 0; k < data.i.length; k++) {
      const x = toX(data.i[k]);
      const y = toY(data.q[k]);
      ctx.beginPath();
      ctx.arc(x, y, POINT_RADIUS, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [data, bounds]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <h3 className="text-sm font-semibold text-gray-700 mb-2">
        Constellation Diagram
      </h3>
      {isLoading && (
        <div className="flex items-center justify-center h-[300px]">
          <div className="animate-spin h-6 w-6 border-2 border-blue-500 border-t-transparent rounded-full" />
        </div>
      )}
      {error && (
        <div className="flex items-center justify-center h-[300px] text-sm text-red-500">
          {error}
        </div>
      )}
      {!isLoading && !error && data && (
        <div className="space-y-2">
          <canvas
            ref={canvasRef}
            style={{ width: CANVAS_SIZE, height: CANVAS_SIZE }}
            className="mx-auto block"
          />
          <div className="flex justify-between text-xs text-gray-500 px-1">
            <span>
              {data["modulation-format"]} · {data.n_symbols.toLocaleString()} sym
            </span>
            {data.measurements["gsnr-db"] != null && (
              <span>GSNR: {data.measurements["gsnr-db"].toFixed(1)} dB</span>
            )}
          </div>
        </div>
      )}
      {!isLoading && !error && !data && (
        <div className="flex items-center justify-center h-[300px] text-sm text-gray-400">
          No data available
        </div>
      )}
    </div>
  );
}
