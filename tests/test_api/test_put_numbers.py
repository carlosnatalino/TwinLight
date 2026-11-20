"""A PUT of the twin's own body, with its 64-bit leaves in either spelling.

A container PUT keeps the entries whose configuration is unchanged, so their
spectrum does not move (RFC 8040 §4.5 replaces what differs). The twin
returns its uint64 and decimal64 leaves as strings (RFC 7951 §6.1) and still
accepts the JSON numbers earlier releases sent. A client that reads a
container and puts it back must find nothing changed in either spelling: a
number compared with its own string as "changed" would re-admit every
service the body lists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _mesh_config
from tests.test_api.test_put_replace import _CC, _R, _everything, _internals, _populate
from twinlight.app import create_app

_UINT64 = {"lower-frequency", "upper-frequency", "central-frequency", "number-of-mc", "number-of-otsi"}
_DECIMAL64 = {"channel-output-power"}


@pytest.fixture(params=["gnpy", "egn"])
def mesh(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, request.param)))


def _as_numbers(value: Any, converted: list[str]) -> Any:
    """The body with every 64-bit leaf as the JSON number of its string."""
    if isinstance(value, list):
        return [_as_numbers(item, converted) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in _UINT64 and isinstance(item, str):
            converted.append(key)
            out[key] = int(item)
        elif key in _DECIMAL64 and isinstance(item, str):
            converted.append(key)
            out[key] = float(item)
        else:
            out[key] = _as_numbers(item, converted)
    return out


@pytest.mark.parametrize("url", [_CC, _R, "/restconf/data"])
@pytest.mark.parametrize("spelling", ["strings", "numbers"])
def test_its_own_body_changes_nothing_in_either_spelling(
    mesh: TestClient, url: str, spelling: str
) -> None:
    _populate(mesh)
    before, internals = _everything(mesh), _internals(mesh)
    body = mesh.get(url).json()
    if spelling == "numbers":
        converted: list[str] = []
        body = _as_numbers(body, converted)
        # The body did carry 64-bit leaves, so the two spellings differ.
        assert {"lower-frequency", "upper-frequency", "central-frequency"} <= set(converted)

    resp = mesh.put(url, json=body)

    assert resp.status_code == 204, resp.text
    assert _everything(mesh) == before
    # Same baseline objects, same OAM jobs: no service was admitted again.
    assert _internals(mesh) == internals
