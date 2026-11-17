"""The QoT what-if for a service with no signal, on both physics backends.

A service whose block lies outside the band its amplifiers pass (only a
restored version-1 snapshot holds one) has no signal before and after any
change: the report gives it the ``out-of-band`` status and no numbers, the
shape it uses for ``link-failed``, never the baseline's sentinel zeros or a
fresh propagation of a channel the first amplifier drops.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.conftest import MeshRefs, tapi_end_point
from tests.test_state.test_snapshot import _v1_snapshot
from twinlight.models.connectivity import ConnectivityService
from twinlight.state.context import TapiContext

NO_SIGNAL_FIELDS = ("current", "predicted", "delta_gsnr_db", "above_required",
                    "meets_admission_margin")


def _no_signal_row(current: str, predicted: str) -> dict[str, Any]:
    return {
        "current": {"status": current, "gsnr_db": None, "osnr_db": None},
        "predicted": {"status": predicted, "gsnr_db": None, "osnr_db": None},
        "delta_gsnr_db": None,
        "above_required": False,
        "meets_admission_margin": False,
    }


def _restored_out_of_band(ctx: TapiContext, tmp_path: Path) -> str:
    path = tmp_path / "v1.json"
    uuid = _v1_snapshot(ctx, path)
    ctx.restore_from(path)
    return uuid


def _fiber_on(ctx: TapiContext, uuid: str) -> str:
    path = ctx._allocated_path_uids(uuid)
    assert path
    fibers = set(ctx._backend.route_fibers(path))
    return next(uid for uid in path if uid in fibers)


class TestOutOfBand:
    """Sync tests: ``_v1_snapshot`` runs its own event loop."""

    def test_no_signal_before_and_after(
        self, mesh_context: TapiContext, tmp_path: Path,
    ) -> None:
        ctx = mesh_context
        uuid = _restored_out_of_band(ctx, tmp_path)
        path = ctx._allocated_path_uids(uuid)
        assert path
        # Touches the service without writing anything.
        [row] = asyncio.run(ctx.preview_qot({path[1]: {"failed": False}}, [], None))
        assert row["service"] == uuid
        assert {k: row[k] for k in NO_SIGNAL_FIELDS} == _no_signal_row(
            "out-of-band", "out-of-band")

    def test_a_parameter_change_does_not_bring_it_into_the_band(
        self, mesh_context: TapiContext, tmp_path: Path,
    ) -> None:
        ctx = mesh_context
        uuid = _restored_out_of_band(ctx, tmp_path)
        fiber = _fiber_on(ctx, uuid)
        [row] = asyncio.run(ctx.preview_qot({fiber: {"loss_coef": 0.25}}, [], None))
        assert {k: row[k] for k in NO_SIGNAL_FIELDS} == _no_signal_row(
            "out-of-band", "out-of-band")
        assert ctx._backend.read_attribute(fiber, "loss_coef") != 0.25

    def test_a_cut_takes_precedence(
        self, mesh_context: TapiContext, tmp_path: Path,
    ) -> None:
        """Failed and out of band at once: link-failed, as the OPM says."""
        ctx = mesh_context
        uuid = _restored_out_of_band(ctx, tmp_path)
        [row] = asyncio.run(ctx.preview_qot({_fiber_on(ctx, uuid): {"failed": True}}, [], None))
        assert {k: row[k] for k in NO_SIGNAL_FIELDS} == _no_signal_row(
            "out-of-band", "link-failed")
        assert ctx.get_failed_links() == set()


class TestLinkFailed:
    async def test_row_of_an_in_band_service(self, mesh_context: TapiContext) -> None:
        ctx = mesh_context
        refs = MeshRefs(ctx)
        svc = ConnectivityService.model_validate({"end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]})
        await ctx.add_service(svc)
        [row] = await ctx.preview_qot({_fiber_on(ctx, svc.uuid): {"failed": True}}, [], None)
        assert row["current"]["status"] == "ok" and row["current"]["gsnr_db"] > 0
        assert row["predicted"] == {"status": "link-failed", "gsnr_db": None, "osnr_db": None}
        assert row["delta_gsnr_db"] is None
        assert row["above_required"] is False and row["meets_admission_margin"] is False
