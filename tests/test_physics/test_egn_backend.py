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
    """Route → link resolution (by terminals, since amplifier design can
    rename fibers into sub-spans)."""

    def _terminals(self, ctx: TapiContext) -> list[str]:
        return [
            uid for uid, loc in ctx._backend._egn_topo.gnpy_uid_index.items()
            if loc.kind in ("roadm", "transceiver")
        ]

    def test_resolves_a_terminal_pair_to_its_link(
        self, egn_context: TapiContext
    ) -> None:
        be = egn_context._backend
        link = be._egn_topo.links[0]
        # A path that just names the two terminals must resolve to that
        # link — no fiber UIDs required.
        assert be._links_for_path([link.source_name, link.target_name]) == [link.id]
        plan = be._link_plan(link.id)
        assert [f.uid for f in plan.fibers] == [s.fiber_uid for s in link.spans]

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
        clean = be._links_for_path([link.source_name, link.target_name])
        noisy = be._links_for_path(with_noise)
        assert noisy is not None and clean is not None
        assert noisy == clean  # same links despite the noise

    def test_resolves_in_both_directions(
        self, egn_context: TapiContext
    ) -> None:
        be = egn_context._backend
        link = be._egn_topo.links[0]
        forward = be._links_for_path([link.source_name, link.target_name])
        reverse = be._links_for_path([link.target_name, link.source_name])
        assert forward is not None and reverse is not None

    def test_no_known_terminals_returns_none(
        self, egn_context: TapiContext
    ) -> None:
        assert egn_context._backend._links_for_path(["nope", "also-nope"]) is None


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
            baseline = await ctx._backend.compute_baseline(
                path, "DP-QPSK", centre_frequency_hz=193.1e12, media_channel_width_hz=56.25e9,
                launch_power_dbm=ctx.default_launch_power_dbm,
            )
            if baseline is not None and baseline.total_fiber_km > 500:
                assert -5.0 < baseline.gsnr_db < 30.0
                return
        pytest.skip("no multi-span route found in the topology")

    def test_long_routes_agree_with_gnpy(self) -> None:
        """Over continental routes, with the SI comb lit, EGN stays within
        0.25 dB of GNPy. The residual is GNPy's frequency dependence (gain
        and NF ripple, dispersion slope) that EGN evaluates at one frequency."""
        import asyncio

        def context(backend: str) -> TapiContext:
            return TapiContext(TwinConfig(
                gnpy=GnpyConfig(
                    topology=_EXAMPLES / "CORONET_CONUS_Topology.json",
                    equipment=_EXAMPLES / "eqpt_config.json",
                    no_insert_edfas=True,
                ),
                physics=PhysicsConfig(backend=backend),
            ))

        gnpy, egn = context("gnpy"), context("egn")
        channel = {"centre_frequency_hz": 193.1e12, "media_channel_width_hz": 56.25e9,
                   "launch_power_dbm": 0.0}
        for src, dst in (("trx Abilene", "trx Baltimore"), ("trx Albany", "trx Long_Island")):
            (g_path,), (e_path,) = gnpy.candidate_paths(src, dst), egn.candidate_paths(src, dst)
            ref = asyncio.run(gnpy._backend.compute_baseline(g_path, "DP-QPSK", **channel))
            ours = asyncio.run(egn._backend.compute_baseline(
                e_path, "DP-QPSK", **channel,
                line_load=TestAgreementWithGnpy._si_load(egn, e_path, 193.1e12, 0.0),
            ))
            assert ref is not None and ours is not None
            assert ours.gsnr_db == pytest.approx(ref.gsnr_db, abs=0.25)
            assert ours.osnr_ase_db == pytest.approx(ref.osnr_ase_db, abs=0.25)


