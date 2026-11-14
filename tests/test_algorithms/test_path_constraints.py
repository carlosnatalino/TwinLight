"""The T-API constraint parser: supported subset accepted, the rest refused by name."""

from __future__ import annotations

import pytest

from twinlight.algorithms.path_constraints import (
    ConstraintError,
    LinkRef,
    NodeRef,
    parse_constraints,
)
from twinlight.algorithms.routing import visits_in_order

N1 = {"topology-uuid": "t", "node-uuid": "n1"}
N2 = {"topology-uuid": "t", "node-uuid": "n2"}
L1 = {"topology-uuid": "t", "link-uuid": "l1"}


def test_no_constraints_is_empty() -> None:
    assert parse_constraints(None, None).is_empty
    assert parse_constraints({}, []).is_empty


def test_supported_topology_constraint() -> None:
    parsed = parse_constraints(None, [{
        "local-id": "1",
        "include-node": [N1, N2],
        "exclude-link": [L1],
        "explicit-route": True,
    }])
    assert parsed.include_nodes == (NodeRef("t", "n1"), NodeRef("t", "n2"))
    assert parsed.exclude_links == frozenset({LinkRef("t", "l1")})
    assert parsed.explicit_route


def test_single_object_and_merged_exclusions() -> None:
    parsed = parse_constraints(None, [
        {"local-id": "1", "exclude-node": [N1]},
        {"local-id": "2", "exclude-node": [N2], "exclude-link": L1},
    ])
    assert parsed.exclude_nodes == frozenset({NodeRef("t", "n1"), NodeRef("t", "n2")})
    assert parse_constraints(None, {"exclude-node": [N1]}).exclude_nodes


def test_include_order_is_kept() -> None:
    parsed = parse_constraints(None, [{"include-node": [N2, N1]}])
    assert [r.node_uuid for r in parsed.include_nodes] == ["n2", "n1"]


@pytest.mark.parametrize("leaf", [
    "include-topology", "exclude-path", "include-node-edge-point",
    "constraint-weight", "preferred-transport-layer",
])
def test_unsupported_topology_leaf_is_named(leaf: str) -> None:
    with pytest.raises(ConstraintError, match=leaf):
        parse_constraints(None, [{"local-id": "1", leaf: []}])


@pytest.mark.parametrize(("leaf", "value"), [
    ("diversity-policy", "SRLG"),
    ("max-allowed-hops", {"value": 3}),
    ("max-allowed-delay", {"value": 1000}),
    ("max-allowed-cost", {"value": 1}),
    ("cost-characteristic", []),
    ("tolerable-impact", "GRADES_OF_IMPACT_HITLESS"),
])
def test_unsupported_routing_leaf_is_named(leaf: str, value: object) -> None:
    with pytest.raises(ConstraintError, match=leaf):
        parse_constraints({leaf: value}, None)


def test_routing_values() -> None:
    parse_constraints(
        {"route-objective-function": "MIN_WORK_ROUTE_LATENCY", "is-exclusive": True},
        None,
    )
    with pytest.raises(ConstraintError, match="MIN_WORK_ROUTE_HOP"):
        parse_constraints({"route-objective-function": "MIN_WORK_ROUTE_HOP"}, None)
    with pytest.raises(ConstraintError, match="is-exclusive"):
        parse_constraints({"is-exclusive": False}, None)


@pytest.mark.parametrize(("entries", "match"), [
    ([{"include-node": [N1], "include-link": [L1]}], "include-node and include-link"),
    ([{"include-node": [N1]}, {"include-node": [N2]}], "more than one"),
    ([{"explicit-route": True}], "explicit-route"),
    ([{"include-node": [N1], "exclude-node": [N1]}], "both included and excluded"),
    ([{"include-node": [{"node-uuid": "n1"}]}], "topology-uuid"),
    ([{"include-node": [{**N1, "extra": 1}]}], "extra"),
    ([{"explicit-route": "yes", "include-node": [N1]}], "boolean"),
])
def test_malformed_or_undefined_combinations(entries: list, match: str) -> None:
    with pytest.raises(ConstraintError, match=match):
        parse_constraints(None, entries)


def test_visits_in_order_matches_gnpy_ispart() -> None:
    from gnpy.topology.request import ispart

    path = ["a", "b", "c", "d"]
    for include in (["b", "d"], ["d", "b"], ["x"], [], ["a", "d"]):
        assert visits_in_order(include, path) == ispart(include, path)
