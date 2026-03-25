"""Tests for the gnpy topology loader."""

from pathlib import Path

from tapi_twin.loader.gnpy_topology import load_gnpy_topology


def test_load_elements(edfa_topology_path: Path) -> None:
    topo = load_gnpy_topology(edfa_topology_path)
    assert len(topo.elements) == 8
    types = {e.type for e in topo.elements}
    assert types == {"Transceiver", "Roadm", "Fiber", "Edfa"}


def test_load_connections(edfa_topology_path: Path) -> None:
    topo = load_gnpy_topology(edfa_topology_path)
    assert len(topo.connections) == 9


def test_element_uids(edfa_topology_path: Path) -> None:
    topo = load_gnpy_topology(edfa_topology_path)
    uids = [e.uid for e in topo.elements]
    assert "trx Brest_1" in uids
    assert "roadm Brest" in uids
    assert "trx Morlaix_1" in uids


def test_transceiver_count(edfa_topology_path: Path) -> None:
    topo = load_gnpy_topology(edfa_topology_path)
    transceivers = [e for e in topo.elements if e.type == "Transceiver"]
    assert len(transceivers) == 2


def test_connection_from_to(edfa_topology_path: Path) -> None:
    topo = load_gnpy_topology(edfa_topology_path)
    first = topo.connections[0]
    assert first.from_node == "trx Brest_1"
    assert first.to_node == "roadm Brest"
