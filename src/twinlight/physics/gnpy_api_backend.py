"""GNPy implementation of ``PhysicalBackend`` that runs GNPy in a gnpy-api service.

Selected when ``physics.gnpy_api_url`` (YAML) or ``--gnpy-api-url`` (CLI) is
set with the ``gnpy`` backend. The twin then needs no gnpy install: at startup
it opens a gnpy-api session on its topology and equipment files, and from then
on every GNPy call ``GnpyBackend`` makes in-process becomes a request on that
session:

==========================================  ======================================
In-process (``GnpyBackend`` and friends)    gnpy-api session call
==========================================  ======================================
``build_gnpy_network`` + ``build_uid_map``  ``POST /sessions``, ``GET /session/elements``
``compute_path_baseline``                   ``POST /session/propagate``
``element_params.write_attr``               ``PATCH /session/elements/{uid}``
``designed_network`` (redesign)             ``POST /session/redesign``
graph-edge removal on fiber failure         ``PATCH /session/elements/{uid}`` ``failed``
``compute_constrained_path``                ``POST /session/paths``
==========================================  ======================================

The service computes exactly what ``GnpyBackend`` computes — same design, same
88-slot comb, same element-by-element propagation — so baselines are identical
and the backend keeps the name ``gnpy``: a snapshot moves freely between the
two. One deliberate difference: a gnpy-api redesign recomputes span losses, so
a changed ``loss_coef`` or ``att_in`` reaches the amplifier gains, where
in-process ``designed_network()`` reuses the losses cached at the first design.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, TypeVar
from urllib.parse import quote

from twinlight.config import TwinConfig
from twinlight.physics.backend import ElementStub, ParamValidationError
from twinlight.physics.gnpy_adapter import (
    _F_CENTER_HZ,
    _NB_CHANNELS,
    _SPACING_HZ,
    OpmBaseline,
)
from twinlight.physics.gnpy_api_client import GnpyApiClient, GnpyApiError, SessionLostError
from twinlight.physics.modulation import get_params

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _element_path(uid: str) -> str:
    # GNPy uids carry spaces, arrows and slashes: "fiber (Abilene → Dallas)-_(1/4)".
    return f"/session/elements/{quote(uid, safe='')}"


class GnpyApiBackend:
    """GNPy QoT propagation + element mutation, run by a gnpy-api service."""

    # Same physics as GnpyBackend, so snapshots stay interchangeable.
    name = "gnpy"

    def __init__(self, config: TwinConfig) -> None:
        self.available: bool = False
        self.uid_map: dict[str, ElementStub] = {}
        # No local GNPy graph: TapiContext asks compute_path() instead.
        self.network = None

        url = config.physics.gnpy_api_url
        if not url:
            raise ValueError("GnpyApiBackend needs physics.gnpy_api_url")
        self._client = GnpyApiClient(url)
        # Element attributes as last returned by the service. Every write goes
        # through this backend, so the copy stays current, and the /config
        # plane and Prometheus can read all elements without a request each.
        self._attributes: dict[str, dict[str, Any]] = {}
        self._schema: dict[str, dict[str, dict[str, Any]]] = {}
        # Every change applied to the session, in order: (uid, attributes) for
        # a PATCH, (None, None) for a redesign. Replayed onto a new session if
        # the service loses ours, e.g. when it restarts.
        self._journal: list[tuple[str | None, dict[str, Any] | None]] = []
        self._reopen_lock = threading.Lock()

        if config.gnpy.equipment is None:
            logger.info("No GNPy equipment file configured — using mock OPM data")
            return
        try:
            self._network_files = (
                json.loads(config.gnpy.topology.read_text()),
                json.loads(config.gnpy.equipment.read_text()),
            )
            self._open_session()
            self._schema = self._client.request("GET", "/element-attributes")
            self.available = True
            logger.info(
                "GNPy network loaded by gnpy-api at %s: %d elements",
                url, len(self.uid_map),
            )
        except (GnpyApiError, ParamValidationError, OSError, ValueError) as exc:
            logger.warning("gnpy-api network load failed (%s) — using mock data", exc)

    # -- session handling ----------------------------------------------------

    def _open_session(self) -> None:
        self._client.open_session(*self._network_files)
        self._refresh()

    def _refresh(self) -> None:
        """Re-read the designed elements: a redesign may add or replace some."""
        elements = self._client.request("GET", "/session/elements")["elements"]
        self.uid_map = {
            el["uid"]: ElementStub(
                uid=el["uid"],
                type=el["type"],
                type_variety=el.get("type-variety"),
                params=(
                    SimpleNamespace(length=el["length-km"] * 1000.0)
                    if "length-km" in el else None
                ),
            )
            for el in elements
        }
        self._attributes = {el["uid"]: el["attributes"] for el in elements}

    def _in_session(self, call: Callable[[], _T]) -> _T:
        """Run ``call``; if the service lost the session, rebuild it and retry once."""
        token = self._client.token
        try:
            return call()
        except SessionLostError:
            self._reopen(token)
            return call()

    def _reopen(self, lost_token: str | None) -> None:
        with self._reopen_lock:
            if self._client.token != lost_token:
                return  # another thread has already reopened it
            logger.warning(
                "gnpy-api lost the session (service restarted?) — reopening "
                "and replaying %d change(s)", len(self._journal),
            )
            self._client.token = None
            self._client.open_session(*self._network_files)
            for uid, attributes in self._journal:
                if uid is None:
                    self._client.request("POST", "/session/redesign")
                else:
                    self._client.request("PATCH", _element_path(uid), json=attributes)
            self._refresh()

    def _patch(self, uid: str, attributes: dict[str, Any]) -> None:
        if uid not in self.uid_map:
            raise KeyError(f"Unknown GNPy element UID: {uid!r}")
        element = self._in_session(
            lambda: self._client.request("PATCH", _element_path(uid), json=attributes)
        )
        self._journal.append((uid, attributes))
        self._attributes[uid] = element["attributes"]

    # -- QoT propagation -------------------------------------------------

    async def compute_baseline(
        self,
        path_uids: list[str],
        modulation_format: str,
    ) -> OpmBaseline | None:
        if not self.available:
            return None
        params = get_params(modulation_format)
        # The comb gnpy_adapter.compute_path_baseline propagates in-process,
        # 88 channels at 190.95-195.30 THz, in the gnpy 2 convention of the
        # gnpy-api service (gnpy 2.14.1): its first channel sits one slot
        # above f-min. When the service moves to gnpy 3, send the adapter's
        # _F_MIN_HZ/_F_MAX_HZ instead, or it launches 89 channels.
        request = {
            "path": path_uids,
            "spectrum": {
                "f-min": _F_CENTER_HZ - (_NB_CHANNELS // 2) * _SPACING_HZ,
                "f-max": _F_CENTER_HZ + (_NB_CHANNELS // 2) * _SPACING_HZ,
                "spacing": _SPACING_HZ,
                "baud-rate": params.baud_rate_hz,
                "roll-off": 0.15,
                "tx-osnr": 100.0,  # ideal Tx — transmitter is never the bottleneck
                "tx-power-dbm": params.tx_power_dbm,
                "delta-pdb": 0.0,
            },
        }
        try:
            result = await asyncio.to_thread(
                self._in_session,
                lambda: self._client.request("POST", "/session/propagate", json=request),
            )
        except (GnpyApiError, KeyError, ParamValidationError) as exc:
            logger.warning("gnpy-api baseline computation failed: %s", exc)
            return None
        return _baseline_from_propagation(result)

    # -- /config plane: per-element parameter reads/writes ---------------

    def read_attribute(self, uid: str, attr: str) -> Any:
        attributes = self.read_all_attributes(uid)
        if attr not in attributes:
            raise ParamValidationError(
                f"{self.uid_map[uid].type} {uid!r}: attribute {attr!r} is not exposed"
            )
        return attributes[attr]

    def write_attribute(self, uid: str, attr: str, value: Any) -> None:
        self._patch(uid, {attr: value})

    def read_all_attributes(self, uid: str) -> dict[str, Any]:
        if uid not in self._attributes:
            raise KeyError(f"Unknown GNPy element UID: {uid!r}")
        return dict(self._attributes[uid])

    def capture_attribute(self, uid: str, attr: str) -> Any:
        """The token is the attribute's value in user units, ``None`` if unset.

        In-process GNPy captures the raw stored state; over the API the user
        value is all there is, so a restore writes it back through the same
        unit conversion, which may differ from the stored value in the last
        bit.
        """
        return self.read_attribute(uid, attr)

    def restore_attribute(self, uid: str, attr: str, token: Any) -> None:
        if self.read_attribute(uid, attr) == token:
            return  # unchanged: no request, nothing to journal
        if token is None:
            # gnpy-api writes numbers only; it cannot put back an attribute
            # GNPy loaded as unset (e.g. an amplifier's tilt).
            raise ParamValidationError(
                f"{self.uid_map[uid].type} {uid!r}: {attr!r} was unset at "
                "capture and gnpy-api cannot unset an attribute"
            )
        self._patch(uid, {attr: token})

    def supported_attributes(self) -> dict[str, dict[str, dict[str, Any]]]:
        return self._schema

    # -- Element kind queries and failures ----------------------------------

    def is_fiber(self, uid: str) -> bool:
        element = self.uid_map.get(uid)
        return element is not None and element.type == "Fiber"

    def notify_fiber_failed(self, uid: str, failed: bool) -> None:
        """The service keeps the failure and routes around the fiber."""
        self._patch(uid, {"failed": failed})

    def compute_path(self, src_uid: str, dst_uid: str) -> list[str] | None:
        """GNPy's shortest path by fiber length, avoiding failed fibers."""
        if not self.available:
            return None
        try:
            result = self._in_session(lambda: self._client.request(
                "POST", "/session/paths",
                json={"source": src_uid, "destination": dst_uid},
            ))
        except (GnpyApiError, KeyError, ParamValidationError):
            logger.debug(
                "gnpy-api path computation failed for %s -> %s",
                src_uid, dst_uid, exc_info=True,
            )
            return None
        paths = result["paths"]
        return list(paths[0]["uids"]) if paths else None

    # -- Re-design and shutdown ------------------------------------------

    def redesign(self) -> None:
        """Re-run GNPy's design in the service against the mutated parameters."""
        if not self.available:
            return
        self._in_session(lambda: self._client.request("POST", "/session/redesign"))
        self._journal.append((None, None))
        self._refresh()

    def close(self) -> None:
        try:
            self._client.close_session()
        except GnpyApiError as exc:
            logger.warning("Could not close the gnpy-api session: %s", exc)


