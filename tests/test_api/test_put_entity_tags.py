"""PUT on the containers and the datastore, with the datastore's validators.

The two were written apart: the replacement of whole lists (RFC 8040 §4.5)
and the entity-tags with their conditional requests (RFC 8040 §3.4.1, RFC
9110 §13). These tests hold them together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import MeshRefs, _mesh_config
from tests.test_api.test_put_replace import _CC, _SVC, _populate, _service, _uuids
from twinlight.app import create_app

_DATA = "/restconf/data"


@pytest.fixture(params=["gnpy", "egn"])
def mesh(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


def _tags(app: TestClient) -> tuple[str, str]:
    """The current (full, configuration) entity-tags of the datastore."""
    return (
        app.get(_DATA).headers["etag"],
        app.get(_DATA, params={"content": "config"}).headers["etag"],
    )


def test_a_refused_replacement_leaves_no_tag_of_a_state_it_passed_through(
    mesh: TestClient,
) -> None:
    """A PUT that deletes three services, admits one and is refused the next
    puts everything back. A reader may have seen the twin in between, with
    the tag of that moment. That tag must not validate the restored state:
    a ``304`` would confirm a body that is no longer served."""
    refs = _populate(mesh)
    ctx = mesh.app.state.context
    revision = ctx.datastore_revision
    seen: list[tuple[str, str]] = []
    admit = ctx._add_service

    async def recording(svc):
        # What a concurrent GET would be handed while this entry is admitted.
        seen.append((revision.etag("full"), revision.etag("config")))
        return await admit(svc)

    ctx._add_service = recording
    body = {"tapi-connectivity:connectivity-context": {"connectivity-service": [
        _service(refs, "new-1", "trx A", "trx C", block=200),
        _service(refs, "new-2", "trx A", "trx B", block=200),  # the same block: refused
    ]}}
    before = _tags(mesh)
    resp = mesh.put(_CC, json=body)
    ctx._add_service = admit
    assert resp.status_code == 409, resp.text
    assert len(seen) == 2
    assert _uuids(mesh) == ["old-1", "old-2", "old-3"]

    full, config = _tags(mesh)
    assert full not in {tag for tag, _ in seen}
    assert config not in {tag for _, tag in seen}
    for stale, _ in [*seen, before]:
        assert mesh.get(_DATA, headers={"If-None-Match": stale}).status_code == 200
    assert mesh.get(_DATA, headers={"If-None-Match": full}).status_code == 304


def test_a_replacement_moves_the_validators(mesh: TestClient) -> None:
    """The container PUT goes through the same mutators as POST and DELETE,
    so both tags move without the route knowing about them."""
    refs = MeshRefs(mesh.app.state.context)
    before = _tags(mesh)
    body = {"tapi-connectivity:connectivity-context": {"connectivity-service": [
        _service(refs, "new-1", "trx A", "trx C"),
    ]}}
    assert mesh.put(_CC, json=body).status_code == 204
    full, config = _tags(mesh)
    assert full != before[0]
    assert config != before[1]


def test_a_guarded_put_of_the_datastore_has_its_precondition_evaluated(
    mesh: TestClient,
) -> None:
    """Once the datastore serves PUT, ``If-Match`` on it is evaluated (RFC
    8040 §3.4.1.2: "The client MAY use an 'If-Match' header in edit
    operation requests for the datastore resource")."""
    _populate(mesh)
    own = mesh.get(_DATA).json()
    _, config = _tags(mesh)

    stale = mesh.put(_DATA, json=own, headers={"If-Match": '"not-the-tag"'})
    assert stale.status_code == 412, stale.text
    (error,) = stale.json()["ietf-restconf:errors"]["error"]
    assert error["error-tag"] == "operation-failed"

    current = mesh.put(_DATA, json=own, headers={"If-Match": config})
    assert current.status_code == 204, current.text
    assert _uuids(mesh) == ["old-1", "old-2", "old-3"]


def test_a_guarded_put_of_a_container_fails_after_another_write(mesh: TestClient) -> None:
    """Edit collision prevention over a list replacement: a client that read
    the list, and replaces it after someone else added a service, is told."""
    refs = _populate(mesh)
    container = mesh.get(_CC).json()
    _, config = _tags(mesh)
    added = mesh.post(_CC, json={_SVC: [_service(refs, "other", "trx A", "trx C", block=300)]})
    assert added.status_code == 201, added.text

    resp = mesh.put(_CC, json=container, headers={"If-Match": config})
    assert resp.status_code == 412, resp.text
    assert "other" in _uuids(mesh)
