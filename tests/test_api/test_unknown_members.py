"""Request members T-API v2.6.0 does not define are refused, at any depth.

RFC 8040 §1.4 maps a RESTCONF edit onto NETCONF's ``<edit-config>``, and RFC
6241 Appendix A has a server refuse a body with "An unexpected element"
(``unknown-element``, the member as ``bad-element``), a module it does not
implement (``unknown-namespace``), or a body that does not follow its
encoding (``malformed-message``: RFC 7951 §4 requires the simple member name
where the module does not change). A *defined* member the twin does not act
on is a different case, ``501`` (``operation-not-supported``), and a
config-false member is state an edit ignores.
"""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from tests.conftest import tapi_end_point
from twinlight.models.connectivity import MCG_CSEP_SPEC, OTSIA_CSEP_SPEC

_BASE = "/restconf/data/tapi-common:context/tapi-connectivity:connectivity-context"
_KEY = "tapi-connectivity:connectivity-service"
_SERVICE = "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"


def _sips(app: TestClient) -> tuple[str, str]:
    sips = app.get("/restconf/data/tapi-common:context/service-interface-point").json()[
        "tapi-common:service-interface-point"]
    return sips[0]["uuid"], sips[1]["uuid"]


def _service(app: TestClient, uuid: str = "svc-1") -> dict:
    sip_a, sip_z = _sips(app)
    a, z = tapi_end_point("a", sip_a), tapi_end_point("z", sip_z)
    a["layer-protocol-constraint"][0][MCG_CSEP_SPEC] = {
        "number-of-mc": 1, "mc-spectrum-config-pac": [{"local-id": "mc"}],
    }
    return {
        "uuid": uuid,
        "name": [{"value-name": "service-name", "value": "unknown-members"}],
        "end-point": [a, z],
        "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY"},
        "topology-constraint": [{"local-id": "1"}],
    }


_EP = f"{_SERVICE}[uuid='svc-1']/end-point[local-id='a']"
_LPC = f"{_EP}/layer-protocol-constraint[local-id='otsi']"

#: (where the member is placed, the error-path the twin must name)
PLACES = {
    "service": f"{_SERVICE}[uuid='svc-1']",
    "end-point": _EP,
    "service-interface-point": f"{_EP}/service-interface-point",
    "routing-constraint": f"{_SERVICE}[uuid='svc-1']/routing-constraint",
    "topology-constraint": f"{_SERVICE}[uuid='svc-1']/topology-constraint[local-id='1']",
    "layer-protocol-constraint": _LPC,
    "otsia-spec": f"{_LPC}/{OTSIA_CSEP_SPEC}",
    "otsi-config": f"{_LPC}/{OTSIA_CSEP_SPEC}/otsi-config[local-id='1']",
    "modulation": f"{_LPC}/{OTSIA_CSEP_SPEC}/otsi-config[local-id='1']/modulation",
    "mcg-spec": f"{_LPC}/{MCG_CSEP_SPEC}",
    "mc-spectrum-config-pac": f"{_LPC}/{MCG_CSEP_SPEC}/mc-spectrum-config-pac[local-id='mc']",
    "name": f"{_SERVICE}[uuid='svc-1']/name[value-name='service-name']",
}


def _node(service: dict, place: str) -> dict:
    ep = service["end-point"][0]
    lpc = ep["layer-protocol-constraint"][0]
    return {
        "service": service,
        "end-point": ep,
        "service-interface-point": ep["service-interface-point"],
        "routing-constraint": service["routing-constraint"],
        "topology-constraint": service["topology-constraint"][0],
        "layer-protocol-constraint": lpc,
        "otsia-spec": lpc[OTSIA_CSEP_SPEC],
        "otsi-config": lpc[OTSIA_CSEP_SPEC]["otsi-config"][0],
        "modulation": lpc[OTSIA_CSEP_SPEC]["otsi-config"][0]["modulation"],
        "mcg-spec": lpc[MCG_CSEP_SPEC],
        "mc-spectrum-config-pac": lpc[MCG_CSEP_SPEC]["mc-spectrum-config-pac"][0],
        "name": service["name"][0],
    }[place]


def _error(resp) -> dict:
    (error,) = resp.json()["ietf-restconf:errors"]["error"]
    return error


def _nothing_created(app: TestClient) -> None:
    assert app.get(f"{_BASE}/connectivity-service").json() == {_KEY: []}


class TestTheBodyWithoutAnUnknownMemberIsAdmitted:
    def test_baseline(self, app: TestClient) -> None:
        """Every place the tests below put a member in is a valid request."""
        resp = app.post(_BASE, json={_KEY: [_service(app)]})
        assert resp.status_code == 201, resp.text


