"""NetworkX DiGraph wrapper for the optical network topology.

Stores the GNPy element graph and provides adjacency/neighbor queries
used by the TAPI builder and later by routing algorithms.
"""

from __future__ import annotations

import networkx as nx

from twinlight.loader.gnpy_topology import GnpyElement, GnpyTopology, fiber_length_m

# Weight of an edge that does not leave a fiber, as gnpy.tools.json_io sets it.
_NON_FIBER_EDGE_WEIGHT = 0.01


class TopologyGraph:
    """Directed graph built from a parsed GNPy topology."""

    def __init__(self, gnpy_topo: GnpyTopology) -> None:
        self.graph = nx.DiGraph()
        self._gnpy_topo = gnpy_topo
        self._build_graph()

    def _build_graph(self) -> None:
        """Nodes are element UIDs; edges carry GNPy's routing ``weight``.

        The weight follows ``gnpy.tools.json_io.network_from_json``: an edge
        leaving a Fiber weighs the fiber's length in metres, every other edge
        0.01. Ranking routes on this graph therefore orders them as GNPy's
        ``compute_constrained_path`` does on its own network, which is what
        lets it stand in when no GNPy network is loaded (the EGN backend).
        A fiber whose length cannot be read weighs as GNPy's default edge.
        """
        for el in self._gnpy_topo.elements:
            self.graph.add_node(el.uid, element=el)
        by_uid = self._gnpy_topo.elements_by_uid
        for conn in self._gnpy_topo.connections:
            source = by_uid.get(conn.from_node)
            weight = _NON_FIBER_EDGE_WEIGHT
            if source is not None and source.type == "Fiber":
                weight = fiber_length_m(source.params) or _NON_FIBER_EDGE_WEIGHT
            self.graph.add_edge(conn.from_node, conn.to_node, weight=weight)

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
