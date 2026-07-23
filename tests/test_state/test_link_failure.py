"""Tests for fiber link failure (M3).

Verifies the end-to-end semantics:

* ``set_link_failed(uid, True)`` removes the fiber's graph edges, flips the
  backing TAPI Link operational-state to DISABLED, and invalidates the
  baseline of every service traversing it,
* an already-admitted service that traversed the fiber starts reporting
  ``status="link-failed"`` on the next OPM sample (no silent auto-reroute),
* RMSA admission for a brand-new service across the failed fiber is
  rejected with ``NoPathError`` (the graph no longer connects),
* ``set_link_failed(uid, False)`` restores the edges exactly, re-enables
  the link, and the affected services start propagating again,
* ``apply_element_overrides({fiber: {"failed": True}})`` routes through
  ``set_link_failed`` so the /config router (M4) can pass one body.
"""

from __future__ import annotations

import pytest
from gnpy.core.elements import Fiber

from twinlight.models.common import NameAndValue, OperationalState
from twinlight.models.connectivity import (
    ConnectivityService,
    ConnectivityServiceEndPoint,
    SipRef,
)
from twinlight.physics.modulation import ModulationFormat
from twinlight.state.context import NoPathError, TapiContext


def _two_sips(ctx: TapiContext) -> tuple[str, str]:
    sips = list(ctx._sip_to_gnpy_uid.keys())
    return sips[0], sips[1]


def _make_service(sip_a: str, sip_z: str) -> ConnectivityService:
    return ConnectivityService(
        name=[NameAndValue(value_name="service-name", value="m3-test")],
        modulation_format=ModulationFormat.DP_QPSK,
        end_point=[
            ConnectivityServiceEndPoint(
                local_id="a",
                service_interface_point=SipRef(
                    service_interface_point_uuid=sip_a,
                ),
            ),
            ConnectivityServiceEndPoint(
                local_id="z",
                service_interface_point=SipRef(
                    service_interface_point_uuid=sip_z,
                ),
            ),
        ],
    )


def _first_fiber_on_path(ctx: TapiContext, sip_a: str, sip_z: str) -> str:
    path = ctx.get_service_path(sip_a, sip_z)
    assert path is not None
    return next(
        uid for uid in path
        if isinstance(ctx._gnpy_uid_map[uid], Fiber)
    )


