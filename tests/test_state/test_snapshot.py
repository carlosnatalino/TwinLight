"""Snapshot round-trip tests (M6).

The tool is pre-release, so the format is a single version (``version: 1``)
that bundles services + spectrum + the /config plane (element overrides,
failed links, twin overrides). Older pre-/config snapshots are not
supported.

Covers:
* a fresh snapshot embeds the /config plane state alongside services
  and spectrum,
* restoring a snapshot re-applies parameter mutations *and* the
  failed-link set on top of a freshly-loaded GNPy network — i.e.
  experiments are reproducible across a process restart,
* restoring replaces existing live state rather than merging,
* any snapshot whose ``version`` is not 1 is rejected with a clear
  error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from gnpy.core.elements import Fiber

from twinlight.config import TwinConfig
from twinlight.models.common import NameAndValue
from twinlight.models.connectivity import (
    ConnectivityService,
    ConnectivityServiceEndPoint,
    SipRef,
)
from twinlight.physics.modulation import ModulationFormat
from twinlight.state.context import TapiContext


def _two_sips(ctx: TapiContext) -> tuple[str, str]:
    sips = list(ctx._sip_to_gnpy_uid.keys())
    return sips[0], sips[1]


def _make_service(sip_a: str, sip_z: str) -> ConnectivityService:
    return ConnectivityService(
        name=[NameAndValue(value_name="service-name", value="snap-v2")],
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
        uid for uid in path if isinstance(ctx._gnpy_uid_map[uid], Fiber)
    )


class TestSnapshotVersionRejection:
    @pytest.mark.parametrize("version", [0, 2, None, "v1"])
    def test_non_v1_rejected(
        self,
        gnpy_context: TapiContext,
        tmp_path: Path,
        version: object,
    ) -> None:
        path = tmp_path / f"v_{version}.json"
        path.write_text(json.dumps({"version": version}))
        with pytest.raises(ValueError, match="Unsupported snapshot version"):
            gnpy_context.restore_from(path)


class TestSnapshotV2RoundTrip:
    @pytest.mark.asyncio
    async def test_round_trip_preserves_overrides_and_failure(
        self,
        gnpy_twin_config: TwinConfig,
        tmp_path: Path,
    ) -> None:
        # 1) Build a context, add a service, mutate parameters, fail a fiber.
        ctx1 = TapiContext(gnpy_twin_config)
        sip_a, sip_z = _two_sips(ctx1)
        svc = _make_service(sip_a, sip_z)
        await ctx1.add_service(svc)
        fiber_uid = _first_fiber_on_path(ctx1, sip_a, sip_z)
        # First mutate loss, then fail the fiber. Both should survive
        # snapshot+restore.
        ctx1.apply_element_overrides({fiber_uid: {"loss_coef": 0.27}})
        ctx1.set_link_failed(fiber_uid, True)
        ctx1.apply_twin_overrides({"rmsa.qot_margin_db": 2.5})

        # 2) Snapshot.
        path = tmp_path / "v2.json"
        ctx1.snapshot(path)
        snap_data = json.loads(path.read_text())
        assert snap_data["version"] == 1
        assert snap_data["element_overrides"][fiber_uid]["loss_coef"] == 0.27
        assert fiber_uid in snap_data["failed_links"]
        assert snap_data["twin_overrides"]["rmsa.qot_margin_db"] == 2.5

        # 3) Build a *fresh* context (simulates a restart) and restore.
        ctx2 = TapiContext(gnpy_twin_config)
        ctx2.restore_from(path)

        # Service came back.
        services = ctx2.get_services()
        assert len(services) == 1
        assert services[0].uuid == svc.uuid

        # Element override re-applied to the live element.
        from twinlight.physics.element_params import read_attr
        loss_now = read_attr(ctx2._gnpy_uid_map[fiber_uid], "loss_coef")
        assert loss_now == pytest.approx(0.27)
        assert ctx2.get_element_overrides()[fiber_uid]["loss_coef"] == 0.27

        # Failure re-applied: fiber is back in the failed set and TAPI
        # link operational-state is DISABLED.
        assert fiber_uid in ctx2.get_failed_links()
        from twinlight.models.common import OperationalState
        for topo_uuid, link_uuid in ctx2._fiber_to_link_refs.get(
            fiber_uid, []
        ):
            link = ctx2.get_link(topo_uuid, link_uuid)
            assert link.operational_state == OperationalState.DISABLED

        # Twin override re-applied.
        assert ctx2._config.rmsa.qot_margin_db == pytest.approx(2.5)
        assert ctx2.get_twin_overrides() == {"rmsa.qot_margin_db": 2.5}

    @pytest.mark.asyncio
    async def test_restore_resets_then_reapplies(
        self,
        gnpy_twin_config: TwinConfig,
        tmp_path: Path,
    ) -> None:
        """Restore must replace existing overrides, not merge them.

        A user who snapshots a clean state, then accidentally fails a
        link, should be able to restore and have the link come back up.
        """
        ctx = TapiContext(gnpy_twin_config)
        _sip_a, _sip_z = _two_sips(ctx)
        # Clean snapshot — no overrides, no failures.
        path = tmp_path / "clean.json"
        ctx.snapshot(path)

        # Now mess with state.
        fiber_uid = next(
            uid for uid, el in ctx._gnpy_uid_map.items()
            if isinstance(el, Fiber)
        )
        ctx.set_link_failed(fiber_uid, True)
        ctx.apply_element_overrides({fiber_uid: {"loss_coef": 0.40}})
        assert fiber_uid in ctx.get_failed_links()

        # Restore clean snapshot.
        ctx.restore_from(path)
        assert ctx.get_failed_links() == set()
        assert ctx.get_element_overrides() == {}


class TestCrossBackendRestore:
    """Cross-backend restore is rejected (M6).

    Snapshot records ``backend`` and restore_from refuses to apply a
    snapshot whose backend differs from the running process — services
    were admitted under different QoT thresholds and silently
    cross-restoring would falsify whatever experiment the snapshot
    represents.
    """

    def test_snapshot_records_backend_name(
        self, gnpy_context: TapiContext, tmp_path: Path
    ) -> None:
        path = tmp_path / "snap.json"
        gnpy_context.snapshot(path)
        data = json.loads(path.read_text())
        assert data["backend"] == "gnpy"

    def test_restore_rejects_mismatched_backend(
        self,
        gnpy_twin_config,
        tmp_path: Path,
    ) -> None:
        from twinlight.config import PhysicsConfig
        from twinlight.state.context import TapiContext

        # Snapshot under GNPy.
        gnpy_ctx = TapiContext(gnpy_twin_config)
        path = tmp_path / "gnpy-snap.json"
        gnpy_ctx.snapshot(path)

        # Spin up a fresh context with EGN selected and try to restore.
        egn_cfg = gnpy_twin_config.model_copy(deep=True)
        egn_cfg.physics = PhysicsConfig(backend="egn")
        egn_ctx = TapiContext(egn_cfg)
        with pytest.raises(ValueError, match="--physics-backend gnpy"):
            egn_ctx.restore_from(path)

    def test_restore_accepts_matching_backend(
        self, gnpy_context: TapiContext, tmp_path: Path
    ) -> None:
        path = tmp_path / "snap.json"
        gnpy_context.snapshot(path)
        # Same backend → restore succeeds (this is the happy path the
        # rest of the snapshot suite already exercises; just an
        # explicit assertion for clarity).
        gnpy_context.restore_from(path)
