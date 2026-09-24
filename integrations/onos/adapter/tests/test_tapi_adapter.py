"""Unit tests for the ONOS-facing T-API adapter.

These encode the four things ONOS's ODTN ``ols`` driver requires of the
RESTCONF payload. Each is a real failure mode that was observed before the
adapter existed, and each fails *silently* in ONOS — a broken payload produces
a device with zero ports and a stack trace buried in the Karaf log, not an
error the operator sees. So they are asserted here rather than left to the
integration run.

Run them directly (the root ``pytest`` only collects ``tests/``, which keeps
the twin's coverage gate measuring the twin):

    .venv/bin/pytest integrations/onos/adapter/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tapi_adapter
from tapi_adapter import app

TWIN_SIP_A = "21359ce4-e8f1-5acf-8b91-40965af0c942"
TWIN_SIP_Z = "ad0d2a78-4f16-50be-bbfd-165e6b4f3871"

# The twin's grid: 768 slots of 6.25 GHz with slot 384 centred on 193.1 THz.
# Slot index is a slot *centre*, so the band edges sit half a slot outside
# the first and last centres -- 4800 GHz wide, not symmetric about 193.1.
BAND_LOWER_HZ = 190_696_875_000_000
BAND_UPPER_HZ = 195_496_875_000_000
BAND_LOWER_MHZ = 190_696_875
BAND_UPPER_MHZ = 195_496_875


def _band(lower_hz: int, upper_hz: int) -> dict:
    """A T-API v2.6.0 spectrum-band: uint64 Hz plus the real grid type."""
    return {
        "lower-frequency": lower_hz,
        "upper-frequency": upper_hz,
        "frequency-constraint": {
            "grid-type": "GRID_TYPE_FLEX",
            "adjustment-granularity": "ADJUSTMENT_GRANULARITY_G_6_25GHZ",
        },
    }


def _sip(uuid: str, node: str, occupied: list[tuple[int, int]] | None = None) -> dict:
    """A twin SIP carrying the 2.6 photonic augment the adapter translates."""
    occupied = occupied or []
    return {
        "uuid": uuid,
        "name": [{"value-name": "node-name", "value": node}],
        "layer-protocol-name": "PHOTONIC_MEDIA",
        "tapi-photonic-media:photonic-media-service-interface-point-spec": {
            "spectrum-capability-pac": {
                "supportable-spectrum": [_band(BAND_LOWER_HZ, BAND_UPPER_HZ)],
                "available-spectrum": [_band(BAND_LOWER_HZ, BAND_UPPER_HZ)],
                "occupied-spectrum": [_band(lo, hi) for lo, hi in occupied],
            }
        },
    }


SIPS = [
    _sip(TWIN_SIP_Z, "trx Atlanta"),
    _sip(TWIN_SIP_A, "trx Abilene"),
]

# The block the twin reports back on an admitted service: 190.725 THz centre,
# 56.25 GHz wide, as an end-point augment with edges in Hz. There is no
# frequency-slot leaf in T-API 2.6.
ADMITTED_CENTRE_THZ = 190.725
ADMITTED_WIDTH_GHZ = 56.25


def _admitted_end_point() -> dict:
    half = ADMITTED_WIDTH_GHZ * 1e9 / 2
    centre = ADMITTED_CENTRE_THZ * 1e12
    return {
        "local-id": "1",
        "layer-protocol-constraint": [
            {
                "local-id": "otsi",
                "tapi-photonic-media:mcg-connectivity-service-end-point-spec": {
                    "number-of-mc": 1,
                    "mc-spectrum-config-pac": [
                        {
                            "local-id": "1",
                            "spectrum": {
                                "lower-frequency": int(centre - half),
                                "upper-frequency": int(centre + half),
                            },
                        }
                    ],
                },
            }
        ],
    }


class TwinStub:
    """Minimal stand-in for the twin, recording what the adapter sends it."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.deleted: list[str] = []
        self.create_status = 201
        self.create_body: dict = {}
        self.services: list[dict] = []
        # Mutable so a test can change what spectrum the twin reports.
        self.sips: list[dict] = list(SIPS)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/service-interface-point"):
            return httpx.Response(
                200,
                json={"tapi-common:context": {"service-interface-point": self.sips}},
            )
        if path.endswith("/connectivity-service") and request.method == "POST":
            self.posted.append(json.loads(request.content))
            return httpx.Response(self.create_status, json=self.create_body)
        if path.endswith("/connectivity-service") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "tapi-connectivity:connectivity-context": {
                        "connectivity-service": self.services
                    }
                },
            )
        if request.method == "DELETE":
            self.deleted.append(path.rsplit("=", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture
def twin() -> TwinStub:
    return TwinStub()


@pytest.fixture
def client(twin: TwinStub):
    # Module-level state persists across requests by design (the SIP catalogue
    # is a cache), so it has to be reset between tests.
    tapi_adapter.catalogue = tapi_adapter.SipCatalogue()
    tapi_adapter.registry = tapi_adapter.OnosServiceRegistry()
    tapi_adapter.MODULATION_FORMAT = "DP-QPSK"

    with TestClient(app) as c:
        c.app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(twin.handler), base_url="http://twin"
        )
        yield c


def context(client) -> list[dict]:
    r = client.get("/restconf/data/tapi-common:context")
    assert r.status_code == 200
    return r.json()["tapi-common:context"]["service-interface-point"]


# --- Requirement 1: port numbers come from the SIP UUID --------------------


def test_sip_uuid_tail_survives_onos_port_parsing(client):
    """ONOS does PortNumber.portNumber(uuid.split("-")[-1]), i.e.
    UnsignedLongs.decode(). A hex tail throws NumberFormatException and the
    device ends up with zero ports."""
    for sip in context(client):
        tail = sip["uuid"].split("-")[-1]
        assert tail.isdigit(), f"{tail!r} would throw NumberFormatException"
        # A leading zero makes decode() read the value as octal.
        assert not tail.startswith("0"), f"{tail!r} is parsed as octal"


def test_port_numbers_are_unique_and_one_based(client):
    tails = [int(s["uuid"].split("-")[-1]) for s in context(client)]
    assert sorted(tails) == list(range(1, len(tails) + 1))


def test_sip_ordering_is_stable_and_alphabetical(client):
    """Port number must not move between polls, or flow rules break."""
    names = [
        next(n["value"] for n in s["name"] if n["value-name"] == "node-name")
        for s in context(client)
    ]
    assert names == ["trx Abilene", "trx Atlanta"]  # input order was reversed
    assert [s["uuid"] for s in context(client)] == [s["uuid"] for s in context(client)]


def test_real_twin_uuid_is_recoverable(client):
    """The real UUID stays visible to ONOS and round-trips back."""
    uuids = [s["uuid"] for s in context(client)]
    assert TWIN_SIP_A + "-1" in uuids
    assert tapi_adapter._to_twin_sip(TWIN_SIP_A + "-1") == TWIN_SIP_A


# --- Requirement 2: the mc-pool block ONOS dereferences unguarded ----------


def test_sips_carry_everything_parse_tapi_ports_touches(client):
    for sip in context(client):
        assert "PHOTONIC_MEDIA" in sip["layer-protocol-name"]
        assert "PHOTONIC_LAYER_QUALIFIER_NMC" in sip["supported-layer-protocol-qualifier"]
        pool = sip["tapi-photonic-media:media-channel-service-interface-point-spec"]["mc-pool"]
        spectrum = pool["available-spectrum"][0]
        assert spectrum["upper-frequency"] > spectrum["lower-frequency"]
        constraint = spectrum["frequency-constraint"]
        assert constraint["grid-type"] == "DWDM"
        # ONOS's getChannelSpacing() has trailing spaces in its other case
        # labels, so anything else silently becomes CHL_0GHZ and then divides
        # by zero in getOchSignal().
        assert constraint["adjustment-granularity"] in ("G_50GHZ", "G_25GHZ")


def test_occupied_spectrum_is_relayed_not_invented(client, twin):
    """The mc-pool reflects the twin's real occupancy.

    Before, the adapter synthesised a full C-band marked entirely available
    on every SIP -- harmless for discovery, but a fiction. It now translates
    the twin's spectrum-capability-pac, so a SIP with a lightpath up reports
    the band that lightpath holds.
    """
    taken_lo = 190_696_875_000_000
    taken_hi = taken_lo + 56_250_000_000  # 9 slots x 6.25 GHz
    twin.sips = [
        _sip(TWIN_SIP_Z, "trx Atlanta", occupied=[(taken_lo, taken_hi)]),
        _sip(TWIN_SIP_A, "trx Abilene"),
    ]

    pools = {
        s["uuid"]: s[
            "tapi-photonic-media:media-channel-service-interface-point-spec"
        ]["mc-pool"]
        for s in context(client)
    }
    atlanta = pools[TWIN_SIP_Z + "-2"]
    assert atlanta["occupied-spectrum"] == [
        {
            "lower-frequency": taken_lo // 1_000_000,
            "upper-frequency": taken_hi // 1_000_000,
            "frequency-constraint": {
                "grid-type": "DWDM",
                "adjustment-granularity": "G_50GHZ",
            },
        }
    ]
    assert pools[TWIN_SIP_A + "-1"]["occupied-spectrum"] == []


def test_per_sip_resource_reads_spectrum_live(client, twin):
    """TapiDeviceLambdaQuery must not be served a cached available-spectrum.

    The catalogue caches SIPs so ONOS port indices stay stable, but that
    cache must not carry occupancy: ONOS picks a lambda out of this block,
    and a stale one would pick a wavelength already in use.
    """
    # Prime the catalogue with an idle SIP, then make the twin report a busy one.
    context(client)
    taken_lo = 190_696_875_000_000
    taken_hi = taken_lo + 56_250_000_000
    twin.sips = [
        _sip(TWIN_SIP_Z, "trx Atlanta", occupied=[(taken_lo, taken_hi)]),
        _sip(TWIN_SIP_A, "trx Abilene"),
    ]

    r = client.get(
        f"/restconf/data/tapi-common:context/service-interface-point={TWIN_SIP_Z}-2"
    )
    assert r.status_code == 200
    pool = r.json()[
        "tapi-photonic-media:media-channel-service-interface-point-spec"
    ]["mc-pool"]
    assert pool["occupied-spectrum"], "served a stale, fully-available mc-pool"


def test_fully_occupied_sip_still_offers_a_band(client, twin):
    """ONOS divides by the band width, so an empty list would break it.

    A SIP with no free spectrum falls back to supportable-spectrum rather
    than advertising nothing.
    """
    twin.sips = [
        {
            **_sip(TWIN_SIP_Z, "trx Atlanta"),
            "tapi-photonic-media:photonic-media-service-interface-point-spec": {
                "spectrum-capability-pac": {
                    "supportable-spectrum": [
                        _band(BAND_LOWER_HZ, BAND_UPPER_HZ)
                    ],
                    "available-spectrum": [],
                    "occupied-spectrum": [
                        _band(BAND_LOWER_HZ, BAND_UPPER_HZ)
                    ],
                }
            },
        },
        _sip(TWIN_SIP_A, "trx Abilene"),
    ]
    pool = context(client)[1][
        "tapi-photonic-media:media-channel-service-interface-point-spec"
    ]["mc-pool"]
    assert pool["available-spectrum"], "ONOS would divide by zero on an empty list"


def test_spectrum_window_is_in_megahertz_around_the_twins_centre(client):
    """ONOS compares against BASE_FREQUENCY = 193100000, i.e. MHz."""
    pool = context(client)[0][
        "tapi-photonic-media:media-channel-service-interface-point-spec"
    ]["mc-pool"]
    spectrum = pool["available-spectrum"][0]
    # Translated from the twin's Hz, not recomputed: these are the twin's own
    # band edges divided by 1e6. 768 slots x 6.25 GHz = 4800 GHz wide.
    assert spectrum["lower-frequency"] == BAND_LOWER_MHZ
    assert spectrum["upper-frequency"] == BAND_UPPER_MHZ
    assert spectrum["upper-frequency"] - spectrum["lower-frequency"] == 4_800_000


def test_onos_first_och_signal_is_computable(client):
    """Replicates TapiDeviceHelper.getOchSignal() far enough to prove it will
    neither divide by zero nor land outside the band."""
    pool = context(client)[0][
        "tapi-photonic-media:media-channel-service-interface-point-spec"
    ]["mc-pool"]
    spec = pool["available-spectrum"][0]
    spacing_mhz = 50_000  # CHL_50GHZ
    lambda_count = (spec["upper-frequency"] - spec["lower-frequency"]) / spacing_mhz
    assert lambda_count == 96
    multiplier = int((spec["upper-frequency"] - 4 // 2 - 193_100_000) / spacing_mhz)
    centre_thz = 193.1 + multiplier * 0.05
    assert spec["lower-frequency"] / 1e6 <= centre_thz <= spec["upper-frequency"] / 1e6


# --- Requirement 3: the per-SIP resource is not wrapped --------------------


def test_single_sip_is_served_unwrapped(client):
    """TapiDeviceLambdaQuery reads mc-pool off the top level of the response;
    the twin wraps it in tapi-common:context."""
    uuid = context(client)[0]["uuid"]
    body = client.get(
        f"/restconf/data/tapi-common:context/service-interface-point={uuid}"
    ).json()
    assert "tapi-common:context" not in body
    assert "tapi-photonic-media:media-channel-service-interface-point-spec" in body


def test_unknown_sip_is_404(client):
    r = client.get("/restconf/data/tapi-common:context/service-interface-point=nope-9")
    assert r.status_code == 404


# --- Requirement 4: the connectivity request shape -------------------------


def onos_connectivity_request(sip_a: str, sip_z: str, uuid: str = "onos-uuid-1") -> dict:
    """What TapiFlowRuleProgrammable.createConnectivityRequest() emits."""
    return {
        "tapi-connectivity:connectivity-service": [
            {
                "uuid": uuid,
                "service-layer": "PHOTONIC_MEDIA",
                "service-type": "POINT_TO_POINT_CONNECTIVITY",
                "end-point": [
                    {
                        "local-id": str(i),
                        "layer-protocol-name": "PHOTONIC_MEDIA",
                        "layer-protocol-qualifier": (
                            "tapi-photonic-media:PHOTONIC_LAYER_QUALIFIER_NMC"
                        ),
                        "service-interface-point": {"service-interface-point-uuid": sip},
                    }
                    for i, sip in ((1, sip_a), (2, sip_z))
                ],
            }
        ]
    }


def test_list_body_is_translated_to_the_twins_object_form(client, twin):
    twin.create_body = {
        "tapi-connectivity:connectivity-service": {
            "uuid": "onos-uuid-1",
            "end-point": [_admitted_end_point()],
        }
    }
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    assert r.status_code == 201

    sent = twin.posted[0]["tapi-connectivity:connectivity-service"]
    assert isinstance(sent, dict), "twin expects a single object, not a list"
    # ONOS's UUID is reused so its later DELETE resolves without a lookup.
    assert sent["uuid"] == "onos-uuid-1"
    # Modulation goes in the T-API 2.6 place -- an augment on the end-point,
    # since tapi-connectivity has no modulation leaf.
    assert "modulation-format" not in sent
    assert tapi_adapter._modulation_of(sent) == "DP-QPSK"
    # Index suffixes must be stripped or the twin cannot resolve the SIPs.
    got = [e["service-interface-point"]["service-interface-point-uuid"] for e in sent["end-point"]]
    assert got == [TWIN_SIP_A, TWIN_SIP_Z]


def test_service_name_carries_the_onos_port_pair(client, twin):
    """The port pair is the only identifier ONOS and the twin share, so it is
    stamped into the T-API name list — that is what makes a row in the ONOS
    Flows view findable in the TwinLight UI, which renders "service-name"."""
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    names = {
        n["value-name"]: n["value"]
        for n in twin.posted[0]["tapi-connectivity:connectivity-service"]["name"]
    }
    assert names["onos-port-pair"] == "1->2"
    assert names["provisioned-by"] == "onos-odtn"
    # Both the ports (to match ONOS) and the cities (to be readable on stage).
    assert "1->2" in names["service-name"]
    assert "Abilene -> Atlanta" in names["service-name"]


def test_status_exposes_the_join_key(client, twin):
    """correlate.sh joins ONOS flows to twin services on the port pair."""
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    endpoints = client.get("/adapter/status").json()["service-endpoints"]["onos-uuid-1"]
    assert endpoints["onos-in-port"] == 1
    assert endpoints["onos-out-port"] == 2
    assert endpoints["onos-port-pair"] == "1->2"
    assert (endpoints["a-end"], endpoints["z-end"]) == ("Abilene", "Atlanta")


def test_deleting_an_already_gone_lightpath_reports_success(client, twin):
    """ONOS retires a flow rule only on 204. After the twin restarts without
    its checkpoint, every flow ONOS holds refers to a service that no longer
    exists; relaying the twin's 404 would wedge all of them in PENDING_REMOVE,
    retried forever and unremovable."""

    def gone(request):
        return httpx.Response(404, json={"detail": "Service ... not found"})

    client.app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(gone), base_url="http://twin"
    )
    r = client.delete(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
        "connectivity-service=vanished"
    )
    assert r.status_code == 204


def test_delete_still_surfaces_real_failures(client, twin):
    """A 500 is not 'already gone' — ONOS must keep the rule and retry."""

    def broken(request):
        return httpx.Response(500, json={"detail": "boom"})

    client.app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(broken), base_url="http://twin"
    )
    r = client.delete(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
        "connectivity-service=onos-uuid-1"
    )
    assert r.status_code == 500


def test_endpoints_are_forgotten_on_delete(client, twin):
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    client.delete(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
        "connectivity-service=onos-uuid-1"
    )
    assert client.get("/adapter/status").json()["service-endpoints"] == {}


def test_qot_refusal_is_relayed_unchanged_and_recorded(client, twin):
    """A 409 is the twin refusing on physics grounds. ONOS must see it, so the
    flow rule fails rather than appearing installed."""
    twin.create_status = 409
    twin.create_body = {
        "ietf-restconf:errors": {
            "error": [
                {
                    "error-tag": "resource-denied",
                    "error-message": "Path GSNR 11.9 dB below required 16.0 dB",
                }
            ]
        }
    }
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    assert r.status_code == 409

    status = client.get("/adapter/status").json()
    assert status["onos-created-services"] == {}
    rejection = status["recent-rejections"][-1]
    assert rejection["reason"] == "rmsa-qot-refused"
    assert "11.9 dB" in rejection["detail"]


def test_delete_returns_204_which_is_all_onos_accepts(client, twin):
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    r = client.delete(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
        "connectivity-service=onos-uuid-1"
    )
    assert r.status_code == 204
    assert twin.deleted == ["onos-uuid-1"]
    assert client.get("/adapter/status").json()["onos-created-services"] == {}


def test_request_without_two_endpoints_is_rejected(client):
    body = onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2")
    body["tapi-connectivity:connectivity-service"][0]["end-point"].pop()
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=body,
    )
    assert r.status_code == 400


