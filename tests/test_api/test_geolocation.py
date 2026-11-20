"""Where the twin's nodes are, in T-API v2.6.0 terms.

``tapi-equipment.yang`` gives every ``equipment`` a ``geolocation``
container (``latitude`` and ``longitude`` in decimal degrees, decimal64 with
``fraction-digits 7``, config false) and augments the topology ``node`` with
``device-supports-node``, whose ``supporting-device`` list names the
``physical-context`` device that supports it. So a client goes from a
topology node to its position with standard members only:

    node / tapi-equipment:device-supports-node / supporting-device / device-uuid
      -> physical-context / device / equipment / geolocation

The positions are the topology file's ``metadata.location``. An element
without a usable one has no ``geolocation`` member at all; (0, 0) is GNPy's
placeholder for "unlocated" (``gnpy.core.elements.Location`` defaults both
to 0), not a position.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.conftest import FIXTURES, _mesh_config
from twinlight.app import create_app
from twinlight.auth import parse_tokens
from twinlight.config import TwinConfig
from twinlight.example_data import find_example_file
from twinlight.loader.gnpy_topology import element_position, load_gnpy_topology
from twinlight.models.equipment import Geolocation

_CONTEXT = "/data/tapi-common:context"
_PHYSICAL = f"{_CONTEXT}/tapi-equipment:physical-context"
_TOPOLOGIES = f"{_CONTEXT}/tapi-topology:topology-context"
_AUGMENT = "tapi-equipment:device-supports-node"
_MESH = json.loads((FIXTURES / "mesh_network.json").read_text())


class _AnyUuid:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and len(other) == 36

    def __repr__(self) -> str:
        return "<any uuid>"


ANY_UUID = _AnyUuid()


def _uid(entry: dict[str, Any]) -> str:
    return next(n["value"] for n in entry["name"] if n["value-name"] == "element-uid")


def _file_positions(topology: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {
        el["uid"]: (el["metadata"]["location"]["latitude"], el["metadata"]["location"]["longitude"])
        for el in topology["elements"]
    }


def _positions(context: dict[str, Any]) -> dict[str, dict[str, str] | None]:
    """Element UID -> its equipment's geolocation (None when absent)."""
    return {
        _uid(device): device["equipment"][0].get("geolocation")
        for device in context["tapi-equipment:physical-context"]["device"]
    }


def _client(tmp_path: Path, backend: str, topology: dict[str, Any] | None = None,
            **server: Any) -> TestClient:
    config = _mesh_config(tmp_path, backend)
    if topology is not None:
        path = tmp_path / "topology.json"
        path.write_text(json.dumps(topology))
        config.gnpy.topology = path
    for key, value in server.items():
        setattr(config.server, key, value)
    return TestClient(create_app(config))


@pytest.fixture(params=["gnpy", "egn"])
def backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)


# ---------------------------------------------------------------------------
# What counts as a position
# ---------------------------------------------------------------------------


class _Location:
    """Stands in for gnpy.core.elements.Location, a named tuple."""

    def __init__(self, latitude: Any, longitude: Any) -> None:
        self.latitude, self.longitude = latitude, longitude


class TestElementPosition:
    @pytest.mark.parametrize("location, expected", [
        ({"latitude": 47.6062, "longitude": -122.3321, "city": "Seattle"}, (47.6062, -122.3321)),
        (_Location(25.7617, -80.1918), (25.7617, -80.1918)),
        # The equator and the prime meridian are places; only both at 0 is not.
        ({"latitude": 0.0, "longitude": 11.9}, (0.0, 11.9)),
        ({"latitude": 51.48, "longitude": 0}, (51.48, 0.0)),
        ({"latitude": -90, "longitude": 180}, (-90.0, 180.0)),
        ({"latitude": 90, "longitude": -180}, (90.0, -180.0)),
    ])
    def test_a_position(self, location: Any, expected: tuple[float, float]) -> None:
        assert element_position(location) == expected

    @pytest.mark.parametrize("location", [
        None,
        {},
        {"city": "Lannion"},
        {"latitude": 48.73},
        {"longitude": -3.46},
        # GNPy's placeholder: Location() and gnpy.tools.convert write 0, 0.
        {"latitude": 0, "longitude": 0},
        {"latitude": 0.0, "longitude": -0.0},
        _Location(0, 0),
        {"latitude": 90.0000001, "longitude": 0.5},
        {"latitude": 10, "longitude": -180.5},
        {"latitude": float("nan"), "longitude": 1},
        {"latitude": 1, "longitude": float("inf")},
        {"latitude": "48.73", "longitude": "-3.46"},
        {"latitude": True, "longitude": 1},
        {"latitude": None, "longitude": None},
        "Lannion",
    ])
    def test_no_position(self, location: Any) -> None:
        assert element_position(location) is None

    def test_the_model_refuses_a_value_outside_the_yang_range(self) -> None:
        with pytest.raises(ValidationError):
            Geolocation(latitude=90.5, longitude=0.0)
        with pytest.raises(ValidationError):
            Geolocation(latitude=0.0, longitude=-181.0)

    def test_coronet_conus_locates_every_element(self) -> None:
        """The reference topology the UI is checked on: 348 elements, each
        with a real position, and so each device with a geolocation."""
        path = find_example_file("CORONET_CONUS_Topology.json")
        if path is None:
            pytest.skip("gnpy's example data is not installed")
        topology = load_gnpy_topology(path)
        assert len(topology.elements) == 348
        assert all(element_position(el.metadata.get("location")) for el in topology.elements)


