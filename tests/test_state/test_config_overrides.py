"""Tests for the /config plane mutation core on ``TapiContext`` (M2).

Covers:
* the element-UID → service-UUID reverse index is populated on
  ``add_service`` and torn down on ``delete_service`` and ``restore_from``,
* ``apply_element_overrides`` validates input, writes to live GNPy
  elements, and invalidates only the affected services' baselines,
* a fiber loss bump actually moves the next computed GSNR downward
  (end-to-end verification that mutations propagate through GNPy),
* ``apply_twin_overrides`` enforces the allow-list and updates the live
  ``TwinConfig`` object so transient flags and RMSA margin change at
  runtime without a restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from tapi_twin.models.common import NameAndValue
from tapi_twin.models.connectivity import (
    ConnectivityService,
    ConnectivityServiceEndPoint,
    SipRef,
)
from tapi_twin.physics.element_params import ParamValidationError
from tapi_twin.physics.modulation import ModulationFormat
from tapi_twin.state.context import TapiContext
from tapi_twin.state.twin_overrides import TwinOverrideError


def _two_sips(ctx: TapiContext) -> tuple[str, str]:
    sips = list(ctx._sip_to_gnpy_uid.keys())
    assert len(sips) >= 2, "fixture topology should expose at least 2 SIPs"
    return sips[0], sips[1]


def _make_service(sip_a: str, sip_z: str) -> ConnectivityService:
    return ConnectivityService(
        name=[NameAndValue(value_name="service-name", value="m2-test")],
        modulation_format=ModulationFormat.DP_QPSK,
        end_point=[
            ConnectivityServiceEndPoint(
                local_id="a", service_interface_point=SipRef(
                    service_interface_point_uuid=sip_a,
                ),
            ),
            ConnectivityServiceEndPoint(
                local_id="z", service_interface_point=SipRef(
                    service_interface_point_uuid=sip_z,
                ),
            ),
        ],
    )


async def _add(ctx: TapiContext, sip_a: str, sip_z: str) -> ConnectivityService:
    svc = _make_service(sip_a, sip_z)
    await ctx.add_service(svc)
    return svc


def _first_fiber_uid(ctx: TapiContext) -> str:
    from gnpy.core.elements import Fiber

    for uid, el in ctx._gnpy_uid_map.items():
        if isinstance(el, Fiber):
            return uid
    raise AssertionError("no Fiber in fixture topology")


class TestReverseIndex:
    @pytest.mark.asyncio
    async def test_add_service_populates_index(self, gnpy_context: TapiContext) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        # Every UID on the path should now point at this service.
        path = gnpy_context.get_service_path(sip_a, sip_z)
        assert path is not None and len(path) > 0
        for uid in path:
            assert svc.uuid in gnpy_context.services_for_element(uid), uid

    @pytest.mark.asyncio
    async def test_delete_service_clears_index(self, gnpy_context: TapiContext) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        gnpy_context.delete_service(svc.uuid)
        for entry in gnpy_context._element_to_services.values():
            assert svc.uuid not in entry


class TestApplyElementOverrides:
    @pytest.mark.asyncio
    async def test_invalidates_only_affected_services(
        self, gnpy_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        # Prime the baseline cache.
        baseline_before = await gnpy_context.get_or_compute_baseline(svc)
        assert baseline_before is not None
        assert svc.uuid in gnpy_context._baseline_cache

        # Mutate a fiber the service traverses → its baseline must be dropped.
        fiber_uid = next(
            uid for uid in gnpy_context.get_service_path(sip_a, sip_z)
            if type(gnpy_context._gnpy_uid_map[uid]).__name__ == "Fiber"
        )
        invalidated = gnpy_context.apply_element_overrides(
            {fiber_uid: {"loss_coef": 0.30}}
        )
        assert invalidated == {fiber_uid: {svc.uuid}}
        assert svc.uuid not in gnpy_context._baseline_cache

    @pytest.mark.asyncio
    async def test_loss_bump_lowers_gsnr(self, gnpy_context: TapiContext) -> None:
        """Sanity-check that a parameter mutation actually reaches GNPy.

        Doubling the fiber loss coefficient on a service's path must
        produce a measurably lower GSNR on the next baseline computation.
        """
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        baseline_before = await gnpy_context.get_or_compute_baseline(svc)
        assert baseline_before is not None
        gsnr_before = baseline_before.gsnr_db

        fiber_uid = next(
            uid for uid in gnpy_context.get_service_path(sip_a, sip_z)
            if type(gnpy_context._gnpy_uid_map[uid]).__name__ == "Fiber"
        )
        gnpy_context.apply_element_overrides(
            {fiber_uid: {"loss_coef": 0.40}}  # original is 0.2
        )
        baseline_after = await gnpy_context.get_or_compute_baseline(svc)
        assert baseline_after is not None
        assert baseline_after.gsnr_db < gsnr_before - 1.0, (
            f"GSNR did not drop after loss bump: "
            f"before={gsnr_before:.2f}, after={baseline_after.gsnr_db:.2f}"
        )

    def test_unknown_uid_rejected(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(KeyError, match="Unknown element UID"):
            gnpy_context.apply_element_overrides({"no-such-uid": {"loss_coef": 0.2}})

    def test_invalid_attribute_rejected(self, gnpy_context: TapiContext) -> None:
        fiber_uid = _first_fiber_uid(gnpy_context)
        with pytest.raises(ParamValidationError, match="not writable"):
            gnpy_context.apply_element_overrides({fiber_uid: {"length": 100.0}})

    def test_records_user_value(self, gnpy_context: TapiContext) -> None:
        fiber_uid = _first_fiber_uid(gnpy_context)
        gnpy_context.apply_element_overrides({fiber_uid: {"loss_coef": 0.27}})
        overrides = gnpy_context.get_element_overrides()
        assert overrides[fiber_uid]["loss_coef"] == 0.27

    def test_failed_key_is_ignored_for_now(self, gnpy_context: TapiContext) -> None:
        """``failed`` is M3 territory; apply_element_overrides must not raise."""
        fiber_uid = _first_fiber_uid(gnpy_context)
        gnpy_context.apply_element_overrides(
            {fiber_uid: {"failed": True, "loss_coef": 0.25}}
        )
        # Real attr was still applied.
        assert gnpy_context.get_element_overrides()[fiber_uid]["loss_coef"] == 0.25
        # And ``failed`` was NOT recorded under element overrides (M3 owns it).
        assert "failed" not in gnpy_context.get_element_overrides()[fiber_uid]


class TestApplyTwinOverrides:
    def test_flips_transient_enable(self, gnpy_context: TapiContext) -> None:
        assert gnpy_context._config.transients.phase_noise.enabled is True
        gnpy_context.apply_twin_overrides(
            {"transients.phase_noise.enabled": False}
        )
        assert gnpy_context._config.transients.phase_noise.enabled is False
        assert gnpy_context.get_twin_overrides() == {
            "transients.phase_noise.enabled": False
        }

    def test_changes_rmsa_margin(self, gnpy_context: TapiContext) -> None:
        original = gnpy_context._config.rmsa.qot_margin_db
        gnpy_context.apply_twin_overrides({"rmsa.qot_margin_db": original + 2.5})
        assert gnpy_context._config.rmsa.qot_margin_db == pytest.approx(
            original + 2.5
        )

    def test_rejects_unknown_key(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(TwinOverrideError, match="not runtime-mutable"):
            gnpy_context.apply_twin_overrides({"gnpy.topology": "/etc/passwd"})

    def test_rejects_bool_for_numeric(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(TwinOverrideError, match="got bool"):
            gnpy_context.apply_twin_overrides({"rmsa.qot_margin_db": True})

    def test_rejects_negative_margin(self, gnpy_context: TapiContext) -> None:
        with pytest.raises(TwinOverrideError, match="must be >= 0"):
            gnpy_context.apply_twin_overrides({"rmsa.qot_margin_db": -1.0})


class TestTwinOverridesModule:
    """Direct unit tests for the helper module — no TapiContext needed."""

    def test_flatten_round_trip(self) -> None:
        from tapi_twin.state.twin_overrides import flatten, to_nested

        nested = {"transients": {"phase_noise": {"enabled": False}},
                  "rmsa": {"qot_margin_db": 2.0}}
        flat: dict[str, Any] = flatten(nested)
        assert flat == {
            "transients.phase_noise.enabled": False,
            "rmsa.qot_margin_db": 2.0,
        }
        assert to_nested(flat) == nested

    def test_validate_accepts_allowed(self) -> None:
        from tapi_twin.state.twin_overrides import validate

        validate({"transients.environmental.enabled": True,
                  "rmsa.qot_margin_db": 1.0})

    def test_validate_rejects_bad_type(self) -> None:
        from tapi_twin.state.twin_overrides import validate

        with pytest.raises(TwinOverrideError):
            validate({"transients.phase_noise.enabled": "yes"})
