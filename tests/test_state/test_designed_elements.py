"""Both backends describe the same designed elements.

GNPy's ``designed_network()`` splits long fibers and inserts boosters and
preamplifiers. Both backends propagate over that design; the EGN backend
routes over the parsed topology, which has none of those elements. The link
index (``link_elements``, ``element_terminals``), the transparent
controller's ``/LinkList`` and the T-API physical inventory must still list
the designed elements under both, and a cut written to a designed fiber must
cut the route, as it does under GNPy.
"""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from tests.conftest import tapi_end_point
from twinlight.algorithms.path_constraints import parse_constraints
from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
from twinlight.models.common import OperationalState
from twinlight.models.connectivity import ConnectivityService
from twinlight.state.context import TapiContext
from twinlight.transparent.path import parse_path
from twinlight.transparent.tree import TreeBuilder, resolve

_EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "gnpy-data"

pytestmark = pytest.mark.skipif(
    not (
        (_EXAMPLES / "CORONET_CONUS_Topology.json").is_file()
        and (_EXAMPLES / "eqpt_config.json").is_file()
    ),
    reason="GNPy example data not provisioned (run twinlight-fetch-examples)",
)


def _coronet(backend: str, tmp: Path) -> TapiContext:
    return TapiContext(TwinConfig(
        gnpy=GnpyConfig(
            topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
            equipment=_EXAMPLES / "eqpt_config.json",
        ),
        physics=PhysicsConfig(backend=backend),
        simulation={"snapshot_dir": str(tmp / backend)},
    ))


@pytest.fixture(scope="module")
def coronet(tmp_path_factory: pytest.TempPathFactory) -> dict[str, TapiContext]:
    """Read-only CORONET CONUS contexts, one per backend."""
    tmp = tmp_path_factory.mktemp("coronet")
    return {backend: _coronet(backend, tmp) for backend in ("gnpy", "egn")}


@pytest.fixture
def egn(tmp_path: Path) -> TapiContext:
    """A CORONET context under EGN that a test may change."""
    return _coronet("egn", tmp_path)


def _split_link(ctx: TapiContext) -> tuple[tuple[str, str], list[str]]:
    """A link whose fiber the design split: (terminals, designed fibers)."""
    for pair, uids in sorted(ctx.link_elements().items()):
        fibers = [uid for uid in uids if ctx._backend.is_fiber(uid)]
        if len(fibers) >= 2 and all(uid not in ctx._topo_graph.graph for uid in fibers):
            return pair, fibers
    raise AssertionError("no split link on CORONET")


def _uses(path: list[str], pair: tuple[str, str]) -> bool:
    terminals = [uid for uid in path if uid.startswith(("roadm ", "trx "))]
    return pair in set(itertools.pairwise(terminals))


class TestSameElementsUnderBothBackends:
    def test_link_elements_are_identical(self, coronet: dict[str, TapiContext]) -> None:
        gnpy, egn = coronet["gnpy"].link_elements(), coronet["egn"].link_elements()
        assert len(gnpy) == 198
        assert egn == gnpy

    def test_element_terminals_are_identical(self, coronet: dict[str, TapiContext]) -> None:
        assert coronet["egn"]._element_terminals == coronet["gnpy"]._element_terminals

    def test_designed_amplifiers_resolve_under_egn(self, coronet: dict[str, TapiContext]) -> None:
        ctx = coronet["egn"]
        path = '/LinkList/"roadm Buffalo"/"roadm Rochester"/ElementList/*/$Edfa/nf0'
        nodes = resolve(TreeBuilder(ctx).root(), parse_path(path))
        owners = {node.key.owner for node in nodes}
        assert owners == {
            uid for uid in ctx.link_elements()[("roadm Buffalo", "roadm Rochester")]
            if uid.startswith("Edfa_")
        }
        assert len(owners) == 2  # the booster and the preamplifier

    def test_link_and_fiber_lengths_are_identical(self, coronet: dict[str, TapiContext]) -> None:
        pair, fibers = _split_link(coronet["egn"])
        link = '/LinkList/"{}"/"{}"'.format(*pair)

        def value(ctx: TapiContext, path: str) -> float:
            (node,) = resolve(TreeBuilder(ctx).root(), parse_path(path))
            return asyncio.run(node.read())

        gnpy_km = value(coronet["gnpy"], f"{link}/length_km")
        assert gnpy_km > 0
        assert value(coronet["egn"], f"{link}/length_km") == pytest.approx(gnpy_km)
        for fiber in fibers:
            path = f'{link}/ElementList/"{fiber}"/length_km'
            assert value(coronet["egn"], path) == pytest.approx(value(coronet["gnpy"], path))

    def test_physical_inventory_is_identical(self, coronet: dict[str, TapiContext]) -> None:
        gnpy, egn = (
            coronet[b]._build_physical_context().model_dump(by_alias=True, mode="json")
            for b in ("gnpy", "egn")
        )
        assert len(gnpy["device"]) == 1218
        assert len(gnpy["physical-span"]) == 870
        assert egn == gnpy

    def test_every_designed_fiber_backs_its_tapi_link(self, coronet: dict[str, TapiContext]) -> None:
        for ctx in coronet.values():
            fibers = {
                uid for uids in ctx.link_elements().values() for uid in uids
                if ctx._backend.is_fiber(uid)
            }
            assert set(ctx._fiber_to_link_refs) == fibers


