"""Tests for the TAPI builder."""

from pathlib import Path

from tapi_twin.loader.gnpy_topology import load_gnpy_topology
from tapi_twin.loader.tapi_builder import TapiBuilder
from tapi_twin.state.topology_state import TopologyGraph


def _build(edfa_topology_path: Path):
    gnpy = load_gnpy_topology(edfa_topology_path)
    graph = TopologyGraph(gnpy)
    builder = TapiBuilder(gnpy, graph)
    return builder.build()


def test_builds_topology_context(edfa_topology_path: Path) -> None:
    ctx, sips = _build(edfa_topology_path)
    assert len(ctx.topology) == 1


def test_topology_has_nodes(edfa_topology_path: Path) -> None:
    ctx, _ = _build(edfa_topology_path)
    topo = ctx.topology[0]
    # 2 Transceivers + 2 Roadms = 4 terminal nodes
    assert len(topo.node) == 4


def test_transceivers_have_sips(edfa_topology_path: Path) -> None:
    _, sips = _build(edfa_topology_path)
    assert len(sips) == 2
    names = {s.name[0].value for s in sips}
    assert "trx Brest_1" in names
    assert "trx Morlaix_1" in names


def test_links_exist(edfa_topology_path: Path) -> None:
    ctx, _ = _build(edfa_topology_path)
    topo = ctx.topology[0]
    # Spans: trx Brest_1 -> roadm Brest (direct),
    #        roadm Brest -> roadm Morlaix (fiber+edfa),
    #        trx Morlaix_1 -> roadm Morlaix (direct),
    #        roadm Morlaix -> roadm Brest (fiber+edfa)
    assert len(topo.link) >= 2  # At least the fiber spans


def test_link_has_two_nep_refs(edfa_topology_path: Path) -> None:
    ctx, _ = _build(edfa_topology_path)
    topo = ctx.topology[0]
    for link in topo.link:
        assert len(link.node_edge_point) == 2


def test_node_uuids_are_deterministic(edfa_topology_path: Path) -> None:
    ctx1, _ = _build(edfa_topology_path)
    ctx2, _ = _build(edfa_topology_path)
    uuids1 = sorted(n.uuid for n in ctx1.topology[0].node)
    uuids2 = sorted(n.uuid for n in ctx2.topology[0].node)
    assert uuids1 == uuids2
