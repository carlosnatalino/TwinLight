"""Ranked simple-path search over a NetworkX DiGraph of network elements.

The search works on any node type, so the same code ranks paths over the GNPy
network (nodes are ``gnpy.core.elements`` objects) and over the twin's parsed
topology graph (nodes are element UIDs).
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from itertools import islice

import networkx as nx

# Upper bound on the simple paths examined when filtering for include
# constraints. Yen's algorithm yields paths lazily, but a constraint no path
# satisfies would otherwise enumerate every simple path of the graph.
MAX_PATHS_EXAMINED = 2000


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

    Uses Yen's algorithm via ``networkx.shortest_simple_paths``, ranked by the
    edge attribute ``weight`` (hop count when ``None``). Paths that do not
    visit ``include`` in order are skipped, examining at most
    :data:`MAX_PATHS_EXAMINED` paths.

    Returns an empty list when no path exists or a node is missing.
    """
    if k < 1:
        return []
    try:
        ranked = nx.shortest_simple_paths(graph, src_uid, dst_uid, weight=weight)
        examined = islice(ranked, MAX_PATHS_EXAMINED)
        return list(islice(
            (p for p in examined if visits_in_order(include, p)), k,
        ))
    except (nx.NetworkXNoPath, nx.NodeNotFound, nx.NetworkXError):
        return []
