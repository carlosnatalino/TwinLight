"""Under the EGN backend, every telemetry surface reports the EGN result.

Lighting a co-propagating channel lowers the channel under test's GSNR; the
OPM endpoint (which gNMI proxies), the Prometheus exporter and the
transparent controller all report the new value on their next read, and
every derived figure (BER, Q, post-FEC BER, GMI) moves with it, because
each is computed from the same backend baseline at read time.

GNPy's propagation is made to fail for the whole test: under EGN, no figure
may come from it. (GNPy's design and equipment models are inputs EGN reads
at startup, before the guard is installed — see docs/PHYSICS.md.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.physics.ber_conversion import ber_to_q_db, gsnr_to_ber

_SVC = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"


def _no_gnpy_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every GNPy propagation entry point raise."""
    from gnpy.core import elements
    from gnpy.core.science_utils import NliSolver

    from twinlight.physics import gnpy_adapter

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GNPy propagation used under the EGN backend")

    monkeypatch.setattr(gnpy_adapter, "compute_path_baseline", forbidden)
    monkeypatch.setattr(NliSolver, "compute_nli", forbidden)
    for cls in (elements.Fiber, elements.Edfa, elements.Roadm, elements.Transceiver):
        monkeypatch.setattr(cls, "__call__", forbidden)
        if hasattr(cls, "propagate"):
            monkeypatch.setattr(cls, "propagate", forbidden)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config = _mesh_config(tmp_path, "egn")
    # Without the time-varying models every surface must report exactly
    # the backend's baseline, so the surfaces can be compared value by value.
    for model in ("edfa_reservoir", "polarization", "phase_noise", "environmental"):
        getattr(config.transients, model).enabled = False
    app = create_app(config)
    _no_gnpy_propagation(monkeypatch)
    return TestClient(app)


def _create(client: TestClient, a: str, z: str) -> str:
    refs = MeshRefs(client.app.state.context)
    resp = client.post(_SVC, json={"tapi-connectivity:connectivity-service": [{
        "end-point": [tapi_end_point("a", refs.sip(a)), tapi_end_point("z", refs.sip(z))],
    }]})
    assert resp.status_code == 201, resp.text
    return resp.json()["tapi-connectivity:connectivity-service"]["uuid"]


def _prometheus(client: TestClient, metric: str, uuid: str) -> float:
    text = client.get("/metrics").text
    match = re.search(rf'^twinlight_{metric}{{[^}}]*service_uuid="{uuid}"[^}}]*}} (\S+)$',
                      text, re.MULTILINE)
    assert match, f"{metric} for {uuid} not exported"
    return float(match.group(1))


def _telemetry(client: TestClient, uuid: str) -> dict[str, float]:
    """The GSNR and its derived figures as each surface reports them."""
    opm = client.get(f"/internal/opm/{uuid}").json()["measurements"]
    qot = client.get(f"/transparent/data/ServiceList/{uuid}/qot").json()[
        "twinlight-transparent:data"]["ServiceList"][uuid]["qot"]
    return {
        "opm_gsnr": opm["gsnr-db"],
        "opm_osnr": opm["osnr-db"],
        "opm_ber": opm["pre-fec-ber"],
        "opm_q": opm["q-factor-db"],
        "opm_gmi": opm["gmi-bits-per-symbol"],
        "prom_gsnr": _prometheus(client, "opm_gsnr_db", uuid),
        "prom_q": _prometheus(client, "opm_q_factor_db", uuid),
        "tree_gsnr": qot["gsnr_db"],
        "tree_osnr": qot["osnr_db"],
    }


def _consistent(values: dict[str, float]) -> None:
    """Every surface reports one GSNR, and the derived figures follow it."""
    gsnr = values["opm_gsnr"]
    assert values["prom_gsnr"] == pytest.approx(gsnr, abs=1e-6)
    assert values["tree_gsnr"] == pytest.approx(gsnr, abs=1e-6)
    assert values["tree_osnr"] == pytest.approx(values["opm_osnr"], abs=1e-6)
    ber = gsnr_to_ber(gsnr, "DP-QPSK")
    assert values["opm_ber"] == pytest.approx(ber, rel=1e-9)
    assert values["opm_q"] == pytest.approx(ber_to_q_db(ber), rel=1e-9)
    assert values["prom_q"] == pytest.approx(values["opm_q"], rel=1e-6)


def test_a_new_channel_shows_on_every_surface(client: TestClient) -> None:
    ctx = client.app.state.context
    assert client.get("/metrics").text.count('twinlight_info{backend="egn"} 1.0') == 1

    cut = _create(client, "trx A", "trx C")
    alone = _telemetry(client, cut)
    _consistent(alone)
    assert alone["opm_gsnr"] == pytest.approx(ctx._baseline_cache[cut].gsnr_db)

    neighbour = _create(client, "trx A", "trx C")
    loaded = _telemetry(client, cut)
    _consistent(loaded)
    assert loaded["opm_gsnr"] < alone["opm_gsnr"]
    assert loaded["opm_ber"] > alone["opm_ber"]
    assert loaded["opm_q"] < alone["opm_q"]
    assert loaded["opm_osnr"] == pytest.approx(alone["opm_osnr"])  # NLI, not ASE

    assert client.delete(f"{_SVC}={neighbour}").status_code == 204
    restored = _telemetry(client, cut)
    _consistent(restored)
    assert restored["opm_gsnr"] == pytest.approx(alone["opm_gsnr"], abs=1e-9)


def test_an_unrelated_channel_changes_nothing(client: TestClient) -> None:
    """A lightpath on the opposite fibers shares none with the channel under
    test, so its telemetry does not move."""
    cut = _create(client, "trx A", "trx C")
    before = _telemetry(client, cut)
    _create(client, "trx C", "trx A")
    assert _telemetry(client, cut)["opm_gsnr"] == pytest.approx(before["opm_gsnr"], abs=1e-12)
