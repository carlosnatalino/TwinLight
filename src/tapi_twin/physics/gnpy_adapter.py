"""GNPy physics adapter: load network, design amplifiers, propagate paths.

This module provides a thin wrapper around gnpy 2.x for computing the
static QoT baseline (GSNR, OSNR, CD, PMD) of a connectivity service.

Key design notes:
  - Network nodes in gnpy are the element objects themselves (Transceiver,
    Fiber, Edfa, Roadm).  The UID is accessed via ``element.uid``.
  - ``designed_network()`` must be called after ``network_from_json()`` to
    set ROADM equalization targets and EDFA operating points.
  - Propagation requires at least 2 spectral channels (EDFA interpol_params
    needs channel spacing).  We therefore use a full C-band comb.
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

from tapi_twin.physics.modulation import ModulationFormat, get_params

if TYPE_CHECKING:
    import networkx as nx

_logger = logging.getLogger(__name__)

# C-band reference comb for propagation
_F_CENTER_HZ = 193.1e12   # ITU-T centre frequency [Hz]
_NB_CHANNELS = 88         # full C-band @ 50 GHz spacing
_SPACING_HZ = 50e9


@dataclass
class OpmBaseline:
    """Static QoT metrics computed by GNPy propagation for a single service."""

    gsnr_db: float             # Worst-channel GSNR (ASE + NLI) [dB]
    osnr_ase_db: float         # Worst-channel OSNR (ASE only) [dB]
    cd_ps_nm: float            # Worst-channel chromatic dispersion [ps/nm]
    pmd_ps: float              # Worst-channel PMD [ps]
    latency_ms: float          # Propagation latency [ms]
    total_fiber_km: float      # Total fiber length [km]
    edfa_uids: list[str] = field(default_factory=list)   # Ordered EDFA UIDs on path
    fiber_uids: list[str] = field(default_factory=list)  # Ordered Fiber UIDs on path
    # Ordered (uid, kind) of PDL hinges on the path, kind in {"Roadm", "Edfa"}.
    # Hinge model of Zarkosvky & Shtaif, Opt. Lett. 45(5):1224 (2020): PDL is
    # dominated by a discrete set of components; fibers contribute negligibly.
    pdl_elements: list[tuple[str, str]] = field(default_factory=list)


def build_gnpy_network(
    topology_path: Path,
    equipment_path: Path,
) -> tuple["nx.DiGraph", dict]:
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


def build_uid_map(network: "nx.DiGraph") -> dict[str, object]:
    """Return a mapping from element UID to gnpy element object.

    In gnpy, the network nodes ARE the element objects (Transceiver, Fiber,
    Edfa, Roadm).  Each element exposes a ``.uid`` attribute.
    """
    return {node.uid: node for node in network.nodes()}


def compute_path_baseline(
    uid_map: dict[str, object],
    path_uids: list[str],
    modulation_format: str | ModulationFormat,
) -> OpmBaseline:
    """Compute the static QoT baseline for a path expressed as a UID sequence.

    The path must start and end with Transceiver elements.  Intermediate
    elements (Fiber, Edfa, Roadm) are propagated in order.

    Args:
        uid_map: Mapping from UID to gnpy element object (from ``build_uid_map``).
        path_uids: Ordered list of GNPy element UIDs from source to destination.
        modulation_format: Modulation format string or enum.

    Returns:
        OpmBaseline with worst-channel metrics.

    Raises:
        KeyError: If a UID in path_uids is not in uid_map.
        RuntimeError: If propagation fails.
    """
    from gnpy.core.elements import Roadm
    from gnpy.core.info import create_input_spectral_information

    params = get_params(modulation_format)
    f_min = _F_CENTER_HZ - (_NB_CHANNELS // 2) * _SPACING_HZ
    f_max = _F_CENTER_HZ + (_NB_CHANNELS // 2) * _SPACING_HZ
    tx_power_w = 10 ** (params.tx_power_dbm / 10.0) * 1e-3  # dBm → W

    # Deep-copy only the path elements (not the whole network) to avoid
    # mutating cached element state during propagation.
    path_items: list[tuple[str, Any]] = [
        (uid, copy.deepcopy(uid_map[uid])) for uid in path_uids
    ]

    # Create a full C-band spectral information object.
    si = create_input_spectral_information(
        f_min=f_min,
        f_max=f_max,
        roll_off=0.15,
        baud_rate=params.baud_rate_hz,
        spacing=_SPACING_HZ,
        tx_osnr=100.0,   # ideal Tx — transmitter is never the bottleneck
        tx_power=tx_power_w,
        delta_pdb=0.0,
    )

    # Propagate element by element.  ROADMs need their degree context.
    for i, (uid, el) in enumerate(path_items):
        if isinstance(el, Roadm):
            prev_uid = path_items[i - 1][0] if i > 0 else None
            next_uid = path_items[i + 1][0] if i < len(path_items) - 1 else None
            si = el(si, degree=next_uid, from_degree=prev_uid)
        else:
            si = el(si)

    # Extract worst-channel (min GSNR) metrics.
    worst = int(np.argmin(si.gsnr_db))
    pmd_ps = float(np.max(si.pmd) * 1e12)   # s → ps
    latency_ms = float(si.latency[0] * 1e3)  # s → ms (same for all channels)

    return OpmBaseline(
        gsnr_db=float(si.gsnr_db[worst]),
        osnr_ase_db=float(si.snr_lin_db[worst]),
        cd_ps_nm=float(si.chromatic_dispersion[worst]),
        pmd_ps=pmd_ps,
        latency_ms=latency_ms,
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

def _count_type(network: "nx.DiGraph", type_name: str) -> int:
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
