"""The bounded search through include constraints (waypoints).

Filtering every simple path for the includes is exponential when an include
is off the direct route; it froze the server on CORONET. The waypoint search
must give the same best routes as that brute force wherever the brute force
finishes, finish quickly where it does not, and say it stopped — never that
no route exists — when its bounds run out.
"""

from __future__ import annotations

import time
from itertools import islice
from pathlib import Path

import networkx as nx
import pytest

from twinlight.algorithms import routing
from twinlight.algorithms.routing import (
    RouteSearchExhausted,
    k_shortest_paths,
    visits_in_order,
    waypoint_paths,
)


def _weighted_graph(seed: int) -> nx.DiGraph:
    """A connected random mesh, both directions, with integer weights."""
    undirected = nx.connected_watts_strogatz_graph(14, 4, 0.3, seed=seed)
    graph = nx.DiGraph()
    for a, b in undirected.edges:
        weight = (a * 7 + b * 13 + seed) % 9 + 1
        graph.add_edge(a, b, weight=weight)
        graph.add_edge(b, a, weight=weight)
    return graph


def _brute_force(graph, waypoints, k):
    src, *include, dst = waypoints
    paths = [p for p in nx.all_simple_paths(graph, src, dst) if visits_in_order(include, p)]
    return sorted(nx.path_weight(graph, p, "weight") for p in paths)[:k]


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("waypoints", [(0, 7, 13), (0, 3, 9, 13), (5, 12, 1), (2, 11, 6, 4)])
def test_the_best_routes_match_brute_force(seed: int, waypoints: tuple) -> None:
    graph = _weighted_graph(seed)
    expected = _brute_force(graph, waypoints, 4)
    found = waypoint_paths(graph, waypoints, 4, weight="weight")
    assert [nx.path_weight(graph, p, "weight") for p in found] == expected
    for path in found:
        assert len(set(path)) == len(path)  # simple
        assert visits_in_order(waypoints, path)


def test_an_uncrossable_segment_is_no_route() -> None:
    graph = nx.DiGraph([(0, 1), (1, 2)])
    assert waypoint_paths(graph, [0, 1, 2], 1) == [[0, 1, 2]]
    assert waypoint_paths(graph, [0, 2, 1], 1) == []
    assert k_shortest_paths(graph, 0, 2, 1, include=[3]) == []


def test_exhausted_bounds_are_not_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _weighted_graph(0)
    monkeypatch.setattr(routing, "SEARCH_STEPS", 0)
    with pytest.raises(RouteSearchExhausted, match="one may exist"):
        waypoint_paths(graph, [0, 7, 13], 1, weight="weight")


def test_without_includes_it_is_yen() -> None:
    graph = _weighted_graph(3)
    expected = list(islice(nx.shortest_simple_paths(graph, 0, 13, weight="weight"), 5))
    assert k_shortest_paths(graph, 0, 13, 5, weight="weight") == expected


_EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "gnpy-data"


@pytest.mark.skipif(
    not (_EXAMPLES / "CORONET_CONUS_Topology.json").is_file(),
    reason="GNPy example data not provisioned (run twinlight-fetch-examples)",
)
@pytest.mark.parametrize("backend", ["gnpy", "egn"])
def test_a_far_include_on_coronet_is_found_quickly(backend: str) -> None:
    """Seattle → Portland through Miami: GNPy's filter ran past 45 s and the
    old 2,000-path cap reported no route after 19 s."""
    from twinlight.algorithms.path_constraints import parse_constraints
    from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
    from twinlight.state.context import TapiContext

    ctx = TapiContext(TwinConfig(
        gnpy=GnpyConfig(topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
                        equipment=_EXAMPLES / "eqpt_config.json", no_insert_edfas=True),
        physics=PhysicsConfig(backend=backend),
    ))
    topology, node = ctx._gnpy_uid_to_node_ref["roadm Miami"]
    constraints = parse_constraints(None, [{
        "local-id": "1", "include-node": [{"topology-uuid": topology, "node-uuid": node}],
    }])
    started = time.monotonic()
    routes = ctx.candidate_paths("trx Seattle", "trx Portland", constraints, k=3)
    assert time.monotonic() - started < 5.0
    assert routes, "a route through Miami exists"
    for route in routes:
        assert route[0] == "trx Seattle" and route[-1] == "trx Portland"
        assert "roadm Miami" in route and len(set(route)) == len(route)
    lengths = [ctx.path_fiber_km(r) for r in routes]
    assert lengths == sorted(lengths)
