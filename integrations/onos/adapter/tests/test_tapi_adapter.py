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
        # The twin's RESTCONF root; None: no host-meta (a twin that predates
        # it). An empty root is advertised as the twin's own absolute URL,
        # whose path is empty, as the twin does.
        self.restconf_root: str | None = "/restconf"
        self.paths: list[str] = []
        # Tokens the twin accepts, as TWINLIGHT_API_TOKENS would set them;
        # empty: an open twin. And what each request presented, in order.
        self.tokens: set[str] = set()
        self.authorization: list[tuple[str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        presented = request.headers.get("authorization")
        self.authorization.append((path, presented))
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        # Like the twin's middleware: everything but /health is protected,
        # and checked before routing, so even an unknown path is a 401.
        if self.tokens and presented not in {f"Bearer {t}" for t in self.tokens}:
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Bearer realm="twinlight"'},
                json={"ietf-restconf:errors": {"error": [{"error-tag": "access-denied"}]}},
            )
        if path == "/.well-known/host-meta":
            if self.restconf_root is None:
                return httpx.Response(404)
            href = self.restconf_root or f"{request.url.scheme}://{request.url.netloc.decode()}"
            return httpx.Response(200, text=(
                "<?xml version='1.0' encoding='UTF-8'?>\n"
                "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n"
                f"    <Link rel='restconf' href='{href}'/>\n</XRD>\n"
            ))
        data = f"{self.restconf_root or ''}/data"
        if not path.startswith(f"{data}/tapi-common:context/"):
            return httpx.Response(404)  # the twin serves T-API only there
        if path.endswith("/service-interface-point"):
            # RFC 8040 §4.3: the list as the body's one member.
            return httpx.Response(200, json={"tapi-common:service-interface-point": self.sips})
        # Like the twin: created by POST on the container (RFC 8040 §4.4.1);
        # the list path serves GET only.
        if path.endswith("/tapi-connectivity:connectivity-context") and request.method == "POST":
            self.posted.append(json.loads(request.content))
            return httpx.Response(self.create_status, json=self.create_body)
        if path.endswith("/connectivity-service") and request.method == "POST":
            return httpx.Response(405)
        if path.endswith("/connectivity-service") and request.method == "GET":
            return httpx.Response(
                200, json={"tapi-connectivity:connectivity-service": self.services}
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
    tapi_adapter._data_root = tapi_adapter.TWIN_DATA_ROOT

    with TestClient(app) as c:
        tapi_adapter.connect_twin(
            c.app, None, transport=httpx.MockTransport(twin.handler), base_url="http://twin"
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
    the twin answers the entry as an array of one under
    tapi-common:service-interface-point (RFC 8040 §4.3, RFC 7951 §5.4)."""
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


def test_the_list_body_is_forwarded_in_its_standard_form(client, twin):
    twin.create_body = {
        "tapi-connectivity:connectivity-service": [{
            "uuid": "onos-uuid-1",
            "end-point": [_admitted_end_point()],
        }]
    }
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    assert r.status_code == 201

    entries = twin.posted[0]["tapi-connectivity:connectivity-service"]
    # RFC 7951 §5.4: an array of one, as ONOS sent it; nothing to reshape.
    assert isinstance(entries, list) and len(entries) == 1
    (sent,) = entries
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    names = {
        n["value-name"]: n["value"]
        for n in twin.posted[0]["tapi-connectivity:connectivity-service"][0]["name"]
    }
    assert names["onos-port-pair"] == "1->2"
    assert names["provisioned-by"] == "onos-odtn"
    # Both the ports (to match ONOS) and the cities (to be readable on stage).
    assert "1->2" in names["service-name"]
    assert "Abilene -> Atlanta" in names["service-name"]


def test_status_exposes_the_join_key(client, twin):
    """correlate.sh joins ONOS flows to twin services on the port pair."""
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
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
        if request.url.path == "/.well-known/host-meta":
            return twin.handler(request)  # the twin is up; its delete fails
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
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


def test_onos_reads_the_twins_connectivity_service_list(client, twin, monkeypatch):
    """The adapter reads the twin's list GET in its standard body and serves
    ONOS the container ONOS asks for."""
    monkeypatch.setattr(tapi_adapter, "EXPOSE_TWIN_SERVICES", True)
    twin.services = [{"uuid": "svc-1"}, {"uuid": "svc-2"}]
    r = client.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context")
    assert r.status_code == 200
    assert r.json() == {
        "tapi-connectivity:connectivity-context": {"connectivity-service": twin.services}
    }
    assert (
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        "/connectivity-service"
    ) in twin.paths


def test_a_list_body_without_the_list_is_a_bad_gateway(client, twin, monkeypatch):
    """The container envelope earlier twins sent from the list GET is not read
    as "no services": ONOS would take that as every service being gone."""
    monkeypatch.setattr(tapi_adapter, "EXPOSE_TWIN_SERVICES", True)
    original = twin.handler

    def old_envelope(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/connectivity-service"):
            return httpx.Response(200, json={
                "tapi-connectivity:connectivity-context": {
                    "connectivity-service": [{"uuid": "svc-1"}]
                }
            })
        return original(request)

    client.app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(old_envelope), base_url="http://twin"
    )
    r = client.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context")
    assert r.status_code == 502


# --- Runtime modulation switch ---------------------------------------------


@pytest.mark.parametrize("fmt", ["DP-QPSK", "DP-16QAM", "DP-64QAM"])
def test_modulation_switch_changes_what_the_twin_is_asked_for(client, twin, fmt):
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "u"}]}
    client.post("/adapter/modulation", json={"modulation-format": fmt})
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    (sent,) = twin.posted[-1]["tapi-connectivity:connectivity-service"]
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "u"}]}
    client.post("/adapter/modulation", json={"modulation-format": "DP-16QAM"})
    client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    (sent,) = twin.posted[-1]["tapi-connectivity:connectivity-service"]
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
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "u"}]}
    body = onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2")
    assert isinstance(body["tapi-connectivity:connectivity-service"], list)
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/",
        json=body,
    )
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Finding the twin's RESTCONF root
# ---------------------------------------------------------------------------


def _twin_paths(twin: TwinStub) -> list[str]:
    return [p for p in twin.paths if p not in ("/health", "/.well-known/host-meta")]


def test_upstream_calls_use_the_discovered_root(client, twin):
    client.get("/restconf/data/tapi-common:context")
    client.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/")
    assert twin.paths.count("/.well-known/host-meta") == 1
    assert _twin_paths(twin)
    assert all(
        p.startswith("/restconf/data/tapi-common:context/") for p in _twin_paths(twin)
    )


def test_services_are_created_on_the_twins_connectivity_context(client, twin):
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    assert r.status_code == 201, r.text
    assert len(twin.posted) == 1
    assert _twin_paths(twin)[-1] == (
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
    )


def test_a_custom_root_is_followed_on_create(twin):
    twin.restconf_root = "/api"
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    with _client_for(twin) as c:
        r = c.post(
            "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context",
            json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
        )
        assert r.status_code == 201, r.text
    assert _twin_paths(twin)[-1] == (
        "/api/data/tapi-common:context/tapi-connectivity:connectivity-context"
    )


def test_a_custom_root_is_followed(twin):
    twin.restconf_root = "/api"
    with _client_for(twin) as c:
        assert c.get("/restconf/data/tapi-common:context").status_code == 200
    assert all(p.startswith("/api/data/tapi-common:context/") for p in _twin_paths(twin))


def test_a_custom_root_serves_the_service_list(twin, monkeypatch):
    monkeypatch.setattr(tapi_adapter, "EXPOSE_TWIN_SERVICES", True)
    twin.restconf_root = "/api"
    twin.services = [{"uuid": "svc-1"}]
    with _client_for(twin) as c:
        r = c.get("/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context")
    assert r.status_code == 200
    assert r.json()["tapi-connectivity:connectivity-context"]["connectivity-service"] == [
        {"uuid": "svc-1"}
    ]
    assert (
        "/api/data/tapi-common:context/tapi-connectivity:connectivity-context"
        "/connectivity-service"
    ) in twin.paths


def test_a_twin_without_host_meta_is_served_at_data(twin):
    twin.restconf_root = None
    with _client_for(twin) as c:
        assert c.get("/restconf/data/tapi-common:context").status_code == 200
    assert all(p.startswith("/data/tapi-common:context/") for p in _twin_paths(twin))


@pytest.mark.parametrize("token", [None, "adapter-token"])
def test_an_empty_root_is_discovered_from_host_meta(twin, token):
    twin.restconf_root = ""
    if token:
        twin.tokens = {token}
    with _client_for(twin, token=token) as c:
        assert c.get("/restconf/data/tapi-common:context").status_code == 200
    assert twin.paths.count("/.well-known/host-meta") == 1
    assert _twin_paths(twin)
    assert all(p.startswith("/data/tapi-common:context/") for p in _twin_paths(twin))


def test_twin_data_root_pins_the_root(twin, monkeypatch):
    twin.restconf_root = "/api"
    monkeypatch.setattr(tapi_adapter, "TWIN_DATA_ROOT", "/api/data")
    with _client_for(twin) as c:
        assert c.get("/restconf/data/tapi-common:context").status_code == 200
    assert "/.well-known/host-meta" not in twin.paths


def _client_for(twin: TwinStub, token: str | None = None, transport=None):
    tapi_adapter.catalogue = tapi_adapter.SipCatalogue()
    tapi_adapter.registry = tapi_adapter.OnosServiceRegistry()
    tapi_adapter._data_root = tapi_adapter.TWIN_DATA_ROOT

    class _Ctx:
        def __enter__(self):
            self.tc = TestClient(app).__enter__()
            tapi_adapter.connect_twin(
                self.tc.app,
                token,
                transport=transport or httpx.MockTransport(twin.handler),
                base_url="http://twin",
            )
            return self.tc

        def __exit__(self, *exc):
            self.tc.__exit__(*exc)

    return _Ctx()


# ---------------------------------------------------------------------------
# The adapter's own token for a twin that requires one
# ---------------------------------------------------------------------------
#
# ONOS knows nothing about it: it talks to the adapter without credentials,
# and the adapter presents TWINLIGHT_API_TOKEN to the twin on every call.

TOKEN = "onos-adapter-token-0123456789abcdef"
WRONG = "not-the-twins-token-0123456789abcd"
CONTEXT = "/restconf/data/tapi-common:context"
CONNECTIVITY = f"{CONTEXT}/tapi-connectivity:connectivity-context/"


def _drive_every_endpoint(c) -> list[httpx.Response]:
    """One call to each adapter endpoint that reaches the twin, as ONOS
    (and an operator) would make them; returns every response."""
    responses = [
        c.get("/health"),
        c.get(CONTEXT),
        c.get(f"{CONTEXT}/service-interface-point={TWIN_SIP_A}-1"),
        c.get(CONNECTIVITY),
        c.post(CONNECTIVITY, json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2")),
        c.delete(f"{CONNECTIVITY}connectivity-service=onos-uuid-1"),
        c.get("/adapter/status"),
        c.get("/adapter/ports"),
    ]
    return responses


def test_the_token_is_read_from_the_client_variable(monkeypatch):
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", f"  {TOKEN}\n")
    assert tapi_adapter._load_token() == TOKEN
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", "   ")
    assert tapi_adapter._load_token() is None
    monkeypatch.delenv("TWINLIGHT_API_TOKEN")
    assert tapi_adapter._load_token() is None


def test_start_up_loads_the_token_into_the_twin_client(monkeypatch):
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", TOKEN)
    with TestClient(app) as c:
        assert c.app.state.client.headers["authorization"] == f"Bearer {TOKEN}"
        assert c.app.state.twin_auth.token_configured
    monkeypatch.delenv("TWINLIGHT_API_TOKEN")
    with TestClient(app) as c:
        assert "authorization" not in c.app.state.client.headers
        assert not c.app.state.twin_auth.token_configured


def test_every_upstream_request_carries_the_token(twin):
    twin.tokens = {TOKEN}
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    with _client_for(twin, token=TOKEN) as c:
        responses = _drive_every_endpoint(c)
    assert [r.status_code for r in responses] == [200, 200, 200, 200, 201, 204, 200, 200]
    paths = {path for path, _ in twin.authorization}
    assert "/.well-known/host-meta" in paths  # root discovery included
    assert any(p.endswith("/connectivity-service") for p in paths)
    assert any("connectivity-service=" in p for p in paths)
    assert {presented for _, presented in twin.authorization} == {f"Bearer {TOKEN}"}


def test_without_a_token_no_authorization_header_is_sent(twin):
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    with _client_for(twin) as c:
        responses = _drive_every_endpoint(c)
        health = c.get("/health").json()
    assert all(r.status_code < 300 for r in responses)
    assert twin.authorization
    assert {presented for _, presented in twin.authorization} == {None}
    assert health["status"] == "ok"
    assert health["twin-authorized"] is True
    assert health["token-configured"] is False


def test_what_onos_sends_is_not_forwarded(twin):
    """The adapter's credential is its own: an Authorization header on an
    ONOS request neither reaches the twin nor replaces the adapter's."""
    twin.tokens = {TOKEN}
    with _client_for(twin, token=TOKEN) as c:
        r = c.get(CONTEXT, headers={"Authorization": "Basic b25vczpyb2Nrcw=="})
    assert r.status_code == 200
    assert {presented for _, presented in twin.authorization} == {f"Bearer {TOKEN}"}


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (None, "the twin requires an API token (HTTP 401) and the adapter has none"),
        (WRONG, "the twin rejected the adapter's token (HTTP 401)"),
    ],
)
def test_a_refused_adapter_is_reported_not_mistaken_for_a_dead_twin(
    twin, caplog, token, reason
):
    twin.tokens = {TOKEN}
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, token=token) as c:
        health = c.get("/health").json()
        status_body = c.get("/adapter/status")
        polls = [c.get(CONTEXT) for _ in range(3)]
        sip = c.get(f"{CONTEXT}/service-interface-point={TWIN_SIP_A}-1")
        listing = c.get(CONNECTIVITY)
        create = c.post(
            CONNECTIVITY, json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2")
        )
        delete = c.delete(f"{CONNECTIVITY}connectivity-service=onos-uuid-1")
        ports = c.get("/adapter/ports")

    # /health: the twin is up, and it is the token that is wrong.
    assert health["status"] == "unauthorized"
    assert health["twin-reachable"] is True
    assert health["twin-authorized"] is False
    assert health["twin-auth-error"].startswith(reason)
    assert health["token-configured"] is (token is not None)

    # /adapter/status still answers, with the reason next to the adapter's state.
    assert status_body.status_code == 200
    assert status_body.json()["twin-authorized"] is False
    assert status_body.json()["twin-auth-error"].startswith(reason)

    # ONOS gets a 502 naming the cause -- never the twin's 401, which would
    # ask it for credentials it does not have and does not need.
    for r in [*polls, sip, listing, create, delete, ports]:
        assert r.status_code == 502, (r.request.url, r.status_code)
        assert r.json()["detail"].startswith(reason)
        assert "www-authenticate" not in r.headers

    # Logged once, at ERROR, however many times ONOS polled.
    refusals = [rec for rec in caplog.records if reason in rec.getMessage()]
    assert len(refusals) == 1
    assert refusals[0].levelname == "ERROR"
    # And not misreported as the twin being unreachable.
    assert not [rec for rec in caplog.records if "unreachable" in rec.getMessage()]


