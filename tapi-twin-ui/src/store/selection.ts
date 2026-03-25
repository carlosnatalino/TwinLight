import { create } from "zustand";

interface SelectionState {
  selectedNodeId: string | null;
  selectedLinkId: string | null;
  selectedTopologyId: string | null;

  selectNode: (nodeId: string, topologyId: string) => void;
  selectLink: (linkId: string, topologyId: string) => void;
  clearSelection: () => void;
}

export const useSelectionStore = create<SelectionState>()((set) => ({
  selectedNodeId: null,
  selectedLinkId: null,
  selectedTopologyId: null,

  selectNode: (nodeId, topologyId) =>
    set({ selectedNodeId: nodeId, selectedLinkId: null, selectedTopologyId: topologyId }),

  selectLink: (linkId, topologyId) =>
    set({ selectedNodeId: null, selectedLinkId: linkId, selectedTopologyId: topologyId }),

  clearSelection: () =>
    set({ selectedNodeId: null, selectedLinkId: null, selectedTopologyId: null }),
}));
