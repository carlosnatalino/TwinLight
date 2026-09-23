"""ONOS-facing T-API adapter for the TwinLight digital twin.

ONOS's ODTN ``ols`` driver (``org.onosproject.drivers.odtn.tapi.*``) speaks
**T-API v2.1**, while TwinLight serves **T-API v2.6.0**. This process is the
version adapter between the two. It is deliberately *not* part of
``src/twinlight``: the reshaping it does is not standard T-API, and CLAUDE.md
constraint #1 keeps the twin's T-API modules pure.

Both now sit under an RFC 8040 RESTCONF root, so the root itself is no longer
one of the differences -- see ``TWIN_DATA_ROOT``.

Everything here is driven by what the ONOS driver source actually does, not by
what the T-API specification says it should do. The four behaviours that matter:

1. ``TapiDeviceDescriptionDiscovery.discoverPortDetails()``
   GETs ``/restconf/data/tapi-common:context`` and, for every SIP, derives the
   ONOS port number from the **last dash-separated segment of the SIP UUID**::

       String[] uuidSeg = uuid.split("-");
       PortNumber portNumber = PortNumber.portNumber(uuidSeg[uuidSeg.length - 1]);

   ``PortNumber.portNumber(String)`` is ``UnsignedLongs.decode()``, which throws
   on TwinLight's ``uuid5`` tails (e.g. ``40965af0c942``). So each SIP is
   re-published to ONOS as ``<real-uuid>-<index>``; the real UUID stays visible
   in the ONOS port annotation, and ``_to_twin_sip()`` strips the suffix again
   on the way back. Indices start at 1 and carry no leading zeros, because
   ``UnsignedLongs.decode`` reads a leading ``0`` as an octal prefix.

2. The same method dereferences
   ``tapi-photonic-media:media-channel-service-interface-point-spec`` →
   ``mc-pool`` → ``available-spectrum`` unconditionally. That is the T-API
   **2.1** shape; 2.6 has no ``mc-pool`` at all, and publishes the same
   information as ``photonic-media-service-interface-point-spec`` →
   ``spectrum-capability-pac``. The twin publishes the 2.6 form, and
   :func:`_mc_pool` translates it -- including Hz to MHz and identityrefs to
   the bare tokens ONOS's string switches match.

3. ``TapiDeviceLambdaQuery`` GETs
   ``/restconf/data/tapi-common:context/service-interface-point=<uuid>`` and
   reads ``mc-pool`` off the **top level** of the response. TwinLight wraps that
   resource in ``{"tapi-common:context": {"service-interface-point": [...]}}``,
   so this adapter serves it unwrapped.

4. ``TapiFlowRuleProgrammable`` POSTs a connectivity-service whose body is a
   **JSON list** under ``tapi-connectivity:connectivity-service`` and carries
   ``service-layer`` / ``service-type`` but no modulation format. The twin
   accepts either spelling of the body now (RFC 7951 §5.4 makes the array of
   one correct, so that is no longer an incompatibility), but it still needs
   a modulation, which T-API 2.1 has nowhere to carry. The adapter supplies
   one as policy, in the T-API 2.6 place: a ``tapi-photonic-media`` augment
   on the end-point. Translated in :func:`create_connectivity_service`.

Non-T-API adapter introspection lives under ``/adapter/`` so it can never be
confused with the RESTCONF surface ONOS polls.
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=os.getenv("ADAPTER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tapi-adapter")

TWIN_BASE_URL = os.getenv("TWIN_BASE_URL", "http://twin:8080").rstrip("/")

# Where the twin serves its T-API data resources. It now mounts them under an
# RFC 8040 root (default /restconf) as well as the bare /data/ it has always
# used, so this adapter asks at the canonical location. Override with
# TWIN_DATA_ROOT=/data to talk to a twin predating that change.
TWIN_DATA_ROOT = os.getenv("TWIN_DATA_ROOT", "/restconf/data").rstrip("/")

# Modulation format the adapter requests when ONOS asks for a lightpath. ONOS's
# TAPI 2.1 connectivity request has nowhere to carry one, so it is adapter
# policy. Flipping this to DP-16QAM is the "the twin refuses an infeasible
# request" demo beat -- see integrations/onos/README.md.
MODULATION_FORMAT = os.getenv("ADAPTER_MODULATION", "DP-QPSK")

# Must be one of the strings ONOS's TapiDeviceHelper.getChannelSpacing()
# recognises. Note that its switch has trailing spaces in the "G_100GHZ ",
# "G_12_5GHZ " and "G_6_25GHZ " cases (an upstream typo), so those silently
# fall through to CHL_0GHZ and then divide by zero in getOchSignal(). Only
# "G_50GHZ" and "G_25GHZ" are safe; 50 GHz is the sane default.
GRID_GRANULARITY = os.getenv("ADAPTER_GRID_GRANULARITY", "G_50GHZ")

# ONOS's TapiDeviceHelper.removeInitalConnectivityServices() deletes every
# connectivity service it can see whenever its flow cache for the device is
# empty -- which includes the moment it first connects. Left exposed, that
# silently tears down lightpaths the twin (or the UI, or examples/
# demo_services.py) already had. Default is therefore to show ONOS only the
# services ONOS itself created.
EXPOSE_TWIN_SERVICES = os.getenv("ADAPTER_EXPOSE_TWIN_SERVICES", "false").lower() in (
    "1",
    "true",
    "yes",
)

SIP_REFRESH_SECONDS = float(os.getenv("ADAPTER_SIP_REFRESH_SECONDS", "30"))
HTTP_TIMEOUT = float(os.getenv("ADAPTER_HTTP_TIMEOUT", "120"))

# ONOS re-publishes SIP UUIDs as "<real-uuid>-<index>"; this peels the index off.
_INDEXED_SIP_RE = re.compile(r"^(?P<real>.+)-(?P<index>\d+)$")

# T-API v2.6.0 has no modulation leaf on connectivity-service -- the photonic
# module augments the end-point's layer-protocol-constraint instead, and that
# is what the twin now accepts. Duplicated here rather than imported because
# this adapter is a standalone container that does not install TwinLight.
# Note ONF spells 16QAM as MT_DP-QAM16, not MT_DP-16QAM.
OTSIA_CSEP_SPEC = "tapi-photonic-media:otsia-connectivity-service-end-point-spec"
MCG_CSEP_SPEC = "tapi-photonic-media:mcg-connectivity-service-end-point-spec"

# The twin's T-API 2.6 SIP augment, which this adapter translates into the
# 2.1 mc-pool that ONOS reads. See _mc_pool().
PHOTONIC_SIP_SPEC = (
    "tapi-photonic-media:photonic-media-service-interface-point-spec"
)
MODULATION_TO_MT = {
    "DP-QPSK": "MT_DP-QPSK",
    "DP-16QAM": "MT_DP-QAM16",
    "DP-64QAM": "MT_DP-QAM64",
}
MT_TO_MODULATION = {mt: fmt for fmt, mt in MODULATION_TO_MT.items()}


def _modulation_augment(modulation: str) -> dict[str, Any]:
    """The layer-protocol-constraint entry carrying a modulation format."""
    return {
        "local-id": "otsi",
        "layer-protocol-name": "PHOTONIC_MEDIA",
        OTSIA_CSEP_SPEC: {
            "otsi-config": [
                {
                    "local-id": "1",
                    "modulation": {
                        "standard-modulation-technique": MODULATION_TO_MT[
                            modulation
                        ],
                    },
                },
            ],
        },
    }


def _spectrum_of(service: dict[str, Any]) -> tuple[float, float] | None:
    """(centre THz, width GHz) of a twin service's assigned spectrum.

    Like the modulation, T-API 2.6 puts this on the end-point rather than on
    the connectivity-service -- there is no ``frequency-slot`` leaf. Band
    edges are in Hz; this converts to the units the demo output prints.
    """
    for end_point in service.get("end-point") or []:
        for constraint in end_point.get("layer-protocol-constraint") or []:
            spec = constraint.get(MCG_CSEP_SPEC) or {}
            for cfg in spec.get("mc-spectrum-config-pac") or []:
                band = cfg.get("spectrum") or {}
                lower, upper = (
                    band.get("lower-frequency"),
                    band.get("upper-frequency"),
                )
                if lower and upper:
                    return (lower + upper) / 2 / 1e12, (upper - lower) / 1e9
    return None


def _modulation_of(service: dict[str, Any]) -> str | None:
    """Read a modulation format back off a twin connectivity-service."""
    for end_point in service.get("end-point") or []:
        for constraint in end_point.get("layer-protocol-constraint") or []:
            spec = constraint.get(OTSIA_CSEP_SPEC) or {}
            for cfg in spec.get("otsi-config") or []:
                identity = (cfg.get("modulation") or {}).get(
                    "standard-modulation-technique", ""
                )
                fmt = MT_TO_MODULATION.get(identity.split(":")[-1])
                if fmt is not None:
                    return fmt
    return None


class TwinUnavailableError(RuntimeError):
    """The twin could not be reached or answered with a non-2xx status."""


class SipCatalogue:
    """Bidirectional map between TwinLight SIP UUIDs and ONOS-visible ones.

    ONOS port numbers are derived from the index, so the index must be stable
    for as long as ONOS holds the device: a SIP that changes index changes port
    number, and any flow rule referring to the old one breaks. SIPs are sorted
    by ``node-name`` (then UUID, to break ties deterministically) so the port
    order matches the alphabetical city order an operator sees in the twin's UI
    -- convenient for a demo, and stable as long as the topology is.
    """

    def __init__(self) -> None:
        self._by_onos: dict[str, dict[str, Any]] = {}
        self._twin_to_onos: dict[str, str] = {}
        self._fetched_at: float = 0.0

    @staticmethod
    def _sort_key(sip: dict[str, Any]) -> tuple[str, str]:
        name = ""
        for entry in sip.get("name") or []:
            if entry.get("value-name") == "node-name":
                name = str(entry.get("value", ""))
                break
        return (name, str(sip.get("uuid", "")))

    def rebuild(self, sips: list[dict[str, Any]]) -> None:
        by_onos: dict[str, dict[str, Any]] = {}
        twin_to_onos: dict[str, str] = {}
        for index, sip in enumerate(sorted(sips, key=self._sort_key), start=1):
            twin_uuid = str(sip["uuid"])
            onos_uuid = f"{twin_uuid}-{index}"
            by_onos[onos_uuid] = sip
            twin_to_onos[twin_uuid] = onos_uuid
        self._by_onos = by_onos
        self._twin_to_onos = twin_to_onos
        self._fetched_at = time.monotonic()
        log.info("SIP catalogue rebuilt: %d service interface points", len(by_onos))

    @property
    def stale(self) -> bool:
        return (
            not self._by_onos
            or (time.monotonic() - self._fetched_at) > SIP_REFRESH_SECONDS
        )

    def onos_uuids(self) -> list[str]:
        return list(self._by_onos)

    def twin_sip(self, onos_uuid: str) -> dict[str, Any] | None:
        return self._by_onos.get(onos_uuid)

    def onos_uuid_for(self, twin_uuid: str) -> str | None:
        return self._twin_to_onos.get(twin_uuid)

    def port_of(self, onos_uuid: str) -> int | None:
        match = _INDEXED_SIP_RE.match(onos_uuid)
        return int(match.group("index")) if match else None

    def __len__(self) -> int:
        return len(self._by_onos)


class OnosServiceRegistry:
    """Connectivity services this adapter created on ONOS's behalf.

    Keyed by the UUID ONOS generated, which the adapter passes through to the
    twin verbatim so that ONOS's later DELETE resolves without a second lookup.
    """

    def __init__(self) -> None:
        self._services: dict[str, dict[str, Any]] = {}
        self._endpoints: dict[str, dict[str, Any]] = {}
        self.rejections: list[dict[str, Any]] = []

    def record(
        self, uuid: str, payload: dict[str, Any], endpoints: dict[str, Any] | None = None
    ) -> None:
        self._services[uuid] = payload
        if endpoints is not None:
            # The ONOS port pair is the only field both sides share: ONOS's
            # Flows view keys on it, and it is what correlate.sh joins on.
            self._endpoints[uuid] = endpoints

    def endpoints(self, uuid: str) -> dict[str, Any]:
        return self._endpoints.get(uuid, {})

    def forget(self, uuid: str) -> None:
        self._services.pop(uuid, None)
        self._endpoints.pop(uuid, None)

    def record_rejection(self, uuid: str, reason: str, detail: str) -> None:
        # Bounded: a demo left running for hours should not grow without limit.
        self.rejections.append(
            {
                "uuid": uuid,
                "reason": reason,
                "detail": detail,
                "at": time.time(),
            }
        )
        del self.rejections[:-50]

    def uuids(self) -> list[str]:
        return list(self._services)

    def all(self) -> dict[str, dict[str, Any]]:
        return dict(self._services)


catalogue = SipCatalogue()
registry = OnosServiceRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(base_url=TWIN_BASE_URL, timeout=HTTP_TIMEOUT)
    log.info("T-API adapter up; twin=%s modulation=%s", TWIN_BASE_URL, MODULATION_FORMAT)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(
    title="TwinLight ONOS T-API adapter",
    description="T-API v2.6.0 (TwinLight) to v2.1 (ONOS ODTN) version adapter.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Twin access helpers
# ---------------------------------------------------------------------------


async def _twin_get(request: Request, path: str) -> Any:
    try:
        response = await request.app.state.client.get(path)
    except httpx.HTTPError as exc:
        raise TwinUnavailableError(f"GET {path}: {exc}") from exc
    if response.status_code >= 400:
        raise TwinUnavailableError(f"GET {path}: HTTP {response.status_code}")
    return response.json()


async def _list_sips(request: Request) -> list[dict[str, Any]]:
    payload = await _twin_get(
        request, f"{TWIN_DATA_ROOT}/tapi-common:context/service-interface-point"
    )
    return payload.get("tapi-common:context", {}).get("service-interface-point", [])


async def _ensure_catalogue(request: Request) -> None:
    if not catalogue.stale:
        return
    catalogue.rebuild(await _list_sips(request))


async def _fresh_sips(request: Request) -> dict[str, dict[str, Any]]:
    """Twin SIP UUID -> its current payload, read live from the twin.

    The catalogue caches SIPs so ONOS port *indices* stay stable, which they
    must -- a SIP that changes index changes port number and breaks every
    flow rule referring to it. But a SIP's mc-pool now carries live spectrum
    occupancy, which must not be served from that cache.
    """
    return {str(sip["uuid"]): sip for sip in await _list_sips(request)}


def _to_mhz(hz: int) -> int:
    """Hz (T-API v2.6.0) -> MHz (what ONOS's getOchSignal() works in).

    ``spectrum-band`` in tapi-photonic-media.yang types the frequency
    bounds as uint64 **Hz**. ONOS compares against
    ``BASE_FREQUENCY = 193100000``, i.e. 193.1 THz expressed in MHz.
    """
    return round(hz / 1_000_000)


def _mc_pool(sip: dict[str, Any]) -> dict[str, Any]:
    """Translate the twin's 2.6 spectrum capability into ONOS's 2.1 mc-pool.

    T-API v2.6.0 has no ``mc-pool``: it augments the SIP with
    ``photonic-media-service-interface-point-spec`` /
    ``spectrum-capability-pac``. ONOS's ODTN driver reads the v2.1
    ``media-channel-service-interface-point-spec`` / ``mc-pool``, so the
    reshaping here is pure version adaptation -- exactly what this process
    exists for -- plus two value translations ONOS needs:

    * Hz -> MHz (see :func:`_to_mhz`).
    * identityrefs -> the bare tokens ONOS's string switches match. The twin
      truthfully advertises ``GRID_TYPE_FLEX`` at
      ``ADJUSTMENT_GRANULARITY_G_6_25GHZ``, but ONOS's
      ``TapiDeviceHelper.getChannelSpacing()`` has trailing spaces in its
      ``G_6_25GHZ``/``G_12_5GHZ``/``G_100GHZ`` case labels, so those fall
      through to ``CHL_0GHZ`` and then divide by zero in ``getOchSignal()``.
      Only ``G_50GHZ`` and ``G_25GHZ`` are safe, so the granularity ONOS is
      shown is forced to GRID_GRANULARITY. The twin is not changed to suit
      it: a 6.25 GHz port that claimed 50 GHz would mislead every other
      client.
    """
    pac = (sip.get(PHOTONIC_SIP_SPEC) or {}).get("spectrum-capability-pac") or {}

    def rewrite(bands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "upper-frequency": _to_mhz(int(b["upper-frequency"])),
                "lower-frequency": _to_mhz(int(b["lower-frequency"])),
                "frequency-constraint": {
                    "grid-type": "DWDM",
                    "adjustment-granularity": GRID_GRANULARITY,
                },
            }
            for b in bands
        ]

    supportable = rewrite(pac.get("supportable-spectrum") or [])
    available = rewrite(pac.get("available-spectrum") or [])
    # ONOS prefers available-spectrum and falls back to supportable; a SIP
    # with every slot taken would otherwise hand it an empty list and a
    # divide by zero, so fall back rather than advertise nothing.
    return {
        "available-spectrum": available or supportable,
        "supportable-spectrum": supportable,
        "occupied-spectrum": rewrite(pac.get("occupied-spectrum") or []),
    }


def _to_twin_sip(onos_uuid: str) -> str:
    """Recover the twin's SIP UUID from the ONOS-visible indexed form."""
    match = _INDEXED_SIP_RE.match(onos_uuid)
    return match.group("real") if match else onos_uuid


def _node_name(sip: dict[str, Any]) -> str:
    """The GNPy element name a SIP sits on, e.g. "Atlanta" from "trx Atlanta"."""
    for entry in sip.get("name") or []:
        if entry.get("value-name") == "node-name":
            return str(entry.get("value", "")).replace("trx ", "")
    return ""


def _decorate_sip(sip: dict[str, Any], onos_uuid: str) -> dict[str, Any]:
    """Return the SIP in the shape the ONOS ODTN ``ols`` driver requires.

    ``supported-layer-protocol-qualifier`` and the ``mc-pool`` block are both
    load-bearing: ``checkValidEndpoint()`` rejects the SIP without the former,
    and ``parseTapiPorts()`` throws a NullPointerException without the latter.
    Both are T-API **2.1** spellings -- 2.6 renamed the qualifier leaf-list to
    ``supported-cep-layer-protocol-qualifier-instances`` and replaced
    ``mc-pool`` outright -- so supplying them is version adaptation, not a
    twin gap.

    The spectrum is the twin's real occupancy now, not a fabricated full
    C-band; see :func:`_mc_pool`. The 2.6 augment is dropped from what ONOS
    sees, because its driver would not know what to do with it.
    """
    decorated = dict(sip)
    decorated.pop(PHOTONIC_SIP_SPEC, None)
    decorated["uuid"] = onos_uuid
    decorated["supported-layer-protocol-qualifier"] = ["PHOTONIC_LAYER_QUALIFIER_NMC"]
    decorated["tapi-photonic-media:media-channel-service-interface-point-spec"] = {
        "mc-pool": _mc_pool(sip)
    }
    return decorated


# ---------------------------------------------------------------------------
# RESTCONF surface consumed by the ONOS ODTN "ols" driver
# ---------------------------------------------------------------------------


@app.get("/restconf/data/tapi-common:context")
async def get_context(request: Request) -> JSONResponse:
    """Full T-API context, reshaped for ONOS.

    Polled by ``discoverPortDetails()`` on every device poll, so it must stay
    cheap and must never raise once the twin is up.
    """
    try:
        await _ensure_catalogue(request)
        live = await _fresh_sips(request)
    except TwinUnavailableError as exc:
        log.error("twin unreachable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    sips = [
        _decorate_sip(
            live.get(_to_twin_sip(onos_uuid))
            or catalogue.twin_sip(onos_uuid)
            or {},
            onos_uuid,
        )
        for onos_uuid in catalogue.onos_uuids()
    ]
    return JSONResponse(
        {"tapi-common:context": {"uuid": "twinlight-tapi-context", "service-interface-point": sips}}
    )


@app.get("/restconf/data/tapi-common:context/service-interface-point={onos_uuid}")
async def get_sip(onos_uuid: str, request: Request) -> JSONResponse:
    """One SIP, **unwrapped** -- TapiDeviceLambdaQuery reads mc-pool at the root."""
    try:
        await _ensure_catalogue(request)
        sip = catalogue.twin_sip(onos_uuid)
        if sip is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"SIP {onos_uuid} not found"
            )
        # Re-read this SIP from the twin rather than serving the catalogue's
        # copy: mc-pool now carries live occupancy, and TapiDeviceLambdaQuery
        # picks a lambda from it. A cached available-spectrum would hand ONOS
        # a wavelength the twin has already assigned to something else.
        live = await _fresh_sips(request)
        sip = live.get(_to_twin_sip(onos_uuid), sip)
    except TwinUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return JSONResponse(_decorate_sip(sip, onos_uuid))


