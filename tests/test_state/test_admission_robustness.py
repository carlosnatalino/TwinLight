"""Admission never guesses, and a refused change leaves the network as it was.

* A propagation that fails gives no QoT verdict, so that (route, format) is
  skipped with the error named, never admitted unchecked.
* Admissions are serialised: two concurrent requests never get one block.
* A refused PUT leaves the old service on its route and block, even when a
  failed fiber means re-admitting it would move it, or find no route.
* A snapshot restore leaves no trace of the services it replaced.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.models.connectivity import OTSIA_CSEP_SPEC, ConnectivityService
from twinlight.physics.backend import PropagationError
from twinlight.state.context import InsufficientQoTError, TapiContext

_CONTAINER = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
_SVC = f"{_CONTAINER}/connectivity-service"


def _service(ctx: TapiContext, a: str = "trx A", z: str = "trx C", fmt: str | None = None):
    refs = MeshRefs(ctx)
    end_points = [tapi_end_point("a", refs.sip(a)), tapi_end_point("z", refs.sip(z))]
    if fmt is None:
        for ep in end_points:
            del ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0]["modulation"]
    return ConnectivityService.model_validate({"end-point": end_points})


class TestNoQotNoAdmission:
    def test_a_failing_route_is_skipped_with_the_error(
        self, mesh_context: TapiContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Propagation fails on every route through ROADM B: admission takes
        the next candidate, with a format its real QoT supports."""
        ctx = mesh_context
        real = ctx._backend.compute_baseline

        async def flaky(path, fmt, **channel):
            if "roadm B" in path:
                raise PropagationError("element rejected")
            return await real(path, fmt, **channel)

        monkeypatch.setattr(ctx._backend, "compute_baseline", flaky)
        svc = _service(ctx)
        asyncio.run(ctx.add_service(svc))
        assert "roadm B" not in ctx._allocated_path_uids(svc.uuid)
        assert ctx._baseline_cache[svc.uuid].gsnr_db >= 0

    @pytest.mark.parametrize("error", [PropagationError("element rejected"),
                                       RuntimeError("numerical failure")])
    def test_no_verdict_anywhere_is_a_refusal_naming_it(
        self, mesh_context: TapiContext, monkeypatch: pytest.MonkeyPatch, error: Exception,
    ) -> None:
        async def broken(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(mesh_context._backend, "compute_baseline", broken)
        with pytest.raises(InsufficientQoTError, match="QoT could not be evaluated") as exc:
            asyncio.run(mesh_context.add_service(_service(mesh_context)))
        assert str(error) in str(exc.value)
        assert mesh_context.get_services() == []

    def test_a_backend_with_no_model_of_the_route_is_a_refusal(
        self, mesh_context: TapiContext, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def nothing(*_args, **_kwargs):
            return None

        monkeypatch.setattr(mesh_context._backend, "compute_baseline", nothing)
        with pytest.raises(InsufficientQoTError, match="could not propagate this route"):
            asyncio.run(mesh_context.add_service(_service(mesh_context, fmt="DP-QPSK")))


def test_concurrent_admissions_get_distinct_blocks(mesh_context: TapiContext) -> None:
    ctx = mesh_context

    async def both() -> None:
        await asyncio.gather(*(ctx.add_service(_service(ctx, fmt="DP-QPSK")) for _ in range(3)))

    asyncio.run(both())
    starts = [alloc[1] for alloc in ctx._service_allocation.values()]
    assert len(set(starts)) == 3


class TestRefusedPutKeepsTheService:
    @staticmethod
    def _client(tmp_path: Path, backend: str) -> TestClient:
        return TestClient(create_app(_mesh_config(tmp_path, backend)))

    @staticmethod
    def _create(client: TestClient) -> dict:
        refs = MeshRefs(client.app.state.context)
        resp = client.post(_CONTAINER, json={"tapi-connectivity:connectivity-service": [{
            "end-point": [tapi_end_point("a", refs.sip("trx A")),
                          tapi_end_point("z", refs.sip("trx C"))],
        }]})
        assert resp.status_code == 201, resp.text
        return resp.json()["tapi-connectivity:connectivity-service"][0]

    @staticmethod
    def _bad_spectrum(service: dict) -> dict:
        """The same service asking for an off-grid central frequency: 400."""
        body = dict(service)
        for ep in body["end-point"]:
            ep["layer-protocol-constraint"][0][OTSIA_CSEP_SPEC]["otsi-config"][0][
                "central-frequency"] = 193_100_000_000_001
        return {"tapi-connectivity:connectivity-service": body}

    @pytest.mark.parametrize("backend", ["gnpy", "egn"])
    def test_with_a_failed_fiber_it_is_not_rerouted(self, tmp_path, backend) -> None:
        client = self._client(tmp_path, backend)
        ctx = client.app.state.context
        service = self._create(client)
        uuid = service["uuid"]
        before = ctx._service_allocation[uuid], ctx.get_service(uuid).modulation_format
        assert "roadm B" in ctx._allocated_path_uids(uuid)
        ctx.set_link_failed("fiber (B → C)", True)

        resp = client.put(f"{_SVC}={uuid}", json=self._bad_spectrum(service))
        assert resp.status_code == 400, resp.text
        assert resp.json()["ietf-restconf:errors"]["error"][0]["error-tag"] == "invalid-value"
        assert (ctx._service_allocation[uuid], ctx.get_service(uuid).modulation_format) == before
        assert "roadm B" in ctx._allocated_path_uids(uuid)

    def test_with_no_route_left_it_is_not_lost(self, tmp_path) -> None:
        """Every fiber out of ROADM A failed: re-admission would find no
        route. The refused PUT still leaves the service in place."""
        client = self._client(tmp_path, "egn")
        ctx = client.app.state.context
        service = self._create(client)
        uuid = service["uuid"]
        before = ctx._service_allocation[uuid]
        for fiber in ("fiber (A → B)", "fiber (A → D)", "fiber (A → E)"):
            ctx.set_link_failed(fiber, True)
        body = {"tapi-connectivity:connectivity-service": {**service, "end-point": [
            tapi_end_point("a", MeshRefs(ctx).sip("trx A"), "DP-64QAM"),
            tapi_end_point("z", MeshRefs(ctx).sip("trx C"), "DP-64QAM"),
        ]}}
        resp = client.put(f"{_SVC}={uuid}", json=body)
        assert resp.status_code == 409, resp.text
        assert ctx._service_allocation[uuid] == before
        assert client.get(f"{_SVC}={uuid}").status_code == 200

    def test_a_successful_put_frees_the_old_block(self, tmp_path) -> None:
        client = self._client(tmp_path, "egn")
        ctx = client.app.state.context
        service = self._create(client)
        uuid = service["uuid"]
        refs = MeshRefs(ctx)
        body = {"tapi-connectivity:connectivity-service": {**service, "end-point": [
            tapi_end_point("a", refs.sip("trx A"), "DP-QPSK"),
            tapi_end_point("z", refs.sip("trx B"), "DP-QPSK"),
        ]}}
        assert client.put(f"{_SVC}={uuid}", json=body).status_code == 204
        assert "roadm C" not in ctx._allocated_path_uids(uuid)
        assert client.delete(f"{_SVC}={uuid}").status_code == 204
        # Neither the old block nor the new one is left behind.
        slots = ctx._spectrum_state.to_dict()["slots"]
        assert not any(any(occupied) for occupied in slots.values())


def test_creating_after_a_restore_under_egn(tmp_path: Path) -> None:
    """The restore dropped the replaced services' fiber records: admitting
    next to a restored service is a 201, not a KeyError."""
    client = TestClient(create_app(_mesh_config(tmp_path, "egn")))
    ctx = client.app.state.context
    refs = MeshRefs(ctx)
    body = {"tapi-connectivity:connectivity-service": [{"end-point": [
        tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]}]}
    assert client.post(_CONTAINER, json=body).status_code == 201
    snapshot = ctx.snapshot(tmp_path / "s.json")
    assert client.post(_CONTAINER, json=body).status_code == 201
    ctx.restore_from(snapshot)
    resp = client.post(_CONTAINER, json=body)
    assert resp.status_code == 201, resp.text
    assert len(ctx.get_services()) == 2
