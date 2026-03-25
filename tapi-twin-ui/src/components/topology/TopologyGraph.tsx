import { useRef, useEffect, useState, useCallback } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape, { type ElementDefinition, type Core } from "cytoscape";
import dagre from "cytoscape-dagre";
import { CYTOSCAPE_STYLESHEET, LAYOUT_CONFIGS, type LayoutName } from "@/lib/cytoscape-layout";
import NodeTooltip from "./NodeTooltip";
import LinkTooltip from "./LinkTooltip";

// Register dagre layout (safe to call multiple times)
try {
  cytoscape.use(dagre);
} catch {
  // Already registered
}

interface TooltipState {
  type: "node" | "edge";
  x: number;
  y: number;
  data: Record<string, unknown>;
}

export interface TopologyGraphRef {
  fit: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

interface TopologyGraphProps {
  elements: ElementDefinition[];
  layoutName: LayoutName;
  showLabels: boolean;
  showArrows: boolean;
  onNodeClick: (nodeId: string, topoId: string) => void;
  onLinkClick: (linkId: string, topoId: string) => void;
  onNodeDblClick: (nodeId: string, topoId: string) => void;
  cyRef: React.MutableRefObject<Core | null>;
}

export default function TopologyGraph({
  elements,
  layoutName,
  showLabels,
  showArrows,
  onNodeClick,
  onLinkClick,
  onNodeDblClick,
  cyRef,
}: TopologyGraphProps) {
  const [cyInstance, setCyInstance] = useState<Core | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const layoutRef = useRef(layoutName);

  const handleCy = useCallback((cy: Core) => {
    cyRef.current = cy;
    setCyInstance(cy);
  }, [cyRef]);

  // Re-run layout when layoutName changes
  useEffect(() => {
    if (!cyInstance || layoutName === layoutRef.current) return;
    layoutRef.current = layoutName;
    const layout = cyInstance.layout(LAYOUT_CONFIGS[layoutName] || LAYOUT_CONFIGS.cose);
    layout.run();
  }, [cyInstance, layoutName]);

  // Update label/arrow classes when toggles change
  useEffect(() => {
    if (!cyInstance) return;
    if (showLabels) {
      cyInstance.elements().removeClass("hidden-label");
    } else {
      cyInstance.elements().addClass("hidden-label");
    }
    if (showArrows) {
      cyInstance.edges().removeClass("no-arrows");
    } else {
      cyInstance.edges().addClass("no-arrows");
    }
  }, [cyInstance, showLabels, showArrows]);

  // Wire event listeners
  useEffect(() => {
    if (!cyInstance) return;

    const handleNodeClick = (evt: cytoscape.EventObject) => {
      const node = evt.target;
      onNodeClick(node.id(), node.data("topoId") as string);
    };

    const handleEdgeClick = (evt: cytoscape.EventObject) => {
      const edge = evt.target;
      onLinkClick(edge.id(), edge.data("topoId") as string);
    };

    const handleNodeDblClick = (evt: cytoscape.EventObject) => {
      const node = evt.target;
      onNodeDblClick(node.id(), node.data("topoId") as string);
    };

    const handleMouseOver = (evt: cytoscape.EventObject) => {
      const el = evt.target;
      const renderedPos = evt.renderedPosition;
      const isNode = el.isNode();
      setTooltip({
        type: isNode ? "node" : "edge",
        x: renderedPos.x,
        y: renderedPos.y,
        data: el.data() as Record<string, unknown>,
      });
    };

    const handleMouseOut = () => {
      setTooltip(null);
    };

    cyInstance.on("tap", "node", handleNodeClick);
    cyInstance.on("tap", "edge", handleEdgeClick);
    cyInstance.on("dbltap", "node", handleNodeDblClick);
    cyInstance.on("mouseover", "node, edge", handleMouseOver);
    cyInstance.on("mouseout", "node, edge", handleMouseOut);

    return () => {
      cyInstance.off("tap", "node", handleNodeClick);
      cyInstance.off("tap", "edge", handleEdgeClick);
      cyInstance.off("dbltap", "node", handleNodeDblClick);
      cyInstance.off("mouseover", "node, edge", handleMouseOver);
      cyInstance.off("mouseout", "node, edge", handleMouseOut);
    };
  }, [cyInstance, onNodeClick, onLinkClick, onNodeDblClick]);

  const currentLayout = LAYOUT_CONFIGS[layoutName] || LAYOUT_CONFIGS.cose;

  return (
    <div className="relative h-full w-full">
      <CytoscapeComponent
        elements={elements}
        stylesheet={CYTOSCAPE_STYLESHEET}
        layout={currentLayout}
        style={{ width: "100%", height: "100%" }}
        cy={handleCy}
      />

      {tooltip && tooltip.type === "node" && (
        <NodeTooltip
          data={{
            label: String(tooltip.data.label ?? ""),
            type: String(tooltip.data.type ?? ""),
            operState: String(tooltip.data.operState ?? ""),
            adminState: String(tooltip.data.adminState ?? ""),
            nepCount: Number(tooltip.data.nepCount ?? 0),
          }}
          x={tooltip.x}
          y={tooltip.y}
        />
      )}
      {tooltip && tooltip.type === "edge" && (
        <LinkTooltip
          data={{
            label: String(tooltip.data.label ?? ""),
            operState: String(tooltip.data.operState ?? ""),
            direction: String(tooltip.data.direction ?? ""),
          }}
          x={tooltip.x}
          y={tooltip.y}
        />
      )}
    </div>
  );
}