@app.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/")
@app.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context")
async def get_connectivity_context(request: Request) -> JSONResponse:
    """Connectivity services visible to ONOS.

    By default only services ONOS created are listed; see EXPOSE_TWIN_SERVICES
    for why hiding the rest protects a running demo.
    """
    try:
        payload = await _twin_get(
            request,
            f"{TWIN_DATA_ROOT}/tapi-connectivity:connectivity-context/connectivity-service",
        )
    except TwinUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    services = payload.get("tapi-connectivity:connectivity-context", {}).get(
        "connectivity-service", []
    )
    if not EXPOSE_TWIN_SERVICES:
        owned = set(registry.uuids())
        services = [s for s in services if str(s.get("uuid")) in owned]

    return JSONResponse(
        {"tapi-connectivity:connectivity-context": {"connectivity-service": services}}
    )


@app.post("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/")
@app.post("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context")
async def create_connectivity_service(body: dict, request: Request) -> Response:
    """Translate ONOS's T-API 2.1 connectivity request and forward it to the twin.

    ONOS sends a *list* under ``tapi-connectivity:connectivity-service``; the
    twin wants a single object plus a ``modulation-format``. ONOS's UUID is
    reused as the twin's service UUID so the later DELETE lines up.

    A 409 from the twin is not a failure of the adapter: it means the RMSA/QoT
    admission path refused the lightpath. It is relayed unchanged so ONOS marks
    the flow rule as failed, and recorded for ``/adapter/status``.
    """
    raw = body.get("tapi-connectivity:connectivity-service", body)
    services = raw if isinstance(raw, list) else [raw]
    if not services:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No connectivity-service in request",
        )

    # ONOS emits exactly one service per flow rule.
    onos_service = services[0]
    onos_uuid = str(onos_service.get("uuid", ""))
    endpoints = onos_service.get("end-point", [])
    if len(endpoints) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected 2 end-points, got {len(endpoints)}",
        )

    await _ensure_catalogue(request)

    twin_endpoints = []
    onos_ports: list[int | None] = []
    node_names: list[str] = []
    for local_id, endpoint in enumerate(endpoints, start=1):
        onos_sip = str(
            endpoint.get("service-interface-point", {}).get("service-interface-point-uuid", "")
        )
        twin_sip = _to_twin_sip(onos_sip)
        onos_ports.append(catalogue.port_of(onos_sip))
        node_names.append(_node_name(catalogue.twin_sip(onos_sip) or {}))
        twin_endpoints.append(
            {
                "local-id": str(local_id),
                "service-interface-point": {"service-interface-point-uuid": twin_sip},
                # The twin takes modulation as a T-API 2.6 photonic augment
                # on the end-point, not as a field on the service.
                "layer-protocol-constraint": [
                    _modulation_augment(MODULATION_FORMAT)
                ],
            }
        )

    # The ONOS Flows view identifies a rule by its in/out port pair, and that
    # pair is the only identifier both systems hold: ONOS's own flow id never
    # reaches the adapter (the driver sends a freshly generated service UUID and
    # nothing else), and the service UUID never appears in the Flows view.
    # Stamping the pair into the T-API name list is therefore what lets an
    # operator look at a row in ONOS and find the same lightpath in the
    # TwinLight UI, which renders "service-name". Extra name entries are
    # ordinary T-API NameAndValue pairs, so nothing non-standard is introduced.
    port_pair = f"{onos_ports[0]}->{onos_ports[1]}"
    endpoint_pair = " -> ".join(n or "?" for n in node_names)

    twin_body = {
        "tapi-connectivity:connectivity-service": {
            "uuid": onos_uuid,
            "name": [
                {
                    "value-name": "service-name",
                    "value": f"ONOS port {port_pair}  ({endpoint_pair})",
                },
                {"value-name": "onos-port-pair", "value": port_pair},
                {"value-name": "provisioned-by", "value": "onos-odtn"},
            ],
            "end-point": twin_endpoints,
        }
    }

    log.info(
        "ONOS requests lightpath %s: %s -> %s (%s)",
        onos_uuid[:8],
        twin_endpoints[0]["service-interface-point"]["service-interface-point-uuid"][:8],
        twin_endpoints[1]["service-interface-point"]["service-interface-point-uuid"][:8],
        MODULATION_FORMAT,
    )

    try:
        response = await request.app.state.client.post(
            f"{TWIN_DATA_ROOT}/tapi-connectivity:connectivity-context/connectivity-service",
            json=twin_body,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    if response.status_code in (200, 201):
        payload = response.json()
        registry.record(
            onos_uuid,
            payload,
            endpoints={
                "onos-in-port": onos_ports[0],
                "onos-out-port": onos_ports[1],
                "onos-port-pair": port_pair,
                "a-end": node_names[0],
                "z-end": node_names[1],
            },
        )
        service = payload.get("tapi-connectivity:connectivity-service", {})
        spectrum = _spectrum_of(service)
        if spectrum is None:
            log.info("twin admitted %s (no spectrum reported)", onos_uuid[:8])
        else:
            log.info(
                "twin admitted %s at %.4f THz (slot width %.2f GHz)",
                onos_uuid[:8],
                *spectrum,
            )
        return JSONResponse(payload, status_code=status.HTTP_201_CREATED)

    detail = _error_detail(response)
    if response.status_code == status.HTTP_409_CONFLICT:
        # The interesting case: the physics said no.
        log.warning("twin REFUSED lightpath %s: %s", onos_uuid[:8], detail)
        registry.record_rejection(onos_uuid, "rmsa-qot-refused", detail)
    else:
        log.error("twin rejected %s: HTTP %s %s", onos_uuid[:8], response.status_code, detail)
        registry.record_rejection(onos_uuid, f"http-{response.status_code}", detail)

    return JSONResponse(
        {"error": detail, "twin-status": response.status_code},
        status_code=response.status_code,
    )


@app.delete(
    "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
    "connectivity-service={uuid}"
)
async def delete_connectivity_service(uuid: str, request: Request) -> Response:
    """Delete a lightpath. ONOS treats only 204 as success."""
    try:
        response = await request.app.state.client.delete(
            f"{TWIN_DATA_ROOT}/tapi-connectivity:connectivity-context"
            f"/connectivity-service={uuid}"
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    if response.status_code in (200, 204):
        registry.forget(uuid)
        log.info("released lightpath %s", uuid[:8])
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    log.error("could not delete %s: HTTP %s", uuid[:8], response.status_code)
    return JSONResponse(
        {"error": _error_detail(response)}, status_code=response.status_code
    )


def _error_detail(response: httpx.Response) -> str:
    """Pull a human-readable message out of a twin error response.

    The twin answers under ``/data/`` with RFC 8040 errors, but FastAPI's own
    validation errors use ``detail``; handle both, and fall back to raw text.
    """
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    errors = payload.get("ietf-restconf:errors", {}).get("error", [])
    if errors:
        return str(errors[0].get("error-message", errors[0]))
    if "detail" in payload:
        return str(payload["detail"])
    return str(payload)[:500]


# ---------------------------------------------------------------------------
# Adapter introspection -- deliberately outside /restconf
# ---------------------------------------------------------------------------


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness plus twin reachability, for the compose healthcheck."""
    twin_ok = True
    try:
        await _twin_get(request, "/health")
    except TwinUnavailableError:
        twin_ok = False
    return {
        "status": "ok" if twin_ok else "degraded",
        "twin": TWIN_BASE_URL,
        "twin-reachable": twin_ok,
        "sips": len(catalogue),
    }


@app.get("/adapter/status")
async def adapter_status(request: Request) -> dict[str, Any]:
    """What the adapter is doing -- the demo's explain-yourself endpoint."""
    try:
        await _ensure_catalogue(request)
    except TwinUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return {
        "twin": TWIN_BASE_URL,
        "modulation-format": MODULATION_FORMAT,
        "grid-granularity": GRID_GRANULARITY,
        "expose-twin-services": EXPOSE_TWIN_SERVICES,
        "sip-count": len(catalogue),
        "onos-created-services": registry.all(),
        # Join key between the ONOS Flows view and the twin's service list.
        "service-endpoints": {u: registry.endpoints(u) for u in registry.uuids()},
        "recent-rejections": registry.rejections[-10:],
    }


@app.post("/adapter/modulation")
async def set_modulation(body: dict) -> dict[str, Any]:
    """Change the modulation format the adapter requests, without a restart.

    ONOS's T-API 2.1 connectivity request has no field for a modulation format,
    so it is adapter policy rather than something ONOS can express. Making it
    switchable at runtime is what lets the demo show the same ONOS flow rule
    being admitted at DP-QPSK and refused at DP-16QAM.
    """
    global MODULATION_FORMAT
    requested = str(body.get("modulation-format", "")).upper()
    valid = {"DP-QPSK", "DP-16QAM", "DP-64QAM"}
    if requested not in valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"modulation-format must be one of {sorted(valid)}",
        )
    previous, MODULATION_FORMAT = MODULATION_FORMAT, requested
    log.info("modulation format %s -> %s", previous, MODULATION_FORMAT)
    return {"previous": previous, "modulation-format": MODULATION_FORMAT}


@app.get("/adapter/ports")
async def adapter_ports(request: Request) -> dict[str, Any]:
    """The SIP-to-ONOS-port map, so a demo can name ports without guessing."""
    try:
        await _ensure_catalogue(request)
    except TwinUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    ports = []
    for onos_uuid in catalogue.onos_uuids():
        sip = catalogue.twin_sip(onos_uuid) or {}
        node_name = next(
            (
                n.get("value")
                for n in sip.get("name", [])
                if n.get("value-name") == "node-name"
            ),
            None,
        )
        ports.append(
            {
                "onos-port": catalogue.port_of(onos_uuid),
                "node-name": node_name,
                "twin-sip-uuid": sip.get("uuid"),
                "onos-sip-uuid": onos_uuid,
            }
        )
    ports.sort(key=lambda p: p["onos-port"] or 0)
    return {"ports": ports}
