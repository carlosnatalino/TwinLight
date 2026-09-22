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

SIPS = [
    {
        "uuid": TWIN_SIP_Z,
        "name": [{"value-name": "node-name", "value": "trx Atlanta"}],
        "layer-protocol-name": "PHOTONIC_MEDIA",
    },
    {
        "uuid": TWIN_SIP_A,
        "name": [{"value-name": "node-name", "value": "trx Abilene"}],
        "layer-protocol-name": "PHOTONIC_MEDIA",
    },
]

SPECTRUM = {
    "tapi-photonic-media:spectrum-context": {
        "num-slots": 768,
        "slot-width-ghz": 6.25,
        "nominal-central-frequency-thz": 193.1,
    }
}


class TwinStub:
    """Minimal stand-in for the twin, recording what the adapter sends it."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.deleted: list[str] = []
        self.create_status = 201
        self.create_body: dict = {}
        self.services: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/service-interface-point"):
            return httpx.Response(
                200, json={"tapi-common:context": {"service-interface-point": SIPS}}
            )
        if path.endswith("spectrum-context"):
            return httpx.Response(200, json=SPECTRUM)
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


def test_spectrum_window_is_in_megahertz_around_the_twins_centre(client):
    """ONOS compares against BASE_FREQUENCY = 193100000, i.e. MHz."""
    pool = context(client)[0][
        "tapi-photonic-media:media-channel-service-interface-point-spec"
    ]["mc-pool"]
    spectrum = pool["available-spectrum"][0]
    # 768 slots * 6.25 GHz = 4800 GHz centred on 193.1 THz.
    assert spectrum["lower-frequency"] == 190_700_000
    assert spectrum["upper-frequency"] == 195_500_000


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
            "frequency-slot": {"nominal-central-frequency": 190.725},
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
    assert sent["modulation-format"] == "DP-QPSK"
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
    assert sent["modulation-format"] == fmt


def test_unknown_modulation_is_rejected(client):
    r = client.post("/adapter/modulation", json={"modulation-format": "DP-256QAM"})
    assert r.status_code == 400