class TestUnknownElement:
    @pytest.mark.parametrize("place", sorted(PLACES))
    @pytest.mark.parametrize("method", ["POST", "PUT"])
    def test_at_any_depth(self, app: TestClient, place: str, method: str) -> None:
        service = _service(app)
        _node(service, place)["foo"] = 1
        if method == "POST":
            resp = app.post(_BASE, json={_KEY: [service]})
        else:
            resp = app.put(f"{_BASE}/connectivity-service=svc-1", json={_KEY: [service]})
        assert resp.status_code == 400, resp.text
        error = _error(resp)
        assert error["error-type"] == "application"
        assert error["error-tag"] == "unknown-element"
        assert error["error-info"] == {"bad-element": "foo"}
        assert error["error-path"] == PLACES[place]
        assert f"{PLACES[place]}/foo" in error["error-message"]
        _nothing_created(app)

    def test_put_replacing_a_service_keeps_it(self, app: TestClient) -> None:
        created = app.post(_BASE, json={_KEY: [_service(app)]})
        assert created.status_code == 201
        before = app.get(f"{_BASE}/connectivity-service=svc-1").json()
        service = _service(app)
        service["end-point"][1]["foo"] = {}
        resp = app.put(f"{_BASE}/connectivity-service=svc-1", json={_KEY: [service]})
        assert resp.status_code == 400
        assert _error(resp)["error-path"] == f"{_SERVICE}[uuid='svc-1']/end-point[local-id='z']"
        assert app.get(f"{_BASE}/connectivity-service=svc-1").json() == before

    def test_patch(self, app: TestClient) -> None:
        assert app.post(_BASE, json={_KEY: [_service(app)]}).status_code == 201
        resp = app.patch(f"{_BASE}/connectivity-service=svc-1",
                         json={_KEY: [{"uuid": "svc-1", "administrative-state": "LOCKED",
                                       "foo": 1}]})
        assert resp.status_code == 400
        assert (_error(resp)["error-tag"], _error(resp)["error-info"]) == (
            "unknown-element", {"bad-element": "foo"})
        got = app.get(f"{_BASE}/connectivity-service=svc-1").json()[_KEY][0]
        assert got["administrative-state"] == "UNLOCKED"

    @pytest.mark.parametrize(("place", "member"), [
        ("service", "service-layer"),
        ("service", "service-type"),
        ("service", "modulation-format"),
        ("end-point", "layer-protocol-qualifiers"),
    ])
    def test_earlier_spellings(self, app: TestClient, place: str, member: str) -> None:
        """T-API 2.1's service-layer and service-type (what ONOS sends) and
        this project's pre-2.6 modulation-format are not v2.6.0 members."""
        service = _service(app)
        _node(service, place)[member] = "PHOTONIC_MEDIA"
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        assert _error(resp)["error-info"] == {"bad-element": member}
        _nothing_created(app)

    def test_every_member_is_named(self, app: TestClient) -> None:
        service = _service(app)
        service["foo"] = 1
        _node(service, "otsi-config")["bar"] = 2
        resp = app.post(_BASE, json={_KEY: [service]})
        message = _error(resp)["error-message"]
        assert f"{PLACES['service']}/foo" in message
        assert f"{PLACES['otsi-config']}/bar" in message

    def test_a_member_beside_the_entry(self, app: TestClient) -> None:
        """The body is the one entry (RFC 8040 §4.4.1); nothing beside it."""
        resp = app.post(_BASE, json={_KEY: [_service(app)], "foo": 1})
        assert resp.status_code == 400
        assert (_error(resp)["error-tag"], _error(resp)["error-info"]) == (
            "unknown-element", {"bad-element": "foo"})
        _nothing_created(app)

    def test_unknown_precedes_unimplemented(self, app: TestClient) -> None:
        """A body that is not valid against the schema is refused as such,
        before the twin weighs what it implements."""
        service = _service(app)
        service["resilience-constraint"] = {}
        service["foo"] = 1
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "unknown-element"