def test_a_twin_that_starts_accepting_the_token_clears_the_report(twin, caplog):
    twin.tokens = {"some-other-token-0123456789abcdef"}
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, token=TOKEN) as c:
        assert c.get("/health").json()["status"] == "unauthorized"
        twin.tokens.add(TOKEN)  # e.g. the twin restarted with an onos: entry
        health = c.get("/health").json()
        assert c.get(CONTEXT).status_code == 200
    assert health["status"] == "ok"
    assert health["twin-authorized"] is True
    assert health["twin-auth-error"] is None
    assert any("accepts the adapter's requests again" in r.getMessage() for r in caplog.records)


def test_a_dead_twin_is_still_degraded_not_unauthorized(twin):
    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    with _client_for(twin, token=TOKEN, transport=httpx.MockTransport(down)) as c:
        health = c.get("/health").json()
    assert health["status"] == "degraded"
    assert health["twin-reachable"] is False
    assert health["twin-authorized"] is None


@pytest.mark.parametrize("accepted", [True, False])
def test_the_token_never_leaves_the_adapter(twin, caplog, accepted):
    """Not in a response body or header, not in a log record -- whether the
    twin accepts it or not. DEBUG, so httpx's own records are included."""
    twin.tokens = {TOKEN if accepted else "the-twins-real-token-0123456789"}
    twin.create_body = {"tapi-connectivity:connectivity-service": [{"uuid": "onos-uuid-1"}]}
    caplog.set_level("DEBUG")
    with _client_for(twin, token=TOKEN) as c:
        responses = _drive_every_endpoint(c)
        responses.append(c.post("/adapter/modulation", json={"modulation-format": "DP-QPSK"}))
        responses.append(c.get("/health"))
        responses.append(c.get("/openapi.json"))
    assert any(TOKEN in (p or "") for _, p in twin.authorization)  # it was sent
    for r in responses:
        assert TOKEN not in r.text, r.request.url
        assert TOKEN not in str(dict(r.headers)), r.request.url
    assert caplog.records
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        assert TOKEN not in str(record.args)
    assert TOKEN not in caplog.text