# --- Protecting the twin's own lightpaths ----------------------------------


def test_twin_native_services_are_hidden_from_onos(client, twin):
    """ONOS deletes every service it can see whenever its flow cache is empty,
    which would tear down lightpaths the UI or demo_services.py created."""
    twin.services = [{"uuid": "created-in-the-ui"}]
    body = client.get(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
    ).json()
    listed = body["tapi-connectivity:connectivity-context"]["connectivity-service"]
    assert listed == []


def test_onos_created_services_remain_visible_for_resync(client, twin):
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    twin.services = [{"uuid": "onos-uuid-1"}, {"uuid": "created-in-the-ui"}]
    body = client.get(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/"
    ).json()
    listed = body["tapi-connectivity:connectivity-context"]["connectivity-service"]
    assert [s["uuid"] for s in listed] == ["onos-uuid-1"]


# --- Runtime modulation switch ---------------------------------------------


@pytest.mark.parametrize("fmt", ["DP-QPSK", "DP-16QAM", "DP-64QAM"])
def test_modulation_switch_changes_what_the_twin_is_asked_for(client, twin, fmt):
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "u"}}
    client.post("/adapter/modulation", json={"modulation-format": fmt})
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    sent = twin.posted[-1]["tapi-connectivity:connectivity-service"]
    assert tapi_adapter._modulation_of(sent) == fmt


