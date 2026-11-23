"""EGN/GN-model implementation of ``PhysicalBackend``.

Selected at startup via ``physics.backend: egn`` (YAML) or
``--physics-backend egn`` (CLI). The propagation model is a clean-room
implementation of the closed-form Gaussian-Noise expressions (see
``physics/egn_kernel.py`` for citations); the network is sourced from
the same GNPy JSON file that the GNPy backend uses, translated to an
EGN-shaped (link, span) view via ``loader/egn_topology.py``.

How a channel is evaluated
--------------------------
Each link (terminal to terminal) is walked once for a *reference channel*
at the equipment library's SI power: the booster after the source ROADM,
then each fiber — connector and padding losses included — and the
amplifier after it. Amplifiers hold the operating point GNPy's design gave
them (power mode: output at the reference power plus ``delta_p``; gain
mode: ``gain_target``), and their noise figure is GNPy's own NF model at
that gain. That gives the reference channel's power at every amplifier
input and every fiber input (``_LinkPlan``).

A channel launched at ``P`` sits ``P − SI power_dbm`` above the reference
everywhere — amplifiers and ROADMs keep that offset, as GNPy's
``delta_pdb`` — so its ASE and NLI follow from the plan without walking the
link again. Its neighbours are the *admitted lightpaths*: each one adds
cross-channel NLI in exactly the fibers both paths traverse, at its own
power and frequency. The line is therefore as loaded as the network is,
which is the difference from the GNPy backend (fully loaded SI comb).

This backend ships zero runtime dependency on
``optical-networking-gym`` — the EGN library is a *code reference*
under GPL-3.0, not an import.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twinlight.config import TwinConfig
from twinlight.loader.egn_topology import (
    DEFAULT_NF_DB,
    EgnAmplifierData,
    EgnNodeKind,
    EgnSpanData,
    EgnTopologyData,
)
from twinlight.loader.egn_topology import (
    convert as convert_to_egn,
)
from twinlight.loader.gnpy_topology import (
    GnpyTopology,
    load_gnpy_topology,
    parse_gnpy_topology_dict,
)
from twinlight.physics.analytical_metrics import (
    chromatic_dispersion_ps_per_nm,
    latency_ms,
    pmd_ps,
)
from twinlight.physics.backend import LitChannel
from twinlight.physics.channel_plan import (
    common_band,
    load_spectral_defaults,
)
from twinlight.physics.egn_kernel import (
    DEFAULT_BETA_2_S2_PER_M,
    DEFAULT_GAMMA_PER_W_PER_M,
    FiberSpan,
    Neighbour,
    NoiseBudget,
    ase_power,
    cross_channel_nli_power,
    db_per_km_to_neper_per_m,
    dbm_to_w,
    nf_db_to_linear,
    self_channel_nli_power,
)
from twinlight.physics.element_params import (
    ParamValidationError as _ParamValidationError,
)
from twinlight.physics.gnpy_adapter import OpmBaseline
from twinlight.physics.modulation import get_params

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Amplifier placement and equipment models
# ---------------------------------------------------------------------------

# EDFA UID → the bands it passes (``Edfa.params.bands`` of the designed network).
EdfaBands = dict[str, list[dict[str, Any]]]
# Noise figure [dB] of one amplifier at (gain [dB], per-channel input [dBm]).
NoiseFigureModel = Callable[[float, float], float]


@dataclass(frozen=True)
class EquipmentModels:
    """What the equipment library adds to the topology JSON.

    Empty when no library is configured: amplifiers then use the noise
    figure their JSON gives and fibers the standard SSMF nonlinearity.
    """

    edfa_bands: EdfaBands
    noise_figure: dict[str, NoiseFigureModel]
    # Fiber UID → (γ [1/W/m], |β₂| [s²/m]) at the fiber's reference frequency.
    nonlinearity: dict[str, tuple[float, float]]


_NO_MODELS = EquipmentModels(edfa_bands={}, noise_figure={}, nonlinearity={})


def _load_topology_with_amplifiers(config: TwinConfig) -> tuple[GnpyTopology, EquipmentModels]:
    """Parse the topology, applying GNPy's amplifier design when possible.

    The GN model needs every span to end in an amplifier: hand it a single
    un-split 336 km span and it charges one amplifier with ~67 dB of gain,
    putting GSNR tens of dB below anything physical.

    GNPy solves exactly this in ``designed_network()``, which splits long
    fibers into sub-spans, inserts boosters and preamplifiers, and sets
    every amplifier's operating point. gnpy is a hard dependency of this
    project regardless of the selected backend, so we reuse that result
    rather than reimplementing it: the designed network is serialised back
    to GNPy JSON — the same schema this loader already reads — so the EGN
    converter consumes it unchanged and both backends end up with identical
    amplifier placement and operating points.

    Design needs an equipment library. Without one (``gnpy.equipment``
    unset) the topology is used exactly as written, which stays correct
    for topologies that already carry their own amplifiers and keeps the
    EGN backend usable with no equipment file at all.

    Also returns the equipment models of the designed elements (bands,
    noise-figure models, fiber nonlinearity); empty when the topology was
    not designed.
    """
    if config.gnpy.equipment is None:
        logger.info(
            "EGN backend: no gnpy.equipment configured — using the topology as "
            "written. Bare fiber spans will each be modelled as one amplified "
            "span, which understates GSNR on long spans."
        )
        return load_gnpy_topology(config.gnpy.topology), _NO_MODELS

    try:
        designed, models = _designed_topology_json(
            config.gnpy.topology, config.gnpy.equipment
        )
    except Exception:
        # A design failure must never take the backend down: the raw
        # topology still yields a usable, if pessimistic, model.
        logger.warning(
            "EGN backend: GNPy amplifier design failed — falling back to the "
            "topology as written. QoT on bare-fiber spans will be pessimistic.",
            exc_info=True,
        )
        return load_gnpy_topology(config.gnpy.topology), _NO_MODELS

    topo = parse_gnpy_topology_dict(designed, name=config.gnpy.topology.stem)
    logger.info(
        "EGN backend: applied GNPy amplifier design (%d elements)",
        len(topo.elements),
    )
    return topo, models


def _designed_topology_json(
    topology_path: Path, equipment_path: Path,
) -> tuple[dict, EquipmentModels]:
    """Return the GNPy-designed network, serialised back to GNPy JSON, and
    the equipment models of its elements (which the JSON does not carry)."""
    from gnpy.tools.json_io import load_equipment, network_from_json, network_to_json
    from gnpy.tools.worker_utils import designed_network

    equipment = load_equipment(str(equipment_path))
    # network_from_json mutates the document it is handed (it pops keys
    # while building elements), so give it a freshly parsed copy.
    network = network_from_json(json.loads(topology_path.read_text()), equipment)
    network, _ref_req, _ref_chan = designed_network(equipment, network)
    result: dict = network_to_json(network)
    edfas = [el for el in network.nodes() if type(el).__name__ == "Edfa"]
    fibers = [el for el in network.nodes() if type(el).__name__ == "Fiber"]
    models = EquipmentModels(
        edfa_bands={el.uid: list(el.params.bands) for el in edfas},
        noise_figure={el.uid: _gnpy_noise_figure(el) for el in edfas},
        nonlinearity={
            el.uid: (
                float(el.gamma(el.params.ref_frequency)),
                abs(float(el.beta2(el.params.ref_frequency))),
            )
            for el in fibers
        },
    )
    return result, models


def _gnpy_noise_figure(edfa: Any) -> NoiseFigureModel:
    """GNPy's noise-figure model of one designed amplifier.

    ``Edfa._calc_nf(avg=True)`` is the per-type model GNPy itself uses when
    it propagates (variable gain, fixed gain, polynomial fit, dual stage,
    the OpenROADM masks). It reads the operating gain from
    ``effective_gain`` and, for the OpenROADM types, the input power, so
    those are set before each call; the element is private to this
    backend. Without a ripple: the NF is the band average.
    """

    def noise_figure_db(gain_db: float, input_dbm: float) -> float:
        edfa.effective_gain = gain_db
        # The OpenROADM masks read the per-channel input power of a
        # 50 GHz-slot channel: one such channel at the given power.
        edfa.pin_db = input_dbm
        edfa.nch = 1
        edfa.slot_width = 50e9
        return float(edfa._calc_nf(avg=True))

    return noise_figure_db


# Per-attribute writable contract — mirrors physics/element_params.py
# with the subset the GN model reads: a fiber's attenuation and its input
# padding, an amplifier's noise figure. ROADM knobs and EDFA gain/tilt/VOA
# have no EGN-side state to write, so /config rejects them here.
_EGN_FIBER_ATTRS = ("loss_coef", "att_in")
_EGN_EDFA_ATTRS = ("nf0",)


# ---------------------------------------------------------------------------
# Link plans: the reference channel's power along a link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AmplifierStage:
    """One amplifier as the reference channel sees it."""

    uid: str | None            # None: the implicit amplifier after a bare span
    input_dbm: float           # reference channel at the amplifier input
    gain_db: float
    noise_figure_db: float


@dataclass(frozen=True)
class _FiberStage:
    """One fiber as the reference channel sees it."""

    uid: str
    span: FiberSpan
    input_dbm: float           # reference channel at the fiber input
    length_km: float


@dataclass(frozen=True)
class _LinkPlan:
    amplifiers: tuple[_AmplifierStage, ...]
    fibers: tuple[_FiberStage, ...]


class EgnBackend:
    """GN-model QoT propagation + per-element /config mutation."""

    name = "egn"
    # Baselines depend on the admitted lightpaths (module docstring).
    uses_line_load = True

    def __init__(self, config: TwinConfig) -> None:
        # Parse the topology and convert to EGN's per-span view. When an
        # equipment library is available the topology is first run through
        # GNPy's design step, so both backends see the same amplifiers.
        gnpy_topo, self._models = _load_topology_with_amplifiers(config)
        # The reference channel and design launch power: the configured
        # equipment library's SI, else GNPy's bundled one (channel_plan.py).
        self.spectral_defaults = load_spectral_defaults(config.gnpy.equipment)
        self._egn_topo: EgnTopologyData = convert_to_egn(gnpy_topo)

        # Build a uid_map mirror so the PhysicalBackend Protocol's
        # ``uid_map``-based UID presence checks (used by TapiContext's
        # apply_element_overrides and services_for_element) work
        # uniformly. Values are stand-ins carrying ``.uid`` and ``.type``.
        self.uid_map: dict[str, Any] = {
            uid: _UidStub(uid=uid, type=kind.kind.title())
            for uid, kind in self._egn_topo.gnpy_uid_index.items()
        }
        # EGN does not need GNPy's DiGraph (routing falls back to the
        # TopologyGraph k-shortest path) — Protocol requires the field.
        self.network = None
        self.available: bool = True

        # Runtime writes, element UID → {attribute: value}. An attribute
        # absent here has its loaded value; the topology itself is never
        # mutated, so a revert is just a delete.
        self._overrides: dict[str, dict[str, float]] = {}
        # Failed fibers (by GNPy fiber UID). Short-circuits
        # compute_baseline to a link-failed sentinel.
        self._failed_fibers: set[str] = set()
        # link_id → plan of the reference channel; dropped when a write
        # touches an element of the link.
        self._link_plans: dict[int, _LinkPlan] = {}

        logger.info(
            "EGN backend ready: %d links, %d spans, %d boosters",
            len(self._egn_topo.links),
            sum(len(lnk.spans) for lnk in self._egn_topo.links),
            sum(lnk.booster is not None for lnk in self._egn_topo.links),
        )

    # -- QoT propagation -------------------------------------------------

    async def compute_baseline(
        self,
        path_uids: list[str],
        modulation_format: str,
        *,
        centre_frequency_hz: float,
        media_channel_width_hz: float,
        launch_power_dbm: float,
        line_load: Sequence[LitChannel] = (),
    ) -> OpmBaseline | None:
        """The channel's QoT with the lightpaths in ``line_load`` lit.

        ``media_channel_width_hz`` is not needed: neighbours are the
        admitted channels, which occupy their own blocks.
        """
        link_ids = self._links_for_path(path_uids)
        if link_ids is None:
            # No leg of the route resolved to an EGN link — skip silently
            # so OPM falls back to mock data rather than reporting the
            # "no spans" saturated GSNR as if it were a real measurement.
            return None
        plans = [self._link_plan(link_id) for link_id in link_ids]
        fiber_uids = [f.uid for plan in plans for f in plan.fibers]
        edfa_uids = [a.uid for plan in plans for a in plan.amplifiers if a.uid is not None]

        # Failure can be flagged against either the route's own fiber UIDs
        # or the (possibly design-split) span UIDs, depending on which
        # topology the caller addressed.
        failed = self._failed_fibers
        if failed and (failed.intersection(path_uids) or failed.intersection(fiber_uids)):
            return OpmBaseline(
                gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0, pmd_ps=0.0,
                latency_ms=0.0, total_fiber_km=0.0, status="link-failed",
            )

        baud_hz = get_params(modulation_format).baud_rate_hz
        # Every power along the line is the reference channel's plus this
        # offset (amplifiers and ROADMs keep it, as GNPy's delta_pdb).
        offset_db = launch_power_dbm - self.spectral_defaults.power_dbm
        neighbours = self._neighbours_by_fiber(
            line_load, set(fiber_uids), centre_frequency_hz, baud_hz,
        )

        budget = NoiseBudget()
        for plan in plans:
            for amp in plan.amplifiers:
                budget.add_ase(
                    ase_power(nf_db_to_linear(amp.noise_figure_db), baud_hz, centre_frequency_hz),
                    dbm_to_w(amp.input_dbm + offset_db),
                )
            for fiber in plan.fibers:
                cut_w = dbm_to_w(fiber.input_dbm + offset_db)
                pumps = [
                    Neighbour(offset_hz, dbm_to_w(fiber.input_dbm + their_offset_db), their_baud)
                    for offset_hz, their_offset_db, their_baud in neighbours.get(fiber.uid, ())
                ]
                budget.add_nli(
                    self_channel_nli_power(fiber.span, cut_w, baud_hz)
                    + cross_channel_nli_power(fiber.span, cut_w, baud_hz, pumps),
                    cut_w,
                )
        qot = budget.result()
        # Treat infinite GSNR (no noise source) as a saturated 60 dB so
        # the BER calculation downstream stays finite.
        total_km = sum(f.length_km for plan in plans for f in plan.fibers)
        return OpmBaseline(
            gsnr_db=min(qot.gsnr_db, 60.0),
            osnr_ase_db=min(qot.osnr_ase_db, 60.0),
            cd_ps_nm=chromatic_dispersion_ps_per_nm(total_km),
            pmd_ps=pmd_ps(total_km),
            latency_ms=latency_ms(total_km),
            total_fiber_km=total_km,
            edfa_uids=edfa_uids,
            fiber_uids=fiber_uids,
            pdl_elements=self._pdl_hinges(path_uids),
        )

    def _neighbours_by_fiber(
        self,
        line_load: Sequence[LitChannel],
        fibers: set[str],
        centre_frequency_hz: float,
        baud_hz: float,
    ) -> dict[str, list[tuple[float, float, float]]]:
        """For each fiber of the channel under test, the lit channels in it:
        ``(frequency offset [Hz], power offset from the reference [dB],
        symbol rate [Hz])``.

        A lit channel counts only in the fibers its own route traverses and
        its light reaches: up to the first failed fiber, not past it. One
        whose signal would overlap the channel under test cannot share a
        fiber with it (spectrum assignment forbids it), so it is left out.
        """
        reference_dbm = self.spectral_defaults.power_dbm
        by_route: dict[tuple[str, ...], set[str]] = {}
        result: dict[str, list[tuple[float, float, float]]] = {}
        for channel in line_load:
            offset_hz = channel.centre_frequency_hz - centre_frequency_hz
            if abs(offset_hz) < (baud_hz + channel.baud_rate_hz) / 2:
                continue
            if channel.path_uids not in by_route:
                by_route[channel.path_uids] = set(self._lit_fibers(list(channel.path_uids)))
            entry = (offset_hz, channel.launch_power_dbm - reference_dbm, channel.baud_rate_hz)
            for uid in by_route[channel.path_uids] & fibers:
                result.setdefault(uid, []).append(entry)
        return result

    def route_fibers(self, path_uids: list[str]) -> list[str]:
        """The designed spans of the route's links: the ``fiber_uids`` a
        baseline of the route reports."""
        link_ids = self._links_for_path(path_uids) or []
        return [f.uid for link_id in link_ids for f in self._link_plan(link_id).fibers]

    def _lit_fibers(self, path_uids: list[str]) -> list[str]:
        """The fibers of a route its light reaches, in order: all of them, or
        those before the first failed one (the transmitter still emits; the
        cut stops the light)."""
        index = self._egn_topo.gnpy_uid_index
        terminals = [
            uid for uid in path_uids
            if (loc := index.get(uid)) is not None and loc.kind in ("roadm", "transceiver")
        ]
        lit: list[str] = []
        position = 0  # where in path_uids the current leg starts
        for source, target in itertools.pairwise(terminals):
            end = path_uids.index(target, position)
            leg = path_uids[position:end]
            position = end
            link_id = self._egn_topo.link_by_endpoints.get((source, target))
            if link_id is None:
                continue
            fibers = [f.uid for f in self._link_plan(link_id).fibers]
            # A failure named by a route UID the design renamed (a split
            # fiber) cuts the leg it lies on, from its start.
            if self._failed_fibers.intersection(leg) - set(fibers):
                return lit
            for uid in fibers:
                if uid in self._failed_fibers:
                    return lit
                lit.append(uid)
        return lit

    def _link_plan(self, link_id: int) -> _LinkPlan:
        """Walk one link with the reference channel (module docstring)."""
        cached = self._link_plans.get(link_id)
        if cached is not None:
            return cached
        link = self._egn_topo.links[link_id]
        reference = self.spectral_defaults.power_dbm
        amplifiers: list[_AmplifierStage] = []
        fibers: list[_FiberStage] = []

        # The fiber launch level. Without a booster the first fiber is
        # launched at the reference power (the ROADM output is not modelled).
        level = reference
        if link.booster is not None:
            level, stage = self._booster(link.booster, reference)
            if stage is not None:
                amplifiers.append(stage)

        for span in link.spans:
            loss_coef = self._value(span.fiber_uid, "loss_coef", span.attenuation_db_per_km)
            att_in = self._value(span.fiber_uid, "att_in", span.att_in_db)
            fiber_in = level - span.con_in_db - att_in
            gamma, beta2 = self._models.nonlinearity.get(
                span.fiber_uid, (DEFAULT_GAMMA_PER_W_PER_M, DEFAULT_BETA_2_S2_PER_M),
            )
            fibers.append(_FiberStage(
                uid=span.fiber_uid,
                span=FiberSpan(
                    length_m=span.length_km * 1e3,
                    attenuation_neper_per_m=db_per_km_to_neper_per_m(loss_coef),
                    gamma_per_w_per_m=gamma,
                    beta2_s2_per_m=beta2,
                ),
                input_dbm=fiber_in,
                length_km=span.length_km,
            ))
            fiber_out = fiber_in - loss_coef * span.length_km - span.con_out_db
            level, stage = self._inline_amplifier(span, fiber_out, restore_dbm=level)
            amplifiers.append(stage)

        plan = _LinkPlan(amplifiers=tuple(amplifiers), fibers=tuple(fibers))
        self._link_plans[link_id] = plan
        return plan

    def _booster(
        self, amp: EgnAmplifierData, reference_dbm: float,
    ) -> tuple[float, _AmplifierStage | None]:
        """The booster's output level and stage.

        Its output is the reference power plus ``delta_p`` (power mode) or
        the reference power; its gain is the designed ``gain_target`` (the
        ROADM's output to that level). Without a gain the input is unknown,
        so the booster adds no noise.
        """
        output = reference_dbm + (amp.delta_p_db or 0.0)
        if amp.gain_target_db is None:
            return output, None
        input_dbm = output + amp.out_voa_db - amp.gain_target_db
        return output, self._stage(amp, input_dbm, amp.gain_target_db)

    def _inline_amplifier(
        self, span: EgnSpanData, input_dbm: float, *, restore_dbm: float,
    ) -> tuple[float, _AmplifierStage]:
        """The output level and stage of the amplifier after a fiber.

        Power mode holds the output at the reference plus ``delta_p``, gain
        mode applies ``gain_target``; with neither — or with no amplifier at
        all, when an implicit one with the default noise figure stands in —
        the amplifier restores the level the span lost.
        """
        amp = span.amplifier
        reference = self.spectral_defaults.power_dbm
        if amp is None:
            return restore_dbm, _AmplifierStage(
                uid=None, input_dbm=input_dbm, gain_db=restore_dbm - input_dbm,
                noise_figure_db=DEFAULT_NF_DB,
            )
        amp_in = input_dbm - amp.in_voa_db
        if amp.delta_p_db is not None:
            output = reference + amp.delta_p_db
        elif amp.gain_target_db is not None:
            output = amp_in + amp.gain_target_db - amp.out_voa_db
        else:
            output = restore_dbm
        gain = output + amp.out_voa_db - amp_in
        return output, self._stage(amp, amp_in, gain)

    def _stage(self, amp: EgnAmplifierData, input_dbm: float, gain_db: float) -> _AmplifierStage:
        """An amplifier stage with its noise figure: a /config ``nf0`` write,
        else GNPy's NF model at this gain, else the JSON's figure."""
        nf = self._overrides.get(amp.uid, {}).get("nf0")
        if nf is None:
            model = self._models.noise_figure.get(amp.uid)
            nf = amp.noise_figure_db
            if model is not None:
                try:
                    nf = model(gain_db, input_dbm)
                except Exception:  # a gap in a model must not stop the QoT
                    logger.warning("GNPy NF model failed for %s; using %.1f dB",
                                   amp.uid, nf, exc_info=True)
        return _AmplifierStage(uid=amp.uid, input_dbm=input_dbm, gain_db=gain_db,
                               noise_figure_db=nf)

    def amplifier_bands(self, path_uids: list[str]) -> list[tuple[float, float]] | None:
        """The bands common to the path's EDFAs, as GNPy computes them
        (``find_common_range``). Without a designed topology the EDFAs' bands
        are unknown and the SI comb range applies, as GNPy does for a path
        with no amplifier."""
        link_ids = self._links_for_path(path_uids) or []
        bands = [
            self._models.edfa_bands[a.uid]
            for link_id in link_ids
            for a in self._link_plan(link_id).amplifiers
            if a.uid in self._models.edfa_bands
        ]
        return common_band(bands, self.spectral_defaults)

    # -- /config plane: per-element parameter reads/writes ---------------

    def read_attribute(self, uid: str, attr: str) -> Any:
        """The value propagation uses: a write, else the loaded value; for an
        amplifier's ``nf0``, its noise figure at the current operating gain."""
        loc = self._locate(uid)
        if loc.kind == "fiber":
            if attr not in _EGN_FIBER_ATTRS:
                self._reject_unsupported("Fiber", attr, _EGN_FIBER_ATTRS)
            span = self._span(loc)
            loaded = span.attenuation_db_per_km if attr == "loss_coef" else span.att_in_db
            return self._value(uid, attr, loaded)
        if loc.kind == "edfa":
            if attr not in _EGN_EDFA_ATTRS:
                self._reject_unsupported("Edfa", attr, _EGN_EDFA_ATTRS)
            assert loc.link_id is not None
            plan = self._link_plan(loc.link_id)
            return next(a.noise_figure_db for a in plan.amplifiers if a.uid == uid)
        # ROADM / Transceiver — no writable params under EGN.
        raise _ParamValidationError(
            f"{loc.kind.title()} {uid!r}: EGN backend exposes no writable "
            f"attributes for this element type"
        )

    def write_attribute(self, uid: str, attr: str, value: Any) -> None:
        loc = self._locate(uid)
        if loc.kind not in ("fiber", "edfa"):
            raise _ParamValidationError(
                f"{loc.kind.title()} {uid!r}: EGN backend exposes no "
                f"writable attributes for this element type"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _ParamValidationError(
                f"{uid}.{attr}: expected float, got {type(value).__name__}"
            )
        numeric = float(value)
        supported = _EGN_FIBER_ATTRS if loc.kind == "fiber" else _EGN_EDFA_ATTRS
        if attr not in supported:
            self._reject_unsupported(loc.kind.title(), attr, supported)
        spec = self.supported_attributes()[loc.kind.title()][attr]
        if not spec["min"] <= numeric <= spec["max"]:
            raise _ParamValidationError(
                f"{uid}.{attr}={numeric} out of range "
                f"[{spec['min']:g}, {spec['max']:g}] {spec['unit']}"
            )
        self._set(uid, attr, numeric)

    def read_all_attributes(self, uid: str) -> dict[str, Any]:
        loc = self._locate(uid)
        if loc.kind == "fiber":
            return {attr: self.read_attribute(uid, attr) for attr in _EGN_FIBER_ATTRS}
        if loc.kind == "edfa":
            return {attr: self.read_attribute(uid, attr) for attr in _EGN_EDFA_ATTRS}
        return {}

    def capture_attribute(self, uid: str, attr: str) -> Any:
        """The written value, or None while the attribute has its loaded
        value; :meth:`restore_attribute` puts it back."""
        self.read_attribute(uid, attr)  # same KeyError / validation errors
        return self._overrides.get(uid, {}).get(attr)

    def restore_attribute(self, uid: str, attr: str, token: Any) -> None:
        self._locate(uid)
        if token is None:
            self._overrides.get(uid, {}).pop(attr, None)
            self._invalidate(uid)
        else:
            self._set(uid, attr, float(token))

    def supported_attributes(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "Fiber": {
                "loss_coef": {"type": "float", "unit": "dB/km",
                              "min": 0.0, "max": 2.0,
                              "description": "Span attenuation coefficient"},
                "att_in": {"type": "float", "unit": "dB",
                           "min": 0.0, "max": 20.0,
                           "description": "Padding attenuation at the fiber input"},
            },
            "Edfa": {
                "nf0": {"type": "float", "unit": "dB",
                        "min": 3.0, "max": 12.0,
                        "description": "Amplifier noise figure (replaces the "
                                       "equipment model's figure at the operating gain)"},
            },
            "Roadm": {},  # no writable params under EGN
        }

    def redesign(self) -> None:
        """EGN has no separate equalisation step; reject loudly so the
        /config router can convert this to a 501."""
        raise NotImplementedError(
            "redesign is not supported by the EGN backend"
        )

    # -- Element kind queries & failure hook ----------------------------

    def is_fiber(self, uid: str) -> bool:
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        return loc is not None and loc.kind == "fiber"

    def notify_fiber_failed(self, fiber_uid: str, failed: bool) -> None:
        """Idempotent flag toggle. Called by ``TapiContext.set_link_failed``
        after the routing graphs are mutated, so EGN's compute_baseline
        short-circuits to ``status="link-failed"`` for any service
        traversing a failed span. The GNPy backend gets failure for
        free from graph-edge removal — this backend has no graph, so
        it tracks the set explicitly."""
        if failed:
            self._failed_fibers.add(fiber_uid)
        else:
            self._failed_fibers.discard(fiber_uid)

    # -- Internals -------------------------------------------------------

    def _links_for_path(self, path_uids: list[str]) -> list[int] | None:
        """Resolve a route to its EGN links, in propagation order.

        Routes are computed over the topology as written, so their fiber
        UIDs do not survive GNPy's amplifier design — it splits a long
        fiber into ``…_(1/4)``-style sub-spans under new UIDs. Matching
        individual fibers would therefore silently resolve to *no* spans
        on a designed topology, and an empty span list reads as infinite
        GSNR.

        The terminals are the stable part: design never renames a ROADM
        or transceiver. So walk the route's terminals in order and look
        up each consecutive pair as an EGN link. This is correct for both
        the as-written and the designed topology, and needs no knowledge of
        GNPy's sub-span naming convention.

        Returns None when no leg of the route resolved to a link.
        """
        link_ids = [
            link_id
            for pair in itertools.pairwise(self._route_terminals(path_uids))
            # Legs with no fiber (a transceiver's access hop to its own
            # ROADM) are not EGN links; they contribute no noise.
            if (link_id := self._egn_topo.link_by_endpoints.get(pair)) is not None
        ]
        return link_ids or None

    def _route_terminals(self, path_uids: list[str]) -> list[str]:
        """The route's ROADMs and transceivers, in order."""
        index = self._egn_topo.gnpy_uid_index
        # Keep only UIDs the designed topology knows as terminals. A raw
        # fiber UID from the route is absent from the designed index; it
        # must be dropped, not defaulted to a terminal, or it would break
        # the ROADM adjacency the link lookup depends on.
        return [
            uid for uid in path_uids
            if (loc := index.get(uid)) is not None
            and loc.kind in ("roadm", "transceiver")
        ]

    def _pdl_hinges(self, path_uids: list[str]) -> list[tuple[str, str]]:
        """The route's PDL hinges in propagation order: every ROADM, and
        between two terminals the amplifiers of the link joining them.

        Hinge model of Zarkosvky & Shtaif, Opt. Lett. 45(5):1224 (2020):
        ROADMs/WSSs and EDFAs are the discrete PDL elements, the same list
        ``gnpy_adapter.compute_path_baseline`` builds from GNPy's path. The
        ROADMs carry the larger PDL (``pdl_per_roadm_db``), so leaving them
        out would understate the PDL penalty under this backend.
        """
        index = self._egn_topo.gnpy_uid_index
        hinges: list[tuple[str, str]] = []
        previous: str | None = None
        for uid in self._route_terminals(path_uids):
            if previous is not None:
                link_id = self._egn_topo.link_by_endpoints.get((previous, uid))
                if link_id is not None:
                    hinges.extend(
                        (amp.uid, "Edfa")
                        for amp in self._link_plan(link_id).amplifiers
                        if amp.uid is not None
                    )
            if index[uid].kind == "roadm":
                hinges.append((uid, "Roadm"))
            previous = uid
        return hinges

    def _locate(self, uid: str) -> EgnNodeKind:
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        if loc is None:
            raise KeyError(f"Unknown element UID: {uid!r}")
        return loc

    def _span(self, loc: EgnNodeKind) -> EgnSpanData:
        assert loc.link_id is not None and loc.span_index is not None
        return self._egn_topo.links[loc.link_id].spans[loc.span_index]

    def _value(self, uid: str, attr: str, loaded: float) -> float:
        return self._overrides.get(uid, {}).get(attr, loaded)

    def _set(self, uid: str, attr: str, value: float) -> None:
        self._overrides.setdefault(uid, {})[attr] = value
        self._invalidate(uid)

    def _invalidate(self, uid: str) -> None:
        """Drop the plan of the link holding ``uid``: a fiber's loss moves
        the gain (so the noise figure) of the amplifier after it."""
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        if loc is not None and loc.link_id is not None:
            self._link_plans.pop(loc.link_id, None)

    @staticmethod
    def _reject_unsupported(
        kind: str, attr: str, supported: tuple[str, ...],
    ) -> None:
        raise _ParamValidationError(
            f"{kind} attribute {attr!r} is not writable under the EGN "
            f"backend (supported: {list(supported)!r})"
        )


# Lightweight stand-ins. Kept module-local so the rest of the codebase
# doesn't grow accidental imports of them.


@dataclass(frozen=True)
class _UidStub:
    """Minimal element stand-in carrying just ``uid`` and ``type`` for
    code paths that do ``type(el).__name__`` on values pulled from
    ``backend.uid_map``."""

    uid: str
    type: str

