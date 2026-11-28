"""What an admission costs under the EGN backend, and that its caches agree
with a cold recomputation.

Under a load-dependent backend an admission re-evaluates every lightpath
sharing a fiber with the new one, each over the whole lit line. Rebuilding
that line for every neighbour, with a GNPy band check per lightpath each
time, made admission O(neighbours × lightpaths): seconds at 1000
lightpaths. The line load is now built once per admission or deletion, from
a per-lightpath memo checked against its inputs, and the EGN backend
memoises each route's bands and lit fibers.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import pytest

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
from twinlight.models.connectivity import ConnectivityService
from twinlight.state import context as context_module
from twinlight.state.context import RmsaError, TapiContext

_EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "gnpy-data"
_HAS_CORONET = (
    (_EXAMPLES / "CORONET_CONUS_Topology.json").is_file()
    and (_EXAMPLES / "eqpt_config.json").is_file()
)
_POPULATION = 1000


def _service(a: str, z: str, fmt: str = "DP-QPSK", uuid: str | None = None) -> ConnectivityService:
    body: dict = {"end-point": [tapi_end_point("a", a, fmt), tapi_end_point("z", z, fmt)]}
    if uuid is not None:
        body["uuid"] = uuid
    return ConnectivityService.model_validate(body)


async def _admit_random(ctx: TapiContext, rng: random.Random, sips: list[str]) -> ConnectivityService:
    """Admit a lightpath between two random transceivers; retry refusals."""
    while True:
        svc = _service(*rng.sample(sips, 2))
        try:
            await ctx.add_service(svc)
        except RmsaError:
            continue
        return svc


@pytest.fixture(scope="module")
def coronet_1000(tmp_path_factory: pytest.TempPathFactory) -> TapiContext:
    """CORONET CONUS under EGN with 1000 seeded lightpaths lit.

    Filled with the neighbour re-evaluation switched off, which is the part
    the tests time: the population's own baselines are therefore stale, and
    each admission under test re-evaluates its neighbours from scratch.
    """
    if not _HAS_CORONET:
        pytest.skip("GNPy example data not provisioned (run twinlight-fetch-examples)")
    ctx = TapiContext(TwinConfig(
        gnpy=GnpyConfig(
            topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
            equipment=_EXAMPLES / "eqpt_config.json",
        ),
        physics=PhysicsConfig(backend="egn"),
        simulation={"snapshot_dir": str(tmp_path_factory.mktemp("snapshots"))},
    ))
    sips = [s.uuid for s in ctx.sips]
    rng = random.Random(7)

    async def fill() -> None:
        reevaluate = ctx._reevaluate

        async def skip(_uuids):
            return None

        ctx._reevaluate = skip  # type: ignore[method-assign]
        try:
            for _ in range(_POPULATION):
                await _admit_random(ctx, rng, sips)
        finally:
            ctx._reevaluate = reevaluate  # type: ignore[method-assign]

    asyncio.run(fill())
    assert len(ctx.get_services()) == _POPULATION
    return ctx


class TestAdmissionAtOneThousandLightpaths:
    def test_admission_is_bounded(self, coronet_1000: TapiContext) -> None:
        sips = [s.uuid for s in coronet_1000.sips]
        rng = random.Random(11)
        durations, neighbours = [], []
        for _ in range(5):
            start = time.perf_counter()
            svc = asyncio.run(_admit_random(coronet_1000, rng, sips))
            durations.append(time.perf_counter() - start)
            neighbours.append(len(coronet_1000._sharing_services(svc.uuid)))
        assert max(neighbours) > 50  # a loaded line: many neighbours re-evaluated
        # 0.04-0.4 s on a laptop, against a median of 3.2 s before; the bound
        # leaves room for a slow runner.
        assert max(durations) < 2.0, durations

    def test_the_line_load_is_not_rebuilt_per_neighbour(
        self, coronet_1000: TapiContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        builds, channels = [0], [0]
        lit_channels = coronet_1000._lit_channels
        lit_channel_type = context_module.LitChannel

        def count_builds():
            builds[0] += 1
            return lit_channels()

        def count_channels(*args, **kwargs):
            channels[0] += 1
            return lit_channel_type(*args, **kwargs)

        monkeypatch.setattr(coronet_1000, "_lit_channels", count_builds)
        monkeypatch.setattr(context_module, "LitChannel", count_channels)
        sips = [s.uuid for s in coronet_1000.sips]
        svc = asyncio.run(_admit_random(coronet_1000, random.Random(13), sips))
        assert len(coronet_1000._sharing_services(svc.uuid)) > 10
        # Once for the admission's own QoT check, once for its neighbours:
        # not once per neighbour.
        assert builds[0] == 2
        # Only the new lightpath's channel is computed; the other 1000 come
        # from the memo.
        assert channels[0] <= 1

    def test_a_deletion_re_evaluates_its_neighbours_in_the_request(
        self, coronet_1000: TapiContext,
    ) -> None:
        sips = [s.uuid for s in coronet_1000.sips]
        svc = asyncio.run(_admit_random(coronet_1000, random.Random(17), sips))
        neighbours = coronet_1000._sharing_services(svc.uuid)
        assert neighbours
        start = time.perf_counter()
        assert asyncio.run(coronet_1000.remove_service(svc.uuid))
        assert time.perf_counter() - start < 2.0
        assert neighbours <= set(coronet_1000._baseline_cache)


def _cold(ctx: TapiContext) -> None:
    """Drop every cache the line load and the baselines come from."""
    ctx._lit_channel_memo.clear()
    ctx._backend._route_bands.clear()
    ctx._backend._route_lit_fibers.clear()


def _assert_caches_match_a_cold_recompute(ctx: TapiContext) -> None:
    warm_load = ctx._lit_channels()
    warm = {
        uuid: asyncio.run(ctx.get_or_compute_baseline(svc))
        for uuid, svc in ((s.uuid, s) for s in ctx.get_services())
    }
    _cold(ctx)
    assert ctx._lit_channels() == warm_load
    for uuid in warm:
        ctx.invalidate_baseline(uuid)
    cold = {
        uuid: asyncio.run(ctx.get_or_compute_baseline(svc))
        for uuid, svc in ((s.uuid, s) for s in ctx.get_services())
    }
    assert cold == warm


class TestCachesNeverGoStale:
    """Every cache behind the line load, after each kind of change, gives
    what a cold recomputation gives."""

    def test_through_admissions_replacements_deletions_and_cuts(self, tmp_path: Path) -> None:
        ctx = TapiContext(_mesh_config(tmp_path, "egn"))
        refs = MeshRefs(ctx)
        a, c = refs.sip("trx A"), refs.sip("trx C")
        services = []
        for _ in range(4):
            svc = _service(a, c)
            asyncio.run(ctx.add_service(svc))
            services.append(svc)
        _assert_caches_match_a_cold_recompute(ctx)

        # A replacement with another format: a new service object, a new
        # allocation, another symbol rate.
        asyncio.run(ctx.replace_service(_service(a, c, "DP-16QAM", uuid=services[1].uuid)))
        _assert_caches_match_a_cold_recompute(ctx)

        assert asyncio.run(ctx.remove_service(services[2].uuid))
        _assert_caches_match_a_cold_recompute(ctx)

        path = ctx._allocated_path_uids(services[0].uuid)
        fiber = next(uid for uid in path if ctx._backend.is_fiber(uid))
        ctx.set_link_failed(fiber, True)
        _assert_caches_match_a_cold_recompute(ctx)
        ctx.set_link_failed(fiber, False)
        _assert_caches_match_a_cold_recompute(ctx)


class TestNeighboursNeverLag:
    def test_a_replacement_re_evaluates_the_route_it_left(self, tmp_path: Path) -> None:
        ctx = TapiContext(_mesh_config(tmp_path, "egn"))
        refs = MeshRefs(ctx)
        a, c = refs.sip("trx A"), refs.sip("trx C")
        stay = _service(a, c)
        asyncio.run(ctx.add_service(stay))
        alone = asyncio.run(ctx.get_or_compute_baseline(stay)).gsnr_db
        moved = _service(a, c)
        asyncio.run(ctx.add_service(moved))
        assert asyncio.run(ctx.get_or_compute_baseline(stay)).gsnr_db < alone

        # Move the second lightpath off the shared route, through roadm D.
        request = ConnectivityService.model_validate({
            "uuid": moved.uuid,
            "end-point": [tapi_end_point("a", a), tapi_end_point("z", c)],
            "topology-constraint": [{"local-id": "1", "include-node": [refs.node("roadm D")]}],
        })
        asyncio.run(ctx.replace_service(request))
        assert "roadm D" in ctx._allocated_path_uids(moved.uuid)
        # Re-evaluated in the request: cached, and back to its lone GSNR.
        assert stay.uuid in ctx._baseline_cache
        assert ctx._baseline_cache[stay.uuid].gsnr_db == pytest.approx(alone)
