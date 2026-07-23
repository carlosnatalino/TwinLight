"""Convert parsed GNPy topology into TAPI model objects.

Mapping rules:
    GNPy Transceiver -> TAPI Node (1 NEP, 1 SIP, layer=PHOTONIC_MEDIA)
    GNPy Roadm       -> TAPI Node (N NEPs, one per degree/direction)
    Fiber+Edfa spans  -> TAPI Link (connecting NEPs on terminal nodes)

A *terminal node* is a Transceiver or Roadm. Fiber, Edfa, Fused, and
RamanFiber elements are intermediate — they become metadata on the TAPI
Link that spans between two terminal nodes.
"""

from __future__ import annotations

import uuid as uuid_lib

from twinlight.loader.gnpy_topology import GnpyTopology
from twinlight.models.common import (
    LayerProtocolName,
    NameAndValue,
    ServiceInterfacePoint,
)
from twinlight.models.topology import (
    ForwardingDirection,
    LatencyCharacteristic,
    Link,
    Node,
    NodeEdgePoint,
    NodeEdgePointRef,
    ServiceInterfacePointRef,
    Topology,
    TopologyContext,
)
from twinlight.state.topology_state import TopologyGraph

TERMINAL_TYPES = {"Transceiver", "Roadm"}


class TapiBuilder:
    """Builds TAPI topology from a GNPy topology graph."""

    def __init__(self, gnpy_topo: GnpyTopology, topo_graph: TopologyGraph) -> None:
        self._gnpy = gnpy_topo
        self._graph = topo_graph
        self._topology_uuid = str(
            uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, gnpy_topo.network_name)
        )

        self._nodes: dict[str, Node] = {}
        self._sips: list[ServiceInterfacePoint] = []
        self._links: list[Link] = []

        # (terminal_uid, peer_terminal_uid) -> NEP uuid
        self._nep_for_direction: dict[tuple[str, str], str] = {}
        # terminal_uid -> Node uuid
        self._node_uuid_for: dict[str, str] = {}

    def build(self) -> tuple[TopologyContext, list[ServiceInterfacePoint]]:
        """Build and return the full TAPI topology context and SIPs."""
        self._build_nodes()
        self._build_links()

        topology = Topology(
            uuid=self._topology_uuid,
            name=[NameAndValue(value_name="network-name", value=self._gnpy.network_name)],
            layer_protocol_name=[LayerProtocolName.PHOTONIC_MEDIA],
            node=list(self._nodes.values()),
            link=self._links,
        )
        ctx = TopologyContext(topology=[topology])
        return ctx, self._sips

    # ------------------------------------------------------------------
    # Node building
    # ------------------------------------------------------------------

    def _build_nodes(self) -> None:
        """Create TAPI Nodes for Transceivers and Roadms."""
        # First, discover which terminal nodes connect to which other terminals
        adjacency = self._find_terminal_adjacency()

        for el in self._gnpy.elements:
            if el.type not in TERMINAL_TYPES:
                continue

            node_uuid = str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, el.uid))
            self._node_uuid_for[el.uid] = node_uuid

            # Build one NEP per adjacent terminal direction
            neps: list[NodeEdgePoint] = []
            peer_terminals = adjacency.get(el.uid, [])

            if not peer_terminals:
                # Isolated terminal: still create one NEP
                nep_uuid = str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"{el.uid}:nep"))
                neps.append(self._make_nep(nep_uuid, el.uid, None))
            else:
                for peer_uid in peer_terminals:
                    nep_uuid = str(
                        uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"{el.uid}:nep:{peer_uid}")
                    )
                    self._nep_for_direction[(el.uid, peer_uid)] = nep_uuid
                    neps.append(self._make_nep(nep_uuid, el.uid, peer_uid))

            # Transceivers get a SIP mapped to their (first) NEP
            sip_refs = []
            if el.type == "Transceiver" and neps:
                sip = ServiceInterfacePoint(
                    uuid=str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"{el.uid}:sip")),
                    name=[NameAndValue(value_name="node-name", value=el.uid)],
                )
                self._sips.append(sip)
                sip_refs.append(ServiceInterfacePointRef(
                    service_interface_point_uuid=sip.uuid,
                ))
                # Map SIP to first NEP
                neps[0].mapped_service_interface_point = sip_refs

            node = Node(
                uuid=node_uuid,
                name=[NameAndValue(value_name="node-name", value=el.uid)],
                owned_node_edge_point=neps,
            )
            self._nodes[el.uid] = node

    def _make_nep(
        self, nep_uuid: str, owner_uid: str, peer_uid: str | None
    ) -> NodeEdgePoint:
        name_parts = [NameAndValue(value_name="nep-name", value=f"{owner_uid}:nep")]
        if peer_uid:
            name_parts.append(
                NameAndValue(value_name="direction", value=f"toward:{peer_uid}")
            )
        return NodeEdgePoint(uuid=nep_uuid, name=name_parts)

    # ------------------------------------------------------------------
    # Link building
    # ------------------------------------------------------------------

    # Speed of light in fiber: c / n_refractive (n ~ 1.47 for silica) [m/s]
    _C_LIGHT_FIBER_M_PER_S: float = 299_792_458 / 1.47

    def _span_fiber_length_m(self, intermediates: list[str]) -> float:
        """Total fiber length [m] along a span (Fiber elements only)."""
        total = 0.0
        for uid in intermediates:
            el = self._gnpy.elements_by_uid.get(uid)
            if el is None or el.type != "Fiber":
                continue
            try:
                total += float(el.params.get("length", 0))
            except (TypeError, ValueError):
                pass
        return total

    def _build_links(self) -> None:
        """Create TAPI Links for spans between terminal nodes."""
        spans = self._find_spans()

        for src_uid, dst_uid, intermediates in spans:
            src_nep = self._nep_for_direction.get((src_uid, dst_uid))
            dst_nep = self._nep_for_direction.get((dst_uid, src_uid))
            if src_nep is None or dst_nep is None:
                continue

            link_uuid = str(
                uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"link:{src_uid}->{dst_uid}")
            )

            span_info = ",".join(intermediates) if intermediates else "direct"
            fiber_m = self._span_fiber_length_m(intermediates)
            latency_ns = int(
                fiber_m * 1e9 / self._C_LIGHT_FIBER_M_PER_S
            ) if fiber_m > 0 else 0
            latency_char = (
                [LatencyCharacteristic(total_size=latency_ns)]
                if latency_ns > 0
                else []
            )

            link = Link(
                uuid=link_uuid,
                name=[
                    NameAndValue(value_name="link-name", value=f"{src_uid} -> {dst_uid}"),
                    NameAndValue(value_name="span-elements", value=span_info),
                ],
                node_edge_point=[
                    NodeEdgePointRef(
                        topology_uuid=self._topology_uuid,
                        node_uuid=self._node_uuid_for[src_uid],
                        node_edge_point_uuid=src_nep,
                    ),
                    NodeEdgePointRef(
                        topology_uuid=self._topology_uuid,
                        node_uuid=self._node_uuid_for[dst_uid],
                        node_edge_point_uuid=dst_nep,
                    ),
                ],
                direction=ForwardingDirection.UNIDIRECTIONAL,
                latency_characteristic=latency_char,
            )
            self._links.append(link)

    # ------------------------------------------------------------------
    # Graph traversal helpers
    # ------------------------------------------------------------------

    def _find_terminal_adjacency(self) -> dict[str, list[str]]:
        """For each terminal, find which other terminals it connects to
        (traversing through intermediate Fiber/Edfa elements).

        Returns:
            Mapping from terminal uid to list of peer terminal uids.
        """
        adjacency: dict[str, list[str]] = {}
        for el in self._gnpy.elements:
            if el.type not in TERMINAL_TYPES:
                continue
            peers = []
            for neighbor in self._graph.neighbors(el.uid):
                peer = self._walk_to_terminal(neighbor)
                if peer is not None:
                    peers.append(peer)
            # Also check predecessors for bidirectional adjacency
            for pred in self._graph.predecessors(el.uid):
                peer = self._walk_to_terminal_reverse(pred)
                if peer is not None and peer not in peers:
                    peers.append(peer)
            adjacency[el.uid] = peers
        return adjacency

    def _walk_to_terminal(self, uid: str) -> str | None:
        """Walk forward from uid through intermediate elements until
        reaching a terminal node. Returns the terminal's uid."""
        visited: set[str] = set()
        current = uid
        while current not in visited:
            visited.add(current)
            el = self._gnpy.elements_by_uid.get(current)
            if el is None:
                return None
            if el.type in TERMINAL_TYPES:
                return current
            neighbors = self._graph.neighbors(current)
            if not neighbors:
                return None
            current = neighbors[0]
        return None

    def _walk_to_terminal_reverse(self, uid: str) -> str | None:
        """Walk backward from uid through intermediate elements."""
        visited: set[str] = set()
        current = uid
        while current not in visited:
            visited.add(current)
            el = self._gnpy.elements_by_uid.get(current)
            if el is None:
                return None
            if el.type in TERMINAL_TYPES:
                return current
            preds = self._graph.predecessors(current)
            if not preds:
                return None
            current = preds[0]
        return None

    def _find_spans(self) -> list[tuple[str, str, list[str]]]:
        """Find all spans: (src_terminal, dst_terminal, [intermediate_uids]).

        A span starts at a terminal, goes through Fiber/Edfa elements,
        and ends at another terminal.
        """
        spans: list[tuple[str, str, list[str]]] = []
        seen: set[tuple[str, str]] = set()

        for el in self._gnpy.elements:
            if el.type not in TERMINAL_TYPES:
                continue
            for neighbor in self._graph.neighbors(el.uid):
                intermediates: list[str] = []
                current = neighbor
                visited: set[str] = set()
                while current not in visited:
                    visited.add(current)
                    cel = self._gnpy.elements_by_uid.get(current)
                    if cel is None:
                        break
                    if cel.type in TERMINAL_TYPES:
                        key = (el.uid, current)
                        if key not in seen:
                            spans.append((el.uid, current, intermediates))
                            seen.add(key)
                        break
                    intermediates.append(current)
                    nexts = self._graph.neighbors(current)
                    if not nexts:
                        break
                    current = nexts[0]

        return spans
