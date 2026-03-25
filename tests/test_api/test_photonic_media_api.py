"""Tests for T-API photonic media (spectrum context) endpoint."""

from fastapi.testclient import TestClient


def test_spectrum_context_returns_grid_params(app: TestClient) -> None:
    """GET tapi-photonic-media:spectrum-context returns num-slots, slot-width, center freq."""
    # Path with colon; client may send %3A
    r = app.get("/data/tapi-photonic-media:spectrum-context")
    assert r.status_code == 200
    data = r.json()
    assert "tapi-photonic-media:spectrum-context" in data
    ctx = data["tapi-photonic-media:spectrum-context"]
    assert "num-slots" in ctx
    assert "slot-width-ghz" in ctx
    assert "nominal-central-frequency-thz" in ctx
    assert ctx["num-slots"] >= 1
    assert ctx["slot-width-ghz"] > 0
    assert ctx["nominal-central-frequency-thz"] > 0
