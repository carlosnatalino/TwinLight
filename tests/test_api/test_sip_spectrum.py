"""The tapi-photonic-media spectrum augment on a service-interface-point.

T-API v2.6.0 augments the SIP with
``photonic-media-service-interface-point-spec`` → ``spectrum-capability-pac``,
holding supportable / available / occupied spectrum-bands. Frequencies are
uint64 **Hz** per ``spectrum-band`` in tapi-photonic-media.yang.

Note this is *not* the T-API 2.1 ``mc-pool`` that ONOS reads — that shape
does not exist in 2.6, and translating to it is the ONOS adapter's job.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.api.common import PHOTONIC_SIP_SPEC

_BASE = "/data/tapi-connectivity:connectivity-context"
_SIPS = "/data/tapi-common:context/service-interface-point"

# The default grid: 768 slots of 6.25 GHz, slot 384 centred on 193.1 THz.
# Slot index denotes a slot *centre* (see TapiContext._slot_range_hz), so the
# band edges sit half a slot outside the first and last centres: the span is
# 4800 GHz, but it is not symmetric about 193.1 THz.
_BAND_LOWER_HZ = 190_696_875_000_000
_BAND_UPPER_HZ = 195_496_875_000_000


def _sip_uuids(app: TestClient) -> tuple[str, str]:
    sips = app.get(_SIPS).json()["tapi-common:context"][
        "service-interface-point"
    ]
    return sips[0]["uuid"], sips[1]["uuid"]


def _pac(app: TestClient, uuid: str) -> dict:
    resp = app.get(f"{_SIPS}={uuid}")
    assert resp.status_code == 200
    sip = resp.json()["tapi-common:context"]["service-interface-point"][0]
    return sip[PHOTONIC_SIP_SPEC]["spectrum-capability-pac"]


def _width(band: dict) -> int:
    return band["upper-frequency"] - band["lower-frequency"]


class TestSupportableSpectrum:
    def test_spans_the_configured_grid_in_hz(self, app: TestClient) -> None:
        sip_a, _ = _sip_uuids(app)
        supportable = _pac(app, sip_a)["supportable-spectrum"]
        assert len(supportable) == 1
        assert supportable[0]["lower-frequency"] == _BAND_LOWER_HZ
        assert supportable[0]["upper-frequency"] == _BAND_UPPER_HZ

    def test_frequency_constraint_describes_the_real_grid(
        self, app: TestClient
    ) -> None:
        """6.25 GHz slots are flexi-grid, and must be advertised as such.

        Claiming a 50 GHz DWDM grid here would be convenient for ONOS and
        wrong: a client would compute channel centres that do not line up
        with the twin's slots. The adapter overrides it for ONOS instead.
        """
        sip_a, _ = _sip_uuids(app)
        constraint = _pac(app, sip_a)["supportable-spectrum"][0][
            "frequency-constraint"
        ]
        assert constraint == {
            "grid-type": "GRID_TYPE_FLEX",
            "adjustment-granularity": "ADJUSTMENT_GRANULARITY_G_6_25GHZ",
        }


class TestOccupancyIsSipLocal:
    """Occupancy reported at a SIP is the SIP's own, not the path's.

    ``mc-pool``/``spectrum-capability-pac`` is a property of the port. A
    service occupies spectrum at the two SIPs it terminates on, and at no
    others — even though its lightpath crosses links all the way between.
    """

    def test_idle_sip_is_entirely_available(self, app: TestClient) -> None:
        sip_a, _ = _sip_uuids(app)
        pac = _pac(app, sip_a)
        assert pac["occupied-spectrum"] == []
        assert len(pac["available-spectrum"]) == 1
        assert _width(pac["available-spectrum"][0]) == (
            _BAND_UPPER_HZ - _BAND_LOWER_HZ
        )

    def test_service_occupies_exactly_its_block(self, app: TestClient) -> None:
        sip_a, sip_z = _sip_uuids(app)
        resp = app.post(
            f"{_BASE}/connectivity-service",
            json={
                "tapi-connectivity:connectivity-service": {
                    "end-point": [
                        tapi_end_point("a", sip_a),
                        tapi_end_point("z", sip_z),
                    ],
                }
            },
        )
        assert resp.status_code == 201
        slot = resp.json()["tapi-connectivity:connectivity-service"][
            "frequency-slot"
        ]

        pac = _pac(app, sip_a)
        assert len(pac["occupied-spectrum"]) == 1
        occupied = pac["occupied-spectrum"][0]
        # The occupied band must agree with the service's own frequency-slot.
        assert _width(occupied) == round(slot["slot-width"] * 1e9)
        centre = (occupied["lower-frequency"] + occupied["upper-frequency"]) / 2
        assert centre == round(slot["nominal-central-frequency"] * 1e12)

        # Available is the rest of the band, and the two partition it.
        total_available = sum(_width(b) for b in pac["available-spectrum"])
        assert total_available + _width(occupied) == (
            _BAND_UPPER_HZ - _BAND_LOWER_HZ
        )

    def test_other_sips_stay_free(self, app: TestClient) -> None:
        sips = app.get(_SIPS).json()["tapi-common:context"][
            "service-interface-point"
        ]
        sip_a, sip_z = sips[0]["uuid"], sips[1]["uuid"]
        app.post(
            f"{_BASE}/connectivity-service",
            json={
                "tapi-connectivity:connectivity-service": {
                    "end-point": [
                        tapi_end_point("a", sip_a),
                        tapi_end_point("z", sip_z),
                    ],
                }
            },
        )
        # Both endpoints see the block; a third SIP, if there is one, does not.
        assert _pac(app, sip_a)["occupied-spectrum"] != []
        assert _pac(app, sip_z)["occupied-spectrum"] != []
        for other in sips[2:]:
            assert _pac(app, other["uuid"])["occupied-spectrum"] == []

    def test_delete_releases_the_band(self, app: TestClient) -> None:
        sip_a, sip_z = _sip_uuids(app)
        created = app.post(
            f"{_BASE}/connectivity-service",
            json={
                "tapi-connectivity:connectivity-service": {
                    "end-point": [
                        tapi_end_point("a", sip_a),
                        tapi_end_point("z", sip_z),
                    ],
                }
            },
        )
        uuid = created.json()["tapi-connectivity:connectivity-service"]["uuid"]
        assert app.delete(f"{_BASE}/connectivity-service={uuid}").status_code == 204
        assert _pac(app, sip_a)["occupied-spectrum"] == []


def test_augment_present_in_the_context_listing(app: TestClient) -> None:
    """All three SIP resources carry the augment, not just the single GET."""
    for path in ("/data/tapi-common:context", _SIPS):
        sips = app.get(path).json()["tapi-common:context"][
            "service-interface-point"
        ]
        assert sips
        for sip in sips:
            assert PHOTONIC_SIP_SPEC in sip