# ---------------------------------------------------------------------------
# On the wire
# ---------------------------------------------------------------------------


class TestEveryNodeReachesItsPosition:
    def test_through_standard_members_only(self, tmp_path: Path, backend: str) -> None:
        client = _client(tmp_path, backend)
        context = client.get(f"/restconf{_CONTEXT}").json()["tapi-common:context"]
        devices = {d["uuid"]: d for d in context["tapi-equipment:physical-context"]["device"]}
        expected = _file_positions(_MESH)
        (topology,) = context["tapi-topology:topology-context"]["topology"]
        assert len(topology["node"]) == 8
        for node in topology["node"]:
            name = next(n["value"] for n in node["name"] if n["value-name"] == "node-name")
            # One node, one device: the list has exactly that device.
            assert node[_AUGMENT] == {"supporting-device": [{"device-uuid": ANY_UUID}]}
            (ref,) = node[_AUGMENT]["supporting-device"]
            device = devices[ref["device-uuid"]]  # the leafref resolves
            assert _uid(device) == name
            geolocation = device["equipment"][0]["geolocation"]
            lat, lng = expected[name]
            # decimal64 on the wire: strings (RFC 7951 §6.1) of the file's value.
            assert geolocation == {"latitude": str(lat), "longitude": str(lng)}
            assert (Decimal(geolocation["latitude"]), Decimal(geolocation["longitude"])) == (
                Decimal(repr(lat)), Decimal(repr(lng)))

    def test_the_resources_one_level_down_agree(self, tmp_path: Path, backend: str) -> None:
        client = _client(tmp_path, backend)
        base = "/restconf"
        topology = client.get(f"{base}{_TOPOLOGIES}").json()[
            "tapi-topology:topology-context"]["topology"][0]
        node = next(n for n in topology["node"] if n["name"][0]["value"] == "roadm C")
        got = client.get(f"{base}{_TOPOLOGIES}/topology={topology['uuid']}/node={node['uuid']}")
        assert got.json()["tapi-topology:node"][0][_AUGMENT] == node[_AUGMENT]
        device_uuid = node[_AUGMENT]["supporting-device"][0]["device-uuid"]
        device = client.get(f"{base}{_PHYSICAL}/device={device_uuid}").json()[
            "tapi-equipment:device"][0]
        equipment = client.get(
            f"{base}{_PHYSICAL}/device={device_uuid}/equipment={device['equipment'][0]['uuid']}"
        ).json()["tapi-equipment:equipment"][0]
        assert device["equipment"][0]["geolocation"] == equipment["geolocation"] == {
            "latitude": "59.3", "longitude": "18.1"}

    def test_a_node_has_no_configuration_of_it(self, tmp_path: Path) -> None:
        """Both members are config false: content=config leaves them out."""
        client = _client(tmp_path, "egn")
        url = f"/restconf{_CONTEXT}"
        config = client.get(f"{url}?content=config").json()["tapi-common:context"]
        nonconfig = client.get(f"{url}?content=nonconfig").json()["tapi-common:context"]
        assert _AUGMENT not in json.dumps(config) and "geolocation" not in json.dumps(config)
        assert _AUGMENT in json.dumps(nonconfig) and "geolocation" in json.dumps(nonconfig)

    def test_gnpys_inserted_amplifiers_sit_where_the_design_put_them(
        self, tmp_path: Path,
    ) -> None:
        client = _client(tmp_path, "gnpy")
        positions = _positions(client.get(f"/restconf{_CONTEXT}").json()["tapi-common:context"])
        roadm_a = {"latitude": "57.7", "longitude": "11.9"}
        assert positions["roadm A"] == roadm_a
        assert positions["Edfa_booster_roadm A_to_fiber (A → B)"] == roadm_a
        assert positions["Edfa_preamp_roadm A_from_fiber (B → A)"] == roadm_a
        assert all(positions.values()), [uid for uid, p in positions.items() if not p]


