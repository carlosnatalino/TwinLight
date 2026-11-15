"""Ranked simple-path search over a NetworkX DiGraph of network elements.

The search works on any node type, so the same code ranks paths over the GNPy
network (nodes are ``gnpy.core.elements`` objects) and over the twin's parsed
topology graph (nodes are element UIDs).
"""

from __future__ import annotations

import itertools
from collections.abc import Hashable, Sequence
from itertools import islice

import networkx as nx

# Bounds of the search through include constraints (waypoints), so a request
# can never stall the server. A route through the waypoints is built segment by
# segment (:func:`waypoint_paths`): at most SEGMENT_CANDIDATES ranked paths are
# tried per segment, at most SEARCH_STEPS segment paths in all, for at most
# SEARCH_SECONDS. Within those bounds the result is exact (the best routes);
# beyond them the search reports that it stopped, never that no route exists.
SEGMENT_CANDIDATES = 25
SEARCH_STEPS = 5000
SEARCH_SECONDS = 2.0


class RouteSearchExhausted(RuntimeError):
    """The waypoint search hit its bounds before finding any route. A route
    may still exist."""


def visits_in_order(include: Sequence[Hashable], path: Sequence[Hashable]) -> bool:
    """True if every element of ``include`` lies on ``path``, in that order.

    Same semantics as ``gnpy.topology.request.ispart``, which is how GNPy's
    ``compute_constrained_path`` checks its ``nodes_list``.
    """
    nodes = list(path)
    position = 0
    for node in include:
        try:
            position = nodes.index(node, position)
        except ValueError:
            return False
    return True


def k_shortest_paths(
    graph: nx.DiGraph,
    src_uid: Hashable,
    dst_uid: Hashable,
    k: int,
    *,
    include: Sequence[Hashable] = (),
    weight: str | None = None,
) -> list[list]:
    """Return up to ``k`` simple paths from ``src_uid`` to ``dst_uid``, best first.

    Ranked by the edge attribute ``weight`` (hop count when ``None``). Without
    ``include``, Yen's algorithm via ``networkx.shortest_simple_paths``; with
    it, :func:`waypoint_paths`, so only paths visiting ``include`` in order
    are produced.

    Returns an empty list when no path exists or a node is missing.

    Raises:
        RouteSearchExhausted: See :func:`waypoint_paths`.
    """
    if k < 1:
        return []
    if include:
        return waypoint_paths(graph, [src_uid, *include, dst_uid], k, weight=weight)
    try:
        return list(islice(nx.shortest_simple_paths(graph, src_uid, dst_uid, weight=weight), k))
    except (nx.NetworkXNoPath, nx.NodeNotFound, nx.NetworkXError):
        return []


def waypoint_paths(
    graph: nx.DiGraph,
    waypoints: Sequence[Hashable],
    k: int,
    *,
    weight: str | None = None,
) -> list[list]:
    """The ``k`` best simple paths visiting ``waypoints`` in order.

    Filtering every simple path for the waypoints (what GNPy's
    ``compute_constrained_path`` does) enumerates paths exponentially when a
    waypoint is off the direct route. Instead the path is built one segment
    (waypoint to waypoint) at a time: each segment's candidates are the ranked
    simple paths of the graph without the other waypoints and without the
    nodes earlier segments used, so every combination is a simple path that
    visits the waypoints in order. A depth-first search over the segments,
    cut whenever the cost so far plus a lower bound on the rest (each
    remaining segment's shortest path ignoring the others) cannot beat the
    k-th best found, returns the k best routes — exactly, unless a segment
    needs more than :data:`SEGMENT_CANDIDATES` alternatives.

    Returns an empty list when no route exists (a segment unreachable even
    ignoring the others, or the search space exhausted).

    Raises:
        RouteSearchExhausted: :data:`SEARCH_STEPS` or :data:`SEARCH_SECONDS`
            ran out before any route was found.
    """
    import heapq
    import time

    waypoints = list(waypoints)
    if any(node not in graph for node in waypoints) or len(set(waypoints)) != len(waypoints):
        return []
    segments = list(itertools.pairwise(waypoints))

    def without(nodes: set) -> nx.DiGraph:
        return nx.restricted_view(graph, nodes, [])

    # Lower bound of each segment: its shortest path ignoring the others.
    lower: list[float] = []
    for a, b in segments:
        others = set(waypoints) - {a, b}
        try:
            lower.append(nx.shortest_path_length(without(others), a, b, weight=weight))
        except nx.NetworkXNoPath:
            return []  # this segment cannot be crossed at all
    rest = [sum(lower[i:]) for i in range(len(segments) + 1)]

    best: list[tuple[float, int, list]] = []  # max-heap by -cost, of size <= k
    deadline = time.monotonic() + SEARCH_SECONDS
    steps = 0
    tie = itertools.count()

    def bound() -> float:
        return -best[0][0] if len(best) == k else float("inf")

    def search(i: int, used: set, prefix: list, cost: float) -> None:
        nonlocal steps
        if i == len(segments):
            heapq.heappush(best, (-cost, next(tie), prefix))
            if len(best) > k:
                heapq.heappop(best)
            return
        a, b = segments[i]
        hidden = (used | set(waypoints)) - {a, b}
        try:
            ranked = nx.shortest_simple_paths(without(hidden), a, b, weight=weight)
            for segment in islice(ranked, SEGMENT_CANDIDATES):
                steps += 1
                if steps > SEARCH_STEPS or time.monotonic() > deadline:
                    raise RouteSearchExhausted
                length = nx.path_weight(graph, segment, weight) if weight else len(segment) - 1
                if cost + length + rest[i + 1] >= bound():
                    return  # segments come cheapest first: none later can do better
                search(i + 1, used | set(segment), prefix + segment[1:], cost + length)
        except nx.NetworkXNoPath:
            return

    try:
        search(0, set(), [waypoints[0]], 0.0)
    except RouteSearchExhausted:
        if not best:
            raise RouteSearchExhausted(
                f"the route search stopped after {steps} segment paths "
                f"({SEARCH_STEPS} paths or {SEARCH_SECONDS:g} s at most) "
                "without finding a route through the includes; one may exist"
            ) from None
    return [path for _cost, _tie, path in sorted(best, key=lambda e: (-e[0], e[1]))]
