"""Constrained route search and connectivity admission on the test mesh.

Runs under both physics backends: the GNPy backend searches the GNPy network
through ``compute_constrained_path``, the EGN backend the parsed topology.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import MeshRefs, tapi_end_point
from twinlight.algorithms.path_constraints import ConstraintError, parse_constraints
from twinlight.models.connectivity import ConnectivityService
from twinlight.state.context import NoPathError


def _route(ctx, constraints=None, k=1) -> list[list[str]]:
    parsed = parse_constraints(None, constraints)
    return ctx.candidate_paths("trx A", "trx C", parsed, k)


def test_unconstrained_best_route(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    (best,) = _route(mesh_context)
    assert refs.roadms(best) == ["A", "B", "C"]


def test_include_node_forces_the_detour(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    (best,) = _route(mesh_context, [{"include-node": [refs.node("roadm D")]}])
    assert refs.roadms(best) == ["A", "D", "C"]


def test_includes_are_visited_in_order(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    nodes = [refs.node("roadm D"), refs.node("roadm B")]
    (best,) = _route(mesh_context, [{"include-node": nodes}])
    assert refs.roadms(best) == ["A", "D", "B", "C"]


def test_include_link(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    (best,) = _route(mesh_context, [{"include-link": [refs.link("roadm D", "roadm C")]}])
    assert refs.roadms(best) == ["A", "D", "C"]


def test_exclude_node_and_link(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    (no_b,) = _route(mesh_context, [{"exclude-node": [refs.node("roadm B")]}])
    assert refs.roadms(no_b) == ["A", "D", "C"]
    (no_bc,) = _route(mesh_context, [{"exclude-link": [refs.link("roadm B", "roadm C")]}])
    assert refs.roadms(no_bc) == ["A", "D", "C"]


def test_every_candidate_honours_the_exclusion(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    routes = _route(mesh_context, [{"exclude-node": [refs.node("roadm B")]}], k=5)
    assert len(routes) >= 2
    assert all("B" not in refs.roadms(r) for r in routes)


def test_explicit_route(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    full = [refs.node(f"roadm {x}") for x in "ADC"]
    (route,) = _route(mesh_context, [{"include-node": full, "explicit-route": True}])
    assert refs.roadms(route) == ["A", "D", "C"]
    # Naming only the detour is not a complete route: refused, not completed.
    partial = [{"include-node": [refs.node("roadm D")], "explicit-route": True}]
    assert _route(mesh_context, partial) == []


def test_unsatisfiable_include_returns_nothing(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    impossible = [{
        "include-node": [refs.node("roadm D")],
        "exclude-link": [refs.link("roadm A", "roadm D"), refs.link("roadm B", "roadm D")],
        "local-id": "1",
    }, {"local-id": "2", "exclude-link": [refs.link("roadm E", "roadm C")]}]
    # D is reachable only from A or B; with both links gone no route visits it.
    assert _route(mesh_context, impossible) == []


def test_excluding_an_end_point_is_refused(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    with pytest.raises(ConstraintError, match="end-point"):
        _route(mesh_context, [{"exclude-node": [refs.node("trx A")]}])


def test_unknown_reference_is_refused(mesh_context) -> None:
    with pytest.raises(ConstraintError, match="not found"):
        _route(mesh_context, [{"exclude-node": [{"topology-uuid": "x", "node-uuid": "y"}]}])


def _service(refs: MeshRefs, **extra) -> ConnectivityService:
    return ConnectivityService.model_validate({
        "end-point": [
            tapi_end_point("a", refs.sip("trx A")),
            tapi_end_point("z", refs.sip("trx C")),
        ],
        **extra,
    })


def test_connectivity_service_follows_its_constraints(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    svc = _service(refs, **{"topology-constraint": [
        {"local-id": "1", "exclude-node": [refs.node("roadm B")]},
    ]})
    asyncio.run(mesh_context.add_service(svc))
    path = mesh_context._allocated_path_uids(svc.uuid)
    assert refs.roadms(path) == ["A", "D", "C"]
    hops = mesh_context.get_service_path_hops(svc)
    assert [h["uid"] for h in hops if h["type"] == "Roadm"] == [
        "roadm A", "roadm D", "roadm C",
    ]


def test_connectivity_service_without_a_route_is_refused(mesh_context) -> None:
    refs = MeshRefs(mesh_context)
    svc = _service(refs, **{"topology-constraint": [{
        "include-node": [refs.node("roadm D")], "explicit-route": True,
    }]})
    with pytest.raises(NoPathError, match="constraints"):
        asyncio.run(mesh_context.add_service(svc))
    assert mesh_context.get_service(svc.uuid) is None