def test_against_a_real_twin_that_requires_tokens(tmp_path, monkeypatch):
    """The adapter in front of an in-process TwinLight app with tokens on:
    discovery, the ONOS-shaped create and delete with the right token; the
    legible refusal with a wrong one."""
    pytest.importorskip("twinlight")
    from twinlight.app import create_app
    from twinlight.auth import parse_tokens
    from twinlight.config import GnpyConfig, TwinConfig

    monkeypatch.delenv("TWINLIGHT_API_TOKENS", raising=False)
    monkeypatch.setattr("twinlight.auth.DEFAULT_ENV_FILE", tmp_path / "absent.env")
    repo = Path(__file__).resolve().parents[4]
    equipment = tmp_path / "equipment.json"
    equipment.write_text("{}")
    config = TwinConfig(
        gnpy=GnpyConfig(
            topology=repo / "tests" / "fixtures" / "edfa_example_network.json",
            equipment=equipment,
        ),
        simulation={"snapshot_dir": str(tmp_path / "snapshots")},
        api_tokens=parse_tokens(f"onos:{TOKEN},cli:{WRONG[::-1]}"),
    )
    twin_app = create_app(config)
    stub = TwinStub()  # unused; _client_for wants one

    with _client_for(stub, token=TOKEN, transport=httpx.ASGITransport(app=twin_app)) as c:
        assert c.get("/health").json()["status"] == "ok"
        sips = c.get(CONTEXT).json()["tapi-common:context"]["service-interface-point"]
        assert len(sips) >= 2
        created = c.post(
            CONNECTIVITY,
            json=onos_connectivity_request(sips[0]["uuid"], sips[1]["uuid"], "onos-real-1"),
        )
        assert created.status_code == 201, created.text
        assert "onos-real-1" in c.get("/adapter/status").json()["onos-created-services"]
        assert c.delete(f"{CONNECTIVITY}connectivity-service=onos-real-1").status_code == 204

    # The twin, asked directly with the token, no longer has it.
    twin_client = TestClient(twin_app, headers={"Authorization": f"Bearer {TOKEN}"})
    listed = twin_client.get(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
        "/connectivity-service"
    ).json()
    # The list GET's standard body (RFC 8040 §4.3). Read leniently, as the
    # container's envelope with a default, it would find "no services"
    # whatever the twin held.
    uuids = [s["uuid"] for s in listed["tapi-connectivity:connectivity-service"]]
    assert "onos-real-1" not in uuids

    with _client_for(stub, token=WRONG, transport=httpx.ASGITransport(app=twin_app)) as c:
        health = c.get("/health").json()
        refused = c.get(CONTEXT)
    assert health["status"] == "unauthorized"
    assert health["twin-auth-error"].startswith("the twin rejected the adapter's token (HTTP 401)")
    assert refused.status_code == 502


