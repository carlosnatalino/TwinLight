import type cytoscape from "cytoscape";

export type LayoutName = "cose" | "dagre" | "grid" | "breadthfirst" | "circle";

export const LAYOUT_CONFIGS: Record<LayoutName, cytoscape.LayoutOptions> = {
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
  cose: "Force-directed",
  dagre: "Hierarchical",
  grid: "Grid",
  breadthfirst: "Breadth-first",
  circle: "Circle",
};

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
