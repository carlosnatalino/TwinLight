"""Ranked simple-path search over a NetworkX DiGraph of network elements.

The search works on any node type, so the same code ranks paths over the GNPy
network (nodes are ``gnpy.core.elements`` objects) and over the twin's parsed
topology graph (nodes are element UIDs).
"""

from __future__ import annotations

import heapq
import itertools
import math
import time
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from itertools import islice

import networkx as nx

# Budget of the search through include constraints (waypoints), so a request
# can never stall the server. The search is exact; when the budget runs out
# before it has proven its answer, it says so (RouteSearchExhausted) instead
# of returning routes that may not be the best, or none. The step count
# (lower-bound evaluations; ~1.6 s on CORONET on a laptop) is what normally
# binds, so whether a request succeeds does not depend on the host's speed or
# load; the time limit is a backstop for larger networks and slower hosts.
SEARCH_STEPS = 3_000
SEARCH_SECONDS = 3.0


class RouteSearchExhausted(RuntimeError):
    """The waypoint search ran out of budget before proving its answer: a
    route may exist, or a shorter one than any found."""


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


# -- Waypoint search ----------------------------------------------------------


@dataclass(frozen=True)
class _Arc:
    """A maximal chain of the element graph between two junctions: the
    amplifiers and fibers of one direction of a link, say."""

    head: Hashable
    cost: float
    interior: tuple[Hashable, ...]


@dataclass
class _Junctions:
    """The element graph with its chains contracted.

    A junction is a node a route can branch at (in- or out-degree other than
    one: ROADMs, the end transceivers) or must stop at (a waypoint). Every
    other node has one way in and one way out, so it lies on exactly one
    chain, and simple paths of the element graph are exactly the simple
    paths of the junctions joined by chains. That makes the graph the search
    works on a few hundred nodes instead of thousands.
    """

    arcs: dict[Hashable, list[_Arc]] = field(default_factory=dict)

    @classmethod
    def of(cls, graph: nx.DiGraph, keep: set, weight: str | None) -> _Junctions:
        def cost(a: Hashable, b: Hashable) -> float:
            return float(graph[a][b].get(weight, 1)) if weight else 1.0

        junctions = {
            n for n in graph
            if n in keep or graph.in_degree(n) != 1 or graph.out_degree(n) != 1
        }
        result = cls({u: [] for u in junctions})
        for u in junctions:
            for first in graph.successors(u):
                interior: list = []
                total, node = cost(u, first), first
                # Chain nodes have a single predecessor, so the walk cannot
                # cycle before it reaches a junction.
                while node not in junctions:
                    interior.append(node)
                    (after,) = graph.successors(node)
                    total += cost(node, after)
                    node = after
                if node != u:
                    result.arcs[u].append(_Arc(node, total, tuple(interior)))
        return result


class _Budget:
    def __init__(self) -> None:
        self.steps = 0
        self.deadline = time.monotonic() + SEARCH_SECONDS

    def spend(self) -> None:
        self.steps += 1
        if self.steps > SEARCH_STEPS or time.monotonic() > self.deadline:
            raise RouteSearchExhausted(
                f"the route search through the includes stopped after "
                f"{self.steps - 1} steps ({SEARCH_STEPS} steps or "
                f"{SEARCH_SECONDS:g} s at most) before proving the best "
                "routes; a route may exist"
            )


def _distance(junctions: _Junctions, src: Hashable, dst: Hashable, banned: set) -> float:
    """Shortest directed distance from ``src`` to ``dst`` avoiding ``banned``."""
    dist = {src: 0.0}
    queue = [(0.0, 0, src)]
    tie = itertools.count(1)
    while queue:
        d, _, u = heapq.heappop(queue)
        if u == dst:
            return d
        if d > dist[u]:
            continue
        for arc in junctions.arcs[u]:
            if arc.head in banned:
                continue
            nd = d + arc.cost
            if nd < dist.get(arc.head, math.inf):
                dist[arc.head] = nd
                heapq.heappush(queue, (nd, next(tie), arc.head))
    return math.inf