# --- The twin's standard bodies are read strictly -------------------------


def test_a_sip_list_without_its_member_is_502_not_zero_ports(client, twin, monkeypatch):
    """The twin's SIP list answers {"tapi-common:service-interface-point": [...]}
    (RFC 8040 §4.3). Any other body -- the parent's envelope earlier releases
    sent, say -- must not read as "no SIPs", which ONOS would turn into a
    device with no ports."""
    original = twin.handler

    def envelope(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service-interface-point"):
            return httpx.Response(
                200, json={"tapi-common:context": {"service-interface-point": twin.sips}}
            )
        return original(request)

    monkeypatch.setattr(twin, "handler", envelope)
    tapi_adapter.connect_twin(
        client.app, None, transport=httpx.MockTransport(envelope), base_url="http://twin"
    )
    r = client.get("/restconf/data/tapi-common:context")
    assert r.status_code == 502
    assert "tapi-common:service-interface-point" in r.json()["detail"]
    assert len(tapi_adapter.catalogue) == 0


def test_a_201_without_an_entry_is_logged_not_read_as_no_spectrum(client, twin, caplog):
    twin.create_body = {"tapi-connectivity:connectivity-service": {"uuid": "onos-uuid-1"}}
    r = client.post(
        "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context",
        json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"),
    )
    assert r.status_code == 201  # the twin did admit it
    assert "carries no tapi-connectivity:connectivity-service entry" in caplog.text
    assert "no spectrum reported" not in caplog.text


# --- A twin that is down, or answers unreadably ----------------------------


class _Switch:
    """A twin that can be taken down and brought back between requests."""

    def __init__(self, twin: TwinStub) -> None:
        self.twin, self.up = twin, True

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        return self.twin.handler(request)


def test_a_create_with_the_twin_down_is_502_not_500(twin):
    """The SIP catalogue is built on ONOS's first POST when nothing else has
    built it; with the twin down that must be the gateway's 502."""
    switch = _Switch(twin)
    switch.up = False
    with _client_for(twin, transport=httpx.MockTransport(switch.handler)) as c:
        r = c.post(CONNECTIVITY, json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"))
    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]


def test_an_outage_is_logged_once_and_its_end_once(twin, caplog):
    switch = _Switch(twin)
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, transport=httpx.MockTransport(switch.handler)) as c:
        assert c.get(CONTEXT).status_code == 200
        switch.up = False
        polls = [c.get(CONTEXT) for _ in range(5)]
        switch.up = True
        after = [c.get(CONTEXT) for _ in range(3)]
    assert [r.status_code for r in polls] == [502] * 5
    assert [r.status_code for r in after] == [200] * 3
    messages = [r.getMessage() for r in caplog.records]
    assert sum("twin unreachable" in m for m in messages) == 1
    assert sum("the twin answers again" in m for m in messages) == 1
    assert [r.levelname for r in caplog.records if "twin unreachable" in r.getMessage()] == ["ERROR"]