class TestAgreementWithGnpy:
    """Given the load GNPy assumes — the SI comb fully lit — the EGN backend
    reproduces GNPy's own propagation: the same amplifiers (boosters
    included) at the same operating points and noise figures, the same
    fiber losses, and the same GN-model NLI (gn_model_analytic)."""

    @staticmethod
    def _contexts(tmp_path) -> tuple[TapiContext, TapiContext]:
        from tests.conftest import _mesh_config

        return (TapiContext(_mesh_config(tmp_path, "gnpy")),
                TapiContext(_mesh_config(tmp_path, "egn")))

    @staticmethod
    def _si_load(ctx: TapiContext, path: list[str], centre_hz: float, power_dbm: float):
        from twinlight.physics.backend import LitChannel
        from twinlight.physics.channel_plan import line_comb

        comb = line_comb(
            ctx._backend.spectral_defaults, centre_frequency_hz=centre_hz,
            media_channel_width_hz=56.25e9, baud_rate_hz=32e9, launch_power_dbm=power_dbm,
            bands=ctx._backend.amplifier_bands(path),
        )
        return [LitChannel(tuple(path), float(comb.frequency_hz[i]),
                           float(comb.baud_rate_hz[i]), float(comb.launch_power_dbm[i]))
                for i in comb.neighbours]

    @pytest.mark.parametrize(("src", "dst", "centre_hz", "power_dbm"), [
        ("trx A", "trx B", 193.1e12, 0.0),
        ("trx A", "trx C", 193.1e12, 0.0),
        ("trx A", "trx C", 191.5e12, 0.0),
        ("trx A", "trx C", 193.1e12, 3.0),
    ])
    def test_fully_loaded_line_matches(self, tmp_path, src, dst, centre_hz, power_dbm) -> None:
        import asyncio

        gnpy, egn = self._contexts(tmp_path)
        channel = {"centre_frequency_hz": centre_hz, "media_channel_width_hz": 56.25e9,
                   "launch_power_dbm": power_dbm}
        (g_path,), (e_path,) = gnpy.candidate_paths(src, dst), egn.candidate_paths(src, dst)
        ref = asyncio.run(gnpy._backend.compute_baseline(g_path, "DP-QPSK", **channel))
        ours = asyncio.run(egn._backend.compute_baseline(
            e_path, "DP-QPSK", **channel,
            line_load=self._si_load(egn, e_path, centre_hz, power_dbm),
        ))
        assert ref is not None and ours is not None
        assert ours.osnr_ase_db == pytest.approx(ref.osnr_ase_db, abs=0.05)
        assert ours.gsnr_db == pytest.approx(ref.gsnr_db, abs=0.05)
        assert ours.edfa_uids == ref.edfa_uids

    @pytest.mark.parametrize(("src", "dst"), [("trx A", "trx B"), ("trx A", "trx C")])
    def test_pdl_hinges_match(self, tmp_path, src, dst) -> None:
        """The time-varying layer sees the same PDL hinges under both
        backends: every ROADM and EDFA on the route, in propagation order.
        The ROADMs carry the larger PDL, so dropping them would make the PDL
        penalty depend on the QoT engine rather than on the line."""
        import asyncio

        gnpy, egn = self._contexts(tmp_path)
        channel = {"centre_frequency_hz": 193.1e12, "media_channel_width_hz": 56.25e9,
                   "launch_power_dbm": 0.0}
        (g_path,), (e_path,) = gnpy.candidate_paths(src, dst), egn.candidate_paths(src, dst)
        ref = asyncio.run(gnpy._backend.compute_baseline(g_path, "DP-QPSK", **channel))
        ours = asyncio.run(egn._backend.compute_baseline(e_path, "DP-QPSK", **channel))
        assert ref is not None and ours is not None
        assert any(kind == "Roadm" for _, kind in ref.pdl_elements)
        assert ours.pdl_elements == ref.pdl_elements

    def test_noise_figure_is_gnpys_at_the_operating_gain(self, tmp_path) -> None:
        """EGN reports (and propagates) the NF GNPy's amplifier has after a
        propagation, not a figure averaged from the type's datasheet."""
        import numpy as np
        from gnpy.core.info import create_input_spectral_information

        gnpy, egn = self._contexts(tmp_path)
        booster = "Edfa_booster_roadm A_to_fiber (A → B)"
        preamp = "Edfa_preamp_roadm B_from_fiber (A → B)"
        # The SI comb as ROADM A hands it to the booster (its -20 dBm per
        # channel target), through booster, fiber and preamplifier.
        si = create_input_spectral_information(
            f_min=191.35e12, f_max=195.1e12, roll_off=0.15, baud_rate=32e9,
            spacing=50e9, tx_osnr=100.0, tx_power=1e-5,
        )
        for uid in (booster, "fiber (A → B)", preamp):
            si = gnpy._backend.uid_map[uid](si)
        for uid in (booster, preamp):
            expected = float(np.mean(gnpy._backend.uid_map[uid].nf))
            assert egn._backend.read_attribute(uid, "nf0") == pytest.approx(expected, abs=0.05)


class TestDesignedLinks:
    """The loader keeps what GNPy's design adds: the booster and every
    amplifier's operating point, and the fibers' lumped losses."""

    def test_booster_and_operating_points(self, tmp_path) -> None:
        from tests.conftest import _mesh_config
        from twinlight.loader.egn_topology import BOOSTER_INDEX

        be = TapiContext(_mesh_config(tmp_path, "egn"))._backend
        (link_id,) = be._links_for_path(["roadm A", "roadm B"])
        link = be._egn_topo.links[link_id]
        assert link.booster is not None
        assert link.booster.uid == "Edfa_booster_roadm A_to_fiber (A → B)"
        assert link.booster.gain_target_db is not None and link.booster.delta_p_db is not None
        index = be._egn_topo.gnpy_uid_index[link.booster.uid]
        assert (index.kind, index.link_id, index.span_index) == ("edfa", link_id, BOOSTER_INDEX)
        (span,) = link.spans
        assert (span.con_in_db, span.con_out_db) == (0.5, 0.5)
        assert span.amplifier is not None and span.amplifier.delta_p_db is not None

    def test_plan_follows_the_power_budget(self, tmp_path) -> None:
        """Booster output at reference + delta_p, the fiber launch after
        con_in, and the preamp input after the whole span loss."""
        from tests.conftest import _mesh_config

        be = TapiContext(_mesh_config(tmp_path, "egn"))._backend
        (link_id,) = be._links_for_path(["roadm A", "roadm B"])
        link, plan = be._egn_topo.links[link_id], be._link_plan(link_id)
        reference = be.spectral_defaults.power_dbm
        assert link.booster is not None and link.booster.delta_p_db is not None
        booster_out = reference + link.booster.delta_p_db
        (span,) = link.spans
        (fiber,) = plan.fibers
        assert fiber.input_dbm == pytest.approx(booster_out - span.con_in_db)
        booster, preamp = plan.amplifiers
        assert booster.gain_db == pytest.approx(link.booster.gain_target_db)
        span_loss = span.con_in_db + span.attenuation_db_per_km * span.length_km + span.con_out_db
        assert preamp.input_dbm == pytest.approx(booster_out - span_loss)