class TestSetLinkFailedBasics:
    def test_failing_a_non_fiber_raises(self, gnpy_context: TapiContext) -> None:
        # ROADMs and EDFAs are not failable in this scope.
        roadm_uid = next(
            uid for uid, el in gnpy_context._gnpy_uid_map.items()
            if type(el).__name__ == "Roadm"
        )
        with pytest.raises(KeyError, match="not a Fiber"):
            gnpy_context.set_link_failed(roadm_uid, True)

    def test_unknown_uid_raises(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(KeyError, match="not a Fiber"):
            gnpy_context.set_link_failed("no-such-uid", True)

    def test_failing_removes_graph_edges(self, gnpy_context: TapiContext) -> None:
        fiber_uid = next(
            uid for uid, el in gnpy_context._gnpy_uid_map.items()
            if isinstance(el, Fiber)
        )
        g = gnpy_context._topo_graph.graph
        before_in = g.in_degree(fiber_uid)
        before_out = g.out_degree(fiber_uid)
        assert before_in > 0 and before_out > 0

        gnpy_context.set_link_failed(fiber_uid, True)
        assert g.in_degree(fiber_uid) == 0
        assert g.out_degree(fiber_uid) == 0
        assert fiber_uid in gnpy_context.get_failed_links()

        gnpy_context.set_link_failed(fiber_uid, False)
        assert g.in_degree(fiber_uid) == before_in
        assert g.out_degree(fiber_uid) == before_out
        assert fiber_uid not in gnpy_context.get_failed_links()

    def test_idempotent(self, gnpy_context: TapiContext) -> None:
        fiber_uid = next(
            uid for uid, el in gnpy_context._gnpy_uid_map.items()
            if isinstance(el, Fiber)
        )
        gnpy_context.set_link_failed(fiber_uid, True)
        # Double-fail is a no-op.
        gnpy_context.set_link_failed(fiber_uid, True)
        assert fiber_uid in gnpy_context.get_failed_links()
        # Double-restore is a no-op.
        gnpy_context.set_link_failed(fiber_uid, False)
        gnpy_context.set_link_failed(fiber_uid, False)
        assert fiber_uid not in gnpy_context.get_failed_links()


class TestServiceImpact:
    @pytest.mark.asyncio
    async def test_admitted_service_reports_link_failed(
        self, gnpy_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = _make_service(sip_a, sip_z)
        await gnpy_context.add_service(svc)

        # Healthy baseline first.
        baseline = await gnpy_context.get_or_compute_baseline(svc)
        assert baseline is not None and baseline.status is None
        healthy_gsnr = baseline.gsnr_db

        # Fail a fiber on the path.
        fiber_uid = _first_fiber_on_path(gnpy_context, sip_a, sip_z)
        affected = gnpy_context.set_link_failed(fiber_uid, True)
        assert svc.uuid in affected

        failed = await gnpy_context.get_or_compute_baseline(svc)
        assert failed is not None
        assert failed.status == "link-failed"

        # Restore: GSNR comes back to roughly the healthy value (allow small
        # numerical wiggle in case GNPy cached anything).
        gnpy_context.set_link_failed(fiber_uid, False)
        recovered = await gnpy_context.get_or_compute_baseline(svc)
        assert recovered is not None and recovered.status is None
        assert abs(recovered.gsnr_db - healthy_gsnr) < 0.5

    @pytest.mark.asyncio
    async def test_rmsa_rejects_new_service_across_failed_fiber(
        self, gnpy_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        # First find a fiber on the only available path, then fail it.
        path = gnpy_context.get_service_path(sip_a, sip_z)
        assert path is not None
        fiber_uid = next(
            uid for uid in path if isinstance(gnpy_context._gnpy_uid_map[uid], Fiber)
        )
        gnpy_context.set_link_failed(fiber_uid, True)

        # New service across the (now disconnected) endpoints must be rejected.
        svc = _make_service(sip_a, sip_z)
        with pytest.raises(NoPathError):
            await gnpy_context.add_service(svc)

    def test_tapi_link_operational_state_flips(
        self, gnpy_context: TapiContext
    ) -> None:
        fiber_uid = next(
            uid for uid, el in gnpy_context._gnpy_uid_map.items()
            if isinstance(el, Fiber)
        )
        refs = gnpy_context._fiber_to_link_refs.get(fiber_uid, [])
        if not refs:
            pytest.skip(f"no TAPI link maps to fiber {fiber_uid!r} in fixture")

        for topo_uuid, link_uuid in refs:
            link = gnpy_context.get_link(topo_uuid, link_uuid)
            assert link is not None
            assert link.operational_state == OperationalState.ENABLED

        gnpy_context.set_link_failed(fiber_uid, True)
        for topo_uuid, link_uuid in refs:
            link = gnpy_context.get_link(topo_uuid, link_uuid)
            assert link.operational_state == OperationalState.DISABLED

        gnpy_context.set_link_failed(fiber_uid, False)
        for topo_uuid, link_uuid in refs:
            link = gnpy_context.get_link(topo_uuid, link_uuid)
            assert link.operational_state == OperationalState.ENABLED


class TestApplyOverridesRoutesFailedKey:
    @pytest.mark.asyncio
    async def test_failed_key_routes_to_set_link_failed(
        self, gnpy_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = _make_service(sip_a, sip_z)
        await gnpy_context.add_service(svc)
        fiber_uid = _first_fiber_on_path(gnpy_context, sip_a, sip_z)

        invalidated = gnpy_context.apply_element_overrides(
            {fiber_uid: {"failed": True}}
        )
        assert svc.uuid in invalidated.get(fiber_uid, set())
        assert fiber_uid in gnpy_context.get_failed_links()

        # Combined: parameter bump + failure in one call.
        invalidated = gnpy_context.apply_element_overrides(
            {fiber_uid: {"loss_coef": 0.28, "failed": False}}
        )
        assert gnpy_context.get_element_overrides()[fiber_uid][
            "loss_coef"
        ] == 0.28
        assert fiber_uid not in gnpy_context.get_failed_links()

    def test_failed_key_rejects_non_bool(self, gnpy_context: TapiContext) -> None:
        fiber_uid = next(
            uid for uid, el in gnpy_context._gnpy_uid_map.items()
            if isinstance(el, Fiber)
        )
        with pytest.raises(ValueError, match="failed must be bool"):
            gnpy_context.apply_element_overrides({fiber_uid: {"failed": "yes"}})