def test_an_unreadable_sip_list_is_logged_once(twin, caplog, monkeypatch):
    caplog.set_level("INFO", logger="tapi-adapter")
    original = twin.handler

    def envelope(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/service-interface-point"):
            return httpx.Response(200, json={"tapi-common:context": {}})
        return original(request)

    with _client_for(twin, transport=httpx.MockTransport(envelope)) as c:
        polls = [c.get(CONTEXT) for _ in range(4)]
    assert [r.status_code for r in polls] == [502] * 4
    messages = [r.getMessage() for r in caplog.records]
    assert sum("cannot read" in m for m in messages) == 1
    assert not any("twin unreachable" in m for m in messages)


def test_adapter_status_answers_200_whatever_the_twin_does(twin):
    """Down, or refusing the token: the same 200, with the reason in the body."""
    switch = _Switch(twin)
    switch.up = False
    with _client_for(twin, transport=httpx.MockTransport(switch.handler)) as c:
        down = c.get("/adapter/status")
        switch.up = True
        up = c.get("/adapter/status")
    assert down.status_code == 200
    assert down.json()["twin-reachable"] is False
    assert "connection refused" in down.json()["twin-error"]
    assert up.status_code == 200
    assert up.json()["twin-reachable"] is True and up.json()["twin-error"] is None
    twin.tokens = {TOKEN}
    with _client_for(twin) as c:  # no token: the twin refuses the adapter
        refused = c.get("/adapter/status")
    assert refused.status_code == 200
    assert refused.json()["twin-authorized"] is False
    assert refused.json()["twin-reachable"] is True


# --- An outage ends only when the twin answers usably ----------------------


class _Faulty:
    """A twin whose T-API resources can answer an error status, refuse the
    connection, or (``mode=None``) answer normally; /health stays up."""

    def __init__(self, twin: TwinStub) -> None:
        self.twin, self.mode = twin, None

    def handler(self, request: httpx.Request) -> httpx.Response:
        mode = self.mode
        if mode is not None and "tapi-common:context" in request.url.path:
            if mode == "refused" or (mode == "alternate" and self.flip()):
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(503, json={})
        return self.twin.handler(request)

    _n = 0

    def flip(self) -> bool:
        self._n += 1
        return self._n % 2 == 1


def _outage_lines(caplog) -> tuple[list[str], list[str]]:
    records = [r for r in caplog.records if r.name == "tapi-adapter"]
    down = [r.levelname for r in records if "twin unreachable" in r.getMessage()]
    back = [r.levelname for r in records if "the twin answers again" in r.getMessage()]
    return down, back


def test_an_error_status_on_every_poll_is_one_outage(twin, caplog):
    """A 503 (a proxy in front of the twin) or a 404 (a twin of another
    version) on every ONOS poll: one ERROR when it starts, one INFO when the
    twin answers usably again -- not two lines per poll."""
    faulty = _Faulty(twin)
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, transport=httpx.MockTransport(faulty.handler)) as c:
        assert c.get(CONTEXT).status_code == 200
        faulty.mode = "503"
        polls = [c.get(CONTEXT).status_code for _ in range(5)]
        status_during, health_during = c.get("/adapter/status").json(), c.get("/health").json()
        polls.append(c.get(CONTEXT).status_code)
        assert _outage_lines(caplog) == (["ERROR"], [])
        faulty.mode = None
        after = [c.get(CONTEXT).status_code for _ in range(3)]
        status_after, health_after = c.get("/adapter/status").json(), c.get("/health").json()
    assert polls == [502] * 6 and after == [200] * 3
    assert _outage_lines(caplog) == (["ERROR"], ["INFO"])
    for during in (status_during, health_during):
        assert during["twin-reachable"] is False
        assert "HTTP 503" in during["twin-error"]
    assert health_during["status"] == "degraded"
    for done in (status_after, health_after):
        assert done["twin-reachable"] is True and done["twin-error"] is None
    assert health_after["status"] == "ok"


