"""T-API v2.6.0 routing and topology constraints, reduced to what the twin honours.

``tapi-path-computation.yang`` defines two constraint groupings, used both by a
``path-comp-service`` and by a ``connectivity-service``:

* ``routing-constraint`` — objective function, diversity, maximum
  cost/hops/delay, cost/latency/risk characteristics, exclusivity;
* ``topology-constraint`` (a list keyed by ``local-id``) — include/exclude
  topology, path, link, node, node-edge-point and their partitions, plus
  ``explicit-route``.

Route search is GNPy's: ``gnpy.topology.request.compute_constrained_path``
over the GNPy network, ranked by fiber length, with an ordered list of nodes to
traverse (``nodes_list``/``loose_list``). So the twin supports exactly what that
search can express, plus exclusions, which are applied by searching a
restricted view of the graph:

* ``include-node`` / ``include-link`` → GNPy ``nodes_list``, all ``STRICT``;
* ``exclude-node`` / ``exclude-link`` → elements hidden from the search;
* ``explicit-route: true`` → the includes must already be the whole route;
* ``route-objective-function: MIN_WORK_ROUTE_LATENCY`` — accepted because it
  is what the ranking already does: a link's latency is its fiber length over
  a constant group velocity (``loader/tapi_builder.py``), so ranking by length
  *is* ranking by latency;
* ``is-exclusive: true`` — the twin only ever allocates spectrum exclusively.

Every other leaf is refused with an error that names it. A constraint the twin
dropped silently would return a route the client believes satisfies something
it was never checked against.

T-API does not say whether ``include-node`` is ordered. GNPy's ``nodes_list``
is (``gnpy.topology.request.ispart``), so the twin visits includes in the order
given — which is also the only reading under which ``explicit-route`` means
anything. For the same reason ``include-node`` and ``include-link`` cannot be
combined (the order between two lists is undefined), and includes may appear in
one ``topology-constraint`` entry only. Exclusions from every entry are merged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


class ConstraintError(ValueError):
    """A routing or topology constraint the twin cannot honour, or a malformed one."""


@dataclass(frozen=True)
class NodeRef:
    """``tapi-topology:node-ref``."""

    topology_uuid: str
    node_uuid: str


@dataclass(frozen=True)
class LinkRef:
    """``tapi-topology:link-ref``."""

    topology_uuid: str
    link_uuid: str


@dataclass(frozen=True)
class PathConstraints:
    """The supported subset of a request's constraints, parsed and checked."""

    include_nodes: tuple[NodeRef, ...] = ()
    include_links: tuple[LinkRef, ...] = ()
    exclude_nodes: frozenset[NodeRef] = field(default_factory=frozenset)
    exclude_links: frozenset[LinkRef] = field(default_factory=frozenset)
    explicit_route: bool = False

    @property
    def is_empty(self) -> bool:
        return not (
            self.include_nodes or self.include_links
            or self.exclude_nodes or self.exclude_links
        )


NO_CONSTRAINTS = PathConstraints()

# Routing-constraint leaves and the values accepted for them (see module docstring).
_ACCEPTED_ROUTING_VALUES: dict[str, tuple[Any, ...]] = {
    "route-objective-function": ("MIN_WORK_ROUTE_LATENCY",),
    "is-exclusive": (True,),
}
# Keys every T-API local-class carries; they identify an entry, constrain nothing.
_LOCAL_CLASS_KEYS = frozenset({"local-id", "name"})
_TOPOLOGY_KEYS = _LOCAL_CLASS_KEYS | {
    "explicit-route", "include-node", "exclude-node", "include-link", "exclude-link",
}


