"""GNPy physics adapter: load network, design amplifiers, propagate paths.

This module provides a thin wrapper around gnpy 3.x for computing the
static QoT baseline (GSNR, OSNR, CD, PMD) of a connectivity service.

Key design notes:
  - Network nodes in gnpy are the element objects themselves (Transceiver,
    Fiber, Edfa, Roadm).  The UID is accessed via ``element.uid``.
  - ``designed_network()`` must be called after ``network_from_json()`` to
    set ROADM equalization targets and EDFA operating points.
  - Propagation launches the service channel at its own frequency, width and
    power inside the equipment library's SI comb, so its NLI reflects a fully
    loaded line, and reports that channel (physics/channel_plan.py).
  - A Transceiver is called with its ``TransceiverRole``: the path's first
    element emits, its last receives.
  - Each propagation deep-copies only the path elements, not the whole
    network, to avoid mutating cached element state.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from twinlight.physics.modulation import ModulationFormat, get_params

if TYPE_CHECKING:
    import networkx as nx

    from twinlight.physics.channel_plan import SpectralDefaults

_logger = logging.getLogger(__name__)

@dataclass
class OpmBaseline:
    """Static QoT of one service channel, from either physics backend.

    Every metric is the service's own channel — at its assigned central
    frequency and launch power, on a fully loaded line — not the worst
    channel of the comb.
    """

    gsnr_db: float             # GSNR (ASE + NLI) [dB]
    osnr_ase_db: float         # OSNR (ASE only) [dB]
    cd_ps_nm: float            # Accumulated chromatic dispersion [ps/nm]
    pmd_ps: float              # Accumulated PMD [ps]
    latency_ms: float          # Propagation latency [ms]
    total_fiber_km: float      # Total fiber length [km]
    edfa_uids: list[str] = field(default_factory=list)   # Ordered EDFA UIDs on path
    fiber_uids: list[str] = field(default_factory=list)  # Ordered Fiber UIDs on path
    # Ordered (uid, kind) of PDL hinges on the path, kind in {"Roadm", "Edfa"}.
    # Hinge model of Zarkosvky & Shtaif, Opt. Lett. 45(5):1224 (2020): PDL is
    # dominated by a discrete set of components; fibers contribute negligibly.
    pdl_elements: list[tuple[str, str]] = field(default_factory=list)
    # Non-OK propagation state. ``None`` means the metrics above are valid
    # GNPy-computed values; ``"link-failed"`` means an element on the path
    # (e.g. a failed fiber) prevented propagation and the numeric fields are
    # sentinel zeros — callers must check ``status`` before reporting metrics.
    status: str | None = None


def build_gnpy_network(
    topology_path: Path,
    equipment_path: Path,
) -> tuple[nx.DiGraph, dict]:
    """Load and design a GNPy network from JSON files.

    Args:
        topology_path: Path to GNPy topology JSON (e.g. CORONET_CONUS_Topology.json).
        equipment_path: Path to equipment config JSON (e.g. eqpt_config.json).

    Returns:
        (network, equipment) tuple ready for propagation.

    Raises:
        ImportError: If gnpy is not installed.
        FileNotFoundError: If the topology or equipment file does not exist.
    """
    from gnpy.tools.json_io import load_equipment, network_from_json
    from gnpy.tools.worker_utils import designed_network

    equipment = load_equipment(str(equipment_path))
    with open(topology_path) as fh:
        topo_data = json.load(fh)
    network = network_from_json(topo_data, equipment)
    network, _ref_req, _ref_chan = designed_network(equipment, network)

    _logger.info(
        "GNPy network built: %d nodes (%d Transceivers, %d EDFAs, %d Fibers, %d ROADMs)",
        network.number_of_nodes(),
        _count_type(network, "Transceiver"),
        _count_type(network, "Edfa"),
        _count_type(network, "Fiber"),
        _count_type(network, "Roadm"),
    )
    return network, equipment


def build_uid_map(network: nx.DiGraph) -> dict[str, object]:
    """Return a mapping from element UID to gnpy element object.

    In gnpy, the network nodes ARE the element objects (Transceiver, Fiber,
    Edfa, Roadm).  Each element exposes a ``.uid`` attribute.
    """
    return {node.uid: node for node in network.nodes()}


def compute_path_baseline(
    uid_map: dict[str, object],
    path_uids: list[str],
    modulation_format: str | ModulationFormat,
    *,
    defaults: SpectralDefaults,
    bands: list[tuple[float, float]] | None,
    centre_frequency_hz: float,
    media_channel_width_hz: float,
    launch_power_dbm: float,
) -> OpmBaseline:
    """Compute the static QoT of a service channel on a path of GNPy UIDs.

    The path must start and end with Transceiver elements.  Intermediate
    elements (Fiber, Edfa, Roadm) are propagated in order.

    The launched spectrum is :func:`~twinlight.physics.channel_plan.line_comb`:
    the SI comb, less the channels overlapping the service's media channel and
    those outside ``bands``, plus the service channel at
    ``centre_frequency_hz``. The metrics reported are that channel's.

    Launch power: the comb's transmitters emit SI ``tx_power_dbm``; the
    service channel's emits ``launch_power_dbm`` and the channel carries an
    offset ``delta_pdb = launch_power_dbm - SI power_dbm``. GNPy's ROADMs set
    every channel to their per-channel target plus its ``delta_pdb``
    (``gnpy.core.elements.Roadm``), so the offset — not the transmitter
    power, which the first ROADM re-equalises — is what keeps the channel
    that many dB above or below its neighbours along the line.

    Args:
        uid_map: Mapping from UID to gnpy element object (from ``build_uid_map``).
        path_uids: Ordered list of GNPy element UIDs from source to destination.
        modulation_format: Modulation format string or enum (its symbol rate).
        defaults: The equipment library's SI defaults.
        bands: The band common to the path's amplifiers.
        centre_frequency_hz: The service channel's central frequency.
        media_channel_width_hz: Width of the service's media channel.
        launch_power_dbm: The service channel's launch power.

    Returns:
        OpmBaseline of the service channel.

    Raises:
        KeyError: If a UID in path_uids is not in uid_map.
        ValueError: The service channel did not reach the receiver (it lies
            outside an amplifier's band, which admission never assigns).
    """
    from gnpy.core.elements import Roadm, Transceiver
    from gnpy.core.info import create_arbitrary_spectral_information
    from gnpy.core.parameters import TransceiverRole

    from twinlight.physics.channel_plan import line_comb

    params = get_params(modulation_format)

    # Deep-copy only the path elements (not the whole network) to avoid
    # mutating cached element state during propagation.
    path_items: list[tuple[str, Any]] = [
        (uid, copy.deepcopy(uid_map[uid])) for uid in path_uids
    ]

    comb = line_comb(
        defaults,
        centre_frequency_hz=centre_frequency_hz,
        media_channel_width_hz=media_channel_width_hz,
        baud_rate_hz=params.baud_rate_hz,
        launch_power_dbm=launch_power_dbm,
        bands=bands,
    )
    service = comb.service_index
    tx_power_dbm = np.full(comb.frequency_hz.size, defaults.tx_power_dbm)
    tx_power_dbm[service] = launch_power_dbm
    delta_pdb = np.zeros(comb.frequency_hz.size)
    delta_pdb[service] = launch_power_dbm - defaults.power_dbm
    tx_power_w = 10 ** (tx_power_dbm / 10.0) * 1e-3  # dBm → W
    si = create_arbitrary_spectral_information(
        comb.frequency_hz,
        pch=tx_power_w,
        tx_power=tx_power_w,
        baud_rate=comb.baud_rate_hz,
        slot_width=comb.slot_width_hz,
        roll_off=defaults.roll_off,
        tx_osnr=100.0,   # ideal Tx — transmitter is never the bottleneck
        delta_pdb_per_channel=delta_pdb,
    )

    # Propagate element by element.  ROADMs need their degree context;
    # Transceivers their role, as in gnpy.topology.request.propagate.
    for i, (uid, el) in enumerate(path_items):
        if isinstance(el, Roadm):
            prev_uid = path_items[i - 1][0] if i > 0 else None
            next_uid = path_items[i + 1][0] if i < len(path_items) - 1 else None
            si = el(si, degree=next_uid, from_degree=prev_uid)
        elif isinstance(el, Transceiver):
            role = TransceiverRole.EMITTER if i == 0 else TransceiverRole.RECEIVER
            si = el(si, role=role)
        else:
            si = el(si)

    # The service channel, found by frequency: an EDFA drops out-of-band
    # carriers, so indices need not survive propagation.
    found = np.flatnonzero(np.abs(si.frequency - centre_frequency_hz) < 1.0)
    if found.size == 0:
        raise ValueError(
            f"the channel at {centre_frequency_hz * 1e-12:.6f} THz was dropped "
            "on the path: it lies outside an amplifier's band"
        )
    ch = int(found[0])

    # Unit conversions follow gnpy's own Transceiver._calc_* methods, which
    # are the authority for what SpectralInformation carries: accumulated
    # CD is in s/m (×1e3 → ps/nm), PMD in s (×1e12 → ps), latency in s
    # (×1e3 → ms).
    return OpmBaseline(
        gsnr_db=float(si.gsnr_db[ch]),
        osnr_ase_db=float(si.snr_lin_db[ch]),
        cd_ps_nm=float(si.chromatic_dispersion[ch]) * 1e3,  # s/m → ps/nm
        pmd_ps=float(si.pmd[ch] * 1e12),                     # s → ps
        latency_ms=float(si.latency[ch] * 1e3),              # s → ms
        total_fiber_km=_sum_fiber_km(path_items),
        edfa_uids=[uid for uid, el in path_items if type(el).__name__ == "Edfa"],
        fiber_uids=[uid for uid, el in path_items if type(el).__name__ == "Fiber"],
        # PDL hinges in propagation order — Zarkosvky & Shtaif, Opt. Lett.
        # 45(5):1224 (2020). ROADMs/WSSs and EDFAs are discrete PDL elements;
        # fibers are excluded (negligible intrinsic PDL per the hinge model).
        pdl_elements=[
            (uid, type(el).__name__)
            for uid, el in path_items
            if type(el).__name__ in ("Roadm", "Edfa")
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_type(network: nx.DiGraph, type_name: str) -> int:
    return sum(1 for n in network.nodes() if type(n).__name__ == type_name)


def _sum_fiber_km(path_items: list[tuple[str, Any]]) -> float:
    """Sum fiber lengths [km] along a path."""
    total = 0.0
    for _uid, el in path_items:
        if type(el).__name__ == "Fiber":
            # gnpy Fiber stores length in metres via params.length
            try:
                total += float(el.params.length) / 1000.0
            except AttributeError:
                pass
    return total