def _flow_bound(junctions: _Junctions, terminals: Sequence[Hashable], banned: set) -> float:
    """A lower bound on the shortest simple path through ``terminals`` in
    order, avoiding ``banned``.

    The route is one path per pair of consecutive terminals, the paths
    sharing no node. Relaxed to a single commodity, with each link usable in
    either direction at the cheaper of its two costs: a minimum-cost flow in
    which alternate terminals supply and absorb the paths' units and every
    other node carries at most one. Any route is such a flow of the same
    cost, so the flow's cost bounds the route's. For one waypoint between the
    ends the relaxation is exact on a network whose links cost the same both
    ways (it is then Suurballe's disjoint pair through the waypoint).

    Solved by successive shortest paths (Dijkstra with potentials) on the
    node-split network: one augmentation per path, so two to a few.
    """
    # Segment i joins terminals i and i+1; orient it from the even one, so
    # interior terminals alternately supply and absorb two units.
    supply: dict[Hashable, int] = {}
    for i, (a, b) in enumerate(itertools.pairwise(terminals)):
        tail, head = (a, b) if i % 2 == 0 else (b, a)
        supply[tail] = supply.get(tail, 0) + 1
        supply[head] = supply.get(head, 0) - 1

    # Node ids: source 0, sink 1, then an in/out pair per junction (one id
    # for a terminal, which nothing passes through).
    index: dict[Hashable, tuple[int, int]] = {}
    count = 2
    for u in junctions.arcs:
        if u in banned:
            continue
        if u in supply:
            index[u] = (count, count)
            count += 1
        else:
            index[u] = (count, count + 1)
            count += 2
    to: list[int] = []
    cap: list[int] = []
    cost: list[float] = []
    out: list[list[int]] = [[] for _ in range(count)]

    def add(a: int, b: int, c: float, capacity: int = 1) -> None:
        out[a].append(len(to))
        to.append(b)
        cap.append(capacity)
        cost.append(c)
        out[b].append(len(to))
        to.append(a)
        cap.append(0)
        cost.append(-c)

    for u, (u_in, u_out) in index.items():
        if u not in supply:
            add(u_in, u_out, 0.0)
        elif supply[u] > 0:
            add(0, u_out, 0.0, supply[u])
        else:
            add(u_in, 1, 0.0, -supply[u])
    for u, arcs in junctions.arcs.items():
        if u not in index:
            continue
        for arc in arcs:
            if arc.head not in index:
                continue
            for a, b in ((u, arc.head), (arc.head, u)):
                # A source terminal takes nothing in, a sink sends nothing on.
                if supply.get(a, 0) < 0 or supply.get(b, 0) > 0:
                    continue
                add(index[a][1], index[b][0], arc.cost)

    units = len(terminals) - 1
    potential = [0.0] * count
    total = 0.0
    for _ in range(units):
        dist = [math.inf] * count
        via = [-1] * count
        dist[0] = 0.0
        queue = [(0.0, 0)]
        while queue:
            d, u = heapq.heappop(queue)
            if d > dist[u]:
                continue
            for e in out[u]:
                if cap[e] <= 0:
                    continue
                v = to[e]
                # Reduced costs are non-negative up to rounding.
                nd = d + max(0.0, cost[e] + potential[u] - potential[v])
                if nd < dist[v]:
                    dist[v] = nd
                    via[v] = e
                    heapq.heappush(queue, (nd, v))
        if math.isinf(dist[1]):
            return math.inf
        for v in range(count):
            if not math.isinf(dist[v]):
                potential[v] += dist[v]
        v = 1
        while v != 0:
            e = via[v]
            cap[e] -= 1
            cap[e ^ 1] += 1
            total += cost[e]
            v = to[e ^ 1]
    return total


def _rest_bound(junctions: _Junctions, terminals: Sequence[Hashable], banned: set) -> float:
    """A lower bound on the shortest simple path through ``terminals`` in
    order, avoiding ``banned``; exact for two terminals.

    Two bounds, the larger kept. :func:`_flow_bound` over all the terminals
    sees how the segments compete for nodes, but may pair the terminals
    wrongly. The other cuts the terminals into consecutive chunks of two or
    three and adds each chunk's bound, computed avoiding every terminal
    outside it (the route passes through none of them): exact per chunk, a
    valid bound in sum since the chunks are disjoint parts of the route.
    Both chunkings (from the first terminal, from the second) are tried.
    """
    if len(terminals) == 2:
        return _distance(junctions, terminals[0], terminals[1], banned)
    best = _flow_bound(junctions, terminals, banned)
    if len(terminals) == 3 or math.isinf(best):
        return best
    for offset in (0, 1):
        cuts = sorted({0, *range(offset, len(terminals) - 1, 2), len(terminals) - 1})
        total = 0.0
        for a, b in itertools.pairwise(cuts):
            chunk = terminals[a:b + 1]
            others = banned | (set(terminals) - set(chunk))
            if len(chunk) == 2:
                total += _distance(junctions, chunk[0], chunk[1], others)
            else:
                total += _flow_bound(junctions, chunk, others)
            if math.isinf(total):
                return total
        best = max(best, total)
    return best


