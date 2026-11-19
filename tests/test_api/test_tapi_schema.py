"""The T-API v2.6.0 data-node tree the request checks are made against."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.api.restconf_resources import IMPLEMENTED
from twinlight.models import tapi_schema
from twinlight.models.tapi_schema_tree import MODULES

_CONTEXT = "/tapi-common:context"
_SERVICE = f"{_CONTEXT}/tapi-connectivity:connectivity-context/connectivity-service"


def test_the_tree_is_of_the_modules_the_yang_library_lists() -> None:
    assert {m["name"] for m in IMPLEMENTED if m["name"].startswith("tapi-")} == MODULES


def test_nodes_and_their_config_statements() -> None:
    service = tapi_schema.node(_SERVICE)
    assert (service.kind, service.keys, service.config) == ("list", ("uuid",), True)
    end_point = service.child("end-point")
    assert end_point is not None and end_point.keys == ("local-id",)
    # tapi-connectivity v2.6.0: lifecycle-state and operational-state are state.
    assert not service.children[("tapi-connectivity", "operational-state")].config
    assert not service.children[("tapi-connectivity", "lifecycle-state")].config
    # The SIP list is config false; the OAM jobs and topologies are not.
    context = tapi_schema.node(_CONTEXT)
    assert not context.children[("tapi-common", "service-interface-point")].config
    assert tapi_schema.node(f"{_CONTEXT}/tapi-oam:oam-context/oam-job").config
    assert tapi_schema.node(f"{_CONTEXT}/tapi-topology:topology-context/topology").config
    # Augments are children of their target, under their own module.
    spec = end_point.children[("tapi-connectivity", "layer-protocol-constraint")].child(
        "tapi-photonic-media:otsia-connectivity-service-end-point-spec")
    assert spec is not None and spec.module == "tapi-photonic-media"
    # Not part of the server's schema: an import-only module, a feature not advertised.
    assert ("tapi-notification", "notification-context") not in context.children
    assert ("tapi-streaming", "stream-admin-context") not in context.children


def test_every_member_the_twin_serves_is_defined(app: TestClient) -> None:
    """RFC 7951 §4 on the twin's own bodies: the datastore GET of a twin
    holding a service, a path-comp-service and its OAM job names no member
    the tree lacks, and qualifies none where it must not."""
    sips = app.get("/restconf/data/tapi-common:context/service-interface-point").json()[
        "tapi-common:service-interface-point"]
    ends = [tapi_end_point("a", sips[0]["uuid"]), tapi_end_point("z", sips[1]["uuid"])]
    base = "/restconf/data/tapi-common:context"
    created = app.post(f"{base}/tapi-connectivity:connectivity-context",
                       json={"tapi-connectivity:connectivity-service": [{"end-point": ends}]})
    assert created.status_code == 201, created.text
    pcs = app.post(f"{base}/tapi-path-computation:path-computation-context", json={
        "tapi-path-computation:path-comp-service": [{"end-point": [
            {"local-id": e["local-id"], "service-interface-point": e["service-interface-point"]}
            for e in ends
        ]}]})
    assert pcs.status_code == 201, pcs.text
    context = app.get("/restconf/data").json()["ietf-restconf:data"]["tapi-common:context"]
    assert context["tapi-oam:oam-context"]["oam-job"]
    assert tapi_schema.unknown_members(context, tapi_schema.node(_CONTEXT), _CONTEXT) == []


class TestUnknownMembers:
    def test_each_kind_of_error(self) -> None:
        body = {
            "uuid": "s",
            "foo": 1,
            "tapi-connectivity:name": [],
            "tapi-bar:baz": 1,
            "end-point": [{"local-id": "a", "qux": {}}],
        }
        errors = tapi_schema.unknown_members(body, tapi_schema.node(_SERVICE), _SERVICE)
        assert [(e.error_tag, e.parent_path, e.member) for e in errors] == [
            ("unknown-element", f"{_SERVICE}[uuid='s']", "foo"),
            ("malformed-message", f"{_SERVICE}[uuid='s']", "tapi-connectivity:name"),
            ("unknown-namespace", f"{_SERVICE}[uuid='s']", "tapi-bar:baz"),
            ("unknown-element", f"{_SERVICE}[uuid='s']/end-point[local-id='a']", "qux"),
        ]
        assert errors[2].namespace == "tapi-bar"

    def test_values_are_not_checked(self) -> None:
        """Only names: a leaf sent as an object is the models' to refuse."""
        body = {"uuid": {"x": 1}, "end-point": "nonsense"}
        assert tapi_schema.unknown_members(body, tapi_schema.node(_SERVICE), _SERVICE) == []

    def test_a_key_with_a_quote_in_it(self) -> None:
        (error,) = tapi_schema.unknown_members(
            {"uuid": "it's", "foo": 1}, tapi_schema.node(_SERVICE), _SERVICE)
        assert error.parent_path == f'{_SERVICE}[uuid="it\'s"]'


class TestConfigView:
    def _view(self, value: dict) -> object:
        return tapi_schema.config_view(value, tapi_schema.node(_CONTEXT))

    def test_state_is_left_out(self) -> None:
        assert self._view({"uuid": "c", "service-interface-point": [{"uuid": "s"}]}) == (
            self._view({"uuid": "c"}))

    def test_an_absent_container_is_an_empty_one(self) -> None:
        assert self._view({"uuid": "c", "tapi-equipment:physical-context": {"device": []}}) == (
            self._view({"uuid": "c"}))

    def test_list_order_does_not_matter(self) -> None:
        a = {"name": [{"value-name": "x", "value": "1"}, {"value-name": "y", "value": "2"}]}
        b = {"name": [{"value-name": "y", "value": "2"}, {"value-name": "x", "value": "1"}]}
        assert self._view(a) == self._view(b)
        assert self._view(a) != self._view({"name": [{"value-name": "x", "value": "1"}]})

    def test_a_number_and_its_decimal_string(self) -> None:
        """RFC 7951 §6.1 encodes a 64-bit number as a string; both compare equal."""
        assert self._view({"uuid": 7}) == self._view({"uuid": "7"})


def test_without_state() -> None:
    body = {"uuid": "s", "operational-state": "ENABLED", "foo": 1,
            "end-point": [{"local-id": "a", "csep-role": [], "direction": "SOURCE"}]}
    assert tapi_schema.without_state(body, tapi_schema.node(_SERVICE)) == {
        "uuid": "s", "foo": 1, "end-point": [{"local-id": "a", "direction": "SOURCE"}],
    }


def test_unsupported_members() -> None:
    body = {"uuid": "s", "resilience-constraint": {}, "operational-state": "X",
            "end-point": [{"local-id": "z", "role": "R", "service-interface-point": {}}]}
    supported = {"uuid": None, "end-point": {"local-id": None, "service-interface-point": None}}
    assert tapi_schema.unsupported_members(body, tapi_schema.node(_SERVICE), supported) == [
        "resilience-constraint", "end-point[z]/role",
    ]
