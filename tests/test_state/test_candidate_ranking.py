"""Candidate ranking follows GNPy: Yen's algorithm on fiber length.

GNPy has no k-shortest function. Its compute_constrained_path runs
networkx.shortest_simple_paths(network, weight="weight") on the GNPy network,
whose edge weight is the length of the fiber the edge leaves. The twin builds
its k-list the same way, so candidate 1 must be GNPy's own route.
"""

from __future__ import annotations

from itertools import islice

import networkx as nx
import pytest

from tests.conftest import MeshRefs
from twinlight.algorithms.path_constraints import parse_constraints

MESH_ORDER = [["A", "B", "C"], ["A", "D", "C"]]  # 160 km, 200 km


def _gnpy_route(ctx, src: str, dst: str, include: list[str] = ()) -> list[str]:
    from gnpy.topology.request import PathRequest, compute_constrained_path

    req = PathRequest(
        request_id="test", source=src, destination=dst,
        nodes_list=[*include, dst], loose_list=["STRICT"] * (len(include) + 1),
    )
    return [el.uid for el in compute_constrained_path(ctx._gnpy_network, req)]


@pytest.mark.parametrize(("src", "dst"), [
    ("trx A", "trx C"), ("trx C", "trx A"), ("trx A", "trx B"), ("trx B", "trx C"),
])
def test_candidate_1_is_compute_constrained_path_route(mesh_gnpy_context, src, dst) -> None:
    ctx = mesh_gnpy_context
    assert ctx.candidate_paths(src, dst, k=5)[0] == _gnpy_route(ctx, src, dst)


def test_candidate_1_with_includes_is_gnpys_route(mesh_gnpy_context) -> None:
    ctx = mesh_gnpy_context
    refs = MeshRefs(ctx)
    parsed = parse_constraints(None, [{"include-node": [refs.node("roadm D")]}])
    assert ctx.candidate_paths("trx A", "trx C", parsed, k=3)[0] == _gnpy_route(
        ctx, "trx A", "trx C", ["roadm D"],
    )


def test_k_list_is_gnpy_weighted_yen(mesh_gnpy_context) -> None:
    """The whole list, not only its head, is GNPy's ranking."""
    ctx = mesh_gnpy_context
    net, uid_map = ctx._gnpy_network, ctx._gnpy_uid_map
    expected = [
        [el.uid for el in path]
        for path in islice(nx.shortest_simple_paths(
            net, uid_map["trx A"], uid_map["trx C"], weight="weight"), 5)
    ]
    assert ctx.candidate_paths("trx A", "trx C", k=5) == expected


def test_ranked_by_fiber_length_on_both_backends(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    routes = mesh_context.candidate_paths("trx A", "trx C", k=5)
    assert [refs.roadms(r) for r in routes[:2]] == MESH_ORDER
    lengths = [mesh_context.path_fiber_km(r) for r in routes]
    assert lengths == sorted(lengths)
    assert lengths[:2] == pytest.approx([160.0, 200.0])


def test_fallback_graph_is_weighted_like_gnpy(mesh_context) -> None:
    """Edges leaving a fiber weigh its length in metres; others 0.01."""
    graph = mesh_context.topo_graph.graph
    assert graph.edges["fiber (A → B)", "roadm B"]["weight"] == pytest.approx(80_000.0)
    assert graph.edges["roadm A", "fiber (A → B)"]["weight"] == pytest.approx(0.01)


def test_fallback_is_used_only_without_a_gnpy_network(mesh_context) -> None:
    graph, _ = mesh_context._route_graph()
    if mesh_context._backend.name == "gnpy":
        assert graph is mesh_context._gnpy_network
    else:
        assert graph is mesh_context.topo_graph.graph