def test_unknown_modulation_is_rejected(client):
    r = client.post("/adapter/modulation", json={"modulation-format": "DP-256QAM"})
    assert r.status_code == 400


def test_spectrum_is_read_from_the_end_point_augment(client, twin):
    """The adapter reads assigned spectrum where T-API 2.6 puts it.

    It only logs this, but a reader that silently returns None would make
    every admission look spectrum-less in the demo output.
    """
    service = {
        "uuid": "u",
        "end-point": [_admitted_end_point()],
    }
    centre, width = tapi_adapter._spectrum_of(service)
    assert centre == pytest.approx(ADMITTED_CENTRE_THZ)
    assert width == pytest.approx(ADMITTED_WIDTH_GHZ)

    # A service with no allocation reports nothing rather than zeroes.
    assert tapi_adapter._spectrum_of({"end-point": [{"local-id": "1"}]}) is None
    assert tapi_adapter._spectrum_of({}) is None


def test_modulation_augment_has_the_tapi_2_6_shape(client, twin):
    """The augment the twin is sent must match tapi-photonic-media.yang.

    ONOS cannot express a modulation at all, so this is adapter policy --
    but the shape it sends has to be the standard one, or the twin refuses
    it and every ONOS flow rule fails.
    """
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "u"}}
    client.post("/adapter/modulation", json={"modulation-format": "DP-16QAM"})
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    sent = twin.posted[-1]["tapi-connectivity:connectivity-service"]
    for end_point in sent["end-point"]:
        constraint = end_point["layer-protocol-constraint"][0]
        assert constraint["layer-protocol-name"] == "PHOTONIC_MEDIA"
        spec = constraint[tapi_adapter.OTSIA_CSEP_SPEC]
        modulation = spec["otsi-config"][0]["modulation"]
        # ONF spells 16QAM as MT_DP-QAM16.
        assert modulation["standard-modulation-technique"] == "MT_DP-QAM16"


def test_list_or_object_both_reach_the_twin(client, twin):
    """The twin now accepts RFC 7951's array-of-one, so B3 is not adapter work.

    The adapter still unwraps, because it has to read the end-points to
    strip SIP index suffixes -- but it no longer does so to work around a
    twin limitation.
    """
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "u"}}
    body = onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2")
    assert isinstance(body["tapi-connectivity:connectivity-service"], list)
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=body,
    )
    assert r.status_code == 201
