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
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tapi_twin.config import TwinConfig
from tapi_twin.loader.gnpy_topology import load_gnpy_topology
from tapi_twin.loader.tapi_builder import TapiBuilder
from tapi_twin.physics.backend import element_kind_name as _kind_name
from tapi_twin.models.common import ServiceInterfacePoint
from tapi_twin.models.connectivity import ConnectivityService
from tapi_twin.models.topology import (
    Link,
    Node,
    NodeEdgePoint,
    Topology,
)
from tapi_twin.state.spectrum_state import SpectrumState
from tapi_twin.state.topology_state import TopologyGraph

if TYPE_CHECKING:
    from tapi_twin.physics.gnpy_adapter import OpmBaseline

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
    """Path GSNR below required threshold + margin (IMPLEMENTATION_PLAN Phase 3)."""


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
        from tapi_twin.physics.backend import build_backend

        self._backend = build_backend(config)

        # -- Per-service caches and serialization lock ---------------------
        self._route_cache: dict[tuple[str, str], list[str]] = {}
        self._baseline_cache: dict[str, OpmBaseline] = {}
        self._propagation_lock: asyncio.Lock = asyncio.Lock()

        # -- EDFA stateful reservoir tracker (Bononi exponential step) ------
        from tapi_twin.physics.transients.edfa_reservoir import (
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

        # -- Path computation: GNPy UID <-> TAPI topology refs -------------
        # gnpy_uid -> (topology_uuid, node_uuid)
        self._gnpy_uid_to_node_ref: dict[str, tuple[str, str]] = {}
        # (src_gnpy_uid, dst_gnpy_uid) -> (topology_uuid, link_uuid)
        self._gnpy_pair_to_link_ref: dict[tuple[str, str], tuple[str, str]] = {}
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

        Order: resolve path (k-shortest from NetworkX; GNPy does not provide
        path computation), first-fit spectrum assignment, optional GSNR
        check via GNPy propagation. Raises NoPathError, InsufficientSpectrumError,
        or InsufficientQoTError on admission failure.
        """
        endpoints = svc.end_point
        if len(endpoints) < 2:
            raise NoPathError("Connectivity service requires two end-points")
        sip_a = endpoints[0].service_interface_point.service_interface_point_uuid
        sip_z = endpoints[1].service_interface_point.service_interface_point_uuid

        path = self.get_service_path(sip_a, sip_z)
        if not path:
            raise NoPathError(f"No path between SIPs {sip_a!r} and {sip_z!r}")

        from tapi_twin.algorithms.spectrum_assignment import (
            first_fit,
            path_uids_to_edges,
        )
        from tapi_twin.physics.modulation import (
            get_params,
            slots_required_for_modulation,
        )

        path_edges = path_uids_to_edges(path)
        slot_width_hz = self._config.spectrum.slot_width_ghz * 1e9
        slots_required = slots_required_for_modulation(
            svc.modulation_format, slot_width_hz
        )
        guard_slots = self._config.rmsa.default_guardband_slots
        start = first_fit(
            self._spectrum_state,
            path_edges,
            slots_required,
            guard_slots,
        )
        if start is None:
            raise InsufficientSpectrumError(
                "No contiguous spectrum block available along the path"
            )

        block_width = slots_required + guard_slots
        self._spectrum_state.allocate(path_edges, start, block_width)
        self._service_allocation[svc.uuid] = (path_edges, start, block_width)

        # QoT admission: require GSNR >= req_gsnr + margin (IMPLEMENTATION_PLAN Phase 3)
        if self._gnpy_available:
            baseline = await self.get_or_compute_baseline(svc)
            if baseline is not None:
                req_gsnr = get_params(svc.modulation_format).req_gsnr_db
                margin = self._config.rmsa.qot_margin_db
                if baseline.gsnr_db < req_gsnr + margin:
                    self._spectrum_state.release(path_edges, start, block_width)
                    self._service_allocation.pop(svc.uuid, None)
                    raise InsufficientQoTError(
                        f"Path GSNR {baseline.gsnr_db:.1f} dB below required "
                        f"{req_gsnr + margin:.1f} dB (req {req_gsnr} + margin {margin})"
                    )

        self._services[svc.uuid] = svc

        # Reverse index for /config invalidation: every element on the path
        # now depends on this service's baseline.
        for uid in path:
            self._element_to_services.setdefault(uid, set()).add(svc.uuid)

        # Notify EDFA tracker of channel add (Bononi reservoir model)
        if self._gnpy_available and baseline is not None:
            edfa_uids = baseline.edfa_uids
            self._service_edfa_uids[svc.uuid] = edfa_uids
            if edfa_uids and self._config.transients.edfa_reservoir.enabled:
                self._edfa_tracker.notify_channel_change(
                    edfa_uids, +1, self._config.transients.edfa_reservoir,
                )

    def get_services(self) -> list[ConnectivityService]:
        return list(self._services.values())

    def get_service(self, uuid: str) -> ConnectivityService | None:
        return self._services.get(uuid)

    def get_service_spectrum(self, uuid: str) -> dict | None:
        """Return T-API frequency-slot for a service if it has an allocation.

        Returns dict with nominal-central-frequency (THz) and slot-width (GHz),
        or None if the service has no spectrum allocation.
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

    def delete_service(self, uuid: str) -> bool:
        """Remove service and release its spectrum allocation."""
        allocation = self._service_allocation.pop(uuid, None)
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

    def _get_path_via_gnpy(self, src_uid: str, dst_uid: str) -> list[str] | None:
        """Use GNPy's compute_constrained_path for weighted shortest path.

        Returns list of element UIDs or None if GNPy path computation is
        unavailable or finds no path. Uses fiber length as weight
        (see https://gnpy.readthedocs.io/).
        """
        if self._gnpy_network is None:
            return None
        try:
            from gnpy.topology.request import PathRequest, compute_constrained_path

            req = PathRequest(
                source=src_uid,
                destination=dst_uid,
                nodes_list=[dst_uid],
                loose_list=["STRICT"],
            )
            path_elements = compute_constrained_path(self._gnpy_network, req)
            if path_elements and getattr(req, "blocking_reason", None) is None:
                return [el.uid for el in path_elements]
        except Exception:  # noqa: BLE001
            pass
        return None

    def get_service_path(
        self, sip_a_uuid: str, sip_z_uuid: str
    ) -> list[str] | None:
        """Return ordered GNPy UIDs for the shortest path between two SIPs.

        When GNPy is loaded, uses GNPy's path computation (weighted by fiber
        length). Otherwise uses NetworkX k-shortest paths. Results are cached.
        Returns None if either SIP has no GNPy UID or no path exists.
        """
        src = self.get_gnpy_uid(sip_a_uuid)
        dst = self.get_gnpy_uid(sip_z_uuid)
        if not src or not dst:
            return None

        cache_key = (src, dst)
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]

        # Prefer GNPy path computation when available (weighted shortest path by
        # fiber length; see gnpy.topology.request.compute_constrained_path).
        path_uids = self._get_path_via_gnpy(src, dst)
        if path_uids is not None:
            self._route_cache[cache_key] = path_uids
            return path_uids

        from tapi_twin.algorithms.routing import k_shortest_paths

        paths = k_shortest_paths(
            self._topo_graph.graph, src, dst, self._config.rmsa.k_shortest_paths
        )
        if not paths:
            logger.warning("No path found between %s and %s", src, dst)
            self._route_cache[cache_key] = []
            return None

        best = paths[0]
        self._route_cache[cache_key] = best
        return best

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
            from tapi_twin.physics.gnpy_adapter import OpmBaseline

            return OpmBaseline(
                gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0, pmd_ps=0.0,
                latency_ms=0.0, total_fiber_km=0.0, status="link-failed",
            )

        async with self._propagation_lock:
            # Re-check after acquiring lock (another coroutine may have computed it)
            if svc.uuid in self._baseline_cache:
                return self._baseline_cache[svc.uuid]

            baseline = await self._backend.compute_baseline(
                path, svc.modulation_format.value,
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
            self._backend.write_attribute(uid, attr, value)
            self._element_overrides.setdefault(uid, {})[attr] = value
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
        from tapi_twin.state.twin_overrides import apply as _apply

        _apply(self._config, updates)
        self._twin_overrides.update(updates)
        return updates

    def get_element_overrides(self) -> dict[str, dict[str, Any]]:
        """Snapshot of every per-element override the user has applied."""
        # Return a shallow copy so callers don't mutate context state.
        return {uid: dict(attrs) for uid, attrs in self._element_overrides.items()}

    def get_twin_overrides(self) -> dict[str, Any]:
        return dict(self._twin_overrides)

    def get_failed_links(self) -> set[str]:
        return set(self._failed_links)

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
        from tapi_twin.models.common import OperationalState

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

        Uses _gnpy_uid_map when available for types and fiber lengths; otherwise
        uses parsed topology elements for type only (distance_km_to_next null).
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

            if el_type == "Fiber" and el is not None:
                try:
                    accumulated_km += float(el.params.length) / 1000.0
                except AttributeError:
                    pass
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
        """
        endpoints = svc.end_point
        if len(endpoints) < 2:
            return None
        sip_a = endpoints[0].service_interface_point.service_interface_point_uuid
        sip_z = endpoints[1].service_interface_point.service_interface_point_uuid
        path_uids = self.get_service_path(sip_a, sip_z)
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
    ) -> OpmBaseline | None:
        """Compute OpmBaseline for path between two SIPs (no service, no cache).

        Uses same GNPy propagation as get_or_compute_baseline. Returns None if
        GNPy unavailable or path not found.
        """
        if not self._backend.available:
            return None
        path = self.get_service_path(sip_a, sip_z)
        if not path:
            return None
        async with self._propagation_lock:
            return await self._backend.compute_baseline(path, modulation_format)

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

    def get_path_candidates(
        self,
        sip_a_uuid: str,
        sip_z_uuid: str,
        max_candidates: int = 1,
    ) -> list[tuple[list[tuple[str, str]], list[tuple[str, str]]]]:
        """Return up to max_candidates path candidates as (link_refs, node_refs).

        Each link_ref is (topology_uuid, link_uuid); each node_ref is (topology_uuid, node_uuid).
        Uses GNPy path computation when available, else NetworkX k-shortest paths.
        Returns empty list if no path exists.
        """
        src = self.get_gnpy_uid(sip_a_uuid)
        dst = self.get_gnpy_uid(sip_z_uuid)
        if not src or not dst:
            return []

        paths_gnpy: list[list[str]] = []
        if max_candidates == 1:
            single = self.get_service_path(sip_a_uuid, sip_z_uuid)
            if single:
                paths_gnpy.append(single)
        else:
            from tapi_twin.algorithms.routing import k_shortest_paths

            paths_gnpy = k_shortest_paths(
                self._topo_graph.graph,
                src,
                dst,
                min(max_candidates, self._config.rmsa.k_shortest_paths),
            )

        TERMINAL_TYPES = {"Transceiver", "Roadm"}
        elements_by_uid = getattr(
            self._topo_graph._gnpy_topo, "elements_by_uid", {}
        )
        results: list[tuple[list[tuple[str, str]], list[tuple[str, str]]]] = []

        for path_uids in paths_gnpy:
            node_refs: list[tuple[str, str]] = []
            link_refs: list[tuple[str, str]] = []
            terminals: list[str] = []

            for uid in path_uids:
                el = self._gnpy_uid_map.get(uid) if self._gnpy_uid_map else None
                if el is not None:
                    el_type = _kind_name(el)
                else:
                    parsed = elements_by_uid.get(uid)
                    el_type = getattr(parsed, "type", "") if parsed else ""
                if el_type in TERMINAL_TYPES:
                    terminals.append(uid)
                    ref = self._gnpy_uid_to_node_ref.get(uid)
                    if ref:
                        node_refs.append(ref)

            for i in range(len(terminals) - 1):
                key = (terminals[i], terminals[i + 1])
                ref = self._gnpy_pair_to_link_ref.get(key)
                if ref:
                    link_refs.append(ref)

            if node_refs and (len(link_refs) == len(terminals) - 1 or link_refs):
                results.append((link_refs, node_refs))

        return results

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
            }
        data = {
            "version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        self._baseline_cache.clear()
        self._element_to_services.clear()
        for fiber_uid in list(self._failed_links):
            self.set_link_failed(fiber_uid, False)
        self._element_overrides.clear()
        self._twin_overrides.clear()

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