class TestUnlocatedElements:
    def test_have_no_geolocation_member(self, tmp_path: Path, backend: str) -> None:
        topology = copy.deepcopy(_MESH)
        elements = {el["uid"]: el for el in topology["elements"]}
        elements["roadm E"]["metadata"]["location"].update(latitude=0, longitude=0)
        del elements["trx C"]["metadata"]
        elements["trx B"]["metadata"]["location"].update(latitude=91.0)
        elements["roadm A"]["metadata"]["location"].update(latitude=0.0)  # the equator
        client = _client(tmp_path, backend, topology)
        r = client.get(f"/restconf{_CONTEXT}")
        context = r.json()["tapi-common:context"]
        positions = _positions(context)
        for uid in ("roadm E", "trx C", "trx B"):
            assert positions[uid] is None, uid
        assert positions["roadm A"] == {"latitude": "0.0", "longitude": "11.9"}
        assert positions["roadm C"] == {"latitude": "59.3", "longitude": "18.1"}
        # Absent, not null; and every node still names its device.
        assert '"geolocation":null' not in r.text.replace(" ", "")
        for node in context["tapi-topology:topology-context"]["topology"][0]["node"]:
            assert len(node[_AUGMENT]["supporting-device"]) == 1

    def test_a_topology_of_placeholders_has_no_positions(
        self, tmp_path: Path, backend: str,
    ) -> None:
        """What gnpy.tools.convert writes for a sheet without coordinates."""
        topology = copy.deepcopy(_MESH)
        for el in topology["elements"]:
            el["metadata"]["location"].update(latitude=0, longitude=0)
        client = _client(tmp_path, backend, topology)
        r = client.get(f"/restconf{_PHYSICAL}")
        assert r.status_code == 200
        assert r.json()["tapi-equipment:physical-context"]["device"]
        assert "geolocation" not in r.text


class TestPositionsStay:
    def test_through_a_redesign_and_a_restore(self, tmp_path: Path, backend: str) -> None:
        client = _client(tmp_path, backend)
        url = f"/restconf{_CONTEXT}"
        before = _positions(client.get(url).json()["tapi-common:context"])
        assert all(before.values())
        redesign = client.post("/config/set", json={"redesign": True})
        assert redesign.status_code == (200 if backend == "gnpy" else 501), redesign.text
        assert _positions(client.get(url).json()["tapi-common:context"]) == before

        snapshot = tmp_path / "state.json"
        assert client.post("/admin/snapshot", json={"path": str(snapshot)}).status_code == 200
        fiber = "fiber (A → B)"
        assert client.post("/config/set", json={
            "devices": {fiber: {"loss_coef": 0.3}}}).status_code == 200
        assert client.post("/admin/restore", json={"path": str(snapshot)}).status_code == 200
        assert _positions(client.get(url).json()["tapi-common:context"]) == before


class TestUnderAnyRootAndWithTokens:
    @pytest.mark.parametrize("root", ["/restconf", "/api/tapi", ""])
    def test_served_under_the_root(self, tmp_path: Path, root: str) -> None:
        client = _client(tmp_path, "egn", restconf_root=root)
        r = client.get(f"{root}{_PHYSICAL}/device")
        assert r.status_code == 200, r.text
        assert all("geolocation" in d["equipment"][0] for d in r.json()["tapi-equipment:device"])
        nodes = client.get(f"{root}{_TOPOLOGIES}").json()[
            "tapi-topology:topology-context"]["topology"][0]["node"]
        assert all(_AUGMENT in n for n in nodes)

    def test_behind_the_token_check(self, tmp_path: Path) -> None:
        token = "geo-token-0123456789abcdef"
        config: TwinConfig = _mesh_config(tmp_path, "egn").model_copy(
            update={"api_tokens": parse_tokens(f"ui:{token}")})
        client = TestClient(create_app(config))
        url = f"/restconf{_PHYSICAL}/device"
        assert client.get(url).status_code == 401
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["tapi-equipment:device"][0]["equipment"][0]["geolocation"]
