"""Entity-tags, Last-Modified and conditional requests on the T-API datastore.

RFC 8040 §3.4.1.2: "The server MUST maintain a unique opaque entity-tag for
the datastore resource and MUST return it in the 'ETag' header"; §3.4.1.1
and §3.5.1–§3.5.2 the timestamp and the data resources, which use the
datastore's. RFC 9110 §13 evaluates If-Match, If-None-Match,
If-Modified-Since and If-Unmodified-Since against them.

The rule (docs/API.md § Entity-tags): ``content=config`` carries the
configuration validator, which only a config-true change moves; the default
view and ``content=nonconfig`` carry the full one, which every change a
T-API body shows moves. A validator never lies: each kind of change is made
here and the tag checked. Driven by the OpenAPI schema, under three roots and
both backends; with tokens, a 401 comes first.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config, tapi_end_point
from tests.test_api.test_response_bodies import _twin
from twinlight.app import create_app
from twinlight.auth import parse_tokens

_CONTEXT = "/data/tapi-common:context"
_CONNECTIVITY = f"{_CONTEXT}/tapi-connectivity:connectivity-context"
_PATH_COMPUTATION = f"{_CONTEXT}/tapi-path-computation:path-computation-context"
_SVC = "tapi-connectivity:connectivity-service"
_TOKEN = "etag-token-0123456789abcdef"


@pytest.fixture(params=[("gnpy", "/restconf"), ("egn", "/api"), ("gnpy", "")],
                ids=["gnpy-restconf", "egn-api", "gnpy-empty-root"])
def twin(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[TestClient, str]:
    backend, root = request.param
    return _twin(tmp_path, backend, root), root


@pytest.fixture
def mesh(tmp_path: Path) -> tuple[TestClient, str]:
    return _twin(tmp_path, "gnpy", "/restconf"), "/restconf"


def _tags(client: TestClient, root: str) -> tuple[str, str]:
    """(configuration tag, full tag) of the datastore."""
    return (client.get(f"{root}/data?content=config").headers["etag"],
            client.get(f"{root}/data").headers["etag"])


def _service_urls(client: TestClient, root: str) -> list[str]:
    url = f"{root}{_CONNECTIVITY}/connectivity-service"
    return [f"{url}={s['uuid']}" for s in client.get(url).json()[_SVC]]


def _error(resp: httpx.Response) -> dict:
    return resp.json()["ietf-restconf:errors"]["error"][0]


class TestValidatorsOnEveryResource:
    def test_every_data_resource_carries_both(self, twin) -> None:
        client, root = twin
        prefix = f"{root}/data"
        full = client.get(f"{root}{_CONTEXT}").json()
        from tests.test_api.test_response_bodies import _resolve

        urls = []
        for path, ops in client.get("/openapi.json").json()["paths"].items():
            if "get" in ops and (path == prefix or path.startswith(prefix + "/")):
                urls.append(path if "{" not in path else
                            f"{prefix}/{_resolve(full, path[len(prefix) + 1:])[0]}")
        assert len(urls) >= 30
        full_tag = _tags(client, root)[1]
        for url in urls:
            get, head = client.get(url), client.head(url)
            assert get.status_code == head.status_code == 200, url
            assert get.headers["etag"] == head.headers["etag"] == full_tag, url
            assert get.headers["last-modified"] == head.headers["last-modified"], url
            assert get.headers["cache-control"] == "no-cache", url
            again = client.get(url, headers={"If-None-Match": full_tag})
            assert (again.status_code, again.content) == (304, b""), url
            assert again.headers["etag"] == full_tag
            stale = client.get(url, headers={"If-None-Match": '"stale"'})
            assert stale.status_code == 200 and stale.content == get.content, url

    def test_the_api_resource_and_errors_carry_none(self, app: TestClient) -> None:
        for resp in (app.get("/restconf"), app.get("/restconf/operations"),
                     app.get(f"/restconf{_CONNECTIVITY}/connectivity-service=missing")):
            assert "etag" not in resp.headers and "last-modified" not in resp.headers

    def test_tags_are_strong_opaque_and_last_modified_is_an_http_date(
        self, app: TestClient,
    ) -> None:
        resp = app.get("/restconf/data")
        tag = resp.headers["etag"]
        assert tag.startswith('"') and tag.endswith('"') and not tag.startswith("W/")
        modified = datetime.strptime(resp.headers["last-modified"], "%a, %d %b %Y %H:%M:%S GMT")
        assert abs(modified.replace(tzinfo=UTC) - datetime.now(UTC)) < timedelta(minutes=5)

    def test_a_restart_never_reuses_a_tag(self, twin_config) -> None:
        first = TestClient(create_app(twin_config))
        second = TestClient(create_app(twin_config))
        tag = first.get("/restconf/data").headers["etag"]
        assert second.get("/restconf/data").headers["etag"] != tag
        assert second.get("/restconf/data", headers={"If-None-Match": tag}).status_code == 200
        url = f"/restconf{_CONNECTIVITY}/connectivity-service=x"
        assert second.put(url, json={}, headers={"If-Match": tag}).status_code == 412


class TestWhatMovesTheTags:
    """Every kind of change: (moves the configuration tag?, request)."""

    def _change(self, client: TestClient, root: str, kind: str, tmp_path: Path) -> None:
        refs = MeshRefs(client.app.state.context)
        services = _service_urls(client, root)
        if kind == "post-service":
            resp = client.post(f"{root}{_CONNECTIVITY}", json={_SVC: [{"end-point": [
                tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx B"))]}]})
        elif kind == "put-service":
            body = client.get(services[0]).json()
            body[_SVC][0]["name"] = [{"value-name": "n", "value": "renamed"}]
            for key in ("operational-state", "lifecycle-state"):
                body[_SVC][0].pop(key)
            resp = client.put(services[0], json=body)
        elif kind == "patch-name":
            resp = client.patch(services[0], json={"name": [{"value-name": "n", "value": "x"}]})
        elif kind == "patch-lifecycle":
            resp = client.patch(services[0], json={"lifecycle-state": "INSTALLED"})
        elif kind == "delete-service":
            resp = client.delete(services[0])
        elif kind == "post-path-comp":
            resp = client.post(f"{root}{_PATH_COMPUTATION}", json={
                "tapi-path-computation:path-comp-service": [{"end-point": [
                    {"local-id": "a", "service-interface-point": {
                        "service-interface-point-uuid": refs.sip("trx B")}},
                    {"local-id": "z", "service-interface-point": {
                        "service-interface-point-uuid": refs.sip("trx A")}}]}]})
        elif kind == "delete-path-comp":
            url = f"{root}{_PATH_COMPUTATION}/path-comp-service"
            (pcs,) = client.get(url).json()["tapi-path-computation:path-comp-service"]
            resp = client.delete(f"{url}={pcs['uuid']}")
        elif kind == "restore":
            snap = tmp_path / "snap.json"
            client.post("/admin/snapshot", json={"path": str(snap)})
            resp = client.post("/admin/restore", json={"path": str(snap)})
        elif kind == "link-failure":
            resp = client.post("/config/set", json={
                "devices": {"fiber (A → B)": {"failed": True}}})
        elif kind == "element-parameter":
            resp = client.post("/config/parameters", json={
                "set": {"/Fiber/fiber (A → B)/loss_coef": 0.3}})
        elif kind == "twin-setting":
            resp = client.post("/config/set", json={"twin": {"rmsa": {"qot_margin_db": 2.5}}})
        elif kind == "transparent-write":
            resp = client.patch("/transparent/data/Config/rmsa", json={"qot_margin_db": 1.5})
        elif kind == "stream-active":
            asyncio.run(client.app.state.measurement_stream.tick())
            return
        else:  # pragma: no cover
            raise AssertionError(kind)
        assert resp.status_code < 300, (kind, resp.text)

    @pytest.mark.parametrize(("kind", "config"), [
        ("post-service", True), ("put-service", True), ("patch-name", True),
        ("delete-service", True), ("post-path-comp", True), ("delete-path-comp", True),
        ("restore", True), ("patch-lifecycle", False), ("link-failure", False),
        ("element-parameter", False), ("twin-setting", False),
        ("transparent-write", False), ("stream-active", False),
    ])
    def test_each_change(self, mesh, tmp_path: Path, kind: str, config: bool) -> None:
        client, root = mesh
        before_config, before_full = _tags(client, root)
        before = client.get(f"{root}/data")
        modified = client.get(f"{root}/data").headers["last-modified"]
        self._change(client, root, kind, tmp_path)
        after_config, after_full = _tags(client, root)
        assert after_full != before_full, kind
        assert (after_config != before_config) is config, kind
        # The old full tag no longer validates the default view ...
        resp = client.get(f"{root}/data", headers={"If-None-Match": before_full})
        assert resp.status_code == 200 and resp.headers["etag"] == after_full
        # ... nor the old configuration tag the config view, if it moved.
        resp = client.get(f"{root}/data?content=config",
                          headers={"If-None-Match": before_config})
        assert resp.status_code == (200 if config else 304), kind
        assert client.get(f"{root}/data", headers={
            "If-Modified-Since": modified}).status_code == 200
        del before

    def test_a_link_failure_changes_what_the_old_tag_described(self, mesh) -> None:
        client, root = mesh
        topology = f"{root}{_CONTEXT}/tapi-topology:topology-context"
        old = client.get(topology)
        client.post("/config/set", json={"devices": {"fiber (A → B)": {"failed": True}}})
        new = client.get(topology, headers={"If-None-Match": old.headers["etag"]})
        assert new.status_code == 200 and new.json() != old.json()
        # The configuration of the topology did not change: same config tag.
        assert client.get(f"{topology}?content=config").headers["etag"] == _tags(
            client, root)[0]

    def test_reads_move_nothing(self, twin) -> None:
        client, root = twin
        tags = _tags(client, root)
        for url in _service_urls(client, root):
            client.get(url)
        client.get(f"{root}/data")
        client.get("/internal/opm")
        assert _tags(client, root) == tags


class TestConditionalReads:
    def test_if_modified_since(self, app: TestClient) -> None:
        rev = app.app.state.context.datastore_revision
        rev.full.modified = datetime.now(UTC) - timedelta(minutes=1)
        rev.full.ambiguous = False
        modified = app.get("/restconf/data").headers["last-modified"]
        assert app.get("/restconf/data", headers={
            "If-Modified-Since": modified}).status_code == 304
        older = format_datetime(rev.full.modified - timedelta(seconds=1), usegmt=True)
        assert app.get("/restconf/data", headers={
            "If-Modified-Since": older}).status_code == 200
        # If-None-Match decides when both are present (RFC 9110 §13.2.2).
        assert app.get("/restconf/data", headers={
            "If-Modified-Since": modified, "If-None-Match": '"other"'}).status_code == 200
        # A second that held two states is never answered with a 304.
        rev.full.ambiguous = True
        assert app.get("/restconf/data", headers={
            "If-Modified-Since": modified}).status_code == 200
        # An invalid date is ignored.
        assert app.get("/restconf/data", headers={
            "If-Modified-Since": "yesterday"}).status_code == 200

    def test_if_match_and_if_unmodified_since_on_get(self, app: TestClient) -> None:
        tag = app.get("/restconf/data").headers["etag"]
        assert app.get("/restconf/data", headers={"If-Match": tag}).status_code == 200
        assert app.get("/restconf/data", headers={"If-Match": "*"}).status_code == 200
        resp = app.get("/restconf/data", headers={"If-Match": '"old"'})
        assert resp.status_code == 412 and _error(resp)["error-tag"] == "operation-failed"
        assert resp.headers["etag"] == tag
        past = format_datetime(datetime.now(UTC) - timedelta(hours=1), usegmt=True)
        assert app.get("/restconf/data", headers={
            "If-Unmodified-Since": past}).status_code == 412

    def test_weak_and_listed_tags(self, app: TestClient) -> None:
        tag = app.get("/restconf/data").headers["etag"]
        for value in (f"W/{tag}", f'"a", {tag}', "*"):
            assert app.get("/restconf/data", headers={
                "If-None-Match": value}).status_code == 304, value
        # If-Match compares strongly: a weak tag never matches.
        assert app.get("/restconf/data", headers={"If-Match": f"W/{tag}"}).status_code == 412

    def test_a_filtered_view_carries_its_own_tag(self, app: TestClient) -> None:
        config = app.get("/restconf/data?content=config").headers["etag"]
        full = app.get("/restconf/data").headers["etag"]
        assert config != full
        assert app.get("/restconf/data?content=nonconfig").headers["etag"] == full
        assert app.get("/restconf/data?content=config", headers={
            "If-None-Match": full}).status_code == 200
        assert app.get("/restconf/data?content=config", headers={
            "If-None-Match": config}).status_code == 304

    def test_a_404_ignores_preconditions(self, app: TestClient) -> None:
        url = f"/restconf{_CONNECTIVITY}/connectivity-service=missing"
        assert app.get(url, headers={"If-None-Match": "*"}).status_code == 404
        assert app.get(url, headers={"If-Match": '"x"'}).status_code == 404


class TestConditionalWrites:
    def test_a_stale_if_match_is_refused_before_anything_changes(self, mesh) -> None:
        client, root = mesh
        url = _service_urls(client, root)[0]
        stale = client.get(url).headers["etag"]
        client.patch(url, json={"name": [{"value-name": "n", "value": "moved on"}]})
        before = client.get(url).json()
        for method, kwargs in (("DELETE", {}), ("PATCH", {"json": {"lifecycle-state": "PLANNED"}}),
                               ("PUT", {"json": before})):
            resp = client.request(method, url, headers={"If-Match": stale}, **kwargs)
            assert resp.status_code == 412, (method, resp.text)
            assert _error(resp)["error-tag"] == "operation-failed"
            assert resp.headers["etag"] == _tags(client, root)[0]
        assert client.get(url).json() == before
        resp = client.post(f"{root}{_CONNECTIVITY}", headers={"If-Match": stale}, json={})
        assert resp.status_code == 412  # before the body is read: no 400

    @pytest.mark.parametrize("view", ["config", "full"])
    def test_a_current_tag_lets_the_write_through(self, mesh, view: str) -> None:
        client, root = mesh
        url = _service_urls(client, root)[0]
        tag = _tags(client, root)[0 if view == "config" else 1]
        resp = client.patch(url, headers={"If-Match": tag}, json={"lifecycle-state": "PLANNED"})
        assert resp.status_code == 200, resp.text
        assert "etag" not in resp.headers  # RFC 9110 §9.3.4: no validator on a write
        assert client.delete(url, headers={"If-Match": "*"}).status_code == 204

    def test_the_config_tag_survives_a_state_change(self, mesh) -> None:
        client, root = mesh
        url = _service_urls(client, root)[0]
        config_tag, full_tag = _tags(client, root)
        client.post("/config/set", json={"devices": {"fiber (A → B)": {"failed": True}}})
        assert client.delete(url, headers={"If-Match": full_tag}).status_code == 412
        assert client.delete(url, headers={"If-Match": config_tag}).status_code == 204

    def test_put_with_if_none_match_star_creates_only(self, mesh) -> None:
        client, root = mesh
        refs = MeshRefs(client.app.state.context)
        url = f"{root}{_CONNECTIVITY}/connectivity-service=only-once"
        body = {_SVC: [{"uuid": "only-once", "end-point": [
            tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx B"))]}]}
        tag = _tags(client, root)[0]
        # If-Match on a resource that does not exist yet fails (RFC 9110 §13.1.1).
        assert client.put(url, json=body, headers={"If-Match": tag}).status_code == 412
        assert client.get(url).status_code == 404
        assert client.put(url, json=body, headers={"If-None-Match": "*"}).status_code == 201
        assert client.put(url, json=body, headers={"If-None-Match": "*"}).status_code == 412

    def test_if_unmodified_since(self, mesh) -> None:
        client, root = mesh
        url = _service_urls(client, root)[0]
        past = format_datetime(datetime.now(UTC) - timedelta(hours=1), usegmt=True)
        assert client.delete(url, headers={"If-Unmodified-Since": past}).status_code == 412
        rev = client.app.state.context.datastore_revision
        rev.config.modified = datetime.now(UTC) - timedelta(minutes=1)
        rev.config.ambiguous = False
        now = format_datetime(datetime.now(UTC), usegmt=True)
        assert client.delete(url, headers={"If-Unmodified-Since": now}).status_code == 204

    def test_a_404_or_405_wins_over_a_precondition(self, mesh) -> None:
        client, root = mesh
        missing = f"{root}{_CONNECTIVITY}/connectivity-service=missing"
        assert client.delete(missing, headers={"If-Match": '"x"'}).status_code == 404
        assert client.patch(missing, json={}, headers={"If-Match": '"x"'}).status_code == 404
        # The topology is config false: no edit is ever served there.
        read_only = f"{root}/data/tapi-common:context/tapi-topology:topology-context"
        resp = client.put(read_only, json={}, headers={"If-Match": '"x"'})
        assert resp.status_code == 405

    def test_with_tokens_a_401_comes_first(self, tmp_path: Path) -> None:
        config = _mesh_config(tmp_path, "gnpy")
        config = config.model_copy(update={"api_tokens": parse_tokens(f"ops:{_TOKEN}")})
        client = TestClient(create_app(config))
        url = f"/restconf{_CONNECTIVITY}/connectivity-service=x"
        assert client.delete(url, headers={"If-Match": '"x"'}).status_code == 401
        assert client.get("/restconf/data", headers={"If-None-Match": "*"}).status_code == 401
        auth = {"Authorization": f"Bearer {_TOKEN}"}
        tag = client.get("/restconf/data", headers=auth).headers["etag"]
        assert client.get("/restconf/data", headers={
            **auth, "If-None-Match": tag}).status_code == 304
        assert client.put(url, json={}, headers={**auth, "If-Match": tag}).status_code == 412


class TestConcurrentWrites:
    async def test_two_writes_holding_one_tag_cannot_both_win(self, tmp_path: Path) -> None:
        """Two clients read the same tag and PATCH concurrently with it: the
        first holds the gate while it writes, the second then finds the tag
        stale. Without the gate both checks pass before either write lands."""
        app = create_app(_mesh_config(tmp_path, "egn"))
        ctx = app.state.context
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://twin") as client:
            refs = MeshRefs(ctx)
            resp = await client.post(f"/restconf{_CONNECTIVITY}", json={_SVC: [{"end-point": [
                tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))]}]})
            uuid = resp.json()[_SVC][0]["uuid"]
            url = f"/restconf{_CONNECTIVITY}/connectivity-service={uuid}"
            tag = (await client.get(url)).headers["etag"]

            release = asyncio.Event()
            entered = asyncio.Event()
            original = ctx.get_or_compute_baseline

            async def slow(svc):  # the first request blocks here, holding the gate
                entered.set()
                await release.wait()
                return await original(svc)

            ctx.get_or_compute_baseline = slow
            first = asyncio.create_task(client.patch(
                url, headers={"If-Match": tag}, json={"name": [{"value-name": "n", "value": "1"}]}))
            await entered.wait()
            second = asyncio.create_task(client.patch(
                url, headers={"If-Match": tag}, json={"name": [{"value-name": "n", "value": "2"}]}))
            # A write through another interface waits too.
            other = asyncio.create_task(client.post("/config/set", json={
                "twin": {"rmsa": {"qot_margin_db": 2.0}}}))
            await asyncio.sleep(0.05)
            assert not second.done() and not other.done()
            ctx.get_or_compute_baseline = original
            release.set()
            statuses = sorted([(await first).status_code, (await second).status_code])
            assert statuses == [200, 412]
            assert (await other).status_code == 200
            name = (await client.get(url)).json()[_SVC][0]["name"]
            assert name == [{"value-name": "n", "value": "1"}]


def test_the_gate_keeps_writes_concurrent_without_preconditions(tmp_path: Path) -> None:
    gate = create_app(_mesh_config(tmp_path, "egn")).state.context.write_gate

    async def run() -> list[str]:
        order: list[str] = []

        async def shared(name: str) -> None:
            async with gate.shared():
                order.append(f"{name}+")
                await asyncio.sleep(0.01)
                order.append(f"{name}-")

        await asyncio.gather(shared("a"), shared("b"))
        return order

    assert asyncio.run(run())[:2] == ["a+", "b+"]


def test_json_is_unchanged_by_the_default_view(app: TestClient) -> None:
    body = app.get("/restconf/data").json()
    assert json.loads(app.get("/restconf/data?content=all").content) == body