def waypoint_paths(
    graph: nx.DiGraph,
    waypoints: Sequence[Hashable],
    k: int,
    *,
    weight: str | None = None,
) -> list[list]:
    """The ``k`` best simple paths visiting ``waypoints`` in order (the first
    and last are the ends), best first.

    Filtering every simple path for the waypoints (what GNPy's
    ``compute_constrained_path`` does) enumerates paths exponentially when a
    waypoint is off the direct route, and building the route from each
    segment's few best paths misses routes whose segments must detour to
    stay disjoint. This is a branch and bound instead: a depth-first
    extension of the route one junction at a time (see :class:`_Junctions`),
    cheapest bound first, dropping a partial route once its cost plus a lower
    bound on the rest cannot beat the k-th best route found. The bound is
    the exact shortest distance once every waypoint is visited, and
    :func:`_flow_bound` before. Since a bound never exceeds the true cost,
    nothing better is ever dropped: the result is exact, whatever the bound's
    tightness, which only decides how fast it is.

    Returns an empty list when no route exists.

    Raises:
        RouteSearchExhausted: the budget (:data:`SEARCH_STEPS`,
            :data:`SEARCH_SECONDS`) ran out before the search finished.
    """
    waypoints = list(waypoints)
    if (
        k < 1
        or any(node not in graph for node in waypoints)
        or len(set(waypoints)) != len(waypoints)
    ):
        return []
    junctions = _Junctions.of(graph, set(waypoints), weight)
    src, dst = waypoints[0], waypoints[-1]
    budget = _Budget()

    best: list[tuple[float, int, list[_Arc]]] = []  # max-heap by -cost, size <= k
    found = itertools.count()

    def bound() -> float:
        return -best[0][0] if len(best) == k else math.inf

    def slack(value: float) -> float:
        # Float sums of the same lengths in another order differ by rounding:
        # never drop a branch over it.
        return 1e-9 * max(1.0, abs(value))

    def lower(node: Hashable, target: int, used: set) -> float:
        budget.spend()
        return _rest_bound(junctions, [node, *waypoints[target:]], used - {node})

    def extend(node: Hashable, target: int, used: set, arcs: list[_Arc], cost: float) -> None:
        if node == dst:
            heapq.heappush(best, (-cost, -next(found), arcs))
            if len(best) > k:
                heapq.heappop(best)
            return
        later = set(waypoints[target + 1:])
        children = []
        for order, arc in enumerate(junctions.arcs[node]):
            v = arc.head
            if v in used or v in later:
                continue
            reached = cost + arc.cost
            if reached - slack(reached) >= bound():
                continue
            nxt = target + 1 if v == waypoints[target] else target
            estimate = reached + (0.0 if v == dst else lower(v, nxt, used | {v}))
            if estimate - slack(estimate) < bound():
                children.append((estimate, order, arc, nxt, reached))
        children.sort(key=lambda c: (c[0], c[1]))
        for estimate, _order, arc, nxt, reached in children:
            if estimate - slack(estimate) >= bound():
                break  # the k-th best improved since: no later child can beat it
            extend(arc.head, nxt, used | {arc.head}, [*arcs, arc], reached)

    if math.isinf(lower(src, 1, {src})):
        return []
    extend(src, 1, {src}, [], 0.0)

    def expand(arcs: list[_Arc]) -> list:
        path = [src]
        for arc in arcs:
            path.extend(arc.interior)
            path.append(arc.head)
        return path

    ranked = sorted(best, key=lambda e: (-e[0], -e[1]))
    return [expand(arcs) for _cost, _found, arcs in ranked]