def _baseline_from_propagation(result: dict[str, Any]) -> OpmBaseline:
    """Map a ``/session/propagate`` answer onto the in-process OpmBaseline."""
    if result["status"] == "link-failed":
        # The sentinel TapiContext.get_or_compute_baseline builds for a failed path.
        return OpmBaseline(
            gsnr_db=0.0, osnr_ase_db=0.0, cd_ps_nm=0.0, pmd_ps=0.0,
            latency_ms=0.0, total_fiber_km=0.0, status="link-failed",
        )
    elements = [(el["uid"], el["type"]) for el in result["elements"]]
    worst = result["worst-channel"]
    return OpmBaseline(
        gsnr_db=worst["gsnr-db"],
        osnr_ase_db=worst["osnr-ase-db"],
        cd_ps_nm=worst["chromatic-dispersion-ps-nm"],
        pmd_ps=result["max-pmd-ps"],
        latency_ms=result["latency-ms"],
        total_fiber_km=result["total-fiber-km"],
        edfa_uids=[uid for uid, kind in elements if kind == "Edfa"],
        fiber_uids=[uid for uid, kind in elements if kind == "Fiber"],
        # PDL hinges in propagation order — Zarkosvky & Shtaif, Opt. Lett.
        # 45(5):1224 (2020). ROADMs/WSSs and EDFAs are discrete PDL elements;
        # fibers are excluded (negligible intrinsic PDL per the hinge model).
        pdl_elements=[(uid, kind) for uid, kind in elements if kind in ("Roadm", "Edfa")],
    )
