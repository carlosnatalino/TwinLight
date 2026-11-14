"""TapiContext: root singleton holding all TAPI state.

Owned exclusively by the FastAPI app. The gRPC server queries
this state only through FastAPI's internal endpoints.

Path computation: when GNPy is loaded we use gnpy.topology.request
compute_constrained_path (weighted shortest path by fiber length; see
https://gnpy.readthedocs.io/). Otherwise we use NetworkX k-shortest paths
(see algorithms/routing.py). GNPy is also used for propagation (QoT).
Spectrum allocation: GNPy has no spectrum/slot APIs; we implement
per-link slot state and first-fit assignment (state/spectrum_state.py,
algorithms/spectrum_assignment.py).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from twinlight.algorithms.path_constraints import (
    NO_CONSTRAINTS,
    ConstraintError,
    LinkRef,
    NodeRef,
    PathConstraints,
)
from twinlight.algorithms.spectrum_assignment import first_fit, path_uids_to_edges
from twinlight.config import TwinConfig
from twinlight.loader.gnpy_topology import load_gnpy_topology
from twinlight.loader.tapi_builder import TapiBuilder
from twinlight.models.common import ServiceInterfacePoint
from twinlight.models.connectivity import ConnectivityService
from twinlight.models.path_computation import PathCompService
from twinlight.models.topology import (
    Link,
    Node,
    NodeEdgePoint,
    Topology,
)
from twinlight.physics.backend import element_kind_name as _kind_name
from twinlight.physics.modulation import ModulationFormat
from twinlight.state.spectrum_state import SpectrumState
from twinlight.state.topology_state import TopologyGraph

if TYPE_CHECKING:
    from twinlight.physics.gnpy_adapter import OpmBaseline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RMSA admission errors (API maps these to 409 Conflict)
# ---------------------------------------------------------------------------

class RmsaError(Exception):
    """Base for RMSA admission failures (no path, no spectrum, or insufficient QoT)."""


class NoPathError(RmsaError):
    """No path exists between the requested SIPs."""


class InsufficientSpectrumError(RmsaError):
    """No contiguous spectrum block available along the path."""


class InsufficientQoTError(RmsaError):
    """Path GSNR below the format's required threshold plus the system margin."""


class SpectrumRequestError(ValueError):
    """A requested spectrum or central frequency that cannot be on the grid at
    all (misaligned, outside it, the wrong width). A client error (422),
    unlike a conflict with spectrum in use or the amplifier band (409)."""


@dataclass(frozen=True)
class _RouteQuery:
    """Constraints resolved to element UIDs of the route graph."""

    include: tuple[str, ...]                 # visited in this order
    hidden_nodes: frozenset[str]             # removed from the search
    hidden_edges: frozenset[tuple[str, str]]
    # explicit-route: the terminal-to-terminal hops the request allows,
    # or None when the route is free between includes.
    explicit_pairs: frozenset[tuple[str, str]] | None


# ---------------------------------------------------------------------------
# Fiber span length
# ---------------------------------------------------------------------------

def _fiber_length_km(gnpy_element: Any, parsed_element: Any) -> float | None:
    """Return a Fiber span's length in km, or None when it cannot be determined.

    Two sources, because only one is available per backend:

    * ``gnpy_element`` — a live ``gnpy.core.elements.Fiber``; its
      ``params.length`` is normalised to **metres** by GNPy regardless of the
      units used in the source file. Present only under the ``gnpy`` backend.
    * ``parsed_element`` — a :class:`GnpyElement` straight from the topology
      JSON, whose ``params`` dict keeps the file's own ``length`` and
      ``length_units`` (GNPy's schema allows "km" or "m"). This is the only
      source under the ``egn`` backend, which builds no GNPy network.
    """
    if gnpy_element is not None:
        try:
            return float(gnpy_element.params.length) / 1000.0
        except (AttributeError, TypeError, ValueError):
            pass  # fall through to the parsed topology

    params = getattr(parsed_element, "params", None)
    if not isinstance(params, dict):
        return None
    try:
        length = float(params["length"])
    except (KeyError, TypeError, ValueError):
        return None
    # "km" is what both bundled examples use; treat anything else as metres
    # only when it says so, rather than guessing.
    units = str(params.get("length_units", "km")).strip().lower()
    return length / 1000.0 if units in ("m", "meter", "metre", "meters", "metres") else length


# ---------------------------------------------------------------------------
# T-API adjustment granularity
# ---------------------------------------------------------------------------

# ADJUSTMENT_GRANULARITY identities from tapi-photonic-media.yang, keyed by
# the slot width they denote in GHz. T-API takes an identityref here, not a
# number, so an unlisted slot width has no truthful spelling.
_ADJUSTMENT_GRANULARITIES: dict[float, str] = {
    100.0: "ADJUSTMENT_GRANULARITY_G_100GHZ",
    50.0: "ADJUSTMENT_GRANULARITY_G_50GHZ",
    25.0: "ADJUSTMENT_GRANULARITY_G_25GHZ",
    12.5: "ADJUSTMENT_GRANULARITY_G_12_5GHZ",
    6.25: "ADJUSTMENT_GRANULARITY_G_6_25GHZ",
    3.125: "ADJUSTMENT_GRANULARITY_G_3_125GHZ",
}


def _adjustment_granularity(slot_width_ghz: float) -> str:
    """Map a configured slot width to its T-API identity.

    Falls back to ``ADJUSTMENT_GRANULARITY_UNCONSTRAINED`` rather than
    guessing: claiming a grid the twin is not using would mislead a client
    into computing channel centres that do not line up with its slots.
    """
    return _ADJUSTMENT_GRANULARITIES.get(
        round(slot_width_ghz, 4), "ADJUSTMENT_GRANULARITY_UNCONSTRAINED"
    )


