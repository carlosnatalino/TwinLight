"""Snapshot round-trip tests (M6).

The format (``version: 2``) bundles services + spectrum + the /config plane
(element overrides, failed links, twin overrides). Version 1, from before
spectrum was bounded by the amplifier band, is deprecated: it is restored with
a warning and rewritten as version 2 (``TestDeprecatedSnapshot``).

Covers:
* a fresh snapshot embeds the /config plane state alongside services
  and spectrum,
* restoring a snapshot re-applies parameter mutations *and* the
  failed-link set on top of a freshly-loaded GNPy network — i.e.
  experiments are reproducible across a process restart,
* restoring replaces existing live state rather than merging,
* any other ``version`` is rejected with a clear error.
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
    @pytest.mark.parametrize("version", [0, 3, None, "v1", True])
    def test_unknown_version_rejected(
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
        assert snap_data["version"] == 2
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


def _v1_snapshot(ctx: TapiContext, path: Path) -> str:
    """A snapshot as main wrote it: version 1, no launch power, and the block
    first-fit gave from slot 0 — outside the band the amplifiers pass."""
    from tests.conftest import MeshRefs, tapi_end_point

    refs = MeshRefs(ctx)
    svc = ConnectivityService.model_validate({"end-point": [
        tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]})
    import asyncio

    asyncio.run(ctx.add_service(svc))
    ctx.snapshot(path)
    data = json.loads(path.read_text())
    data["version"] = 1
    alloc = data["service_allocation"][svc.uuid]
    del alloc["launch_power_dbm"]
    start, width = alloc["start"], alloc["block_width"]
    for slots in data["spectrum"]["slots"].values():
        if any(slots[start:start + width]):
            slots[start:start + width] = [0] * width
            slots[0:width] = [1] * width
    alloc["start"] = 0
    for ep in data["services"][0]["end-point"]:
        cfg = ep["layer-protocol-constraint"][0][
            "tapi-photonic-media:otsia-connectivity-service-end-point-spec"]["otsi-config"][0]
        cfg.pop("central-frequency", None)
        cfg.pop("channel-output-power", None)
    path.write_text(json.dumps(data))
    return svc.uuid


class TestDeprecatedSnapshot:
    def test_restored_with_warnings_and_rewritten(self, mesh_context: TapiContext, tmp_path) -> None:
        import asyncio

        from twinlight.models.common import OperationalState

        ctx = mesh_context
        path = tmp_path / "checkpoint.json"
        uuid = _v1_snapshot(ctx, path)

        warnings = ctx.restore_from(path)
        assert "deprecated format version 1" in warnings[0]
        assert any(uuid in w and "out-of-band" in w for w in warnings[1:])
        # The old file is gone, replaced by the same state in version 2.
        rewritten = json.loads(path.read_text())
        assert rewritten["version"] == 2
        assert rewritten["service_allocation"][uuid]["start"] == 0
        assert rewritten["service_allocation"][uuid]["launch_power_dbm"] == (
            ctx.default_launch_power_dbm)
        assert not list(tmp_path.glob("*.tmp"))

        svc = ctx.get_service(uuid)
        baseline = asyncio.run(ctx.get_or_compute_baseline(svc))
        assert baseline is not None and baseline.status == "out-of-band"
        assert svc.operational_state == OperationalState.DISABLED
        # Not a neighbour of anything: the first amplifier drops it.
        assert ctx._line_load() == []

        # Restoring the rewritten file warns only about the band.
        again = ctx.restore_from(path)
        assert len(again) == 1 and "out-of-band" in again[0]

    def test_a_read_only_file_is_restored_not_rewritten(self, tmp_path) -> None:
        """A checkpoint on a read-only mount: the restore succeeds, the
        migration is skipped with a warning, the file is left as it was."""
        from fastapi.testclient import TestClient

        from tests.conftest import _mesh_config
        from twinlight.app import create_app

        client = TestClient(create_app(_mesh_config(tmp_path, "egn")))
        ctx = client.app.state.context
        mount = tmp_path / "ro"
        mount.mkdir()
        path = mount / "checkpoint.json"
        uuid = _v1_snapshot(ctx, path)
        original = path.read_text()
        mount.chmod(0o555)
        try:
            resp = client.post("/admin/restore", json={"path": str(path)})
        finally:
            mount.chmod(0o755)
        assert resp.status_code == 200
        warning = resp.json()["warnings"][0]
        assert "could not be rewritten" in warning and "stays at version 1" in warning
        assert path.read_text() == original
        assert ctx.get_service(uuid) is not None

    def test_an_unreadable_file_is_a_400(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from tests.conftest import _mesh_config
        from twinlight.app import create_app

        client = TestClient(create_app(_mesh_config(tmp_path, "egn")))
        path = tmp_path / "secret.json"
        path.write_text("{}")
        path.chmod(0o000)
        try:
            resp = client.post("/admin/restore", json={"path": str(path)})
        finally:
            path.chmod(0o644)
        assert resp.status_code == 400
        assert "Cannot read snapshot" in resp.text

    def test_the_api_reports_it(self, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from tests.conftest import _mesh_config
        from twinlight.app import create_app

        client = TestClient(create_app(_mesh_config(tmp_path, "egn")))
        ctx = client.app.state.context
        path = tmp_path / "old.json"
        uuid = _v1_snapshot(ctx, path)

        resp = client.post("/admin/restore", json={"path": str(path)})
        assert resp.status_code == 200
        assert len(resp.json()["warnings"]) == 2
        opm = client.get(f"/internal/opm/{uuid}").json()
        assert opm["status"] == "out-of-band"
        assert opm["measurements"]["gsnr-db"] is None
        service = client.get(
            f"/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service={uuid}",
        ).json()["tapi-connectivity:connectivity-service"]
        assert service["operational-state"] == "DISABLED"
        assert f'twinlight_service_enabled{{service_name="{uuid[:8]}",service_uuid="{uuid}"}} 0.0' in (
            client.get("/metrics").text)
        tree = client.get(f"/transparent/data/ServiceList/{uuid}").json()[
            "twinlight-transparent:data"]["ServiceList"][uuid]
        assert tree["operational_state"] == "DISABLED"
        assert tree["qot"]["status"] == "out-of-band"
