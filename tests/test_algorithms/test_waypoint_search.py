"""The search through include constraints (waypoints).

Filtering every simple path for the includes is exponential when an include
is off the direct route; it froze the server on CORONET. Building the route
from each segment's few best paths was fast but wrong: segments that must
detour to stay disjoint fell outside the few, so it missed routes and
returned longer ones. The search must give exactly the best routes — checked
against brute force on small graphs and against an independent min-cost-flow
optimum on large ones — and, when its budget runs out, say so instead of
answering.
"""

from __future__ import annotations

import random
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


def _with_chains(graph: nx.DiGraph) -> nx.DiGraph:
    """Each directed edge becomes a chain of two intermediate nodes (a
    link's amplifier and fiber, say), its weight split along it."""
    chained = nx.DiGraph()
    for a, b, data in graph.edges(data=True):
        amp, fiber = ("amp", a, b), ("fiber", a, b)
        chained.add_edge(a, amp, weight=0)
        chained.add_edge(amp, fiber, weight=data["weight"] / 2)
        chained.add_edge(fiber, b, weight=data["weight"] / 2)
    return chained


def _one_way(seed: int) -> nx.DiGraph:
    """A random directed graph: links one way only, different costs."""
    rng = random.Random(seed)
    graph = nx.DiGraph()
    for a, b in nx.gnp_random_graph(12, 0.35, seed=seed, directed=True).edges:
        graph.add_edge(a, b, weight=rng.randint(1, 9))
    return graph


def _brute_force(graph, waypoints, k):
    src, *include, dst = waypoints
    paths = [p for p in nx.all_simple_paths(graph, src, dst) if visits_in_order(include, p)]
    return sorted(nx.path_weight(graph, p, "weight") for p in paths)[:k]


def _check(graph, waypoints, k) -> None:
    expected = _brute_force(graph, waypoints, k)
    found = waypoint_paths(graph, waypoints, k, weight="weight")
    assert [nx.path_weight(graph, p, "weight") for p in found] == pytest.approx(expected)
    assert len({tuple(p) for p in found}) == len(found)
    for path in found:
        assert len(set(path)) == len(path)  # simple
        assert path[0] == waypoints[0] and path[-1] == waypoints[-1]
        assert visits_in_order(waypoints, path)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("waypoints", [(0, 7, 13), (0, 3, 9, 13), (5, 12, 1), (2, 11, 6, 4)])
def test_the_best_routes_match_brute_force(seed: int, waypoints: tuple) -> None:
    _check(_weighted_graph(seed), waypoints, 4)


@pytest.mark.parametrize("seed", range(4))
def test_chains_between_junctions(seed: int) -> None:
    """Element graphs: amplifiers and fibers between the ROADMs."""
    _check(_with_chains(_weighted_graph(seed)), (0, 7, 13), 3)
    _check(_with_chains(_weighted_graph(seed)), (2, 11, 6, 4), 3)


@pytest.mark.parametrize("seed", range(12))
def test_one_way_links(seed: int) -> None:
    """Exact even where the bound is loose: links one way only."""
    graph = _one_way(seed)
    for waypoints in [(0, 5, 11), (1, 3, 8, 10), (4, 9, 2)]:
        if all(n in graph for n in waypoints):
            _check(graph, waypoints, 3)


def _disjoint_pair_optimum(graph: nx.DiGraph, src, via, dst) -> float | None:
    """The shortest simple src → via → dst route on a graph whose links cost
    the same both ways: the cheapest two node-disjoint paths from ``via`` to
    ``src`` and to ``dst``, by networkx's network simplex on the node-split
    graph. Independent of the search's own bound."""
    flow_graph = nx.DiGraph()
    for a, b, data in graph.edges(data=True):
        if a in (src, dst) or b == via:
            continue
        # Integer costs: network simplex is unreliable with floats.
        flow_graph.add_edge(("out", a), ("in", b), weight=round(data["weight"]), capacity=1)
    for node in graph:
        if node not in (src, dst, via):
            flow_graph.add_edge(("in", node), ("out", node), weight=0, capacity=1)
    flow_graph.add_edge("S", ("out", via), weight=0, capacity=2)
    flow_graph.add_edge(("in", src), "T", weight=0, capacity=1)
    flow_graph.add_edge(("in", dst), "T", weight=0, capacity=1)
    flow = nx.max_flow_min_cost(flow_graph, "S", "T")
    if sum(flow["S"].values()) < 2:
        return None
    return nx.cost_of_flow(flow_graph, flow)