class TestRouteConstraintsOverSplitLinksUnderEgn:
    def test_exclude_link_avoids_a_split_link(self, coronet: dict[str, TapiContext]) -> None:
        ctx = coronet["egn"]
        pair, _fibers = _split_link(ctx)
        # A request whose best route crosses that link: its own terminals'
        # transceivers.
        src, dst = (f"trx {uid.removeprefix('roadm ')}" for uid in pair)
        (best,) = ctx.candidate_paths(src, dst)
        assert _uses(best, pair)
        topo, link = ctx._gnpy_pair_to_link_ref[pair]
        constraints = parse_constraints(None, [{"exclude-link": [
            {"topology-uuid": topo, "link-uuid": link},
        ]}])
        routes = ctx.candidate_paths(src, dst, constraints, k=3)
        assert routes, "the exclusion left no route"
        assert not any(_uses(route, pair) for route in routes)

    def test_include_link_takes_a_split_link(self, coronet: dict[str, TapiContext]) -> None:
        ctx = coronet["egn"]
        pair, _fibers = _split_link(ctx)
        topo, link = ctx._gnpy_pair_to_link_ref[pair]
        constraints = parse_constraints(None, [{"include-link": [
            {"topology-uuid": topo, "link-uuid": link},
        ]}])
        (route,) = ctx.candidate_paths("trx Seattle", "trx Miami", constraints)
        assert _uses(route, pair)


class TestCuttingADesignedFiberUnderEgn:
    def _route_over(self, ctx: TapiContext, pair: tuple[str, str]) -> tuple[str, str]:
        src, dst = (f"trx {uid.removeprefix('roadm ')}" for uid in pair)
        (best,) = ctx.candidate_paths(src, dst)
        assert _uses(best, pair)
        return src, dst

    def test_the_route_avoids_the_cut_link(self, egn: TapiContext) -> None:
        pair, fibers = _split_link(egn)
        src, dst = self._route_over(egn, pair)
        egn.set_link_failed(fibers[0], True)
        assert not any(_uses(route, pair) for route in egn.candidate_paths(src, dst, k=3))
        egn.set_link_failed(fibers[0], False)
        assert _uses(egn.candidate_paths(src, dst)[0], pair)

    def test_the_link_stays_cut_while_another_sub_span_is(self, egn: TapiContext) -> None:
        pair, fibers = _split_link(egn)
        src, dst = self._route_over(egn, pair)
        egn.set_link_failed(fibers[0], True)
        egn.set_link_failed(fibers[1], True)
        egn.set_link_failed(fibers[0], False)
        assert not any(_uses(route, pair) for route in egn.candidate_paths(src, dst, k=3))
        egn.set_link_failed(fibers[1], False)
        assert _uses(egn.candidate_paths(src, dst)[0], pair)

    def test_the_tapi_link_is_disabled(self, egn: TapiContext) -> None:
        pair, fibers = _split_link(egn)
        topo, link = egn._gnpy_pair_to_link_ref[pair]
        egn.set_link_failed(fibers[-1], True)
        assert egn.get_link(topo, link).operational_state is OperationalState.DISABLED
        egn.set_link_failed(fibers[-1], False)
        assert egn.get_link(topo, link).operational_state is OperationalState.ENABLED

    def test_a_lightpath_over_it_has_no_signal(self, egn: TapiContext) -> None:
        pair, fibers = _split_link(egn)
        src, dst = self._route_over(egn, pair)
        sip = {egn.get_gnpy_uid(s.uuid): s.uuid for s in egn.sips}
        svc = ConnectivityService.model_validate({"end-point": [
            tapi_end_point("a", sip[src]), tapi_end_point("z", sip[dst]),
        ]})
        asyncio.run(egn.add_service(svc))
        path = egn._allocated_path_uids(svc.uuid)
        assert all(uid not in path for uid in fibers)  # the route names the parsed fiber
        # The rule OPM and the QoT what-if share sees the designed fiber.
        assert egn._no_signal_status(svc.uuid, path, {fibers[1]}) == "link-failed"
        egn.set_link_failed(fibers[1], True)
        baseline = asyncio.run(egn.get_or_compute_baseline(svc))
        assert baseline is not None and baseline.status == "link-failed"
