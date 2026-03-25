import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import type { Core } from "cytoscape";
import { useConnectionStore } from "@/store/connection";
import { useTopologyStore } from "@/store/topology";
import { useSelectionStore } from "@/store/selection";
import { useTopologyGraph } from "@/hooks/useTopologyGraph";
import TopologyGraph from "@/components/topology/TopologyGraph";
import GraphControls from "@/components/topology/GraphControls";
import DetailPanel from "@/components/topology/DetailPanel";
import EmptyState from "@/components/common/EmptyState";
import { Network, Loader2 } from "lucide-react";
import type { LayoutName } from "@/lib/cytoscape-layout";

export default function TopologyPage() {
  const { baseUrl } = useConnectionStore();
  const { data, isLoading, fetchTopology } = useTopologyStore();
  const { selectNode, selectLink } = useSelectionStore();
  const navigate = useNavigate();

  const [layoutName, setLayoutName] = useState<LayoutName>("cose");
  const [showLabels, setShowLabels] = useState(true);
  const [showArrows, setShowArrows] = useState(true);
  const cyRef = useRef<Core | null>(null);

  useEffect(() => {
    if (baseUrl) void fetchTopology(baseUrl);
  }, [baseUrl, fetchTopology]);

  const topologies = data?.topologies ?? [];
  const sips = data?.sips ?? [];
  const allLinks = topologies.flatMap((t) => t.link);
  const allNodes = topologies.flatMap((t) => t.node);
  const elements = useTopologyGraph(topologies, sips);

  const handleNodeClick = useCallback(
    (nodeId: string, topoId: string) => {
      selectNode(nodeId, topoId);
    },
    [selectNode]
  );

  const handleLinkClick = useCallback(
    (linkId: string, topoId: string) => {
      selectLink(linkId, topoId);
    },
    [selectLink]
  );

  const handleNodeDblClick = useCallback(
    (nodeId: string, topoId: string) => {
      if (topoId) navigate(`/topology/${topoId}/node/${nodeId}`);
    },
    [navigate]
  );

  const handleFit = () => cyRef.current?.fit();
  const handleZoomIn = () => {
    const cy = cyRef.current;
    if (cy) cy.zoom({ level: cy.zoom() * 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };
  const handleZoomOut = () => {
    const cy = cyRef.current;
    if (cy) cy.zoom({ level: cy.zoom() / 1.25, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };

  return (
    <div className="flex h-full flex-col">
      <GraphControls
        layoutName={layoutName}
        onLayoutChange={setLayoutName}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onFit={handleFit}
        onRefresh={() => void fetchTopology(baseUrl)}
        showLabels={showLabels}
        onToggleLabels={() => setShowLabels((v) => !v)}
        showArrows={showArrows}
        onToggleArrows={() => setShowArrows((v) => !v)}
        isLoading={isLoading}
        linkCount={allLinks.length}
        nodeCount={allNodes.length}
      />

      <div className="flex flex-1 overflow-hidden">
        {isLoading && !data && (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
          </div>
        )}

        {!isLoading && elements.length === 0 && (
          <div className="flex flex-1 items-center justify-center">
            <EmptyState
              icon={<Network className="h-8 w-8" />}
              title="No topology data"
              description="Click Refresh to load the network from the Digital Twin."
            />
          </div>
        )}

        {elements.length > 0 && (
          <div className="flex-1 overflow-hidden">
            <TopologyGraph
              elements={elements}
              layoutName={layoutName}
              showLabels={showLabels}
              showArrows={showArrows}
              onNodeClick={handleNodeClick}
              onLinkClick={handleLinkClick}
              onNodeDblClick={handleNodeDblClick}
              cyRef={cyRef}
            />
          </div>
        )}

        <DetailPanel />
      </div>
    </div>
  );
}
