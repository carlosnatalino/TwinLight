import type cytoscape from "cytoscape";
import type { Point } from "@/lib/geo";

/** A layout that needs nothing but the graph. */
export type GraphLayoutName = "cose" | "dagre" | "grid" | "breadthfirst" | "circle";
/** "geographic" draws each node at its position (lib/geo.ts), when the twin gives them. */
export type LayoutName = "geographic" | GraphLayoutName;

/** The layout a page starts with when the twin gives no positions. */
export const DEFAULT_GRAPH_LAYOUT: GraphLayoutName = "cose";

export const LAYOUT_CONFIGS: Record<GraphLayoutName, cytoscape.LayoutOptions> = {
  cose: {
    name: "cose",
    animate: true,
    animationDuration: 500,
    nodeRepulsion: () => 4_000_000,
    idealEdgeLength: () => 100,
    edgeElasticity: () => 45,
    fit: true,
    padding: 40,
  } as cytoscape.LayoutOptions,
  dagre: {
    name: "dagre",
    animate: true,
    animationDuration: 500,
    fit: true,
    padding: 40,
  } as cytoscape.LayoutOptions,
  grid: {
    name: "grid",
    animate: true,
    animationDuration: 400,
    fit: true,
    padding: 40,
    avoidOverlapPadding: 20,
  } as cytoscape.LayoutOptions,
  breadthfirst: {
    name: "breadthfirst",
    animate: true,
    animationDuration: 500,
    directed: true,
    fit: true,
    padding: 40,
    spacingFactor: 1.5,
  } as cytoscape.LayoutOptions,
  circle: {
    name: "circle",
    animate: true,
    animationDuration: 400,
    fit: true,
    padding: 40,
  } as cytoscape.LayoutOptions,
};

export const LAYOUT_LABELS: Record<LayoutName, string> = {
  geographic: "Geographic",
  cose: "Force-directed",
  dagre: "Hierarchical",
  grid: "Grid",
  breadthfirst: "Breadth-first",
  circle: "Circle",
};

/**
 * The layout to draw with: the one the user chose, or by default
 * "geographic" when the graph has positions and force-directed otherwise.
 * A choice of "geographic" that the graph cannot honour (the positions went
 * away on a refresh from an older twin) falls back to the default.
 */
export function chooseLayout(chosen: LayoutName | null, hasPositions: boolean): LayoutName {
  if (chosen === "geographic" && !hasPositions) return DEFAULT_GRAPH_LAYOUT;
  return chosen ?? (hasPositions ? "geographic" : DEFAULT_GRAPH_LAYOUT);
}

/** The layouts a page offers: "geographic" only when there are positions. */
export function availableLayouts(hasPositions: boolean): LayoutName[] {
  return (Object.keys(LAYOUT_LABELS) as LayoutName[]).filter(
    (name) => name !== "geographic" || hasPositions,
  );
}

/**
 * Cytoscape options for a layout. "geographic" is a `preset` layout on the
 * projected positions, fitted to the viewport like the others and not
 * animated: it is placed in one step, so there is no frame left to run after
 * the page is gone. Without positions it is the default layout.
 */
export function layoutOptions(
  name: LayoutName,
  positions: Record<string, Point> | undefined,
): cytoscape.LayoutOptions {
  if (name === "geographic") {
    if (!positions) return LAYOUT_CONFIGS[DEFAULT_GRAPH_LAYOUT];
    return { name: "preset", positions, fit: true, padding: 40, animate: false };
  }
  return LAYOUT_CONFIGS[name] ?? LAYOUT_CONFIGS[DEFAULT_GRAPH_LAYOUT];
}

export const CYTOSCAPE_STYLESHEET: Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS> = [
  {
    selector: "node",
    style: {
      label: "data(label)",
      "text-valign": "bottom",
      "text-halign": "center",
      "text-margin-y": 8,
      "font-size": 11,
      color: "#374151",
      "text-wrap": "wrap",
      "text-max-width": "120px",
    },
  },
  {
    selector: "node[type='transceiver']",
    style: {
      shape: "round-rectangle",
      "background-color": "#3b82f6",
      width: 40,
      height: 28,
    },
  },
  {
    selector: "node[type='roadm']",
    style: {
      shape: "diamond",
      "background-color": "#22c55e",
      width: 44,
      height: 44,
    },
  },
  {
    selector: "node[operState='DISABLED']",
    style: {
      "background-color": "#9ca3af",
      "border-style": "dashed",
      "border-width": 2,
      "border-color": "#ef4444",
      opacity: 0.7,
    },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "line-color": "#6b7280",
      "target-arrow-color": "#6b7280",
      width: 2,
      label: "data(label)",
      "font-size": 9,
      "text-rotation": "autorotate",
      "text-margin-y": -8,
      color: "#6b7280",
    },
  },
  {
    selector: "edge[operState='ENABLED']",
    style: {
      "line-color": "#22c55e",
      "target-arrow-color": "#22c55e",
    },
  },
  {
    selector: "edge[operState='DISABLED']",
    style: {
      "line-color": "#ef4444",
      "target-arrow-color": "#ef4444",
      "line-style": "dashed",
    },
  },
  {
    selector: "node:selected",
    style: {
      "border-width": 3,
      "border-color": "#f59e0b",
      "border-style": "solid",
    },
  },
  {
    selector: "edge:selected",
    style: {
      "line-color": "#f59e0b",
      "target-arrow-color": "#f59e0b",
      width: 3,
    },
  },
  {
    selector: "node.hidden-label",
    style: {
      label: "",
    },
  },
  {
    selector: "edge.hidden-label",
    style: {
      label: "",
    },
  },
  {
    selector: "edge.no-arrows",
    style: {
      "target-arrow-shape": "none",
    },
  },
];
