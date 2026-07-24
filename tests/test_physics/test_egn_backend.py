"""End-to-end tests for the EGN backend (M4).

Exercises EgnBackend selected via TwinConfig + driven through the
public TapiContext entry points (add_service, get_or_compute_baseline,
apply_element_overrides, set_link_failed). The kernel itself is unit-
tested separately in test_egn_kernel.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twinlight.config import GnpyConfig, PhysicsConfig, TwinConfig
from twinlight.models.common import NameAndValue
from twinlight.models.connectivity import (
    ConnectivityService,
    ConnectivityServiceEndPoint,
    SipRef,
)
from twinlight.physics.egn_backend import EgnBackend
from twinlight.physics.element_params import ParamValidationError
from twinlight.physics.modulation import ModulationFormat
from twinlight.state.context import TapiContext


FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def egn_config() -> TwinConfig:
    return TwinConfig(
        gnpy=GnpyConfig(topology=FIXTURES / "edfa_example_network.json"),
        physics=PhysicsConfig(backend="egn"),
    )


@pytest.fixture
def egn_context(egn_config: TwinConfig) -> TapiContext:
    return TapiContext(egn_config)


def _two_sips(ctx: TapiContext) -> tuple[str, str]:
    sips = list(ctx._sip_to_gnpy_uid.keys())
    return sips[0], sips[1]


def _make_service(sip_a: str, sip_z: str) -> ConnectivityService:
    return ConnectivityService(
        name=[NameAndValue(value_name="service-name", value="egn-test")],
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


class TestEgnBackendBootstrap:
    def test_backend_loaded(self, egn_context: TapiContext) -> None:
        assert isinstance(egn_context._backend, EgnBackend)
        assert egn_context._backend.available is True
        # No GNPy DiGraph under EGN — path computation uses NetworkX
        # k-shortest on the format-only topo_graph instead.
        assert egn_context._backend.network is None

    def test_uid_map_covers_fibers_and_edfas(
        self, egn_context: TapiContext
    ) -> None:
        kinds = {
            type(el).__name__ for el in egn_context._backend.uid_map.values()
        }
        # All stand-ins; the kind itself is on ``.type``.
        assert kinds == {"_UidStub"}
        types = {
            getattr(el, "type", None)
            for el in egn_context._backend.uid_map.values()
        }
        assert {"Fiber", "Edfa", "Roadm", "Transceiver"} <= types


class TestEgnBackendBaseline:
    @pytest.mark.asyncio
    async def test_baseline_is_finite_and_nonzero(
        self, egn_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(egn_context)
        svc = _make_service(sip_a, sip_z)
        await egn_context.add_service(svc)
        baseline = await egn_context.get_or_compute_baseline(svc)
        assert baseline is not None
        assert baseline.status is None
        # GSNR on an 80 km SSMF span with typical NF should land
        # somewhere in the 20–40 dB range.
        assert 15.0 < baseline.gsnr_db < 50.0
        # OSNR(ASE-only) must be at least as good as GSNR.
        assert baseline.osnr_ase_db >= baseline.gsnr_db - 0.5

    @pytest.mark.asyncio
    async def test_cd_pmd_latency_populated(
        self, egn_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(egn_context)
        svc = _make_service(sip_a, sip_z)
        await egn_context.add_service(svc)
        baseline = await egn_context.get_or_compute_baseline(svc)
        assert baseline is not None
        # 80 km × 16.7 ps/(nm·km).
        assert baseline.cd_ps_nm == pytest.approx(80 * 16.7, abs=1.0)
        # √80 ≈ 8.944; with default coef 0.04 → ~0.358 ps.
        assert baseline.pmd_ps == pytest.approx(0.04 * 80 ** 0.5, abs=1e-3)
        # ~0.39 ms over 80 km of SSMF.
        assert 0.3 < baseline.latency_ms < 0.5
        assert baseline.total_fiber_km == pytest.approx(80.0)


class TestEgnBackendMutation:
    @pytest.mark.asyncio
    async def test_loss_bump_lowers_gsnr(
        self, egn_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(egn_context)
        svc = _make_service(sip_a, sip_z)
        await egn_context.add_service(svc)
        before = await egn_context.get_or_compute_baseline(svc)
        assert before is not None

        fiber_uid = next(
            uid for uid, loc
            in egn_context._backend._egn_topo.gnpy_uid_index.items()
            if loc.kind == "fiber"
        )
        egn_context.apply_element_overrides(
            {fiber_uid: {"loss_coef": 0.40}}  # double the default
        )
        after = await egn_context.get_or_compute_baseline(svc)
        assert after is not None and after.status is None
        # Doubling fiber loss roughly doubles ASE in dB-linear terms:
        # we just need a measurable drop.
        assert after.gsnr_db < before.gsnr_db - 1.0

    def test_gnpy_only_attr_rejected(
        self, egn_context: TapiContext
    ) -> None:
        # ``tilt_target`` exists on the GNPy Edfa allow-list but EGN
        # has no analogue (its Span only carries length/atten/NF).
        edfa_uid = next(
            uid for uid, loc
            in egn_context._backend._egn_topo.gnpy_uid_index.items()
            if loc.kind == "edfa"
        )
        with pytest.raises(ParamValidationError, match="EGN"):
            egn_context.apply_element_overrides(
                {edfa_uid: {"tilt_target": 0.5}}
            )

    def test_redesign_raises_not_implemented(
        self, egn_context: TapiContext
    ) -> None:
        with pytest.raises(NotImplementedError, match="EGN"):
            egn_context.apply_element_overrides({}, redesign=True)


class TestEgnBackendFailure:
    @pytest.mark.asyncio
    async def test_failed_fiber_short_circuits_baseline(
        self, egn_context: TapiContext
    ) -> None:
        sip_a, sip_z = _two_sips(egn_context)
        svc = _make_service(sip_a, sip_z)
        await egn_context.add_service(svc)

        fiber_uid = next(
            uid for uid, loc
            in egn_context._backend._egn_topo.gnpy_uid_index.items()
            if loc.kind == "fiber"
        )
        # set_link_failed lives on TapiContext; under EGN it routes
        # the failure to the backend via notify_fiber_failed.
        egn_context.set_link_failed(fiber_uid, True)

        baseline = await egn_context.get_or_compute_baseline(svc)
        assert baseline is not None
        assert baseline.status == "link-failed"
        assert fiber_uid in egn_context._backend._failed_fibers

        egn_context.set_link_failed(fiber_uid, False)
        baseline = await egn_context.get_or_compute_baseline(svc)
        assert baseline is not None
        assert baseline.status is None
        assert fiber_uid not in egn_context._backend._failed_fibers


class TestEgnPathResolution:
    """Route → span resolution (the link-based path used since amplifier
    design can rename fibers into sub-spans)."""

    def _terminals(self, ctx: TapiContext) -> list[str]:
        return [
            uid for uid, loc in ctx._backend._egn_topo.gnpy_uid_index.items()
            if loc.kind in ("roadm", "transceiver")
        ]

    def test_resolves_a_terminal_pair_to_spans(
        self, egn_context: TapiContext
    ) -> None:
        be = egn_context._backend
        link = be._egn_topo.links[0]
        # A path that just names the two terminals must resolve to that
        # link's spans — no fiber UIDs required.
        resolved = be._spans_for_path([link.source_name, link.target_name])
        assert resolved is not None
        span_keys, fiber_uids, _edfa_uids = resolved
        assert len(span_keys) == len(link.spans)
        assert fiber_uids == [s.fiber_uid for s in link.spans]

    def test_ignores_interspersed_non_terminal_uids(
        self, egn_context: TapiContext
    ) -> None:
        # Regression: a UID not in the index (e.g. a raw fiber UID that
        # design renamed) must be dropped, not treated as a terminal —
        # otherwise it breaks the ROADM adjacency the link lookup needs.
        be = egn_context._backend
        link = be._egn_topo.links[0]
        with_noise = [
            link.source_name,
            "fiber (some → renamed)_(1/4)",   # unknown to the designed index
            "totally-unknown-uid",
            link.target_name,
        ]
        clean = be._spans_for_path([link.source_name, link.target_name])
        noisy = be._spans_for_path(with_noise)
        assert noisy is not None and clean is not None
        assert noisy[0] == clean[0]  # same span keys despite the noise

    def test_resolves_in_both_directions(
        self, egn_context: TapiContext
    ) -> None:
        be = egn_context._backend
        link = be._egn_topo.links[0]
        forward = be._spans_for_path([link.source_name, link.target_name])
        reverse = be._spans_for_path([link.target_name, link.source_name])
        assert forward is not None and reverse is not None

    def test_no_known_terminals_returns_none(
        self, egn_context: TapiContext
    ) -> None:
        assert egn_context._backend._spans_for_path(["nope", "also-nope"]) is None


# The amplifier-design branch needs a GNPy equipment library. That data is
# not redistributed (see examples/gnpy-data/README.md), so this is skipped
# wherever it has not been provisioned — e.g. a clean CI checkout.
_EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "gnpy-data"
_HAS_EXAMPLE_DATA = (
    (_EXAMPLES / "CORONET_CONUS_Topology.json").is_file()
    and (_EXAMPLES / "eqpt_config.json").is_file()
)


@pytest.mark.skipif(
    not _HAS_EXAMPLE_DATA,
    reason="GNPy example data not provisioned (run twinlight-fetch-examples)",
)
class TestEgnAmplifierDesign:
    def _designed_backend(self) -> EgnBackend:
        cfg = TwinConfig(
            gnpy=GnpyConfig(
                topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
                equipment=_EXAMPLES / "eqpt_config.json",
                no_insert_edfas=True,
            ),
            physics=PhysicsConfig(backend="egn"),
        )
        return EgnBackend(cfg)

    def test_design_splits_spans_and_inserts_edfas(self) -> None:
        # CORONET ships bare fiber spans; after design every span must end
        # in an EDFA, and long fibers must be split so no span is absurdly
        # long (which would make the per-span-amplified GN model diverge).
        be = self._designed_backend()
        spans = [s for lnk in be._egn_topo.links for s in lnk.spans]
        assert len(spans) > 198  # more sub-spans than raw fibers
        assert all(s.edfa_uid is not None for s in spans)
        assert max(s.length_km for s in spans) < 200.0

    @pytest.mark.asyncio
    async def test_designed_gsnr_is_physical(self) -> None:
        # With the amplifier design applied, a continental path must land
        # in a plausible GSNR range — not the tens-of-dB-negative figure a
        # single un-split 2000 km span would produce.
        ctx = TapiContext(
            TwinConfig(
                gnpy=GnpyConfig(
                    topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
                    equipment=_EXAMPLES / "eqpt_config.json",
                    no_insert_edfas=True,
                ),
                physics=PhysicsConfig(backend="egn"),
            )
        )
        sips = list(ctx._sip_to_gnpy_uid.keys())
        # Try pairs until one yields a multi-span route.
        for a, z in ((sips[0], sips[i]) for i in range(1, len(sips))):
            path = ctx.get_service_path(a, z)
            if not path:
                continue
            baseline = await ctx._backend.compute_baseline(path, "DP-QPSK")
            if baseline is not None and baseline.total_fiber_km > 500:
                assert -5.0 < baseline.gsnr_db < 30.0
                return
        pytest.skip("no multi-span route found in the topology")
