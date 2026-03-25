"""Tests for internal /internal/links endpoint (ROADM–ROADM links only)."""

from __future__ import annotations


def test_internal_links_returns_roadm_to_roadm_only(app) -> None:
    """GET /internal/links returns only links whose endpoints are both ROADMs."""
    resp = app.get("/internal/links")
    assert resp.status_code == 200
    data = resp.json()
    assert "links" in data
    links = data["links"]
    assert isinstance(links, list)
    for item in links:
        assert "link-uuid" in item
        assert "label" in item
        assert "topology-uuid" in item
        assert "operational-state" in item


def test_spectrum_grid_links_are_roadm_to_roadm_subset(app) -> None:
    """Spectrum grid links are a subset of /internal/links (same ROADM–ROADM filter)."""
    links_resp = app.get("/internal/links")
    assert links_resp.status_code == 200
    roadm_link_uuids = {l["link-uuid"] for l in links_resp.json()["links"]}

    grid_resp = app.get("/internal/spectrum-grid")
    assert grid_resp.status_code == 200
    grid_links = grid_resp.json()["links"]
    for gl in grid_links:
        assert gl["link-uuid"] in roadm_link_uuids