@pytest.mark.parametrize("seed", range(4))
def test_the_best_route_is_the_optimum_on_large_meshes(seed: int) -> None:
    """60-node meshes, where segments must detour to stay disjoint: a fixed
    number of candidates per segment missed routes and returned longer ones
    here, and brute force does not finish."""
    rng = random.Random(seed)
    undirected = nx.random_geometric_graph(60, 0.22, seed=seed)
    graph = nx.DiGraph()
    for a, b in undirected.edges:
        weight = rng.randint(1, 50)
        graph.add_edge(a, b, weight=weight)
        graph.add_edge(b, a, weight=weight)
    for _ in range(15):
        src, via, dst = rng.sample(sorted(graph), 3)
        optimum = _disjoint_pair_optimum(graph, src, via, dst)
        found = waypoint_paths(graph, [src, via, dst], 3, weight="weight")
        if optimum is None:
            assert found == []
            continue
        assert nx.path_weight(graph, found[0], "weight") == optimum
        lengths = [nx.path_weight(graph, p, "weight") for p in found]
        assert lengths == sorted(lengths)


def test_no_route_is_no_route() -> None:
    graph = nx.DiGraph([(0, 1), (1, 2)])
    assert waypoint_paths(graph, [0, 1, 2], 1) == [[0, 1, 2]]
    assert waypoint_paths(graph, [0, 2, 1], 1) == []
    assert k_shortest_paths(graph, 0, 2, 1, include=[3]) == []
    # Each segment can be crossed, but not both without sharing node 1.
    graph = nx.DiGraph([(0, 1), (1, 2), (2, 1), (1, 3)])
    assert waypoint_paths(graph, [0, 2, 3], 1) == []


def test_an_exhausted_budget_is_not_an_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _weighted_graph(0)
    monkeypatch.setattr(routing, "SEARCH_STEPS", 3)
    with pytest.raises(RouteSearchExhausted, match="a route may exist"):
        waypoint_paths(graph, [0, 7, 13], 1, weight="weight")


def test_without_includes_it_is_yen() -> None:
    graph = _weighted_graph(3)
    expected = list(islice(nx.shortest_simple_paths(graph, 0, 13, weight="weight"), 5))
    assert k_shortest_paths(graph, 0, 13, 5, weight="weight") == expected


_EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "gnpy-data"
_CORONET = pytest.mark.skipif(
    not (_EXAMPLES / "CORONET_CONUS_Topology.json").is_file(),
    reason="GNPy example data not provisioned (run twinlight-fetch-examples)",
)


@pytest.fixture(scope="module", params=["gnpy", "egn"])
def coronet(request: pytest.FixtureRequest):
    from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
    from twinlight.state.context import TapiContext

    return TapiContext(TwinConfig(
        gnpy=GnpyConfig(topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
                        equipment=_EXAMPLES / "eqpt_config.json", no_insert_edfas=True),
        physics=PhysicsConfig(backend=request.param),
    ))


def _through(ctx, *roadms: str):
    from twinlight.algorithms.path_constraints import parse_constraints

    refs = []
    for roadm in roadms:
        topology, node = ctx._gnpy_uid_to_node_ref[roadm]
        refs.append({"topology-uuid": topology, "node-uuid": node})
    return parse_constraints(None, [{"local-id": "1", "include-node": refs}])


@_CORONET
def test_a_far_include_on_coronet_is_found_quickly(coronet) -> None:
    """Seattle → Portland through Miami: GNPy's filter ran past 45 s and the
    old 2,000-path cap reported no route after 19 s."""
    started = time.monotonic()
    routes = coronet.candidate_paths(
        "trx Seattle", "trx Portland", _through(coronet, "roadm Miami"), k=3)
    assert time.monotonic() - started < 5.0
    assert routes, "a route through Miami exists"
    for route in routes:
        assert route[0] == "trx Seattle" and route[-1] == "trx Portland"
        assert "roadm Miami" in route and len(set(route)) == len(route)
    lengths = [coronet.path_fiber_km(r) for r in routes]
    assert lengths == sorted(lengths)


@_CORONET
@pytest.mark.parametrize(("src", "via", "dst", "km"), [
    # A fixed number of candidates per segment found no route here...
    ("Providence", "Spokane", "Boston", 11_462),
    # ... and an 8,917 km one here.
    ("Newark", "Bismarck", "Long_Island", 8_416),
])
def test_coronet_routes_are_the_shortest(coronet, src, via, dst, km) -> None:
    routes = coronet.candidate_paths(
        f"trx {src}", f"trx {dst}", _through(coronet, f"roadm {via}"), k=1)
    assert routes and f"roadm {via}" in routes[0]
    graph, key_of = coronet._route_graph()
    keys = [key_of(uid) for uid in routes[0]]
    # The reference rounds each edge to the metre.
    assert nx.path_weight(graph, keys, "weight") == pytest.approx(
        _disjoint_pair_optimum(graph, key_of(f"trx {src}"), key_of(f"roadm {via}"),
                               key_of(f"trx {dst}")), abs=len(keys))
    assert coronet.path_fiber_km(routes[0]) == pytest.approx(km, abs=1)