def parse_constraints(
    routing_constraint: Mapping[str, Any] | None,
    topology_constraint: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> PathConstraints:
    """Validate T-API constraint JSON and return the supported subset.

    ``topology_constraint`` is a list on ``path-comp-service`` and
    ``connectivity-service``; a single object is accepted too, the same
    list-or-object leniency the connectivity router applies to list entries.

    Raises:
        ConstraintError: A leaf the twin does not support, an unsupported value,
            a malformed reference, or a combination with no defined meaning.
            The message names the leaf.
    """
    _check_routing_constraint(routing_constraint)

    if topology_constraint is None:
        entries: list[Mapping[str, Any]] = []
    elif isinstance(topology_constraint, Mapping):
        entries = [topology_constraint]
    else:
        entries = list(topology_constraint)

    include_nodes: tuple[NodeRef, ...] = ()
    include_links: tuple[LinkRef, ...] = ()
    explicit_route = False
    exclude_nodes: set[NodeRef] = set()
    exclude_links: set[LinkRef] = set()
    includes_seen_in: str | None = None

    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ConstraintError(f"topology-constraint[{index}] must be an object")
        label = f"topology-constraint[local-id={entry.get('local-id', index)}]"
        unsupported = sorted(set(entry) - _TOPOLOGY_KEYS)
        if unsupported:
            raise ConstraintError(
                f"{label}/{unsupported[0]} is not supported; supported leaves "
                f"are {sorted(_TOPOLOGY_KEYS - _LOCAL_CLASS_KEYS)}"
            )

        nodes = tuple(_node_refs(entry.get("include-node"), f"{label}/include-node"))
        links = tuple(_link_refs(entry.get("include-link"), f"{label}/include-link"))
        exclude_nodes.update(_node_refs(entry.get("exclude-node"), f"{label}/exclude-node"))
        exclude_links.update(_link_refs(entry.get("exclude-link"), f"{label}/exclude-link"))

        explicit = entry.get("explicit-route", False)
        if not isinstance(explicit, bool):
            raise ConstraintError(f"{label}/explicit-route must be a boolean")
        if nodes and links:
            raise ConstraintError(
                f"{label}: include-node and include-link together are not "
                "supported — the order between the two lists is undefined, and "
                "the route visits includes in order. Use one of them."
            )
        if explicit and not (nodes or links):
            raise ConstraintError(
                f"{label}/explicit-route is true but the entry lists no "
                "include-node or include-link to form the route"
            )
        if nodes or links:
            if includes_seen_in is not None:
                raise ConstraintError(
                    f"{label}: includes already given in {includes_seen_in}; "
                    "includes in more than one topology-constraint entry have "
                    "no defined order"
                )
            includes_seen_in = label
            include_nodes, include_links, explicit_route = nodes, links, explicit

    overlap = set(include_nodes) & exclude_nodes
    if overlap:
        ref = next(iter(overlap))
        raise ConstraintError(f"node {ref.node_uuid} is both included and excluded")
    overlap_links = set(include_links) & exclude_links
    if overlap_links:
        ref_l = next(iter(overlap_links))
        raise ConstraintError(f"link {ref_l.link_uuid} is both included and excluded")

    return PathConstraints(
        include_nodes=include_nodes,
        include_links=include_links,
        exclude_nodes=frozenset(exclude_nodes),
        exclude_links=frozenset(exclude_links),
        explicit_route=explicit_route,
    )


def _check_routing_constraint(routing_constraint: Mapping[str, Any] | None) -> None:
    if routing_constraint is None:
        return
    if not isinstance(routing_constraint, Mapping):
        raise ConstraintError("routing-constraint must be an object")
    for leaf, value in routing_constraint.items():
        accepted = _ACCEPTED_ROUTING_VALUES.get(leaf)
        if accepted is None:
            raise ConstraintError(
                f"routing-constraint/{leaf} is not supported; supported leaves "
                f"are {sorted(_ACCEPTED_ROUTING_VALUES)}"
            )
        if value not in accepted:
            raise ConstraintError(
                f"routing-constraint/{leaf}={value!r} is not supported; "
                f"supported values: {list(accepted)}"
            )


def _refs(raw: Any, where: str, keys: tuple[str, str]) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):  # a one-entry list spelled as an object
        raw = [raw]
    if not isinstance(raw, list):
        raise ConstraintError(f"{where} must be a list")
    refs: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping) or not all(
            isinstance(item.get(k), str) and item.get(k) for k in keys
        ):
            raise ConstraintError(f"{where} entries need {' and '.join(keys)}")
        extra = sorted(set(item) - set(keys))
        if extra:
            raise ConstraintError(f"{where}/{extra[0]} is not part of the reference")
        refs.append((item[keys[0]], item[keys[1]]))
    return refs


def _node_refs(raw: Any, where: str) -> list[NodeRef]:
    return [NodeRef(t, n) for t, n in _refs(raw, where, ("topology-uuid", "node-uuid"))]


def _link_refs(raw: Any, where: str) -> list[LinkRef]:
    return [LinkRef(t, lk) for t, lk in _refs(raw, where, ("topology-uuid", "link-uuid"))]