class TestWrongModuleOrParent:
    """RFC 7951 §4: a member is qualified exactly where its module differs
    from its parent's."""

    def test_an_augment_left_unqualified(self, app: TestClient) -> None:
        """The simple form names a tapi-connectivity node, and there is none."""
        service = _service(app)
        lpc = _node(service, "layer-protocol-constraint")
        lpc["otsia-connectivity-service-end-point-spec"] = lpc.pop(OTSIA_CSEP_SPEC)
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        error = _error(resp)
        assert error["error-tag"] == "unknown-element"
        assert error["error-path"] == _LPC
        assert OTSIA_CSEP_SPEC in error["error-message"]  # the right spelling
        _nothing_created(app)

    def test_an_augment_under_the_wrong_parent(self, app: TestClient) -> None:
        service = _service(app)
        _node(service, "end-point")[OTSIA_CSEP_SPEC] = {}
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "unknown-element"
        assert _error(resp)["error-info"] == {"bad-element": OTSIA_CSEP_SPEC}
        assert _error(resp)["error-path"] == _EP

    def test_a_node_qualified_with_another_implemented_module(self, app: TestClient) -> None:
        """administrative-state is a tapi-connectivity node here (the
        grouping it comes from is tapi-common's, the node is not)."""
        service = _service(app)
        service["tapi-common:administrative-state"] = "LOCKED"
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        assert _error(resp)["error-tag"] == "unknown-element"
        assert "'administrative-state'" in _error(resp)["error-message"]

    @pytest.mark.parametrize("module", ["tapi-foo", "tapi-notification", "ietf-interfaces"])
    def test_a_module_the_server_does_not_implement(self, app: TestClient, module: str) -> None:
        """tapi-notification is import-only (ietf-yang-library), so its
        namespace is not one whose data the twin implements."""
        service = _service(app)
        _node(service, "end-point")[f"{module}:bar"] = 1
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        error = _error(resp)
        assert error["error-tag"] == "unknown-namespace"
        assert error["error-info"] == {"bad-element": f"{module}:bar", "bad-namespace": module}
        assert error["error-path"] == _EP
        _nothing_created(app)

    @pytest.mark.parametrize(("place", "member"), [
        ("service", "tapi-connectivity:name"),
        ("end-point", "tapi-connectivity:service-interface-point"),
        ("otsi-config", "tapi-photonic-media:modulation"),
    ])
    def test_a_member_qualified_where_the_module_does_not_change(
        self, app: TestClient, place: str, member: str,
    ) -> None:
        """The node exists, but RFC 7951 §4 says "the simple form of the
        member name MUST be used": the body does not follow its encoding."""
        service = _service(app)
        node = _node(service, place)
        node[member] = node.pop(member.split(":")[1])
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 400
        error = _error(resp)
        assert (error["error-type"], error["error-tag"]) == ("rpc", "malformed-message")
        assert error["error-path"] == PLACES[place]
        assert "simple form" in error["error-message"]
        _nothing_created(app)


class TestStandardMembersTheTwinDoesNotImplement:
    """Defined, configuration, not acted on: 501, as before, and now for
    every such member of the YANG, not a hand-kept list."""

    @pytest.mark.parametrize(("place", "member", "named"), [
        ("service", "tapi-oam:connectivity-oam-service", "tapi-oam:connectivity-oam-service"),
        ("mcg-spec", "mc-bandwidth-config-pac", "mc-bandwidth-config-pac"),
        ("mc-spectrum-config-pac", "power-management-config-pac",
         "mc-spectrum-config-pac[mc]/power-management-config-pac"),
        ("modulation", "proprietary-modulation-technique",
         "modulation/proprietary-modulation-technique"),
        ("otsi-config", "otsi-threshold-power-config", "otsi-config[1]/otsi-threshold-power-config"),
    ])
    def test_501(self, app: TestClient, place: str, member: str, named: str) -> None:
        service = _service(app)
        _node(service, place)[member] = {}
        resp = app.post(_BASE, json={_KEY: [service]})
        assert resp.status_code == 501, resp.text
        assert _error(resp)["error-tag"] == "operation-not-supported"
        assert named in _error(resp)["error-message"]
        _nothing_created(app)


class TestConfigFalseMembersAreIgnored:
    def test_state_members_do_not_refuse_the_request(self, app: TestClient) -> None:
        """operational-state, connection and the end-point's csep-role and
        connection-end-point are state the twin reports, not configuration."""
        service = _service(app)
        service["operational-state"] = "DISABLED"
        service["connection"] = [{"connection-uuid": "c"}]
        ep = _node(service, "end-point")
        ep["csep-role"] = [{"role-name": "r"}]
        ep["operational-state"] = "DISABLED"
        resp = app.post(_BASE, json={_KEY: [copy.deepcopy(service)]})
        assert resp.status_code == 201, resp.text
        (got,) = resp.json()[_KEY]
        assert got["operational-state"] == "ENABLED"
        assert "connection" not in got and "csep-role" not in got["end-point"][0]
