"""EGN/GN-model implementation of ``PhysicalBackend``.

Selected at startup via ``physics.backend: egn`` (YAML) or
``--physics-backend egn`` (CLI). The propagation model is a clean-room
implementation of the closed-form Gaussian-Noise expressions (see
``physics/egn_kernel.py`` for citations); the network is sourced from
the same GNPy JSON file that the GNPy backend uses, translated to an
EGN-shaped (link, span) view via ``loader/egn_topology.py``.

This backend ships zero runtime dependency on
``optical-networking-gym`` — the EGN library is a *code reference*
under GPL-3.0, not an import.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twinlight.config import TwinConfig
from twinlight.loader.egn_topology import (
    EgnNodeKind,
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
from twinlight.physics.egn_kernel import (
    PathQoT,
    SpanInputs,
    accumulate_path_noise,
    db_per_km_to_neper_per_m,
    nf_db_to_linear,
)
from twinlight.physics.element_params import (
    ParamValidationError as _ParamValidationError,
)
from twinlight.physics.gnpy_adapter import OpmBaseline
from twinlight.physics.modulation import get_params

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Amplifier placement
# ---------------------------------------------------------------------------

def _load_topology_with_amplifiers(config: TwinConfig) -> GnpyTopology:
    """Parse the topology, applying GNPy's amplifier design when possible.

    The GN model charges ASE *per span*, on the assumption that each span
    ends in an amplifier restoring the launch power. A topology of bare
    fiber spans therefore has to be amplifier-designed before the kernel
    sees it: hand it a single un-split 336 km span and the model accounts
    for one amplifier compensating ~67 dB, putting GSNR tens of dB below
    anything physical.

    GNPy solves exactly this in ``designed_network()``, which splits long
    fibers into sub-spans and inserts EDFAs. gnpy is a hard dependency of
    this project regardless of the selected backend, so we reuse that
    result rather than reimplementing span splitting: the designed network
    is serialised back to GNPy JSON — the same schema this loader already
    reads — so the EGN converter consumes it unchanged and both backends
    end up with identical amplifier placement.

    Design needs an equipment library. Without one (``gnpy.equipment``
    unset) the topology is used exactly as written, which stays correct
    for topologies that already carry their own amplifiers and keeps the
    EGN backend usable with no equipment file at all.
    """
    if config.gnpy.equipment is None:
        logger.info(
            "EGN backend: no gnpy.equipment configured — using the topology as "
            "written. Bare fiber spans will each be modelled as one amplified "
            "span, which understates GSNR on long spans."
        )
        return load_gnpy_topology(config.gnpy.topology)

    try:
        designed = _designed_topology_json(
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
        return load_gnpy_topology(config.gnpy.topology)

    topo = parse_gnpy_topology_dict(designed, name=config.gnpy.topology.stem)
    logger.info(
        "EGN backend: applied GNPy amplifier design (%d elements)",
        len(topo.elements),
    )
    return topo


def _designed_topology_json(topology_path: Path, equipment_path: Path) -> dict:
    """Return the GNPy-designed network, serialised back to GNPy JSON."""
    from gnpy.tools.json_io import load_equipment, network_from_json, network_to_json
    from gnpy.tools.worker_utils import designed_network

    equipment = load_equipment(str(equipment_path))
    # network_from_json mutates the document it is handed (it pops keys
    # while building elements), so give it a freshly parsed copy.
    network = network_from_json(json.loads(topology_path.read_text()), equipment)
    network, _ref_req, _ref_chan = designed_network(equipment, network)
    result: dict = network_to_json(network)
    return result


# Per-attribute writable contract — mirrors physics/element_params.py
# but with a far smaller surface because EGN's Span only models
# {length, attenuation, NF}. ROADM-specific knobs (target_pch_out_db),
# Edfa-specific knobs (gain_target / tilt_target / out_voa) have no
# EGN-side state to write, so /config rejects them under this backend.
_EGN_FIBER_ATTRS = ("loss_coef", "att_in")
_EGN_EDFA_ATTRS = ("nf0",)


class EgnBackend:
    """GN-model QoT propagation + per-span /config mutation."""

    name = "egn"

    def __init__(self, config: TwinConfig) -> None:
        # Parse the topology and convert to EGN's per-span view. When an
        # equipment library is available the topology is first run through
        # GNPy's design step, so both backends see the same amplifier
        # placement (see _load_topology_with_amplifiers).
        gnpy_topo = _load_topology_with_amplifiers(config)
        self._egn_topo: EgnTopologyData = convert_to_egn(gnpy_topo)

        # Build a uid_map mirror so the PhysicalBackend Protocol's
        # ``uid_map``-based UID presence checks (used by TapiContext's
        # apply_element_overrides and services_for_element) work
        # uniformly. Values are dummy namespaces carrying ``.uid`` and
        # ``.type`` so existing topology-serialization code that does
        # ``type(el).__name__`` keeps working.
        self.uid_map: dict[str, Any] = {
            uid: _UidStub(uid=uid, type=kind.kind.title())
            for uid, kind in self._egn_topo.gnpy_uid_index.items()
        }
        # EGN does not need GNPy's DiGraph (routing falls back to the
        # TopologyGraph k-shortest path) — Protocol requires the field.
        self.network = None
        self.available: bool = True

        # Per-span runtime overrides: (link_id, span_idx) → updated
        # SpanInputs. Empty until /config writes happen. We do NOT
        # mutate self._egn_topo in place so a future "reset to file"
        # operation is one-line: just clear this dict.
        self._span_overrides: dict[tuple[int, int], _SpanFields] = {}

        # Failed fibers (by GNPy fiber UID). Short-circuits
        # compute_baseline to a link-failed sentinel.
        self._failed_fibers: set[str] = set()

        # Cache of (link_id, span_idx) → SpanInputs ready for the
        # kernel — invalidated whenever a write hits the same key.
        self._span_inputs_cache: dict[tuple[int, int], SpanInputs] = {}

        logger.info(
            "EGN backend ready: %d links, %d spans (no GNPy library used)",
            len(self._egn_topo.links),
            sum(len(lnk.spans) for lnk in self._egn_topo.links),
        )

    # -- QoT propagation -------------------------------------------------

    async def compute_baseline(
        self,
        path_uids: list[str],
        modulation_format: str,
    ) -> OpmBaseline | None:
        # Resolve the path to its spans (in propagation order) and bail
        # with a link-failed sentinel if any fiber on the path is failed.
        resolved = self._spans_for_path(path_uids)
        if resolved is None:
            # No leg of the route resolved to an EGN link — skip silently
            # so OPM falls back to mock data rather than reporting the
            # "no spans" saturated GSNR as if it were a real measurement.
            return None
        span_keys, fiber_uids, edfa_uids = resolved

        # Failure can be flagged against either the route's own fiber UIDs
        # or the (possibly design-split) span UIDs, depending on which
        # topology the caller addressed.
        failed = self._failed_fibers
        if failed and (failed.intersection(path_uids) or failed.intersection(fiber_uids)):
            return OpmBaseline(
                gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0, pmd_ps=0.0,
                latency_ms=0.0, total_fiber_km=0.0, status="link-failed",
            )

        span_inputs: list[SpanInputs] = [
            self._span_inputs_for_key(key) for key in span_keys
        ]

        params = get_params(modulation_format)
        launch_power_w = (10.0 ** (params.tx_power_dbm / 10.0)) * 1e-3
        bandwidth_hz = params.baud_rate_hz
        # Centre-of-C-band default; M5+ may thread the per-service
        # spectrum allocation through to use the actual centre frequency.
        centre_frequency_hz = 193.1e12

        qot: PathQoT = accumulate_path_noise(
            span_inputs,
            launch_power_w=launch_power_w,
            bandwidth_hz=bandwidth_hz,
            centre_frequency_hz=centre_frequency_hz,
        )
        # Treat infinite GSNR (empty-span path) as a saturated 60 dB so
        # the BER calculation downstream stays finite.
        gsnr_db = qot.gsnr_db if qot.gsnr_db != float("inf") else 60.0
        osnr_db = (
            qot.osnr_ase_db if qot.osnr_ase_db != float("inf") else 60.0
        )

        total_km = sum(s.length_m / 1000.0 for s in span_inputs)
        return OpmBaseline(
            gsnr_db=gsnr_db,
            osnr_ase_db=osnr_db,
            cd_ps_nm=chromatic_dispersion_ps_per_nm(total_km),
            pmd_ps=pmd_ps(total_km),
            latency_ms=latency_ms(total_km),
            total_fiber_km=total_km,
            edfa_uids=edfa_uids,
            fiber_uids=fiber_uids,
            pdl_elements=[
                (uid, "Edfa") for uid in edfa_uids
            ],
        )

    # -- /config plane: per-element parameter reads/writes ---------------

    def read_attribute(self, uid: str, attr: str) -> Any:
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        if loc is None:
            raise KeyError(f"Unknown element UID: {uid!r}")
        if loc.kind == "fiber":
            if attr not in _EGN_FIBER_ATTRS:
                self._reject_unsupported("Fiber", attr, _EGN_FIBER_ATTRS)
            assert loc.link_id is not None and loc.span_index is not None
            span = self._egn_topo.links[loc.link_id].spans[loc.span_index]
            override = self._span_overrides.get((loc.link_id, loc.span_index))
            if attr == "loss_coef":
                return (
                    override.attenuation_db_per_km if override
                    else span.attenuation_db_per_km
                )
            return 0.0  # att_in: EGN's span model has no padding loss
        if loc.kind == "edfa":
            if attr not in _EGN_EDFA_ATTRS:
                self._reject_unsupported("Edfa", attr, _EGN_EDFA_ATTRS)
            assert loc.link_id is not None and loc.span_index is not None
            span = self._egn_topo.links[loc.link_id].spans[loc.span_index]
            override = self._span_overrides.get((loc.link_id, loc.span_index))
            return override.noise_figure_db if override else span.noise_figure_db
        # ROADM / Transceiver — no writable params under EGN.
        raise _ParamValidationError(
            f"{loc.kind.title()} {uid!r}: EGN backend exposes no writable "
            f"attributes for this element type"
        )

    def write_attribute(self, uid: str, attr: str, value: Any) -> None:
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        if loc is None:
            raise KeyError(f"Unknown element UID: {uid!r}")
        if loc.kind not in ("fiber", "edfa"):
            raise _ParamValidationError(
                f"{loc.kind.title()} {uid!r}: EGN backend exposes no "
                f"writable attributes for this element type"
            )
        if isinstance(value, bool):
            raise _ParamValidationError(
                f"{uid}.{attr}: expected float, got bool"
            )
        if not isinstance(value, (int, float)):
            raise _ParamValidationError(
                f"{uid}.{attr}: expected float, got {type(value).__name__}"
            )
        numeric = float(value)

        if loc.kind == "fiber":
            if attr not in _EGN_FIBER_ATTRS:
                self._reject_unsupported("Fiber", attr, _EGN_FIBER_ATTRS)
            if attr == "att_in":
                # EGN's per-span model has no padding-attenuation
                # field; accept the call (so /config doesn't 400 on
                # what would work under GNPy) but treat it as a no-op
                # and don't invalidate caches.
                return
            if numeric < 0.0 or numeric > 2.0:
                raise _ParamValidationError(
                    f"{uid}.loss_coef={numeric} out of range [0, 2] dB/km"
                )
            self._update_span(loc, attenuation_db_per_km=numeric)
            return

        # Edfa: nf0 is the only knob we expose.
        if attr not in _EGN_EDFA_ATTRS:
            self._reject_unsupported("Edfa", attr, _EGN_EDFA_ATTRS)
        if numeric < 3.0 or numeric > 12.0:
            raise _ParamValidationError(
                f"{uid}.nf0={numeric} out of range [3, 12] dB"
            )
        self._update_span(loc, noise_figure_db=numeric)

    def read_all_attributes(self, uid: str) -> dict[str, Any]:
        loc = self._egn_topo.gnpy_uid_index.get(uid)
        if loc is None:
            raise KeyError(f"Unknown element UID: {uid!r}")
        if loc.kind == "fiber":
            return {attr: self.read_attribute(uid, attr) for attr in _EGN_FIBER_ATTRS}
        if loc.kind == "edfa":
            return {attr: self.read_attribute(uid, attr) for attr in _EGN_EDFA_ATTRS}
        return {}

    def supported_attributes(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {
            "Fiber": {
                "loss_coef": {"type": "float", "unit": "dB/km",
                              "min": 0.0, "max": 2.0},
                "att_in": {"type": "float", "unit": "dB",
                           "min": 0.0, "max": 20.0,
                           "note": "accepted but no-op under EGN"},
            },
            "Edfa": {
                "nf0": {"type": "float", "unit": "dB",
                        "min": 3.0, "max": 12.0},
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

    def _spans_for_path(
        self, path_uids: list[str]
    ) -> tuple[list[tuple[int, int]], list[str], list[str]] | None:
        """Resolve a route to its EGN spans, in propagation order.

        Routes are computed over the topology as written, so their fiber
        UIDs do not survive GNPy's amplifier design — it splits a long
        fiber into ``…_(1/4)``-style sub-spans under new UIDs. Matching
        individual fibers would therefore silently resolve to *no* spans
        on a designed topology, and an empty span list reads as infinite
        GSNR.

        The terminals are the stable part: design never renames a ROADM
        or transceiver. So walk the route's terminals in order and look
        up each consecutive pair as an EGN link, taking whatever spans
        that link holds. This is correct for both the as-written and the
        designed topology, and needs no knowledge of GNPy's sub-span
        naming convention.

        Returns ``(span_keys, fiber_uids, edfa_uids)``, or None when no
        leg of the route resolved to a link.
        """
        index = self._egn_topo.gnpy_uid_index
        # Keep only UIDs the designed topology knows as terminals. A raw
        # fiber UID from the route is absent from the designed index (its
        # span was renamed by the design split); it must be dropped, not
        # defaulted to a terminal, or it would break the ROADM adjacency
        # the link lookup depends on. So test membership explicitly rather
        # than via a roadm-kind sentinel default.
        terminals = [
            uid for uid in path_uids
            if (loc := index.get(uid)) is not None
            and loc.kind in ("roadm", "transceiver")
        ]

        span_keys: list[tuple[int, int]] = []
        fiber_uids: list[str] = []
        edfa_uids: list[str] = []
        for source_uid, target_uid in itertools.pairwise(terminals):
            link_id = self._egn_topo.link_by_endpoints.get((source_uid, target_uid))
            if link_id is None:
                # Legs with no fiber (a transceiver's access hop to its
                # own ROADM) are not EGN links; they contribute no noise.
                continue
            for span_index, span in enumerate(self._egn_topo.links[link_id].spans):
                span_keys.append((link_id, span_index))
                fiber_uids.append(span.fiber_uid)
                if span.edfa_uid is not None:
                    edfa_uids.append(span.edfa_uid)

        if not span_keys:
            return None
        return span_keys, fiber_uids, edfa_uids

    def _span_inputs_for(
        self, fiber_uid: str
    ) -> SpanInputs | None:
        loc = self._egn_topo.gnpy_uid_index.get(fiber_uid)
        if loc is None or loc.kind != "fiber":
            return None
        # Fibers always populate link_id/span_index in the converter.
        assert loc.link_id is not None and loc.span_index is not None
        return self._span_inputs_for_key((loc.link_id, loc.span_index))

    def _span_inputs_for_key(self, key: tuple[int, int]) -> SpanInputs:
        """Build (and cache) kernel inputs for one ``(link_id, span_index)``."""
        cached = self._span_inputs_cache.get(key)
        if cached is not None:
            return cached
        link_id, span_index = key
        span = self._egn_topo.links[link_id].spans[span_index]
        override = self._span_overrides.get(key)
        loss = override.attenuation_db_per_km if override else span.attenuation_db_per_km
        nf = override.noise_figure_db if override else span.noise_figure_db
        inputs = SpanInputs(
            length_m=span.length_km * 1e3,
            attenuation_neper_per_m=db_per_km_to_neper_per_m(loss),
            noise_figure_linear=nf_db_to_linear(nf),
        )
        self._span_inputs_cache[key] = inputs
        return inputs

    def _update_span(
        self,
        loc: EgnNodeKind,
        *,
        attenuation_db_per_km: float | None = None,
        noise_figure_db: float | None = None,
    ) -> None:
        assert loc.link_id is not None and loc.span_index is not None
        key: tuple[int, int] = (loc.link_id, loc.span_index)
        original = self._egn_topo.links[loc.link_id].spans[loc.span_index]
        current = self._span_overrides.get(key) or _SpanFields(
            attenuation_db_per_km=original.attenuation_db_per_km,
            noise_figure_db=original.noise_figure_db,
        )
        self._span_overrides[key] = _SpanFields(
            attenuation_db_per_km=(
                attenuation_db_per_km
                if attenuation_db_per_km is not None
                else current.attenuation_db_per_km
            ),
            noise_figure_db=(
                noise_figure_db
                if noise_figure_db is not None
                else current.noise_figure_db
            ),
        )
        self._span_inputs_cache.pop(key, None)

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


@dataclass(frozen=True)
class _SpanFields:
    attenuation_db_per_km: float
    noise_figure_db: float


# Sentinel used in ``.get(uid, _MISSING)`` fallbacks. Only its ``.kind``
# is compared against the real "fiber" / "edfa" / etc. values.
_MISSING = EgnNodeKind(kind="roadm")
