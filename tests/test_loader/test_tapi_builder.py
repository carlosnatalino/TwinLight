"""Tests for the TAPI builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from twinlight.loader.gnpy_topology import (
    GnpyConnection,
    GnpyElement,
    GnpyTopology,
    load_gnpy_topology,
)
from twinlight.loader.tapi_builder import TapiBuilder
from twinlight.state.topology_state import TopologyGraph

# Speed of light in silica used by TapiBuilder (c / 1.47).
_C_FIBER = 299_792_458 / 1.47


def _build(edfa_topology_path: Path):
    gnpy = load_gnpy_topology(edfa_topology_path)
    graph = TopologyGraph(gnpy)
    builder = TapiBuilder(gnpy, graph)
    return builder.build()


def _two_site_topo(*, fiber_params: dict) -> GnpyTopology:
    """Minimal A→fiber→B span so a single T-API link carries the fiber length."""
    elements = [
        GnpyElement(uid="roadm A", type="Roadm"),
        GnpyElement(uid="fiber A->B", type="Fiber", params=dict(fiber_params)),
        GnpyElement(uid="roadm B", type="Roadm"),
    ]
    connections = [
        GnpyConnection(from_node="roadm A", to_node="fiber A->B"),
        GnpyConnection(from_node="fiber A->B", to_node="roadm B"),
    ]
    return GnpyTopology(
        network_name="latency-unit-test",
        elements=elements,
        connections=connections,
        elements_by_uid={el.uid: el for el in elements},
    )


def _link_delay_ns(fiber_params: dict) -> int:
    gnpy = _two_site_topo(fiber_params=fiber_params)
    ctx, _ = TapiBuilder(gnpy, TopologyGraph(gnpy)).build()
    links = ctx.topology[0].link
    assert len(links) == 1
    chars = links[0].latency_characteristic
    assert len(chars) == 1
    return chars[0].total_size


def test_builds_topology_context(edfa_topology_path: Path) -> None:
    ctx, _sips = _build(edfa_topology_path)
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


def test_propagation_delay_km_units() -> None:
    """100 km of fibre → int(1e5 · 1e9 / (c/1.47)) = 490_339 ns."""
    expected = int(100_000.0 * 1e9 / _C_FIBER)
    assert expected == 490_339
    assert _link_delay_ns({"length": 100.0, "length_units": "km"}) == expected


def test_propagation_delay_metre_units() -> None:
    """Explicit metres must not be scaled by 1000 again."""
    expected = int(100_000.0 * 1e9 / _C_FIBER)
    assert _link_delay_ns({"length": 100_000.0, "length_units": "m"}) == expected


def test_propagation_delay_missing_units_defaults_to_km() -> None:
    """GNPy's default unit is km when length_units is omitted."""
    expected = int(80_000.0 * 1e9 / _C_FIBER)
    assert _link_delay_ns({"length": 80.0}) == expected


def test_propagation_delay_unknown_unit_omitted(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown unit must not be guessed as metres or km."""
    gnpy = _two_site_topo(fiber_params={"length": 100.0, "length_units": "furlongs"})
    with caplog.at_level("WARNING", logger="twinlight.loader.tapi_builder"):
        ctx, _ = TapiBuilder(gnpy, TopologyGraph(gnpy)).build()
    assert ctx.topology[0].link[0].latency_characteristic == []
    assert any("unknown length_units" in r.message for r in caplog.records)


def test_edfa_fixture_propagation_delay_is_80_km_scale(
    edfa_topology_path: Path,
) -> None:
    """Brest↔Morlaix fibres are 80 km; delay must be ~0.39 ms, not ~0.39 µs."""
    ctx, _ = _build(edfa_topology_path)
    expected = int(80_000.0 * 1e9 / _C_FIBER)
    fiber_links = [
        link
        for link in ctx.topology[0].link
        if link.latency_characteristic
    ]
    assert fiber_links
    for link in fiber_links:
        assert link.latency_characteristic[0].total_size == expected
