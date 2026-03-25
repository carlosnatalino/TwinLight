"""Tests for TAPI Equipment API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_BASE = "/data/tapi-equipment:equipment-context"


class TestEquipmentContext:
    def test_get_equipment_context_returns_equipment(self, app: TestClient) -> None:
        resp = app.get(_BASE)
        assert resp.status_code == 200
        data = resp.json()
        assert "tapi-equipment:equipment-context" in data
        ctx = data["tapi-equipment:equipment-context"]
        assert "equipment" in ctx
        assert isinstance(ctx["equipment"], list)
        # At least nodes and links from topology
        assert len(ctx["equipment"]) >= 1

    def test_get_equipment_list(self, app: TestClient) -> None:
        resp = app.get(f"{_BASE}/equipment")
        assert resp.status_code == 200
        data = resp.json()
        eq_list = data["tapi-equipment:equipment-context"]["equipment"]
        assert isinstance(eq_list, list)
        for eq in eq_list[:3]:
            assert "uuid" in eq
            assert "equipment-type" in eq
            assert "name" in eq

    def test_get_equipment_by_uuid(self, app: TestClient) -> None:
        resp = app.get(f"{_BASE}/equipment")
        assert resp.status_code == 200
        eq_list = resp.json()["tapi-equipment:equipment-context"]["equipment"]
        if not eq_list:
            pytest.skip("No equipment in topology")
        uuid = eq_list[0]["uuid"]
        get_one = app.get(f"{_BASE}/equipment={uuid}")
        assert get_one.status_code == 200
        data = get_one.json()
        assert data["tapi-equipment:equipment"][0]["uuid"] == uuid

    def test_get_equipment_404_when_not_found(self, app: TestClient) -> None:
        resp = app.get(f"{_BASE}/equipment=non-existent-uid-12345")
        assert resp.status_code == 404