def test_refused_and_503_alternating_is_still_one_outage(twin, caplog):
    faulty = _Faulty(twin)
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, transport=httpx.MockTransport(faulty.handler)) as c:
        assert c.get(CONTEXT).status_code == 200
        faulty.mode = "alternate"
        polls = [c.get(CONTEXT).status_code for _ in range(6)]
        assert _outage_lines(caplog) == (["ERROR"], [])
        faulty.mode = None
        assert c.get(CONTEXT).status_code == 200
    assert polls == [502] * 6
    assert _outage_lines(caplog) == (["ERROR"], ["INFO"])


def test_a_health_probe_that_works_does_not_end_an_outage(twin, caplog):
    """/health is public and can answer while the T-API resources do not
    (a 503 in front of /restconf only): it must not end the outage, or every
    healthcheck between two polls would log it ending and starting again."""
    faulty = _Faulty(twin)
    caplog.set_level("INFO", logger="tapi-adapter")
    with _client_for(twin, transport=httpx.MockTransport(faulty.handler)) as c:
        assert c.get(CONTEXT).status_code == 200
        faulty.mode = "503"
        for _ in range(3):
            assert c.get(CONTEXT).status_code == 502
            assert c.get("/health").json()["twin-reachable"] is False
    assert _outage_lines(caplog) == (["ERROR"], [])


