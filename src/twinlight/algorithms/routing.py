"""k-shortest-path routing over a NetworkX DiGraph of GNPy element UIDs."""

from __future__ import annotations

from itertools import islice

import networkx as nx


def k_shortest_paths(
    graph: nx.DiGraph,
    src_uid: str,
    dst_uid: str,
    k: int,
) -> list[list[str]]:
    """Return up to k shortest simple paths from src_uid to dst_uid.

    Uses Yen's algorithm via ``networkx.shortest_simple_paths`` (hop-count
    weighted since the graph has no explicit edge weights).

    Args:
        graph: NetworkX DiGraph whose nodes are GNPy element UIDs.
        src_uid: Source Transceiver UID.
        dst_uid: Destination Transceiver UID.
        k: Maximum number of paths to return.

    Returns:
        List of paths, each path is a list of element UIDs from src to dst.
        Returns an empty list if no path exists.
    """
    try:
        return list(islice(nx.shortest_simple_paths(graph, src_uid, dst_uid), k))
    except (nx.NetworkXNoPath, nx.NodeNotFound, nx.exception.NetworkXError):
        return []
