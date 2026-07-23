"""Convert a parsed GNPy topology into EGN-shaped data.

The EGN engine (``optical_networking_gym_v2``) wants a ``TopologyModel``
whose ``Link`` objects are broken into per-fiber ``Span`` objects, where
each span carries its own length, attenuation, and noise-figure. GNPy
JSON instead describes a chain of Transceiver → Roadm → (Fiber, Edfa)*
→ Roadm → Transceiver, with the noise figure living on the EDFAs.

This module walks the GNPy graph and builds a backend-neutral
``EgnTopologyData`` dataclass: a list of EGN-style links plus an index
that lets the /config plane resolve a GNPy UID (a fiber or EDFA) back
to its (link, span) location in EGN-space. The actual EGN
``TopologyModel`` instance is constructed by ``EgnBackend`` in M4 from
this data — keeping this module free of any ``optical_networking_gym``
imports so the converter can be unit-tested without the EGN extra.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from twinlight.loader.gnpy_topology import GnpyTopology

# Default span attenuation when a fiber doesn't declare loss_coef
# (matches the GNPy SSMF default). Used as a fallback only.
_DEFAULT_ATTENUATION_DB_PER_KM = 0.2
# Default EDFA noise figure when an EDFA element doesn't expose nf0
# (e.g. the std_medium_gain variety uses nf_min/nf_max instead — we
# pick a representative mid-range value).
_DEFAULT_NF_DB = 6.0


@dataclass(frozen=True)
class EgnSpanData:
    """One fiber segment plus the EDFA that terminates it.

    The noise figure is the EDFA *after* this span — EGN's QoT engine
    accumulates ASE per-span using this attribute, so the mapping is:
    ``span_i.noise_figure_db = nf(EDFA_after_fiber_i)`` (or the default
    when no EDFA follows, e.g. the last span before a ROADM).
    """

    length_km: float
    attenuation_db_per_km: float
    noise_figure_db: float
    # Originating GNPy element UIDs for the /config reverse-map.
    fiber_uid: str
    edfa_uid: str | None


@dataclass(frozen=True)
class EgnLinkData:
    """One ROADM-to-ROADM (or TRX-to-TRX) directional link in EGN terms."""

    id: int
    source_name: str
    target_name: str
    length_km: float
    spans: tuple[EgnSpanData, ...]


@dataclass(frozen=True)
class EgnNodeKind:
    """Type and EGN location of a GNPy UID, for the /config plane."""

    # "fiber" | "edfa" | "roadm" | "transceiver"
    kind: Literal["fiber", "edfa", "roadm", "transceiver"]
    # Only set for fiber/edfa: which EGN (link_id, span_index) they map to.
    link_id: int | None = None
    span_index: int | None = None


@dataclass
class EgnTopologyData:
    """Backend-neutral EGN-shaped topology + GNPy-UID reverse index."""

    node_names: tuple[str, ...]
    links: tuple[EgnLinkData, ...]
    # GNPy UID → (kind, optional link_id, optional span_index). Used by
    # the /config plane to route a parameter mutation to the right EGN
    # ``Span`` field (or to reject it if the UID is a ROADM/TRX, since
    # EGN has no per-node tunable parameters in the M4 scope).
    gnpy_uid_index: dict[str, EgnNodeKind] = field(default_factory=dict)


def _is_terminal(el_type: str) -> bool:
    return el_type in ("Roadm", "Transceiver")


def _walk_through_inline(
    start_uid: str,
    next_neighbour: dict[str, str],
    elements_by_uid: dict,
) -> tuple[list[str], list[str | None], str] | None:
    """Walk from a terminal out along ``next_neighbour`` through Fiber/Edfa
    elements until reaching the next terminal.

    Returns ``(fiber_uids, edfa_uids_per_fiber, target_terminal_uid)``
    where ``edfa_uids_per_fiber[i]`` is the EDFA immediately following
    ``fiber_uids[i]`` (or ``None`` if the fiber connects directly to
    the next terminal). Returns ``None`` if the walk dead-ends, loops,
    or hits an unsupported element type (Fused / RamanFiber).
    """
    fibers: list[str] = []
    edfas: list[str | None] = []

    cur: str | None = next_neighbour.get(start_uid)
    if cur is None:
        return None
    seen = {start_uid}
    pending_fiber: str | None = None

    while cur is not None:
        if cur in seen:
            return None
        seen.add(cur)
        el = elements_by_uid.get(cur)
        if el is None:
            return None
        kind = el.type
        if kind == "Fiber":
            if pending_fiber is not None:
                fibers.append(pending_fiber)
                edfas.append(None)
            pending_fiber = cur
        elif kind == "Edfa":
            if pending_fiber is None:
                # EDFA before any span on this leg — typically a booster
                # straight out of the ROADM. Out of scope for the MVP.
                pass
            else:
                fibers.append(pending_fiber)
                edfas.append(cur)
                pending_fiber = None
        elif _is_terminal(kind):
            if pending_fiber is not None:
                fibers.append(pending_fiber)
                edfas.append(None)
            return fibers, edfas, cur
        else:
            return None
        cur = next_neighbour.get(cur)

    return None


def _fiber_length_km(el) -> float:
    """Read a Fiber element's length in km from its params dict.

    GNPy stores length in the unit specified by ``length_units`` (km, m,
    miles). Default unit is km if unspecified.
    """
    params = el.params or {}
    length = float(params.get("length", 0))
    units = (params.get("length_units") or "km").lower()
    if units == "m":
        return length / 1000.0
    if units in ("mi", "miles"):
        return length * 1.609344
    return length  # km


def _fiber_loss_coef(el) -> float:
    params = el.params or {}
    raw = params.get("loss_coef")
    if isinstance(raw, dict):
        # Frequency-dependent loss — average is a reasonable EGN scalar.
        values = raw.get("value") or []
        if values:
            return float(sum(values) / len(values))
    if raw is not None:
        return float(raw)
    return _DEFAULT_ATTENUATION_DB_PER_KM


def _edfa_nf(el) -> float:
    params = el.params or {}
    nf0 = params.get("nf0")
    if nf0 is not None:
        return float(nf0)
    # Average nf_min/nf_max if the type-variety model uses those instead.
    nf_min, nf_max = params.get("nf_min"), params.get("nf_max")
    if nf_min is not None and nf_max is not None:
        return float((nf_min + nf_max) / 2.0)
    return _DEFAULT_NF_DB


def convert(gnpy_topo: GnpyTopology) -> EgnTopologyData:
    """Build an ``EgnTopologyData`` snapshot of a parsed GNPy topology.

    Conventions:
    * One EGN ``Link`` per directed terminal→terminal walk (so each
      bidirectional fiber pair becomes two EGN links).
    * Each ``Fiber`` between the two terminals becomes one ``Span``;
      the ``EDFA`` immediately following it (if any) supplies its
      ``noise_figure_db``.
    * ``node_names`` is the de-duplicated list of terminal UIDs
      (ROADMs + Transceivers) in declaration order.
    """
    elements_by_uid = gnpy_topo.elements_by_uid

    # Build a forward-neighbour index from the connection list. GNPy
    # connections are directed; we assume each non-terminal has at most
    # one outgoing edge along the propagation chain.
    next_neighbour: dict[str, str] = {}
    out_count: dict[str, int] = defaultdict(int)
    for conn in gnpy_topo.connections:
        out_count[conn.from_node] += 1
        next_neighbour.setdefault(conn.from_node, conn.to_node)

    # Terminal UIDs in declaration order.
    terminals: list[str] = [
        el.uid for el in gnpy_topo.elements if _is_terminal(el.type)
    ]

    node_names = tuple(terminals)
    links: list[EgnLinkData] = []
    uid_index: dict[str, EgnNodeKind] = {}
    for uid in node_names:
        kind: Literal["roadm", "transceiver"] = (
            "transceiver" if elements_by_uid[uid].type == "Transceiver"
            else "roadm"
        )
        uid_index[uid] = EgnNodeKind(kind=kind)

    # One outgoing leg per terminal → next terminal. A terminal with
    # multiple successors (e.g. a ROADM with two outgoing degrees) gets
    # one link per neighbour — we re-walk from the same start for each.
    succ_by_terminal: dict[str, list[str]] = defaultdict(list)
    for conn in gnpy_topo.connections:
        if _is_terminal(elements_by_uid[conn.from_node].type):
            succ_by_terminal[conn.from_node].append(conn.to_node)

    for source_uid in node_names:
        for first_next in succ_by_terminal.get(source_uid, []):
            # Override next_neighbour[source_uid] for THIS iteration so a
            # ROADM with multiple outgoing degrees walks each successor
            # exactly once (dict-merge order matters — later keys win).
            walk_index = {**next_neighbour, source_uid: first_next}
            walk = _walk_through_inline(
                source_uid, walk_index, elements_by_uid,
            )
            if walk is None:
                continue
            fiber_uids, edfa_uids, target_uid = walk
            if not fiber_uids:
                # TRX→ROADM access or any other walk with no fiber — EGN
                # only models inter-terminal spans, so skip.
                continue

            spans: list[EgnSpanData] = []
            for fiber_uid, edfa_uid in zip(fiber_uids, edfa_uids):
                fiber_el = elements_by_uid[fiber_uid]
                length_km = _fiber_length_km(fiber_el)
                loss = _fiber_loss_coef(fiber_el)
                if edfa_uid is not None:
                    nf = _edfa_nf(elements_by_uid[edfa_uid])
                else:
                    nf = _DEFAULT_NF_DB
                spans.append(EgnSpanData(
                    length_km=length_km,
                    attenuation_db_per_km=loss,
                    noise_figure_db=nf,
                    fiber_uid=fiber_uid,
                    edfa_uid=edfa_uid,
                ))

            link_id = len(links)
            for span_idx, span in enumerate(spans):
                uid_index[span.fiber_uid] = EgnNodeKind(
                    kind="fiber",
                    link_id=link_id,
                    span_index=span_idx,
                )
                if span.edfa_uid is not None:
                    uid_index[span.edfa_uid] = EgnNodeKind(
                        kind="edfa",
                        link_id=link_id,
                        span_index=span_idx,
                    )

            links.append(EgnLinkData(
                id=link_id,
                source_name=source_uid,
                target_name=target_uid,
                length_km=sum(s.length_km for s in spans),
                spans=tuple(spans),
            ))

    return EgnTopologyData(
        node_names=node_names,
        links=tuple(links),
        gnpy_uid_index=uid_index,
    )
