import { useMemo, useRef } from "react";
import type { ElementDefinition } from "cytoscape";
import { geographicPositions, type GeoPosition, type Point } from "@/lib/geo";

/**
 * Where the "geographic" layout draws each node of `elements`, or undefined
 * when the twin places none of them.
 *
 * The same positions come back as the same object, so a refresh that brings
 * an unchanged topology does not re-run the layout (which would undo the
 * user's pan and zoom); new or moved nodes do.
 */
export function useGeographicPositions(
  elements: ElementDefinition[],
  positions: Record<string, GeoPosition> | undefined,
): Record<string, Point> | undefined {
  const computed = useMemo(() => {
    const nodes = elements
      .filter((e) => e.data.source == null)
      .map((e) => ({
        id: String(e.data.id),
        type: e.data.type as string | undefined,
        label: e.data.label as string | undefined,
      }));
    return geographicPositions(nodes, positions);
  }, [elements, positions]);

  const kept = useRef<{ key: string; value: Record<string, Point> | undefined }>({
    key: "",
    value: undefined,
  });
  const key = computed ? JSON.stringify(computed) : "";
  if (kept.current.key !== key) kept.current = { key, value: computed };
  return kept.current.value;
}
