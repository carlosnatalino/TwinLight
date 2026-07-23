"""NetworkX DiGraph wrapper for the optical network topology.

Stores the GNPy element graph and provides adjacency/neighbor queries
used by the TAPI builder and later by routing algorithms.
"""

from __future__ import annotations

import networkx as nx

from twinlight.loader.gnpy_topology import GnpyElement, GnpyTopology


class TopologyGraph:
    """Directed graph built from a parsed GNPy topology."""

    def __init__(self, gnpy_topo: GnpyTopology) -> None:
        self.graph = nx.DiGraph()
        self._gnpy_topo = gnpy_topo
        self._build_graph()

    def _build_graph(self) -> None:
        for el in self._gnpy_topo.elements:
            self.graph.add_node(el.uid, element=el)
        for conn in self._gnpy_topo.connections:
            self.graph.add_edge(conn.from_node, conn.to_node)

    def get_element(self, uid: str) -> GnpyElement:
        return self.graph.nodes[uid]["element"]

    def neighbors(self, uid: str) -> list[str]:
        return list(self.graph.successors(uid))

    def predecessors(self, uid: str) -> list[str]:
        return list(self.graph.predecessors(uid))

    def elements_of_type(self, element_type: str) -> list[GnpyElement]:
        return [
            data["element"]
            for _, data in self.graph.nodes(data=True)
            if data["element"].type == element_type
        ]
