"""Tests for internal spectrum-grid endpoint (Spectrum grid UI)."""

from __future__ import annotations

_BASE_CONN = "/data/tapi-connectivity:connectivity-context"


def _get_two_sips(app):
    data = app.get("/data/tapi-common:context/service-interface-point").json()
    sips = data["tapi-common:context"]["service-interface-point"]
    assert len(sips) >= 2
    return sips[0]["uuid"], sips[1]["uuid"]


def _create_payload(sip_a: str, sip_z: str) -> dict:
    return {
        "tapi-connectivity:connectivity-service": {
            "name": [{"value-name": "service-name", "value": "grid-test"}],
            "modulation-format": "DP-QPSK",
            "end-point": [
                {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": sip_a}},
                {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": sip_z}},
            ],
        }
    }


def test_spectrum_grid_returns_context_links_occupancy(app) -> None:
    resp = app.get("/internal/spectrum-grid")
    assert resp.status_code == 200
    data = resp.json()
    assert "spectrum-context" in data
    assert "links" in data
    assert "occupancy" in data
    assert "service-allocation" in data
    ctx = data["spectrum-context"]
    assert "num-slots" in ctx
    assert "slot-width-ghz" in ctx
    links = data["links"]
    occupancy = data["occupancy"]
    assert isinstance(links, list)
    assert isinstance(occupancy, list)
    assert len(occupancy) == len(links)
    for row in occupancy:
        assert isinstance(row, list)
        assert len(row) == ctx["num-slots"]
        for cell in row:
            assert cell is None or isinstance(cell, str)
    alloc = data["service-allocation"]
    assert isinstance(alloc, list)
    for entry in alloc:
        assert "service-uuid" in entry
        assert "start-slot" in entry
        assert "num-slots" in entry


def test_spectrum_grid_includes_all_connectivity_services(app) -> None:
    """service-allocation has one entry per connectivity service (all shown in UI)."""
    sip_a, sip_z = _get_two_sips(app)
    r1 = app.post(f"{_BASE_CONN}/connectivity-service", json=_create_payload(sip_a, sip_z))
    assert r1.status_code == 201
    uuid1 = r1.json()["tapi-connectivity:connectivity-service"]["uuid"]
    r2 = app.post(f"{_BASE_CONN}/connectivity-service", json=_create_payload(sip_a, sip_z))
    assert r2.status_code == 201
    uuid2 = r2.json()["tapi-connectivity:connectivity-service"]["uuid"]
    grid = app.get("/internal/spectrum-grid")
    assert grid.status_code == 200
    alloc = grid.json()["service-allocation"]
    uuids = {e["service-uuid"] for e in alloc}
    assert uuid1 in uuids
    assert uuid2 in uuids
    assert len(alloc) >= 2


def test_spectrum_grid_occupancy_includes_every_allocated_service(app) -> None:
    """Every service with num-slots > 0 appears in at least one occupancy row when
    the grid has ROADM–ROADM links.

    Spectrum grid only includes ROADM–ROADM links (excludes TRX–ROADM). In topologies
    with only TRX–ROADM links the grid may have zero rows; then we only check structure.
    """
    sip_a, sip_z = _get_two_sips(app)
    r = app.post(f"{_BASE_CONN}/connectivity-service", json=_create_payload(sip_a, sip_z))
    assert r.status_code == 201
    svc_uuid = r.json()["tapi-connectivity:connectivity-service"]["uuid"]
    grid = app.get("/internal/spectrum-grid")
    assert grid.status_code == 200
    data = grid.json()
    alloc = {e["service-uuid"]: e for e in data["service-allocation"]}
    occupancy = data["occupancy"]
    links = data["links"]
    assert svc_uuid in alloc
    entry = alloc[svc_uuid]
    if entry["num-slots"] == 0:
        return  # no path or no spectrum; nothing to check
    # If grid has no ROADM–ROADM links, we cannot require the service to appear
    if len(links) == 0:
        return
    # Service has spectrum and grid has links: it must appear in at least one row
    link_rows_with_service = sum(
        1 for row in occupancy if any(cell == svc_uuid for cell in row)
    )
    assert link_rows_with_service >= 1, (
        f"Service {svc_uuid} has num-slots={entry['num-slots']} but no occupancy row"
    )