def test_a_refusal_or_a_missing_service_is_no_outage(twin, caplog):
    """A 409 (admission refused) and a 404 on DELETE (already gone) are the
    twin answering usably: they neither start an outage nor fail to end one."""
    caplog.set_level("INFO", logger="tapi-adapter")
    twin.create_status = 409
    twin.create_body = {"ietf-restconf:errors": {"error": [
        {"error-tag": "resource-denied", "error-message": "no spectrum"}]}}
    original = twin.handler

    def gone(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(404, json={})
        return original(request)

    with _client_for(twin, transport=httpx.MockTransport(gone)) as c:
        create = c.post(CONNECTIVITY, json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"))
        delete = c.delete(f"{CONNECTIVITY}connectivity-service=onos-uuid-1")
        status_body = c.get("/adapter/status").json()
    assert (create.status_code, delete.status_code) == (409, 204)
    assert _outage_lines(caplog) == ([], [])
    assert status_body["twin-reachable"] is True and status_body["twin-error"] is None


def test_a_409_ends_an_outage_the_twin_has_recovered_from(twin, caplog):
    faulty = _Faulty(twin)
    caplog.set_level("INFO", logger="tapi-adapter")
    twin.create_status = 409
    twin.create_body = {"ietf-restconf:errors": {"error": [{"error-message": "no spectrum"}]}}
    with _client_for(twin, transport=httpx.MockTransport(faulty.handler)) as c:
        assert c.get(CONTEXT).status_code == 200  # the catalogue is built
        faulty.mode = "refused"
        assert c.get(CONTEXT).status_code == 502
        faulty.mode = None
        create = c.post(CONNECTIVITY, json=onos_connectivity_request(TWIN_SIP_A + "-1", TWIN_SIP_Z + "-2"))
    assert create.status_code == 409
    assert _outage_lines(caplog) == (["ERROR"], ["INFO"])


def test_a_refused_token_after_an_outage_is_not_reported_unreachable(twin):
    """The twin answering 401 is reachable; the refusal is TwinAuthorization's
    to report, so /adapter/status must not say both "unreachable" and
    "unauthorized"."""
    faulty = _Faulty(twin)
    with _client_for(twin, transport=httpx.MockTransport(faulty.handler)) as c:
        assert c.get(CONTEXT).status_code == 200
        faulty.mode = "refused"
        assert c.get(CONTEXT).status_code == 502
        faulty.mode = None
        twin.tokens = {TOKEN}  # the twin comes back requiring a token the adapter lacks
        assert c.get(CONTEXT).status_code == 502
        status_body = c.get("/adapter/status").json()
        health = c.get("/health").json()
    assert status_body["twin-reachable"] is True and status_body["twin-error"] is None
    assert status_body["twin-authorized"] is False
    assert health["status"] == "unauthorized" and health["twin-reachable"] is True