class TapiContext:
    """In-memory TAPI context with indexed lookups."""

    def __init__(self, config: TwinConfig) -> None:
        self._config = config

        # Load and parse topology (no gnpy library needed)
        gnpy_topo = load_gnpy_topology(config.gnpy.topology)
        self._topo_graph = TopologyGraph(gnpy_topo)

        # Build TAPI models
        builder = TapiBuilder(gnpy_topo, self._topo_graph)
        self.topology_context, self.sips = builder.build()

        # Build indexes for fast lookups
        self._sip_index: dict[str, ServiceInterfacePoint] = {
            sip.uuid: sip for sip in self.sips
        }
        self._topology_index: dict[str, Topology] = {
            t.uuid: t for t in self.topology_context.topology
        }
        self._node_index: dict[str, dict[str, Node]] = {}
        self._link_index: dict[str, dict[str, Link]] = {}
        self._nep_index: dict[str, dict[str, dict[str, NodeEdgePoint]]] = {}

        for topo in self.topology_context.topology:
            self._node_index[topo.uuid] = {n.uuid: n for n in topo.node}
            self._link_index[topo.uuid] = {lnk.uuid: lnk for lnk in topo.link}
            self._nep_index[topo.uuid] = {}
            for node in topo.node:
                self._nep_index[topo.uuid][node.uuid] = {
                    nep.uuid: nep for nep in node.owned_node_edge_point
                }

        self._services: dict[str, ConnectivityService] = {}

        # -- SIP → GNPy UID map -------------------------------------------
        # TapiBuilder stores the GNPy element UID in sip.name[0] with
        # value_name == "node-name".  Recover it here so the OPM endpoint
        # can look up elements in the GNPy network by UID.
        self._sip_to_gnpy_uid: dict[str, str] = {}
        for sip in self.sips:
            for n in sip.name:
                if n.value_name == "node-name":
                    self._sip_to_gnpy_uid[sip.uuid] = n.value
                    break

        # -- Physical-layer backend ----------------------------------------
        # All QoT propagation, per-element parameter writes, and the
        # equipment/network handles needed by ``redesign`` live on the
        # backend. ``TapiContext`` exposes ``_gnpy_*`` properties below
        # so existing call sites and tests don't have to change. The
        # specific backend is chosen at startup via ``physics.backend``
        # (yaml) / ``--physics-backend`` (CLI).
        from twinlight.physics.backend import build_backend

        self._backend = build_backend(config)

        # -- Per-service caches and serialization lock ---------------------
        self._route_cache: dict[tuple[str, str], list[str]] = {}
        self._baseline_cache: dict[str, OpmBaseline] = {}
        self._propagation_lock: asyncio.Lock = asyncio.Lock()

        # -- EDFA stateful reservoir tracker (Bononi exponential step) ------
        from twinlight.physics.transients.edfa_reservoir import (
            EdfaStateTracker,
        )
        self._edfa_tracker = EdfaStateTracker()
        # service_uuid -> list of EDFA UIDs (for drop notification)
        self._service_edfa_uids: dict[str, list[str]] = {}

        # -- Spectrum state (Phase 3 RMSA) ---------------------------------
        # Per-link slot occupancy; first-fit assignment in add_service.
        self._spectrum_state = SpectrumState(config.spectrum.num_slots)
        # service_uuid -> (path_edges, start_slot, block_width) for release on delete
        self._service_allocation: dict[str, tuple[list[tuple[str, str]], int, int]] = {}
        # service_uuid -> launch power in effect [dBm] (requested or SI default)
        self._service_launch_power: dict[str, float] = {}

        # -- Path computation: GNPy UID <-> TAPI topology refs -------------
        # gnpy_uid -> (topology_uuid, node_uuid)
        self._gnpy_uid_to_node_ref: dict[str, tuple[str, str]] = {}
        # (src_gnpy_uid, dst_gnpy_uid) -> (topology_uuid, link_uuid)
        self._gnpy_pair_to_link_ref: dict[tuple[str, str], tuple[str, str]] = {}
        # and back: resolves the link refs of topology constraints
        self._link_ref_to_pair: dict[tuple[str, str], tuple[str, str]] = {}
        self._build_gnpy_topology_ref_indexes()

        # -- /config plane runtime overrides --------------------------------
        # Per-element user-visible overrides actually applied: {uid: {attr: user_value}}.
        # Kept in user units so snapshots round-trip without re-deriving display
        # values from the (sometimes transformed) internal storage.
        self._element_overrides: dict[str, dict[str, Any]] = {}
        # Reserved here so M3 can fill it without changing snapshot/restore plumbing.
        self._failed_links: set[str] = set()
        # Flat dotted-key dict of applied twin-config overrides (a sparse mirror
        # of TWIN_ALLOWED). Empty when the user has set nothing.
        self._twin_overrides: dict[str, Any] = {}
        # Reverse index: GNPy element UID → set of service UUIDs whose path
        # traverses it. Maintained in add_service / delete_service so a
        # parameter mutation can invalidate only the affected baselines.
        self._element_to_services: dict[str, set[str]] = {}
        # NetworkX edges removed by set_link_failed, keyed by fiber UID, so
        # set_link_failed(False) can restore them exactly. Each entry is a
        # list of per-graph stashes — the two routing graphs (topo_graph
        # and the GNPy network) key nodes differently, so we store
        # (graph_ref, node_key, in_edges, out_edges) for each.
        self._removed_edges: dict[str, list[tuple]] = {}
        # Fiber UID → list of TAPI (topology_uuid, link_uuid) refs the fiber
        # backs. Built once below by walking through inline EDFAs out to the
        # nearest ROADM/TRX on each side. Used to flip Link.operational_state
        # when a fiber fails. Topologies with multiple fibers per TAPI link
        # are handled the same way: failing any one fiber disables the link.
        self._fiber_to_link_refs: dict[str, list[tuple[str, str]]] = {}
        if self._backend.available:
            self._build_fiber_to_link_index()
        # Inline element UID (Fiber/Edfa) → (upstream, downstream) terminal
        # UID (Roadm/Transceiver). Built once, before any link can fail, so
        # removing a failed fiber's edges never hides it from the index.
        # Backs the /Link scope of parameter paths.
        self._element_terminals: dict[str, tuple[str | None, str | None]] = {}
        # (upstream, downstream) terminal pair → its inline element UIDs in
        # propagation order. Built by the same walk; backs /LinkList in the
        # transparent controller.
        self._link_elements: dict[tuple[str, str], list[str]] = {}
        self._build_element_terminal_index()

        # -- Running-datastore revision and startup values -------------------
        # Every change to configuration state — element parameters, failed
        # links, twin config — bumps the revision. It is the entity-tag of the
        # transparent controller's running datastore, whichever interface
        # made the change, so a client holding an ETag learns of a write made
        # through /config/set as surely as of one made through /transparent.
        self._revision: int = 0
        self._last_modified: datetime = datetime.now(UTC)
        # The configuration as loaded, for reverting twin-config overrides.
        self._startup_config: TwinConfig = config.model_copy(deep=True)
        # (uid, attr) → (user value, backend capture token) taken just before
        # the first write to that attribute: the value the element had at
        # startup, exactly as the engine stored it (an unset value included).
        self._element_startup: dict[tuple[str, str], tuple[Any, Any]] = {}

        # -- T-API path computation (path-comp-service and path lists) -------
        # Results of what-if path computations. They commit no resources and
        # are not part of snapshots: a computed path is a function of the
        # topology at the time it was asked for.
        self._path_comp_services: dict[str, PathCompService] = {}
        self._paths: dict[str, dict[str, Any]] = {}

        # -- Transparent controller: candidate datastore ---------------------
        from twinlight.transparent.datastore import CandidateDatastore

        self.candidate = CandidateDatastore()

        self._log_rmsa_settings()

    def _log_rmsa_settings(self) -> None:
        """Log the admission policy in effect, once, at startup.

        These four values decide every admission, and two of them do not come
        from the YAML file the operator is reading: the launch power is the
        equipment library's SI default, and the margin and guard band may have
        been left at their code defaults. Logging the resolved values makes a
        run's admission decisions reproducible from its log alone.
        """
        rmsa = self._config.rmsa
        defaults = self._backend.spectral_defaults
        logger.info(
            "RMSA settings: qot_margin_db=%.2f dB, k_shortest_paths=%d, "
            "default_guardband_slots=%d (%.2f GHz), default launch power=%.2f dBm "
            "(SI power_dbm from %s)",
            rmsa.qot_margin_db,
            rmsa.k_shortest_paths,
            rmsa.default_guardband_slots,
            rmsa.default_guardband_slots * self._config.spectrum.slot_width_ghz,
            defaults.power_dbm,
            defaults.source,
        )

    # -- Backend pass-throughs ------------------------------------------
    # These three properties existed as direct attributes before the
    # ``PhysicalBackend`` refactor; keeping them as read-only aliases
    # avoids churning every call site (and every test) that already
    # reads ``ctx._gnpy_uid_map`` / ``ctx._gnpy_network`` etc.

    @property
    def _gnpy_available(self) -> bool:
        return self._backend.available

    @property
    def _gnpy_uid_map(self) -> dict[str, Any]:
        return self._backend.uid_map

    @property
    def _gnpy_network(self):
        return self._backend.network

    @property
    def _gnpy_equipment(self) -> Any:
        return getattr(self._backend, "equipment", None)

    # -- Query methods ---------------------------------------------------

    def get_sips(self) -> list[ServiceInterfacePoint]:
        return self.sips

    def get_sip(self, uuid: str) -> ServiceInterfacePoint | None:
        return self._sip_index.get(uuid)

    def get_topologies(self) -> list[Topology]:
        return self.topology_context.topology

    def get_topology(self, uuid: str) -> Topology | None:
        return self._topology_index.get(uuid)

    def get_node(self, topo_uuid: str, node_uuid: str) -> Node | None:
        return self._node_index.get(topo_uuid, {}).get(node_uuid)

    def get_link(self, topo_uuid: str, link_uuid: str) -> Link | None:
        return self._link_index.get(topo_uuid, {}).get(link_uuid)

    def get_nep(
        self, topo_uuid: str, node_uuid: str, nep_uuid: str
    ) -> NodeEdgePoint | None:
        return self._nep_index.get(topo_uuid, {}).get(node_uuid, {}).get(nep_uuid)

    async def add_service(self, svc: ConnectivityService) -> None:
        """Add a connectivity service with RMSA: path, spectrum, and QoT check.

        Order: route under the service's routing and topology constraints
        (:meth:`candidate_paths`), first-fit spectrum assignment, then the
        GSNR check against the active backend's propagation.

        Raises:
            ConstraintError: A constraint that does not resolve against the
                topology (unsupported leaves are refused when the model is
                parsed).
            NoPathError, InsufficientSpectrumError, InsufficientQoTError:
                Admission failed.
        """
        endpoints = svc.end_point
        if len(endpoints) < 2:
            raise NoPathError("Connectivity service requires two end-points")
        sip_a = endpoints[0].service_interface_point.service_interface_point_uuid
        sip_z = endpoints[1].service_interface_point.service_interface_point_uuid
        src, dst = self.get_gnpy_uid(sip_a), self.get_gnpy_uid(sip_z)
        if not src or not dst:
            raise NoPathError(f"No path between SIPs {sip_a!r} and {sip_z!r}")

        constraints = svc.path_constraints()
        paths = self.candidate_paths(
            src, dst, constraints, self._config.rmsa.k_shortest_paths,
        )
        if not paths:
            raise NoPathError(
                f"No path between SIPs {sip_a!r} and {sip_z!r}"
                + ("" if constraints.is_empty else " satisfies the constraints")
            )
        path, fmt, start, block_width, baseline = await self._admit(svc, paths)

        path_edges = path_uids_to_edges(path)
        self._spectrum_state.allocate(path_edges, start, block_width)
        self._service_allocation[svc.uuid] = (path_edges, start, block_width)
        lower, upper = self._slot_range_hz(start, block_width)
        power_dbm = self._launch_power_for(svc)
        self._service_launch_power[svc.uuid] = power_dbm
        svc.assign_channel(fmt, (lower + upper) // 2, power_dbm)
        self._services[svc.uuid] = svc
        if baseline is not None:
            # The admission propagation is the service's baseline: same path,
            # channel and launch power. Seeding the cache saves a propagation.
            self._baseline_cache[svc.uuid] = baseline

        # Reverse index for /config invalidation: every element on the path
        # now depends on this service's baseline.
        for uid in path:
            self._element_to_services.setdefault(uid, set()).add(svc.uuid)

        # Notify EDFA tracker of channel add (Bononi reservoir model)
        if baseline is not None:
            edfa_uids = baseline.edfa_uids
            self._service_edfa_uids[svc.uuid] = edfa_uids
            if edfa_uids and self._config.transients.edfa_reservoir.enabled:
                self._edfa_tracker.notify_channel_change(
                    edfa_uids, +1, self._config.transients.edfa_reservoir,
                )

    async def _admit(
        self, svc: ConnectivityService, paths: list[list[str]],
    ) -> tuple[list[str], ModulationFormat, int, int, OpmBaseline | None]:
        """Walk the candidates and formats; return the first that passes.

        Candidates are tried in rank order. On each, the requested format is
        tried, or — when the request names none — every format from the
        highest spectral efficiency down, so the densest format that closes
        the link wins. A (path, format) passes when a block is free on every
        link and, with a physics backend loaded, the propagated GSNR reaches
        the format's requirement plus ``rmsa.qot_margin_db``.

        A requested spectrum (or central frequency) is checked against the
        grid once, then used as is on each candidate: it must lie in the band
        the path's amplifiers pass and be free on every link, or the
        candidate is skipped with the conflict named. Without a request, the
        block is first-fit within that band.

        Without a physics backend there is no QoT to check, so a request that
        names no format gets the most robust one rather than the densest:
        the twin cannot know a denser format would close the link.

        Propagations are memoised for this admission only, keyed by
        (path, central frequency, launch power, symbol rate) — what the
        propagation depends on — so formats that occupy the same block share
        one. The memo never outlives the call, so it cannot go stale on a
        parameter change.

        Returns ``(path, format, start slot, block width, baseline)``.

        Raises:
            SpectrumRequestError: The requested spectrum does not fit the grid
                or any format's width.
            InsufficientSpectrumError: No candidate had a usable block for any
                format tried. The message names each conflict.
            InsufficientQoTError: Spectrum was found, but no (path, format)
                reached its GSNR requirement. The message lists every attempt.
        """
        from twinlight.physics.modulation import (
            formats_by_spectral_efficiency,
            get_params,
        )

        qot_available = self._gnpy_available
        if svc.requested_modulation is not None:
            formats = [svc.requested_modulation]
        elif qot_available:
            formats = formats_by_spectral_efficiency()
        else:
            formats = formats_by_spectral_efficiency()[-1:]
        requested = self._requested_starts(svc, formats)
        if requested is not None:
            formats = [f for f in formats if f in requested]

        guard_slots = self._config.rmsa.default_guardband_slots
        margin = self._config.rmsa.qot_margin_db
        power_dbm = self._launch_power_for(svc)
        memo: dict[tuple[tuple[str, ...], int, int, float, float], OpmBaseline | None] = {}
        attempts: list[str] = []
        spectrum_found = False

        for rank, path in enumerate(paths, start=1):
            edges = path_uids_to_edges(path)
            assignable = self._assignable_slots(path)
            label = f"candidate {rank} ({' > '.join(self._terminals(path))})"
            for fmt in formats:
                params = get_params(fmt)
                block = self._block_width(fmt)
                if requested is None:
                    start = first_fit(
                        self._spectrum_state, edges, block - guard_slots,
                        guard_slots, assignable,
                    )
                    if start is None:
                        attempts.append(
                            f"{label} {fmt.value}: no free {block}-slot block "
                            "inside the amplifier band"
                        )
                        continue
                else:
                    start = requested[fmt]
                    conflict = self._block_conflict(path, edges, start, block, assignable)
                    if conflict is not None:
                        attempts.append(f"{label} {fmt.value}: {conflict}")
                        continue
                spectrum_found = True
                if not qot_available:
                    return path, fmt, start, block, None

                lower, upper = self._slot_range_hz(start, block)
                centre_hz = (lower + upper) // 2
                # What the propagation depends on: the route, where the channel
                # sits, how wide its media channel is (which comb neighbours it
                # displaces), its power and its symbol rate. Formats sharing
                # all of these share one propagation.
                key = (tuple(path), centre_hz, upper - lower, power_dbm,
                       params.baud_rate_hz)
                if key not in memo:
                    async with self._propagation_lock:
                        memo[key] = await self._backend.compute_baseline(
                            path, fmt.value,
                            centre_frequency_hz=centre_hz,
                            media_channel_width_hz=upper - lower,
                            launch_power_dbm=power_dbm,
                        )
                baseline = memo[key]
                if baseline is None:
                    # Propagation could not run (e.g. an element GNPy rejects):
                    # admitted without a QoT check, as before, so the OPM layer
                    # falls back to its mock data for this service.
                    return path, fmt, start, block, None
                required = params.req_gsnr_db + margin
                if baseline.gsnr_db >= required:
                    return path, fmt, start, block, baseline
                attempts.append(
                    f"{label} {fmt.value}: GSNR {baseline.gsnr_db:.1f} dB below "
                    f"{required:.1f} dB (required {params.req_gsnr_db} + "
                    f"margin {margin})"
                )

        error_type = InsufficientQoTError if spectrum_found else InsufficientSpectrumError
        raise error_type("; ".join(attempts))

    def _launch_power_for(self, svc: ConnectivityService) -> float:
        """The requested launch power, else the line default (SI power_dbm)."""
        if svc.requested_launch_power_dbm is not None:
            return svc.requested_launch_power_dbm
        return self.default_launch_power_dbm

    # -- Spectrum: grid, amplifier band, requested blocks ------------------

    def _slot_hz(self) -> int:
        return round(self._config.spectrum.slot_width_ghz * 1e9)

    def _block_width(self, fmt: ModulationFormat) -> int:
        """Slots of one media channel for ``fmt``: the channel plus guard."""
        from twinlight.physics.modulation import slots_required_for_modulation

        return (
            slots_required_for_modulation(fmt, self._slot_hz())
            + self._config.rmsa.default_guardband_slots
        )

    def _assignable_slots(self, path: list[str]) -> np.ndarray | None:
        """Slots whose whole width lies in a band every amplifier on ``path``
        passes — GNPy's ``is_in_band`` test, which is how an EDFA decides
        which carriers to drop. None when the backend has no amplifier model.
        """
        bands = self._backend.amplifier_bands(path)
        if bands is None:
            return None
        n = self._config.spectrum.num_slots
        grid_lower, _ = self._slot_range_hz(0, n)
        lower = grid_lower + np.arange(n) * self._slot_hz()
        upper = lower + self._slot_hz()
        mask = np.zeros(n, dtype=bool)
        for f_min, f_max in bands:
            mask |= (lower >= f_min) & (upper <= f_max)
        return mask

    def _requested_starts(
        self, svc: ConnectivityService, formats: list[ModulationFormat],
    ) -> dict[ModulationFormat, int] | None:
        """Start slot of the requested media channel for each format it fits.

        None when the request names no spectrum. The spectrum must sit on the
        grid (edges on slot edges, a central frequency at a block centre),
        inside it, and be exactly one media channel wide for the format —
        the width the twin reports for an admitted service, so a GET's
        spectrum can be sent back unchanged.

        Raises:
            SpectrumRequestError: Off-grid, outside the grid, a central
                frequency that is not the spectrum's centre, a frequency
                constraint other than the twin's grid, or no format of that
                width.
        """
        edge = svc.requested_edge_frequency_constraint
        if edge is not None:
            ours = self._frequency_constraint()
            for leaf, value in edge.items():
                if ours.get(leaf) != value:
                    raise SpectrumRequestError(
                        f"edge-frequency-constraint/{leaf}={value!r} is not this "
                        f"twin's grid ({leaf}={ours.get(leaf)!r})"
                    )
        spectrum = svc.requested_spectrum_hz
        centre = svc.requested_central_frequency_hz
        if spectrum is None and centre is None:
            return None

        slot = self._slot_hz()
        n = self._config.spectrum.num_slots
        grid_lower, grid_upper = self._slot_range_hz(0, n)
        thz = 1e-12
        starts: dict[ModulationFormat, int] = {}
        widths: list[str] = []
        for fmt in formats:
            width = self._block_width(fmt)
            widths.append(f"{fmt.value} {width} slots ({width * slot * 1e-9:g} GHz)")
            start: int | None = None
            if spectrum is not None:
                lower, upper = spectrum
                if (lower - grid_lower) % slot or (upper - lower) % slot:
                    raise SpectrumRequestError(
                        f"spectrum {lower}-{upper} Hz is not aligned to the "
                        f"{slot * 1e-9:g} GHz grid: edges must sit at "
                        f"{grid_lower} Hz + n x {slot} Hz"
                    )
                if (upper - lower) // slot != width:
                    continue
                start = (lower - grid_lower) // slot
            if centre is not None:
                twice = 2 * (centre - grid_lower) - width * slot
                on_grid = twice % (2 * slot) == 0
                if spectrum is not None and (not on_grid or start != twice // (2 * slot)):
                    raise SpectrumRequestError(
                        f"central-frequency {centre} Hz is not the centre of the "
                        f"requested spectrum {spectrum[0]}-{spectrum[1]} Hz"
                    )
                if not on_grid:
                    raise SpectrumRequestError(
                        f"central-frequency {centre} Hz is not the centre of "
                        f"any {width}-slot block on the {slot * 1e-9:g} GHz grid"
                    )
                start = twice // (2 * slot)
            assert start is not None
            if start < 0 or start + width > n:
                lo, hi = self._slot_range_hz(start, width)
                raise SpectrumRequestError(
                    f"requested spectrum {lo * thz:.6f}-{hi * thz:.6f} THz lies "
                    f"outside the grid {grid_lower * thz:.6f}-{grid_upper * thz:.6f} THz"
                )
            starts[fmt] = start
        if not starts:
            raise SpectrumRequestError(
                f"requested spectrum is {(spectrum[1] - spectrum[0]) // slot} slots "
                f"wide; a media channel is {', '.join(widths)} "
                f"(channel plus {self._config.rmsa.default_guardband_slots} guard)"
                if spectrum is not None else "no format fits the requested spectrum"
            )
        return starts

    def _block_conflict(
        self, path: list[str], edges: list[tuple[str, str]], start: int, width: int,
        assignable: np.ndarray | None,
    ) -> str | None:
        """Why a requested block cannot be used on ``path``, or None if it can."""
        lower, upper = self._slot_range_hz(start, width)
        thz = 1e-12
        where = f"requested {lower * thz:.6f}-{upper * thz:.6f} THz"
        if assignable is not None and not assignable[start:start + width].all():
            bands = self._backend.amplifier_bands(path) or []
            passed = ", ".join(f"{lo * thz:.6f}-{hi * thz:.6f} THz" for lo, hi in bands)
            return (
                f"{where} is outside the band every amplifier on the path passes "
                f"({passed or 'none'})"
            )
        if self._spectrum_state.is_available(edges, start, width):
            return None
        for uuid, (other_edges, other_start, other_width) in self._service_allocation.items():
            overlaps = other_start < start + width and start < other_start + other_width
            shared = set(edges).intersection(other_edges)
            if overlaps and shared:
                a, b = next(e for e in edges if e in shared)
                return f"{where} is occupied by service {uuid} on {a} -> {b}"
        return f"{where} is occupied on the path"

    def get_services(self) -> list[ConnectivityService]:
        return list(self._services.values())

    def get_service(self, uuid: str) -> ConnectivityService | None:
        return self._services.get(uuid)

    def get_service_spectrum(self, uuid: str) -> dict | None:
        """Return the assigned centre frequency and slot width for a service.

        ``{nominal-central-frequency: THz, slot-width: GHz}``, or ``None``
        when the service has no allocation.

        This is *not* a T-API shape — ``tapi-connectivity`` has no
        ``frequency-slot`` leaf. It stays because the ``/internal/`` views
        and the UI read these units directly;
        :meth:`service_spectrum_spec` is what goes on the wire.
        """
        allocation = self._service_allocation.get(uuid)
        if allocation is None:
            return None
        _path_edges, start_slot, block_width = allocation
        cfg = self._config.spectrum
        # Center of block in slot index; convert to THz
        # Grid: slot 0 at (center_thz - num_slots/2 * slot_width_ghz/1000)
        center_slot = start_slot + (block_width - 1) / 2.0
        center_thz = (
            cfg.center_frequency_thz
            + (center_slot - cfg.num_slots / 2.0) * (cfg.slot_width_ghz / 1000.0)
        )
        width_ghz = block_width * cfg.slot_width_ghz
        return {
            "nominal-central-frequency": round(center_thz, 6),
            "slot-width": round(width_ghz, 3),
        }

    def service_spectrum_spec(self, uuid: str) -> dict | None:
        """The T-API v2.6.0 MCG spec for a service's assigned spectrum.

        ``tapi-connectivity`` has no way to express assigned spectrum; the
        photonic module augments the connectivity-service end-point's
        layer-protocol-constraint:

            tapi-photonic-media:mcg-connectivity-service-end-point-spec
              mc-spectrum-config-pac[local-id]
                spectrum { lower-frequency, upper-frequency }   # uint64 Hz
                edge-frequency-constraint { grid-type, adjustment-granularity }

        Returns ``None`` when the service has no allocation, in which case
        the end-point carries no MCG spec at all — which is the honest
        encoding for a service that holds no spectrum.
        """
        allocation = self._service_allocation.get(uuid)
        if allocation is None:
            return None
        _path_edges, start_slot, block_width = allocation
        lower, upper = self._slot_range_hz(start_slot, block_width)
        return {
            "number-of-mc": 1,
            "mc-spectrum-config-pac": [
                {
                    "local-id": "1",
                    "spectrum": {
                        "lower-frequency": lower,
                        "upper-frequency": upper,
                    },
                    "edge-frequency-constraint": self._frequency_constraint(),
                },
            ],
        }

    # -- T-API photonic spectrum capability on a SIP -----------------------

    def _frequency_constraint(self) -> dict:
        """The grid this twin runs, as a T-API frequency-constraint.

        ITU-T G.694.1 flexi-grid: for ``GRID_TYPE_FLEX`` the adjustment
        granularity is half the minimum slot width, which is what the
        configured ``slot_width_ghz`` denotes here.
        """
        return {
            "grid-type": "GRID_TYPE_FLEX",
            "adjustment-granularity": _adjustment_granularity(
                self._config.spectrum.slot_width_ghz
            ),
        }

    def _slot_range_hz(self, start_slot: int, width: int) -> tuple[int, int]:
        """Frequency bounds [Hz] of a slot block, as a T-API spectrum-band.

        Grid convention, which is subtle enough to be worth stating: slot
        index *i* denotes the slot's **centre** at
        ``center_frequency + (i - num_slots/2) x slot_width``, the same
        arithmetic ``get_service_spectrum`` uses. The band edges are
        therefore half a slot outside the first and last slot centres.

        That convention is the one that keeps the twin ITU-T G.694.1 legal:
        the standard allows nominal central frequencies at
        193.1 THz + n x 6.25 GHz, and with slot *centres* on the grid an
        odd-width block — which is what RMSA allocates, channel plus one
        guard slot — centres on a grid point. Reading the index as a lower
        edge instead would put every such block half a granule off-grid.
        """
        cfg = self._config.spectrum
        centre_hz = cfg.center_frequency_thz * 1e12
        slot_hz = cfg.slot_width_ghz * 1e9
        first_centre = centre_hz + (start_slot - cfg.num_slots / 2.0) * slot_hz
        lower = first_centre - slot_hz / 2.0
        upper = lower + width * slot_hz
        # T-API types these as uint64 Hz, so they are rounded, not truncated.
        return round(lower), round(upper)

    def _sip_occupied_slots(self, sip_uuid: str) -> list[tuple[int, int]]:
        """(start, width) blocks occupied by services terminating on a SIP."""
        blocks: list[tuple[int, int]] = []
        for svc in self._services.values():
            terminates_here = any(
                ep.service_interface_point.service_interface_point_uuid
                == sip_uuid
                for ep in svc.end_point
            )
            if not terminates_here:
                continue
            alloc = self._service_allocation.get(svc.uuid)
            if alloc is not None:
                _edges, start, width = alloc
                blocks.append((start, width))
        return sorted(blocks)

    def spectrum_capability_for_sip(self, sip_uuid: str) -> dict:
        """Build ``spectrum-capability-pac`` for a SIP (T-API v2.6.0).

        ``tapi-photonic-media`` augments the SIP with
        ``photonic-media-service-interface-point-spec``, whose
        ``spectrum-capability-pac`` carries three lists of spectrum-bands:
        supportable, available and occupied. Frequencies are uint64 **Hz**
        (``spectrum-band`` in tapi-photonic-media.yang).

        What "available at a SIP" means here: a SIP sits on a transceiver,
        and this reports **SIP-local** occupancy — the blocks used by
        services terminating on this SIP, and everything else in the
        supportable range as available. That is the only reading that
        makes the answer a property of the SIP itself, which is what the
        YANG models; folding in the occupancy of links downstream would
        attribute link state to a port, and which link that is depends on
        where the lightpath is going.
        """
        cfg = self._config.spectrum
        band_lower, band_upper = self._slot_range_hz(0, cfg.num_slots)
        constraint = self._frequency_constraint()

        def band(lower: int, upper: int) -> dict:
            return {
                "upper-frequency": upper,
                "lower-frequency": lower,
                "frequency-constraint": dict(constraint),
            }

        occupied_blocks = self._sip_occupied_slots(sip_uuid)
        occupied = [
            band(*self._slot_range_hz(start, width))
            for start, width in occupied_blocks
        ]

        # Available = supportable minus occupied, as maximal free runs.
        available: list[dict] = []
        cursor = 0
        for start, width in occupied_blocks:
            if start > cursor:
                available.append(band(*self._slot_range_hz(cursor, start - cursor)))
            cursor = max(cursor, start + width)
        if cursor < cfg.num_slots:
            available.append(
                band(*self._slot_range_hz(cursor, cfg.num_slots - cursor))
            )

        return {
            "supportable-spectrum": [band(band_lower, band_upper)],
            "available-spectrum": available,
            "occupied-spectrum": occupied,
        }

    def sip_photonic_spec(self, sip_uuid: str) -> dict:
        """The ``tapi-photonic-media`` augment for one SIP."""
        return {
            "spectrum-capability-pac": self.spectrum_capability_for_sip(
                sip_uuid
            ),
        }

    def delete_service(self, uuid: str) -> bool:
        """Remove service and release its spectrum allocation."""
        allocation = self._service_allocation.pop(uuid, None)
        self._service_launch_power.pop(uuid, None)
        if allocation is not None:
            path_edges, start, block_width = allocation
            self._spectrum_state.release(path_edges, start, block_width)

        # Notify EDFA tracker of channel drop (Bononi reservoir model)
        edfa_uids = self._service_edfa_uids.pop(uuid, [])
        if edfa_uids and self._config.transients.edfa_reservoir.enabled:
            self._edfa_tracker.notify_channel_change(
                edfa_uids, -1, self._config.transients.edfa_reservoir,
            )

        self.invalidate_baseline(uuid)
        # Strip this service from the /config reverse index.
        for svc_set in self._element_to_services.values():
            svc_set.discard(uuid)
        return self._services.pop(uuid, None) is not None

    @property
    def topo_graph(self) -> TopologyGraph:
        return self._topo_graph

    @property
    def edfa_tracker(self):
        """EDFA stateful reservoir tracker (Bononi model)."""
        return self._edfa_tracker

    # -- GNPy / physics helpers ------------------------------------------

    def get_gnpy_uid(self, sip_uuid: str) -> str | None:
        """Return the GNPy element UID for a given SIP UUID, or None."""
        return self._sip_to_gnpy_uid.get(sip_uuid)

    # -- Route search ------------------------------------------------------
    #
    # GNPy has no k-shortest-path function. Its compute_constrained_path runs
    # networkx.shortest_simple_paths(network, weight="weight") over the GNPy
    # network, where an edge's weight is the length of the fiber it leaves
    # (gnpy/tools/json_io.py, network_from_json; 0.01 for other edges, as
    # gnpy/core/network.py also sets on the edges it adds). The k-list is
    # built the same way: compute_constrained_path gives candidate 1,
    # honouring ordered includes, and the same weighted Yen search over the
    # same graph gives the rest. Exclusions are applied by searching a
    # restricted view of the graph, which GNPy's search accepts like any
    # other DiGraph. Without a GNPy network (the EGN backend) the search
    # falls back to the parsed topology graph, weighted the same way
    # (state/topology_state.py). See algorithms/path_constraints.py for which
    # T-API constraints map onto this.

    def _element_kind(self, uid: str) -> str:
        """"Roadm"/"Transceiver"/"Fiber"/"Edfa"/… for an element UID."""
        el = self._gnpy_uid_map.get(uid) if self._gnpy_uid_map else None
        if el is not None:
            return _kind_name(el)
        parsed = self._topo_graph._gnpy_topo.elements_by_uid.get(uid)
        return getattr(parsed, "type", "") if parsed else ""

    def _terminals(self, path: list[str]) -> list[str]:
        """The ROADMs and transceivers of a path, in order."""
        return [
            uid for uid in path
            if self._element_kind(uid) in ("Roadm", "Transceiver")
        ]

    def _resolve_node(self, ref: NodeRef, where: str) -> str:
        node = self.get_node(ref.topology_uuid, ref.node_uuid)
        uid = next(
            (n.value for n in node.name if n.value_name == "node-name"), None,
        ) if node is not None else None
        if uid is None:
            raise ConstraintError(
                f"{where}: node {ref.node_uuid} not found in topology "
                f"{ref.topology_uuid}"
            )
        return uid

    def _resolve_link(self, ref: LinkRef, where: str) -> tuple[str, str]:
        pair = self._link_ref_to_pair.get((ref.topology_uuid, ref.link_uuid))
        if pair is None:
            raise ConstraintError(
                f"{where}: link {ref.link_uuid} not found in topology "
                f"{ref.topology_uuid}"
            )
        return pair

    def _route_graph(self) -> tuple[Any, Any]:
        """(graph searched for routes, node key of an element UID)."""
        net = self._gnpy_network
        if net is not None:
            uid_map = self._gnpy_uid_map
            return net, uid_map.get
        graph = self._topo_graph.graph
        return graph, lambda uid: uid if uid in graph else None

    def _link_inline_elements(self, pair: tuple[str, str]) -> list[str]:
        """Inline elements (fibers, amplifiers) of a terminal-to-terminal link,
        in propagation order, as they appear in the route graph."""
        return list(self._link_elements.get(pair, []))

    def _resolve_route_query(
        self, constraints: PathConstraints, src: str, dst: str,
    ) -> _RouteQuery:
        """Translate T-API refs to element UIDs of the route graph.

        Raises:
            ConstraintError: A reference that does not resolve, or an exclusion
                that removes one of the request's own end-points.
        """
        include: list[str] = []
        for ref in constraints.include_nodes:
            include.append(self._resolve_node(ref, "include-node"))
        for link_ref in constraints.include_links:
            pair = self._resolve_link(link_ref, "include-link")
            inline = self._link_inline_elements(pair)
            # Any inline element pins the route to this link: each one belongs
            # to exactly one directed link. A link with none (a transceiver's
            # access link) is pinned by its two terminals.
            include.extend(inline[:1] or [pair[0], pair[1]])

        hidden_nodes: set[str] = set()
        for ref in constraints.exclude_nodes:
            uid = self._resolve_node(ref, "exclude-node")
            if uid in (src, dst):
                raise ConstraintError(
                    f"exclude-node {ref.node_uuid} is an end-point of the request"
                )
            hidden_nodes.add(uid)
        hidden_edges: set[tuple[str, str]] = set()
        for link_ref in constraints.exclude_links:
            pair = self._resolve_link(link_ref, "exclude-link")
            inline = self._link_inline_elements(pair)
            if inline:
                hidden_nodes.update(inline)
            else:
                hidden_edges.add(pair)

        explicit_pairs: frozenset[tuple[str, str]] | None = None
        if constraints.explicit_route:
            if constraints.include_links:
                explicit_pairs = frozenset(
                    self._resolve_link(r, "include-link")
                    for r in constraints.include_links
                )
            else:
                nodes = [src, *include, dst]
                explicit_pairs = frozenset(itertools.pairwise(nodes))
        return _RouteQuery(
            include=tuple(include),
            hidden_nodes=frozenset(hidden_nodes),
            hidden_edges=frozenset(hidden_edges),
            explicit_pairs=explicit_pairs,
        )

    def _satisfies_explicit_route(
        self, path: list[str], query: _RouteQuery, src: str, dst: str,
    ) -> bool:
        """With ``explicit-route``, every hop between terminals must be one the
        request named. A transceiver's access link to its own ROADM need not
        be listed: the end-points already fix it."""
        if query.explicit_pairs is None:
            return True
        terminals = self._terminals(path)
        for hop in itertools.pairwise(terminals):
            if hop in query.explicit_pairs or src in hop or dst in hop:
                continue
            return False
        return True

    def _gnpy_best_path(
        self, graph: Any, src: str, dst: str, include: tuple[str, ...],
    ) -> list[str] | None:
        """``gnpy.topology.request.compute_constrained_path`` on ``graph``.

        Every include is ``STRICT``: a T-API include is a requirement. GNPy
        treats an all-``LOOSE`` list it cannot satisfy by dropping the
        constraints and returning the unconstrained route, which would be
        exactly the silent relaxation this twin refuses to make.
        """
        from gnpy.topology.request import PathRequest, compute_constrained_path

        req = PathRequest(
            request_id=f"{src} -> {dst}",
            source=src,
            destination=dst,
            nodes_list=[*include, dst],
            loose_list=["STRICT"] * (len(include) + 1),
        )
        try:
            elements = compute_constrained_path(graph, req)
        except StopIteration:  # an include GNPy cannot find in this view
            return None
        if not elements or req.blocking_reason is not None:
            return None
        return [el.uid for el in elements]

    def candidate_paths(
        self,
        src: str,
        dst: str,
        constraints: PathConstraints = NO_CONSTRAINTS,
        k: int = 1,
    ) -> list[list[str]]:
        """Up to ``k`` candidate routes between two transceivers, best first.

        Each route is the ordered list of element UIDs. Every candidate
        satisfies ``constraints``; an empty list means none exists.

        Raises:
            ConstraintError: See :meth:`_resolve_route_query`.
        """
        import networkx as nx

        from twinlight.algorithms.routing import k_shortest_paths

        query = self._resolve_route_query(constraints, src, dst)
        graph, key_of = self._route_graph()
        if key_of(src) is None or key_of(dst) is None:
            return []
        view = nx.restricted_view(
            graph,
            [n for uid in query.hidden_nodes if (n := key_of(uid)) is not None],
            [
                (a, b) for u, v in query.hidden_edges
                if (a := key_of(u)) is not None and (b := key_of(v)) is not None
            ],
        )
        include_keys = [key_of(uid) for uid in query.include]
        if any(key is None for key in include_keys):
            return []  # an include the route graph does not contain

        candidates: list[list[str]] = []
        if self._gnpy_network is not None:
            best = self._gnpy_best_path(view, src, dst, query.include)
            if best is not None:
                candidates.append(best)
        if len(candidates) < k:
            # Ranked as GNPy ranks: Yen's algorithm on the edge ``weight`` —
            # fiber length (gnpy.tools.json_io.network_from_json) on both
            # graphs — which is the search compute_constrained_path itself
            # runs, so the best candidate above is also first here.
            more = k_shortest_paths(
                view, key_of(src), key_of(dst), k + len(candidates),
                include=include_keys, weight="weight",
            )
            for path in more:
                uids = [getattr(n, "uid", n) for n in path]
                if uids not in candidates:
                    candidates.append(uids)

        hidden = query.hidden_nodes
        return [
            path for path in candidates
            if not hidden.intersection(path)
            and self._satisfies_explicit_route(path, query, src, dst)
        ][:k]

    def get_service_path(
        self, sip_a_uuid: str, sip_z_uuid: str
    ) -> list[str] | None:
        """Return the best unconstrained route between two SIPs, or None.

        Cached per (source, destination) in ``_route_cache``, which link
        failure clears.
        """
        src = self.get_gnpy_uid(sip_a_uuid)
        dst = self.get_gnpy_uid(sip_z_uuid)
        if not src or not dst:
            return None

        cache_key = (src, dst)
        if cache_key in self._route_cache:
            return self._route_cache[cache_key] or None

        paths = self.candidate_paths(src, dst)
        if not paths:
            logger.warning("No path found between %s and %s", src, dst)
        self._route_cache[cache_key] = paths[0] if paths else []
        return paths[0] if paths else None

    def path_fiber_km(self, path_uids: list[str]) -> float:
        """Total fiber length of a route [km], spans of unknown length skipped."""
        elements_by_uid = self._topo_graph._gnpy_topo.elements_by_uid
        total = 0.0
        for uid in path_uids:
            if self._element_kind(uid) != "Fiber":
                continue
            km = _fiber_length_km(
                self._gnpy_uid_map.get(uid) if self._gnpy_uid_map else None,
                elements_by_uid.get(uid),
            )
            total += km or 0.0
        return total

    # -- T-API path computation -------------------------------------------

    def create_path_comp_service(self, service: PathCompService) -> PathCompService:
        """Compute up to ``rmsa.k_shortest_paths`` routes and record them.

        The service and its ``path`` objects are stored in the
        path-computation context, best route first. Nothing is allocated.

        Raises:
            KeyError: An end-point SIP does not exist.
            ConstraintError: A constraint does not resolve against the topology.
            NoPathError: No route satisfies the request. T-API requires at
                least one path per service, so none is recorded.
            ValueError: The UUID is already in use.
        """
        if service.uuid in self._path_comp_services:
            raise ValueError(f"path-comp-service {service.uuid} already exists")
        sip_a, sip_z = service.sip_uuids
        for sip in (sip_a, sip_z):
            if self.get_sip(sip) is None:
                raise KeyError(f"SIP {sip!r} not found")
        src, dst = self.get_gnpy_uid(sip_a), self.get_gnpy_uid(sip_z)
        constraints = service.path_constraints()
        routes = (
            self.candidate_paths(
                src, dst, constraints, self._config.rmsa.k_shortest_paths,
            )
            if src and dst else []
        )
        if not routes:
            raise NoPathError(
                f"No path between SIPs {sip_a!r} and {sip_z!r}"
                + ("" if constraints.is_empty else " satisfies the constraints")
            )

        refs: list[dict[str, str]] = []
        for rank, route in enumerate(routes, start=1):
            path = self._tapi_path(route, rank)
            self._paths[path["uuid"]] = path
            refs.append({"path-uuid": path["uuid"]})
        service.path = refs
        self._path_comp_services[service.uuid] = service
        return service

    def _tapi_path(self, route: list[str], rank: int) -> dict[str, Any]:
        """A ``tapi-path-computation:path`` for a route.

        Fiber length is reported as a ``cost-characteristic`` of the path's
        ``routing-constraint`` — the v2.6.0 place for a path's cost, and the
        quantity the candidates are ranked by.
        """
        refs = self.route_refs(route)
        return {
            "uuid": str(uuid_lib.uuid4()),
            "name": [{"value-name": "path-rank", "value": str(rank)}],
            "layer-protocol-name": "PHOTONIC_MEDIA",
            "direction": "UNIDIRECTIONAL",
            "link": [
                {"topology-uuid": t, "link-uuid": lk} for t, lk in refs["link"]
            ],
            "node-edge-point": [
                {"topology-uuid": t, "node-uuid": n, "node-edge-point-uuid": e}
                for t, n, e in refs["node-edge-point"]
            ],
            "routing-constraint": {
                "cost-characteristic": [{
                    "cost-name": "fiber-length-km",
                    "cost-value": f"{self.path_fiber_km(route):.3f}",
                    "cost-algorithm": "sum of the fiber span lengths on the path",
                }],
            },
        }

    def get_path_comp_services(self) -> list[PathCompService]:
        return list(self._path_comp_services.values())

    def get_path_comp_service(self, uuid: str) -> PathCompService | None:
        return self._path_comp_services.get(uuid)

    def delete_path_comp_service(self, uuid: str) -> bool:
        """Remove a path-comp-service and the paths it owns."""
        service = self._path_comp_services.pop(uuid, None)
        if service is None:
            return False
        for ref in service.path:
            self._paths.pop(ref["path-uuid"], None)
        return True

    def get_paths(self) -> list[dict[str, Any]]:
        return list(self._paths.values())

    def get_path(self, uuid: str) -> dict[str, Any] | None:
        return self._paths.get(uuid)

    async def get_or_compute_baseline(
        self, svc: ConnectivityService
    ) -> OpmBaseline | None:
        """Return a cached OpmBaseline, computing it on first access.

        Propagation is serialised through ``_propagation_lock`` so that
        multiple concurrent OPM requests don't flood GNPy with simultaneous
        deep-copy + propagation calls.
        """
        if not self._gnpy_available:
            return None

        if svc.uuid in self._baseline_cache:
            return self._baseline_cache[svc.uuid]

        # Determine path from SIP endpoints
        endpoints = svc.end_point
        if len(endpoints) < 2:
            return None

        sip_a = endpoints[0].service_interface_point.service_interface_point_uuid
        sip_z = endpoints[1].service_interface_point.service_interface_point_uuid
        # Prefer the path recorded at admission over re-deriving from the
        # current graph: failing a fiber must surface as "link-failed" on
        # the affected service, not as a silent auto-reroute through some
        # alternate path.
        path = self._allocated_path_uids(svc.uuid) or self.get_service_path(
            sip_a, sip_z
        )
        if not path:
            return None

        # Link-failure short-circuit: any UID on the path is in
        # ``_failed_links`` → emit a sentinel baseline so the OPM layer can
        # report ``status="link-failed"`` without invoking GNPy propagation
        # (which would crash on a missing edge anyway).
        if self.is_path_failed(path):
            from twinlight.physics.gnpy_adapter import OpmBaseline

            return OpmBaseline(
                gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0, pmd_ps=0.0,
                latency_ms=0.0, total_fiber_km=0.0, status="link-failed",
            )

        async with self._propagation_lock:
            # Re-check after acquiring lock (another coroutine may have computed it)
            if svc.uuid in self._baseline_cache:
                return self._baseline_cache[svc.uuid]

            centre_hz, width_hz, power_dbm = self._service_channel(svc, path)
            baseline = await self._backend.compute_baseline(
                path, svc.modulation_format.value,
                centre_frequency_hz=centre_hz, media_channel_width_hz=width_hz,
                launch_power_dbm=power_dbm,
            )
            if baseline is None:
                return None
            self._baseline_cache[svc.uuid] = baseline
            logger.debug(
                "Computed baseline for service %s: GSNR=%.2f dB",
                svc.uuid,
                baseline.gsnr_db,
            )
            return baseline

    def invalidate_baseline(self, service_uuid: str) -> None:
        """Remove a cached baseline so it is recomputed on next OPM request."""
        self._baseline_cache.pop(service_uuid, None)

    # -- /config plane: runtime device & twin parameter mutation -------------

    def apply_element_overrides(
        self,
        updates: dict[str, dict[str, Any]],
        redesign: bool = False,
    ) -> dict[str, set[str]]:
        """Mutate live GNPy element parameters from a {uid: {attr: value}} dict.

        Validates every (uid, attr, value) against ``physics.element_params``
        before any write so a partial batch never leaves the network half-
        updated. After applying, invalidates the baseline of every service
        whose path traverses a mutated element (looked up via the reverse
        index built in ``add_service``).

        The ``"failed"`` key on a Fiber UID is routed through
        ``set_link_failed`` so the API can pass a single ``{uid: {attr:
        value}}`` body through one method and have both parameter writes
        and link failure handled together.

        Returns:
            ``{element_uid: set_of_invalidated_service_uuids}`` for the
            caller to surface in the API response.

        Raises:
            KeyError: An updated UID is not in the live GNPy network.
            ParamValidationError: An attribute or value violates the
                allow-list. No mutations are applied.
        """
        if not self._backend.available:
            raise RuntimeError(
                "Physical-layer backend not loaded — element overrides require it"
            )

        # Validate first: resolve every UID and (attr, value) without writing.
        # ``failed`` keys are split out and dispatched to set_link_failed
        # below; everything else goes through the backend's allow-list.
        resolved: list[tuple[str, str, Any]] = []
        failure_actions: list[tuple[str, bool]] = []
        for uid, attrs in updates.items():
            if uid not in self._backend.uid_map:
                raise KeyError(f"Unknown element UID: {uid!r}")
            for attr, value in attrs.items():
                if attr == "failed":
                    if not isinstance(value, bool):
                        raise ValueError(
                            f"{uid}.failed must be bool, got "
                            f"{type(value).__name__}"
                        )
                    failure_actions.append((uid, value))
                    continue
                # backend.write_attribute does the heavy validation;
                # validation failures surface as ParamValidationError
                # from inside the write loop below. Earlier good values
                # stay applied — partial-batch semantics match how RMSA
                # also handles failures.
                resolved.append((uid, attr, value))

        invalidated: dict[str, set[str]] = {}
        for uid, attr, value in resolved:
            self._remember_startup(uid, attr)
            self._backend.write_attribute(uid, attr, value)
            self._element_overrides.setdefault(uid, {})[attr] = value
            self._bump_revision()
            affected = set(self._element_to_services.get(uid, ()))
            for svc_uuid in affected:
                self.invalidate_baseline(svc_uuid)
            if affected:
                invalidated[uid] = affected

        # Apply link failure / restore after parameter writes so an operator
        # can bump a span's loss and immediately fail it in one /config/set
        # call (parameter bump is durable, failure can be cleared later).
        for uid, failed in failure_actions:
            affected = self.set_link_failed(uid, failed)
            if affected:
                invalidated.setdefault(uid, set()).update(affected)

        # Per-UID route cache stays valid for parameter-only edits (length /
        # topology didn't change); set_link_failed clears it when needed.

        # Optional re-design pass: re-equalise the backend's design state
        # against the mutated parameters (GNPy: ``designed_network``;
        # other backends may no-op or raise). This is opt-in because it
        # is expensive on large topologies and resets some operator-set
        # state. After redesign, every baseline must be recomputed —
        # clear the whole cache, not just the per-element entries.
        if redesign and self._backend.available:
            self._backend.redesign()
            self._baseline_cache.clear()
            self._bump_revision()
            # Reverse-index entries were keyed by UIDs that did not change
            # across redesign, so they stay valid; same for failed-links.
            for svc_uuid in list(self._services):
                invalidated.setdefault("__redesign__", set()).add(svc_uuid)

        return invalidated

    def apply_twin_overrides(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Mutate runtime-safe twin-config knobs from a *flat* dotted-key dict.

        E.g. ``{"transients.phase_noise.enabled": False, "rmsa.qot_margin_db": 2.0}``.

        Returns the (validated) updates dict for echoing back in API responses.
        Validation rejects any key not in ``state.twin_overrides.TWIN_ALLOWED``.
        Existing OPM samples are not invalidated — transient flags are read
        live on every call to ``apply_all_transients``, and margin changes
        gate only future admissions.
        """
        from twinlight.state.twin_overrides import apply as _apply

        _apply(self._config, updates)
        self._twin_overrides.update(updates)
        if updates:
            self._bump_revision()
        return updates

    def get_element_overrides(self) -> dict[str, dict[str, Any]]:
        """Snapshot of every per-element override the user has applied."""
        # Return a shallow copy so callers don't mutate context state.
        return {uid: dict(attrs) for uid, attrs in self._element_overrides.items()}

    def apply_element_overrides_atomic(
        self, updates: dict[str, dict[str, Any]],
    ) -> dict[str, set[str]]:
        """All-or-nothing variant of ``apply_element_overrides``.

        ``apply_element_overrides`` keeps the writes that preceded a failing
        one. Callers that validated a whole batch up front (the
        ``/config/parameters`` endpoints) promise their clients that either
        every value lands or none does, so a write failing despite that
        validation rolls back: earlier writes are reverted, the override
        record is restored, and the touched services' baselines are
        dropped, since some propagations may already have seen new values.
        """
        previous: dict[tuple[str, str], Any] = {}
        for uid, attrs in updates.items():
            for attr in attrs:
                if attr == "failed" or uid not in self._backend.uid_map:
                    continue
                try:
                    previous[(uid, attr)] = self._backend.capture_attribute(uid, attr)
                except (KeyError, ValueError):  # the write below reports it
                    continue
        prior_records = self.get_element_overrides()
        try:
            return self.apply_element_overrides(updates)
        except Exception:
            # Capture tokens restore exactly what was stored, an unset value
            # included, which a write of the previous user value could not.
            for (uid, attr), token in previous.items():
                try:
                    self._backend.restore_attribute(uid, attr, token)
                except Exception:  # keep unwinding the rest
                    logger.exception("Rollback of %s.%s failed", uid, attr)
            self._element_overrides = prior_records
            for uid in updates:
                for svc_uuid in self.services_for_element(uid):
                    self.invalidate_baseline(svc_uuid)
            raise

    def get_twin_overrides(self) -> dict[str, Any]:
        return dict(self._twin_overrides)

    # -- Running-datastore revision, startup values and revert ---------------

    @property
    def revision(self) -> int:
        """Counter bumped by every configuration change (see ``__init__``)."""
        return self._revision

    @property
    def last_modified(self) -> datetime:
        return self._last_modified

    def _bump_revision(self) -> None:
        self._revision += 1
        self._last_modified = datetime.now(UTC)

    def _remember_startup(self, uid: str, attr: str) -> None:
        """Record (uid, attr)'s value before the twin first changes it."""
        key = (uid, attr)
        if key in self._element_startup:
            return
        try:
            self._element_startup[key] = (
                self._backend.read_attribute(uid, attr),
                self._backend.capture_attribute(uid, attr),
            )
        except Exception:  # noqa: BLE001 - the write itself will report it
            return

    def startup_element_value(self, uid: str, attr: str) -> Any:
        """The value ``attr`` had when the twin started (user units).

        ``failed`` is always ``False`` at startup: a restored snapshot's
        failures are state applied after startup, like any other change.
        """
        if attr == "failed":
            return False
        recorded = self._element_startup.get((uid, attr))
        if recorded is not None:
            return recorded[0]
        return self._backend.read_attribute(uid, attr)

    def startup_twin_value(self, key: str) -> Any:
        """The value twin-config ``key`` had when the twin started."""
        from twinlight.state.twin_overrides import read_key

        return read_key(self._startup_config, key)

    def revert_element_overrides(
        self, pairs: list[tuple[str, str]],
    ) -> dict[str, set[str]]:
        """Put (uid, attr) pairs back to their startup values.

        Restores exactly what the engine stored at startup (via the backend
        capture token), so an attribute that was unset becomes unset again.
        ``failed`` restores the fiber. The override record is dropped and the
        affected services' baselines are invalidated, as for a write.

        Returns ``{uid: invalidated service UUIDs}``.
        """
        invalidated: dict[str, set[str]] = {}
        for uid, attr in pairs:
            if attr == "failed":
                affected = self.set_link_failed(uid, False)
            else:
                recorded = self._element_startup.get((uid, attr))
                if recorded is not None:
                    self._backend.restore_attribute(uid, attr, recorded[1])
                attrs = self._element_overrides.get(uid)
                if attrs is not None:
                    attrs.pop(attr, None)
                    if not attrs:
                        del self._element_overrides[uid]
                affected = self.services_for_element(uid)
                for svc_uuid in affected:
                    self.invalidate_baseline(svc_uuid)
                self._bump_revision()
            if affected:
                invalidated.setdefault(uid, set()).update(affected)
        return invalidated

    def revert_twin_overrides(self, keys: list[str]) -> dict[str, Any]:
        """Put twin-config keys back to their startup values.

        Returns ``{key: restored value}``.
        """
        from twinlight.state.twin_overrides import _walk_for_write

        restored: dict[str, Any] = {}
        for key in keys:
            value = self.startup_twin_value(key)
            parent, leaf = _walk_for_write(self._config, key)
            setattr(parent, leaf, value)
            self._twin_overrides.pop(key, None)
            restored[key] = value
        if restored:
            self._bump_revision()
        return restored

    def revert_all_overrides(self) -> None:
        """Return every element, fiber and twin-config setting to startup."""
        pairs = [
            (uid, attr)
            for uid, attrs in self._element_overrides.items()
            for attr in attrs
        ]
        pairs += [(uid, "failed") for uid in sorted(self._failed_links)]
        self.revert_element_overrides(pairs)
        self.revert_twin_overrides(list(self._twin_overrides))

    def get_failed_links(self) -> set[str]:
        return set(self._failed_links)

    async def preview_qot(
        self,
        element_values: dict[str, dict[str, Any]],
        element_reverts: list[tuple[str, str]],
        margin_db: float | None = None,
    ) -> list[dict[str, Any]]:
        """Predict the QoT of every service a change would touch, without
        making it.

        ``element_values`` (``{uid: {attr: value}}``, ``failed`` allowed) and
        ``element_reverts`` (``[(uid, attr)]`` going back to startup) are
        assumed validated. Parameter changes are written to the live
        elements, the affected services' allocated paths are propagated, and
        every written attribute is restored from its capture token in a
        ``finally``. Failure changes are evaluated hypothetically — a path
        through a fiber that would be failed reports ``link-failed`` — so the
        routing graphs are never touched.

        The whole write → propagate → restore sequence runs under
        ``_propagation_lock`` and the backends' ``compute_baseline`` does not
        yield to the event loop, so no other request can observe the
        tentative values. Nothing is cached and no revision is bumped.

        ``margin_db`` is the admission margin to judge against (the candidate
        value when the change also moves ``rmsa.qot_margin_db``).
        """
        from twinlight.physics.modulation import get_params

        touched = set(element_values) | {uid for uid, _ in element_reverts}
        affected: set[str] = set()
        for uid in touched:
            affected |= self.services_for_element(uid)
        services = [
            svc for uuid in sorted(affected)
            if (svc := self._services.get(uuid)) is not None
        ]
        if margin_db is None:
            margin_db = self._config.rmsa.qot_margin_db

        current = {
            svc.uuid: await self.get_or_compute_baseline(svc) for svc in services
        }

        failed_after = set(self._failed_links)
        for uid, attrs in element_values.items():
            if "failed" in attrs:
                (failed_after.add if attrs["failed"] else failed_after.discard)(uid)
        for uid, attr in element_reverts:
            if attr == "failed":
                failed_after.discard(uid)

        predicted: dict[str, OpmBaseline | None] = {}
        async with self._propagation_lock:
            tokens: list[tuple[str, str, Any]] = []
            try:
                for uid, attrs in element_values.items():
                    for attr, value in attrs.items():
                        if attr == "failed":
                            continue
                        tokens.append(
                            (uid, attr, self._backend.capture_attribute(uid, attr))
                        )
                        self._backend.write_attribute(uid, attr, value)
                for uid, attr in element_reverts:
                    recorded = self._element_startup.get((uid, attr))
                    if attr == "failed" or recorded is None:
                        continue
                    tokens.append(
                        (uid, attr, self._backend.capture_attribute(uid, attr))
                    )
                    self._backend.restore_attribute(uid, attr, recorded[1])
                for svc in services:
                    path = self._allocated_path_uids(svc.uuid)
                    if not path:
                        predicted[svc.uuid] = None
                    elif any(uid in failed_after for uid in path):
                        from twinlight.physics.gnpy_adapter import OpmBaseline

                        predicted[svc.uuid] = OpmBaseline(
                            gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0,
                            pmd_ps=0.0, latency_ms=0.0, total_fiber_km=0.0,
                            status="link-failed",
                        )
                    else:
                        centre_hz, width_hz, power_dbm = self._service_channel(svc, path)
                        predicted[svc.uuid] = await self._backend.compute_baseline(
                            path, svc.modulation_format.value,
                            centre_frequency_hz=centre_hz, media_channel_width_hz=width_hz,
                            launch_power_dbm=power_dbm,
                        )
            finally:
                for uid, attr, token in reversed(tokens):
                    try:
                        self._backend.restore_attribute(uid, attr, token)
                    except Exception:  # keep unwinding the rest
                        logger.exception("What-if restore of %s.%s failed", uid, attr)

        def summary(b: OpmBaseline | None) -> dict[str, Any] | None:
            if b is None:
                return None
            if b.status == "link-failed":
                return {"status": "link-failed", "gsnr_db": None, "osnr_db": None}
            return {
                "status": b.status or "ok",
                "gsnr_db": round(b.gsnr_db, 4),
                "osnr_db": round(b.osnr_ase_db, 4),
                "cd_ps_nm": round(b.cd_ps_nm, 4),
                "pmd_ps": round(b.pmd_ps, 4),
            }

        report: list[dict[str, Any]] = []
        for svc in services:
            required = get_params(svc.modulation_format).req_gsnr_db
            before, after = summary(current[svc.uuid]), summary(predicted[svc.uuid])
            gsnr_before = before["gsnr_db"] if before else None
            gsnr_after = after["gsnr_db"] if after else None
            report.append({
                "service": svc.uuid,
                "name": next(
                    (n.value for n in svc.name or [] if n.value_name == "service-name"),
                    None,
                ),
                "modulation_format": svc.modulation_format.value,
                "required_gsnr_db": required,
                "qot_margin_db": margin_db,
                "current": before,
                "predicted": after,
                "delta_gsnr_db": (
                    round(gsnr_after - gsnr_before, 4)
                    if gsnr_before is not None and gsnr_after is not None
                    else None
                ),
                # Two thresholds, because they answer different questions:
                # would the lightpath still work, and would the controller
                # still admit it today?
                "above_required": gsnr_after is not None and gsnr_after >= required,
                "meets_admission_margin": (
                    gsnr_after is not None and gsnr_after >= required + margin_db
                ),
            })
        return report

    def services_for_element(self, uid: str) -> set[str]:
        """Service UUIDs whose path traverses the element with ``uid``."""
        return set(self._element_to_services.get(uid, ()))

    # -- /config plane: fiber link failure (M3) ------------------------------

    @staticmethod
    def _uids_from_edges(edges: list[tuple]) -> list[str]:
        """Recover the ordered path UID list from a list of consecutive edges."""
        if not edges:
            return []
        return [edges[0][0]] + [b for _a, b in edges]

    def _walk_to_terminal(self, start_uid: str, direction: str) -> str | None:
        """Walk through inline Edfa/Fiber elements to the nearest Roadm/Transceiver.

        ``direction`` is ``"pred"`` (predecessors) or ``"succ"`` (successors).
        Returns the first ROADM or Transceiver UID encountered, or ``None`` if
        the walk dead-ends or loops. Used by ``_build_fiber_to_link_index``.
        """
        g = self._topo_graph.graph
        # GNPy's designed_network() may split a long fiber into
        # sub-span Fiber nodes (e.g. ``fiber (A → B)-_(1/4)``) that
        # exist in the GNPy uid_map but not in the parsed topo_graph.
        # Walking from such a node is meaningless here; bail.
        if start_uid not in g:
            return None
        cur = start_uid
        seen = {cur}
        while True:
            neighbours = (
                list(g.predecessors(cur)) if direction == "pred"
                else list(g.successors(cur))
            )
            if not neighbours:
                return None
            nxt = neighbours[0]
            if nxt in seen:
                return None
            seen.add(nxt)
            el = self._gnpy_uid_map.get(nxt)
            if el is None:
                return None
            kind = _kind_name(el)
            if kind in ("Roadm", "Transceiver"):
                return nxt
            if kind not in ("Edfa", "Fiber"):
                return None
            cur = nxt

    def _build_fiber_to_link_index(self) -> None:
        """Populate ``_fiber_to_link_refs`` for every Fiber in the network."""
        for uid, el in self._gnpy_uid_map.items():
            if _kind_name(el) != "Fiber":
                continue
            a = self._walk_to_terminal(uid, "pred")
            z = self._walk_to_terminal(uid, "succ")
            if not a or not z:
                continue
            refs: list[tuple[str, str]] = []
            # Both directions: GNPy uses unidirectional links so the user-
            # visible TAPI link may appear as either (a,z) or (z,a).
            for pair in ((a, z), (z, a)):
                ref = self._gnpy_pair_to_link_ref.get(pair)
                if ref and ref not in refs:
                    refs.append(ref)
            if refs:
                self._fiber_to_link_refs[uid] = refs

    def _build_element_terminal_index(self) -> None:
        """Populate ``_element_terminals`` for every inline Fiber and Edfa.

        Walks the backend's own graph when it has one: GNPy's designed
        network contains booster amplifiers and fiber sub-spans that the
        parsed topology graph does not. The EGN backend has no graph, so it
        falls back to the parsed topology.
        """
        net = getattr(self._backend, "network", None)
        uid_map = self._backend.uid_map
        graph = net if net is not None else self._topo_graph.graph

        def uid_of(node: Any) -> str:
            return str(node.uid) if net is not None else str(node)

        def kind_of(node: Any) -> str:
            return _kind_name(node if net is not None else uid_map.get(node))

        def walk(start: Any, step: Any) -> tuple[str | None, int]:
            """(terminal UID or None, number of hops to it)."""
            cur, seen = start, {start}
            while True:
                neighbours = list(step(cur))
                if len(neighbours) != 1:
                    return None, 0
                cur = neighbours[0]
                if cur in seen:
                    return None, 0
                seen.add(cur)
                kind = kind_of(cur)
                if kind in ("Roadm", "Transceiver"):
                    return uid_of(cur), len(seen) - 1
                if kind not in ("Edfa", "Fiber"):
                    return None, 0

        positions: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for node in graph.nodes:
            if kind_of(node) in ("Fiber", "Edfa"):
                (a, hops), (z, _) = (
                    walk(node, graph.predecessors),
                    walk(node, graph.successors),
                )
                self._element_terminals[uid_of(node)] = (a, z)
                if a is not None and z is not None:
                    positions.setdefault((a, z), []).append((hops, uid_of(node)))
        self._link_elements = {
            pair: [uid for _hops, uid in sorted(items)]
            for pair, items in positions.items()
        }

    def link_elements(self) -> dict[tuple[str, str], list[str]]:
        """(upstream, downstream) terminals → inline element UIDs, in order.

        Links are directional, as GNPy's are: a bidirectional span pair is
        two entries.
        """
        return {pair: list(uids) for pair, uids in self._link_elements.items()}

    def element_terminals(self, uid: str) -> tuple[str | None, str | None]:
        """(upstream, downstream) ROADM/transceiver UIDs of an inline element.

        ``(None, None)`` for terminals themselves and for elements the walk
        could not place.
        """
        return self._element_terminals.get(uid, (None, None))

    def set_link_failed(self, fiber_uid: str, failed: bool) -> set[str]:
        """Fail or restore a fiber link, mutating routing graph + TAPI state.

        Failure semantics:
        * The fiber's two graph edges are removed from the NetworkX topology
          graph so ``get_service_path`` (and RMSA admission) can no longer
          route through it. Edges are stashed so a later restore re-adds the
          *exact* original edge data.
        * The ``_route_cache`` is flushed (any cached path may now be stale).
        * Every service in the reverse index for this UID has its baseline
          dropped; the next OPM sample short-circuits to ``status=link-failed``
          because the service's *allocated* path still includes the failed UID
          (we deliberately do not auto-reroute admitted services).
        * The backing TAPI Link object(s) have ``operational-state`` flipped
          to ``DISABLED`` so TAPI clients see the failure too.

        Returns the set of affected service UUIDs.

        Raises:
            KeyError: ``fiber_uid`` is not a Fiber in the GNPy network.
        """
        from twinlight.models.common import OperationalState

        if not self._backend.is_fiber(fiber_uid):
            raise KeyError(
                f"{fiber_uid!r} is not a Fiber (link failure only applies "
                f"to fiber spans)"
            )
        # The backend's uid_map may carry GNPy element objects (GnpyBackend)
        # or lightweight stubs (EgnBackend) — both expose ``.uid``.
        el = self._backend.uid_map.get(fiber_uid)

        # Idempotent: no-op if state is already what was requested.
        if failed and fiber_uid in self._failed_links:
            return self.services_for_element(fiber_uid)
        if not failed and fiber_uid not in self._failed_links:
            return self.services_for_element(fiber_uid)

        # Mutate both routing graphs: ``_topo_graph.graph`` powers the
        # NetworkX k-shortest fallback used by ``get_service_path``, and
        # ``_gnpy_network`` powers GNPy's ``compute_constrained_path``
        # (the preferred path engine when GNPy is loaded). The two graphs
        # key nodes differently — TopologyGraph uses UID strings, the GNPy
        # DiGraph uses element *objects* — so we resolve the right node
        # key per graph before touching edges.
        graphs: list = [(self._topo_graph.graph, fiber_uid)]
        if self._gnpy_network is not None and el in self._gnpy_network:
            graphs.append((self._gnpy_network, el))

        # Each entry: (graph, node_key, in_edges, out_edges).
        if failed:
            stash: list[tuple] = []
            for g, key in graphs:
                in_edges = list(g.in_edges(key, data=True))
                out_edges = list(g.out_edges(key, data=True))
                stash.append((g, key, in_edges, out_edges))
                g.remove_edges_from(
                    [(u, v) for u, v, _ in in_edges + out_edges]
                )
            self._removed_edges[fiber_uid] = stash
            self._failed_links.add(fiber_uid)
            new_op = OperationalState.DISABLED
        else:
            stash = self._removed_edges.pop(fiber_uid, [])
            for g, _key, in_edges, out_edges in stash:
                for u, v, d in in_edges:
                    g.add_edge(u, v, **d)
                for u, v, d in out_edges:
                    g.add_edge(u, v, **d)
            self._failed_links.discard(fiber_uid)
            new_op = OperationalState.ENABLED

        # Cached routes may include this fiber; rebuild lazily on demand.
        self._route_cache.clear()
        self._bump_revision()

        affected = self.services_for_element(fiber_uid)
        for svc_uuid in affected:
            self.invalidate_baseline(svc_uuid)

        # Flip TAPI Link operational-state on the underlying link(s).
        for topo_uuid, link_uuid in self._fiber_to_link_refs.get(fiber_uid, []):
            link = self.get_link(topo_uuid, link_uuid)
            if link is not None:
                link.operational_state = new_op

        # Backend-side notification (no-op for GNPy, updates _failed_fibers
        # for EGN since it has no DiGraph to remove edges from).
        self._backend.notify_fiber_failed(fiber_uid, failed)

        return affected

    def is_path_failed(self, path_uids: list[str]) -> bool:
        """True if any UID on the path is a currently-failed fiber."""
        if not self._failed_links:
            return False
        return any(uid in self._failed_links for uid in path_uids)

    def _allocated_path_uids(self, service_uuid: str) -> list[str] | None:
        """Return the ordered GNPy path UIDs recorded at admission, or None."""
        alloc = self._service_allocation.get(service_uuid)
        if alloc is None:
            return None
        return self._uids_from_edges(alloc[0])

    def _path_uids_to_hops(self, path_uids: list[str]) -> list[dict] | None:
        """Build hop list (Transceiver/Roadm + distances) from path UID list.

        Fiber lengths come from the live GNPy element when one is available
        (the ``gnpy`` backend), and otherwise from the parsed topology JSON —
        the ``egn`` backend builds no GNPy network, so ``_gnpy_uid_map`` is
        empty and the parsed elements are the only source of span lengths.
        Spans whose length cannot be determined from either source are
        skipped rather than treated as zero, so a distance is the sum of the
        spans actually known.
        """
        elements_by_uid = getattr(
            self._topo_graph._gnpy_topo, "elements_by_uid", {}
        )
        hops: list[dict] = []
        accumulated_km = 0.0

        for uid in path_uids:
            el = self._gnpy_uid_map.get(uid) if self._gnpy_uid_map else None
            if el is not None:
                el_type = _kind_name(el)
            else:
                parsed = elements_by_uid.get(uid)
                el_type = getattr(parsed, "type", "") if parsed else ""

            if el_type == "Fiber":
                span_km = _fiber_length_km(el, elements_by_uid.get(uid))
                if span_km is not None:
                    accumulated_km += span_km
            elif el_type in ("Transceiver", "Roadm"):
                if hops:
                    hops[-1]["distance_km_to_next"] = (
                        round(accumulated_km, 2) if accumulated_km else None
                    )
                accumulated_km = 0.0
                hops.append({
                    "uid": uid,
                    "type": el_type,
                    "distance_km_to_next": None,
                })

        return hops or None

    def get_service_path_hops(
        self, svc: ConnectivityService
    ) -> list[dict] | None:
        """Return an ordered list of named hops for display.

        Walks the raw GNPy path, accumulates Fiber span distances between
        Transceiver and Roadm nodes, and skips inline Edfa elements.

        Each hop dict:
            uid: str               — GNPy element UID / human-readable name
            type: str              — "Transceiver" or "Roadm"
            distance_km_to_next:   — accumulated fiber km before the next hop,
              float | None           or None for the last hop

        Returns None if no path exists.

        The route is the one recorded at admission. Re-deriving it from the
        SIPs would show the unconstrained shortest route, which is not the
        one a constrained or second-candidate service actually took.
        """
        endpoints = svc.end_point
        if len(endpoints) < 2:
            return None
        sip_a = endpoints[0].service_interface_point.service_interface_point_uuid
        sip_z = endpoints[1].service_interface_point.service_interface_point_uuid
        path_uids = self._allocated_path_uids(svc.uuid) or self.get_service_path(
            sip_a, sip_z
        )
        if not path_uids:
            return None
        return self._path_uids_to_hops(path_uids)

    def get_path_hops_between_sips(
        self, sip_a: str, sip_z: str
    ) -> list[dict] | None:
        """Return hop list for the path between two SIPs (no service required)."""
        path_uids = self.get_service_path(sip_a, sip_z)
        if not path_uids:
            return None
        return self._path_uids_to_hops(path_uids)

    async def get_path_baseline(
        self, sip_a: str, sip_z: str, modulation_format: str
    ) -> tuple[OpmBaseline, int, float] | None:
        """QoT of the best route between two SIPs, without a service or cache.

        Evaluated on the channel admission would give a new service of this
        format on that route — the first free block inside the amplifier band,
        at the default launch power — so the estimate matches what creating
        the service would see.

        Returns ``(baseline, central frequency [Hz], launch power [dBm])``, or
        None when the backend is unavailable or there is no route.
        """
        if not self._backend.available:
            return None
        path = self.get_service_path(sip_a, sip_z)
        if not path:
            return None
        fmt = ModulationFormat(modulation_format)
        centre_hz = self._preview_centre_frequency(path, fmt)
        power_dbm = self.default_launch_power_dbm
        async with self._propagation_lock:
            baseline = await self._backend.compute_baseline(
                path, modulation_format,
                centre_frequency_hz=centre_hz,
                media_channel_width_hz=self._block_width(fmt) * self._slot_hz(),
                launch_power_dbm=power_dbm,
            )
        return None if baseline is None else (baseline, centre_hz, power_dbm)

    def _preview_centre_frequency(self, path: list[str], fmt: ModulationFormat) -> int:
        """Centre of the block admission would assign ``fmt`` on ``path``.

        The first free block inside the amplifier band; when the band is full,
        the first block inside it regardless of occupancy (the physics is
        still meaningful, though admission would refuse).
        """
        width = self._block_width(fmt)
        guard = self._config.rmsa.default_guardband_slots
        assignable = self._assignable_slots(path)
        edges = path_uids_to_edges(path)
        start = first_fit(self._spectrum_state, edges, width - guard, guard, assignable)
        if start is None:
            start = first_fit(SpectrumState(self._config.spectrum.num_slots),
                              edges, width - guard, guard, assignable) or 0
        lower, upper = self._slot_range_hz(start, width)
        return (lower + upper) // 2

    @property
    def default_launch_power_dbm(self) -> float:
        """Launch power of a channel that requests none: the equipment
        library's SI ``power_dbm``, the per-channel power the line is
        designed for."""
        return self._backend.spectral_defaults.power_dbm

    def _service_channel(
        self, svc: ConnectivityService, path: list[str],
    ) -> tuple[int, int, float]:
        """(central frequency [Hz], media channel width [Hz], launch power [dBm])
        the service runs at.

        Taken from its allocation; a service without one (only possible for
        state built outside admission) is evaluated where admission would
        put it.
        """
        power_dbm = self._service_launch_power.get(svc.uuid, self.default_launch_power_dbm)
        allocation = self._service_allocation.get(svc.uuid)
        if allocation is None:
            fmt = svc.modulation_format
            return (
                self._preview_centre_frequency(path, fmt),
                self._block_width(fmt) * self._slot_hz(),
                power_dbm,
            )
        _edges, start, width = allocation
        lower, upper = self._slot_range_hz(start, width)
        return (lower + upper) // 2, upper - lower, power_dbm

    def _build_gnpy_topology_ref_indexes(self) -> None:
        """Build gnpy_uid -> (topo_uuid, node_uuid) and (src,dst) -> (topo_uuid, link_uuid)."""
        for topo in self.topology_context.topology:
            for node in topo.node:
                for nv in node.name:
                    if getattr(nv, "value_name", None) == "node-name":
                        self._gnpy_uid_to_node_ref[nv.value] = (topo.uuid, node.uuid)
                        break
            node_to_gnpy: dict[str, str] = {}
            for node in topo.node:
                for nv in node.name:
                    if getattr(nv, "value_name", None) == "node-name":
                        node_to_gnpy[node.uuid] = nv.value
                        break
            for link in topo.link:
                if len(link.node_edge_point) >= 2:
                    n1, n2 = link.node_edge_point[0], link.node_edge_point[1]
                    g1, g2 = node_to_gnpy.get(n1.node_uuid), node_to_gnpy.get(n2.node_uuid)
                    if g1 and g2:
                        self._gnpy_pair_to_link_ref[(g1, g2)] = (topo.uuid, link.uuid)
                        self._link_ref_to_pair[(topo.uuid, link.uuid)] = (g1, g2)

    def route_refs(self, path_uids: list[str]) -> dict[str, list[tuple[str, ...]]]:
        """T-API references for a route given as element UIDs.

        Returns ``{"link": [(topology, link)], "node": [(topology, node)],
        "node-edge-point": [(topology, node, nep)]}``, each in path order.
        The links and NEPs are what a T-API ``path`` carries; the node list
        is for the internal compute-path view.
        """
        terminals = self._terminals(path_uids)
        nodes = [
            ref for uid in terminals
            if (ref := self._gnpy_uid_to_node_ref.get(uid)) is not None
        ]
        links: list[tuple[str, ...]] = []
        neps: list[tuple[str, ...]] = []
        for hop in itertools.pairwise(terminals):
            ref = self._gnpy_pair_to_link_ref.get(hop)
            if ref is None:
                continue
            links.append(ref)
            link = self.get_link(*ref)
            for nep in link.node_edge_point if link is not None else []:
                key = (nep.topology_uuid, nep.node_uuid, nep.node_edge_point_uuid)
                if not neps or neps[-1] != key:
                    neps.append(key)
        return {"link": links, "node": list(nodes), "node-edge-point": neps}

    def get_equipment_list(self) -> list[dict]:
        """Return equipment inventory from GNPy elements or parsed topology.

        Each item has uuid, name, equipment-type, and optionally length-km (Fiber).
        State is derived from _gnpy_uid_map when GNPy is loaded, else from topology.
        """
        equipment: list[dict] = []

        if self._gnpy_uid_map:
            for uid, el in self._gnpy_uid_map.items():
                el_type = _kind_name(el)
                item: dict = {
                    "uuid": uid,
                    "name": [{"value-name": "element-uid", "value": uid}],
                    "equipment-type": el_type,
                }
                if el_type == "Fiber" and hasattr(el, "params") and el.params:
                    try:
                        length_m = float(getattr(el.params, "length", 0))
                        item["length-km"] = round(length_m / 1000.0, 4)
                    except (TypeError, ValueError):
                        pass
                equipment.append(item)
        else:
            for el in self._topo_graph._gnpy_topo.elements:
                item = {
                    "uuid": el.uid,
                    "name": [{"value-name": "element-uid", "value": el.uid}],
                    "equipment-type": el.type,
                }
                if el.type == "Fiber" and el.params:
                    try:
                        length_m = float(el.params.get("length", 0))
                        item["length-km"] = round(length_m / 1000.0, 4)
                    except (TypeError, ValueError):
                        pass
                equipment.append(item)

        return equipment

    # -- Snapshot / restore (checkpoints) ------------------------------------

    def snapshot(self, path: Path) -> Path:
        """Serialize connectivity and spectrum state to a JSON file.

        Does not persist topology (reloaded from config on startup).
        Caller must ensure the directory exists.
        """
        allocation_ser: dict = {}
        for uuid, (path_edges, start, block_width) in self._service_allocation.items():
            allocation_ser[uuid] = {
                "path_edges": [list(e) for e in path_edges],
                "start": start,
                "block_width": block_width,
                "launch_power_dbm": self._service_launch_power.get(uuid),
            }
        data = {
            "version": 1,
            "timestamp": datetime.now(UTC).isoformat(),
            # Backend name lets restore reject mismatched snapshots
            # (services admitted under different QoT thresholds would
            # be replayed against the wrong physics — silently
            # falsifying experiments). Same string as physics.backend.
            "backend": self._backend.name,
            "spectrum": self._spectrum_state.to_dict(),
            "services": [s.model_dump(by_alias=True) for s in self._services.values()],
            "service_allocation": allocation_ser,
            # /config plane state. Stored in *user units* — the same
            # values the user supplied — so restore replays via
            # apply_element_overrides without any unit re-derivation.
            "element_overrides": self.get_element_overrides(),
            "failed_links": sorted(self._failed_links),
            "twin_overrides": dict(self._twin_overrides),
        }
        path.write_text(json.dumps(data, indent=2))
        logger.info("Snapshot written: %s (%d services)", path, len(self._services))
        return path

    def restore_from(self, path: Path) -> None:
        """Restore connectivity and spectrum state from a snapshot file.

        Topology and GNPy network are unchanged (already loaded in __init__).
        Clears existing services and spectrum, then applies the snapshot.
        """
        data = json.loads(path.read_text())
        version = data.get("version")
        if version != 1:
            raise ValueError(
                f"Unsupported snapshot version: {version!r} (expected 1)"
            )
        # Cross-backend restore is rejected: the snapshot's services
        # were admitted under the recorded backend's QoT thresholds,
        # and replaying them against a different physics engine would
        # silently falsify whatever experiment the snapshot represents.
        snap_backend = data.get("backend")
        if snap_backend and snap_backend != self._backend.name:
            raise ValueError(
                f"Snapshot was taken under physics backend "
                f"{snap_backend!r} but the running process is "
                f"{self._backend.name!r} — start the twin with "
                f"--physics-backend {snap_backend} to restore this snapshot"
            )

        # Clear existing live state. Restore replaces, never merges:
        # un-fail every currently-failed link, drop overrides + caches,
        # then re-apply whatever the snapshot recorded.
        self._services.clear()
        self._service_allocation.clear()
        self._service_launch_power.clear()
        self._baseline_cache.clear()
        self._element_to_services.clear()
        # Revert, not just forget: dropping the override records alone would
        # leave the overridden values live on the elements and in the config,
        # and the restored twin would silently keep them.
        self.revert_all_overrides()

        # Restore spectrum
        self._spectrum_state = SpectrumState.from_dict(data["spectrum"])

        # Restore services
        for svc_dict in data.get("services", []):
            svc = ConnectivityService.model_validate(svc_dict)
            self._services[svc.uuid] = svc

        # Restore allocation (no need to re-allocate spectrum; already in state)
        for uuid, alloc in data.get("service_allocation", {}).items():
            path_edges = [tuple(e) for e in alloc["path_edges"]]
            self._service_allocation[uuid] = (
                path_edges,
                int(alloc["start"]),
                int(alloc["block_width"]),
            )
            # Snapshots taken before launch power was tracked ran every
            # channel at the line default.
            power = alloc.get("launch_power_dbm")
            self._service_launch_power[uuid] = (
                float(power) if power is not None else self.default_launch_power_dbm
            )
            # Rebuild the /config reverse index from path_edges (each edge is
            # (uid_a, uid_b) for consecutive GNPy elements, so we get all UIDs
            # by taking the first endpoint of every edge plus the final tail).
            if path_edges:
                uids = [path_edges[0][0]] + [b for _a, b in path_edges]
                for uid in uids:
                    self._element_to_services.setdefault(uid, set()).add(uuid)

        # Re-apply /config plane state. Order matters: element overrides
        # first (so a span's failed-by-override state isn't masked by a
        # stale baseline), then link failures (mutates the routing graph),
        # then twin overrides (no side effects on baselines).
        element_overrides = data.get("element_overrides") or {}
        if element_overrides:
            self.apply_element_overrides(element_overrides)
        for fiber_uid in data.get("failed_links") or []:
            if fiber_uid in self._gnpy_uid_map:
                self.set_link_failed(fiber_uid, True)
        twin_overrides = data.get("twin_overrides") or {}
        if twin_overrides:
            self.apply_twin_overrides(twin_overrides)
        self._bump_revision()

        logger.info(
            "Restored from %s: %d services, %d element overrides, "
            "%d failed links",
            path,
            len(self._services),
            len(self._element_overrides),
            len(self._failed_links),
        )

    def _get_transceiver_node_refs(self) -> set[tuple[str, str]]:
        """Set of (topology_uuid, node_uuid) for nodes that are Transceivers (have SIPs)."""
        refs: set[tuple[str, str]] = set()
        for topo in self.topology_context.topology:
            for node in topo.node:
                if any(
                    nep.mapped_service_interface_point
                    for nep in node.owned_node_edge_point
                ):
                    refs.add((topo.uuid, node.uuid))
        return refs

    def _is_roadm_to_roadm_link(
        self, link: Link, topo_uuid: str, transceiver_refs: set[tuple[str, str]]
    ) -> bool:
        """True if both link endpoints are ROADM nodes (neither is a Transceiver)."""
        if len(link.node_edge_point) < 2:
            return False
        for ref in link.node_edge_point[:2]:
            if (ref.topology_uuid, ref.node_uuid) in transceiver_refs:
                return False
        return True

    def get_roadm_to_roadm_links(self) -> list[dict]:
        """Return all ROADM–ROADM links for UI focus (excludes TRX–ROADM access links).

        Returns list of {"link-uuid", "label", "topology-uuid", "operational-state"}.
        """
        transceiver_refs = self._get_transceiver_node_refs()
        result: list[dict] = []
        for topo in self.topology_context.topology:
            for link in topo.link:
                if not self._is_roadm_to_roadm_link(link, topo.uuid, transceiver_refs):
                    continue
                label = (
                    link.name[0].value
                    if link.name
                    else link.uuid[:8]
                )
                result.append({
                    "link-uuid": link.uuid,
                    "label": label,
                    "topology-uuid": topo.uuid,
                    "operational-state": link.operational_state,
                })
        return result

    def get_spectrum_grid_data(self) -> dict:
        """Build link×slot grid for UI: links (T-API order), occupancy per link per slot.

        Returns dict: spectrum-context (grid params), links [{ link-uuid, label }],
        occupancy (2D: occupancy[link_idx][slot_idx] = service_uuid or null).
        Only includes ROADM–ROADM links (excludes TRX–ROADM access links) that have
        spectrum state or appear in a service path.
        """
        # (topo_uuid, link_uuid) -> (from_uid, to_uid)
        link_ref_to_edge: dict[tuple[str, str], tuple[str, str]] = {}
        for (g1, g2), (topo_uuid, link_uuid) in self._gnpy_pair_to_link_ref.items():
            link_ref_to_edge[(topo_uuid, link_uuid)] = (g1, g2)

        num_slots = self._spectrum_state.num_slots
        cfg = self._config.spectrum
        transceiver_refs = self._get_transceiver_node_refs()

        # Include all ROADM–ROADM links in the grid so the UI always shows backbone links
        # (with free slots until services use them). No need to require spectrum state.
        ordered_links: list[dict] = []  # [{ link-uuid, label, edge }, ...]
        for topo in self.topology_context.topology:
            for link in topo.link:
                edge = link_ref_to_edge.get((topo.uuid, link.uuid))
                if edge is None:
                    continue
                if not self._is_roadm_to_roadm_link(link, topo.uuid, transceiver_refs):
                    continue
                label = (
                    link.name[0].value
                    if link.name
                    else link.uuid[:8]
                )
                ordered_links.append({
                    "link-uuid": link.uuid,
                    "label": label,
                    "edge": edge,
                })

        # occupancy[link_idx][slot_idx] = service_uuid or None
        occupancy: list[list[str | None]] = [
            [None] * num_slots for _ in ordered_links
        ]

        def _path_traverses_link(path_edges: list[tuple[str, str]], link_edge: tuple[str, str]) -> bool:
            """True if the path (chain of path_edges) traverses the link (g1, g2).

            path_edges are consecutive element pairs (may include Fiber, EDFA);
            link_edge is a TAPI link (node-to-node). The path traverses the link
            if the two nodes are connected in the undirected graph of path_edges.
            """
            g1, g2 = link_edge
            if not path_edges:
                return False
            # Build undirected adjacency from path_edges
            adj: dict[str, set[str]] = {}
            for a, b in path_edges:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
            # BFS from g1; see if g2 is reachable
            seen = {g1}
            stack = [g1]
            while stack:
                u = stack.pop()
                for v in adj.get(u, ()):
                    if v in seen:
                        continue
                    seen.add(v)
                    if v == g2:
                        return True
                    stack.append(v)
            return False

        for service_uuid, (path_edges, start, block_width) in self._service_allocation.items():
            for i, row in enumerate(ordered_links):
                link_edge = row["edge"]
                if _path_traverses_link(path_edges, link_edge):
                    for slot in range(start, min(start + block_width, num_slots)):
                        occupancy[i][slot] = service_uuid

        # Include all connectivity services so the UI can show every service with a color
        # and ROADMs traversed for the overlay
        service_allocation = []
        for svc in self._services.values():
            uuid = svc.uuid
            alloc = self._service_allocation.get(uuid)
            hops = self.get_service_path_hops(svc)
            roadms = (
                [h["uid"] for h in hops if h.get("type") == "Roadm"]
                if hops
                else []
            )
            if alloc:
                _path_edges, start, block_width = alloc
                service_allocation.append({
                    "service-uuid": uuid,
                    "start-slot": start,
                    "num-slots": block_width,
                    "roadms": roadms,
                })
            else:
                service_allocation.append({
                    "service-uuid": uuid,
                    "start-slot": 0,
                    "num-slots": 0,
                    "roadms": roadms,
                })

        return {
            "spectrum-context": {
                "num-slots": cfg.num_slots,
                "slot-width-ghz": cfg.slot_width_ghz,
                "nominal-central-frequency-thz": cfg.center_frequency_thz,
            },
            "links": [{"link-uuid": r["link-uuid"], "label": r["label"]} for r in ordered_links],
            "occupancy": occupancy,
            "service-allocation": service_allocation,
        }
