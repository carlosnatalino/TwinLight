"""RFC 7951 §6.1: 64-bit numbers are JSON strings, everything else is not.

    "A value of the "int8", "int16", "int32", "uint8", "uint16", or "uint32"
    type is represented as a JSON number.

    A value of the "int64", "uint64", or "decimal64" type is represented as
    a JSON string whose content is the lexical representation of the
    corresponding YANG type as specified in Sections 9.2.1 and 9.3.1 of
    [RFC7950]."

The walk below reads every T-API body a populated twin returns — every GET
the OpenAPI schema lists, for every entry, plus the 201 of each creating
POST and PUT and the 200 of a PATCH — on both physics backends, and checks
every leaf value against ``tests/fixtures/tapi_v2.6.0_numeric_leaves.json``,
the table of all 542 numeric leaves of the T-API v2.6.0 data tree derived
from the YANG with pyang (see the fixture's ``$comment``). It is independent
of the twin's own list (``TAPI_64BIT_LEAVES``): a leaf the twin starts to emit
with the wrong encoding fails here, whichever list it is missing from.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FIXTURES, MeshRefs, _mesh_config, tapi_end_point
from twinlight.app import create_app
from twinlight.models.connectivity import MCG_CSEP_SPEC, OTSIA_CSEP_SPEC
from twinlight.models.yang_numbers import TAPI_64BIT_LEAVES

DATA = "/restconf/data"
CC = f"{DATA}/tapi-common:context/tapi-connectivity:connectivity-context"
PC = f"{DATA}/tapi-common:context/tapi-path-computation:path-computation-context"
SVC = "tapi-connectivity:connectivity-service"


def _flatten(tree: dict[str, Any], prefix: str = "") -> Iterator[tuple[str, str]]:
    for name, child in tree.items():
        path = f"{prefix}/{name}"
        if isinstance(child, dict):
            yield from _flatten(child, path)
        else:
            yield path, child


NUMERIC_LEAVES: dict[str, str] = dict(
    _flatten(json.loads((FIXTURES / "tapi_v2.6.0_numeric_leaves.json").read_text())["leaves"])
)

# Ranges of the YANG built-in types (RFC 7950 §9.2).
_RANGES = {
    "int8": (-(2**7), 2**7 - 1), "int16": (-(2**15), 2**15 - 1),
    "int32": (-(2**31), 2**31 - 1), "int64": (-(2**63), 2**63 - 1),
    "uint8": (0, 2**8 - 1), "uint16": (0, 2**16 - 1),
    "uint32": (0, 2**32 - 1), "uint64": (0, 2**64 - 1),
}
# Canonical forms, RFC 7950 §9.2.2 ("does not include the sign "+". Leading
# zeros are prohibited.") and §9.3.2 ("The decimal point is required. Leading
# and trailing zeros are prohibited ... zero is represented as "0.0"").
_CANONICAL_INTEGER = re.compile(r"0|-?[1-9][0-9]*")
_CANONICAL_DECIMAL = re.compile(r"-?(0|[1-9][0-9]*)\.(0|[0-9]*[1-9])")


def test_the_table_is_what_the_yang_says() -> None:
    """Spot checks of the derived table against the modules' text."""
    assert len(NUMERIC_LEAVES) == 542
    # T-API v2.6.0 types every numeric leaf as a 64-bit one.
    assert set(NUMERIC_LEAVES.values()) == {"uint64", "int64", "decimal64:7"}
    sip_bands = (
        "/tapi-common:context/service-interface-point/tapi-photonic-media:"
        "photonic-media-service-interface-point-spec/spectrum-capability-pac/"
    )
    otsia = (
        "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service/"
        "end-point/layer-protocol-constraint/tapi-photonic-media:"
        "otsia-connectivity-service-end-point-spec/"
    )
    # tapi-photonic-media.yang, grouping spectrum-band: "leaf upper-frequency {
    # type uint64;"; grouping otsi-config-pac: central-frequency uint64,
    # channel-output-power decimal64 { fraction-digits 7; }.
    assert NUMERIC_LEAVES[sip_bands + "available-spectrum/upper-frequency"] == "uint64"
    assert NUMERIC_LEAVES[otsia + "otsi-config/central-frequency"] == "uint64"
    assert NUMERIC_LEAVES[otsia + "otsi-config/channel-output-power"] == "decimal64:7"
    assert NUMERIC_LEAVES[otsia + "number-of-otsi"] == "uint64"
    # Every leaf the twin encodes is one the YANG types as 64-bit.
    for path, kind in TAPI_64BIT_LEAVES.items():
        assert NUMERIC_LEAVES[path].split(":")[0] == kind, path


def check_leaf(path: str, value: Any) -> str | None:
    """Why ``value`` is a wrong RFC 7951 encoding for the leaf at ``path``."""
    kind = NUMERIC_LEAVES.get(path)
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind is None:
        return f"{path}: JSON number {value!r} for a leaf with no numeric type" if number else None
    base, _, digits = kind.partition(":")
    if base in ("int64", "uint64", "decimal64"):
        if not isinstance(value, str):
            return f"{path}: {base} must be a JSON string, got {value!r}"
        if base == "decimal64":
            match = _CANONICAL_DECIMAL.fullmatch(value)
            if not match or value == "-0.0":
                return f"{path}: {value!r} is not a canonical decimal64"
            if match.group(2) != "0" and len(match.group(2)) > int(digits):
                return f"{path}: {value!r} has more than {digits} fraction digits"
            steps = int(value.replace(".", "") + "0" * (int(digits) - len(match.group(2))))
            low, high = _RANGES["int64"]
            return None if low <= steps <= high else f"{path}: {value!r} out of range"
        if not _CANONICAL_INTEGER.fullmatch(value):
            return f"{path}: {value!r} is not a canonical {base}"
        low, high = _RANGES[base]
        return None if low <= int(value) <= high else f"{path}: {value!r} out of range"
    # int8 ... uint32 (none in T-API v2.6.0, kept for a module that has them).
    if not number or isinstance(value, float):
        return f"{path}: {base} must be a JSON number, got {value!r}"
    low, high = _RANGES[base]
    return None if low <= value <= high else f"{path}: {value!r} out of range"


def walk(value: Any, path: str, found: list[str], seen: set[str]) -> None:
    """Check every leaf under ``value``, whose schema path is ``path``."""
    if isinstance(value, dict):
        for key, child in value.items():
            walk(child, f"{path}/{key}", found, seen)
        return
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, (dict, list)):
            walk(item, path, found, seen)
            continue
        if NUMERIC_LEAVES.get(path, "").split(":")[0] in ("int64", "uint64", "decimal64"):
            seen.add(path)
        problem = check_leaf(path, item)
        if problem:
            found.append(problem)


def _module(segment: str, current: str) -> str:
    return segment.split(":")[0] if ":" in segment else current


def body_path(url: str, member: str) -> str:
    """The schema path of a body's top-level ``member`` returned for ``url``.

    The body of a GET, PUT or PATCH is its target: the member names the
    URL's last node. A creating POST's 201 holds a child of the target.
    Paths are spelled as RFC 7951 names members, which is also how RFC 8040
    §3.5.3 qualifies URL segments: with the module where it changes.
    """
    segments = [s.split("=")[0] for s in url[len(DATA):].split("/") if s]
    module = ""
    for segment in segments:
        module = _module(segment, module)
    member_module, _, member_name = member.rpartition(":")
    if segments and segments[-1].split(":")[-1] == member_name:
        return "/" + "/".join(segments)
    child = member_name if member_module == module else member
    return "/" + "/".join([*segments, child])


def check_body(url: str, body: Any, found: list[str], seen: set[str]) -> None:
    assert isinstance(body, dict), (url, body)
    for member, value in body.items():
        if member == "ietf-restconf:data":  # the datastore: every top-level node
            for top, top_value in value.items():
                walk(top_value, f"/{top}", found, seen)
        elif url.startswith(DATA):
            walk(value, body_path(url, member), found, seen)
        else:  # {+restconf}, operations, yang-library-version
            walk(value, f"/{member}", found, seen)


def _expand(template: str, context: dict[str, Any]) -> list[str]:
    """Every URL of a templated data path, keyed by the entries in ``context``."""
    tail = template[len(f"{DATA}/tapi-common:context"):].strip("/").split("/")
    urls: list[str] = []

    def go(node: Any, segments: list[str], url: str) -> None:
        if not segments:
            urls.append(url)
            return
        name, _, key = segments[0].partition("=")
        child = node.get(name) if isinstance(node, dict) else None
        if not key:
            go(child if child is not None else {}, segments[1:], f"{url}/{name}")
            return
        for entry in child or []:
            go(entry, segments[1:], f"{url}/{name}={entry['uuid']}")

    go(context, [s for s in tail if s], f"{DATA}/tapi-common:context")
    return urls


def _populate(app: TestClient) -> list[tuple[str, Any]]:
    """Services (one with every channel leaf), a PATCH and a path-comp-service.

    Returns the write responses' bodies as ``(url, body)``.
    """
    refs = MeshRefs(app.app.state.context)
    bodies: list[tuple[str, Any]] = []
    r = app.post(CC, json={SVC: [{
        "name": [{"value-name": "service-name", "value": "plain"}],
        "end-point": [tapi_end_point("a", refs.sip("trx A")), tapi_end_point("z", refs.sip("trx C"))],
        "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY",
                               "is-exclusive": True},
        "topology-constraint": [{"local-id": "1", "exclude-node": [refs.node("roadm E")]}],
    }]})
    assert r.status_code == 201, r.text
    bodies.append((CC, r.json()))
    r = app.post(CC, json={SVC: [{"end-point": [
        {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx B")}},
        {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx C")}},
    ]}]})
    assert r.status_code == 201, r.text
    bodies.append((CC, r.json()))
    second = r.json()[SVC][0]["uuid"]
    # Every channel leaf the twin acts on, in the standard spelling, with a
    # launch power that needs the fraction digits: -2.345 dBm.
    lower, upper = app.app.state.context._slot_range_hz(120, 9)
    uuid = "11111111-2222-3333-4444-555555555555"
    end_points = []
    for local_id, trx in (("a", "trx A"), ("z", "trx B")):
        ep = tapi_end_point(local_id, refs.sip(trx))
        constraint = ep["layer-protocol-constraint"][0]
        constraint[OTSIA_CSEP_SPEC]["number-of-otsi"] = "1"
        constraint[OTSIA_CSEP_SPEC]["otsi-config"][0].update({
            "central-frequency": str((lower + upper) // 2), "channel-output-power": "-234.5",
        })
        constraint[MCG_CSEP_SPEC] = {"number-of-mc": "1", "mc-spectrum-config-pac": [{
            "local-id": "1",
            "spectrum": {"lower-frequency": str(lower), "upper-frequency": str(upper)}}]}
        end_points.append(ep)
    r = app.put(f"{CC}/connectivity-service={uuid}", json={SVC: [{"uuid": uuid, "end-point": end_points}]})
    assert r.status_code == 201, r.text
    bodies.append((f"{CC}/connectivity-service={uuid}", r.json()))
    r = app.patch(f"{CC}/connectivity-service={second}", json={SVC: [{
        "name": [{"value-name": "service-name", "value": "patched"}],
        "administrative-state": "LOCKED"}]})
    assert r.status_code == 200, r.text
    bodies.append((f"{CC}/connectivity-service={second}", r.json()))
    r = app.post(PC, json={"tapi-path-computation:path-comp-service": [{
        "end-point": [
            {"local-id": "a", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx A")}},
            {"local-id": "z", "service-interface-point": {"service-interface-point-uuid": refs.sip("trx B")}},
        ],
        "routing-constraint": {"route-objective-function": "MIN_WORK_ROUTE_LATENCY"},
    }]})
    assert r.status_code == 201, r.text
    bodies.append((PC, r.json()))
    return bodies


@pytest.mark.parametrize("backend", ["gnpy", "egn"])
def test_every_tapi_body_encodes_numbers_as_the_yang_types_them(
    tmp_path: Path, backend: str,
) -> None:
    app = TestClient(create_app(_mesh_config(tmp_path, backend)))
    bodies = _populate(app)
    context = app.get(f"{DATA}/tapi-common:context").json()["tapi-common:context"]
    assert context["tapi-oam:oam-context"]["oam-job"], "no OAM job to walk"

    templates = [
        path for path, ops in app.get("/openapi.json").json()["paths"].items()
        if path.startswith("/restconf") and "get" in ops
    ]
    for template in templates:
        urls = _expand(template, context) if "{" in template else [template]
        assert urls, f"no instance of {template} to walk"
        for url in urls:
            r = app.get(url)
            assert r.status_code == 200, (url, r.text)
            bodies.append((url, r.json()))

    found: list[str] = []
    seen: set[str] = set()
    for url, body in bodies:
        check_body(url, body, found, seen)
    assert not found, "\n".join(sorted(set(found)))
    # The walk reached every 64-bit leaf the twin emits, and no other.
    assert seen == set(TAPI_64BIT_LEAVES)


def test_the_walk_catches_a_number(tmp_path: Path) -> None:
    """The checker itself: a uint64 as a JSON number, a non-canonical string
    and a number in a string leaf are each reported."""
    lpc = (
        "/tapi-common:context/tapi-connectivity:connectivity-context/"
        "connectivity-service/end-point/layer-protocol-constraint"
    )
    otsi = f"{lpc}/{OTSIA_CSEP_SPEC}/otsi-config"
    assert check_leaf(f"{otsi}/central-frequency", 191278125000000)
    assert check_leaf(f"{otsi}/central-frequency", "191278125000000.0")
    assert check_leaf(f"{otsi}/central-frequency", "+191278125000000")
    assert check_leaf(f"{otsi}/central-frequency", "0191278125000000")
    assert check_leaf(f"{otsi}/central-frequency", "18446744073709551616")
    assert check_leaf(f"{otsi}/central-frequency", "191278125000000") is None
    assert check_leaf(f"{otsi}/channel-output-power", 150.0)
    assert check_leaf(f"{otsi}/channel-output-power", "150")
    assert check_leaf(f"{otsi}/channel-output-power", "150.50")
    assert check_leaf(f"{otsi}/channel-output-power", "-0.0")
    assert check_leaf(f"{otsi}/channel-output-power", "1.00000001")
    assert check_leaf(f"{otsi}/channel-output-power", "150.5") is None
    assert check_leaf(f"{otsi}/channel-output-power", "0.0") is None
    assert check_leaf(f"{otsi}/local-id", 1)
    assert check_leaf(f"{otsi}/local-id", "1") is None
    assert check_leaf("/tapi-common:context/uuid", True) is None
    assert body_path(f"{CC}/connectivity-service=x", SVC) == (
        "/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service"
    )
    assert body_path(CC, SVC) == body_path(f"{CC}/connectivity-service", SVC)


# ---------------------------------------------------------------------------
# Values on the wire
# ---------------------------------------------------------------------------


@pytest.fixture
def mesh(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_mesh_config(tmp_path, "egn")))


@pytest.fixture(scope="module")
def refusing(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """One twin for the refusal tests: a refused request changes nothing."""
    return TestClient(create_app(_mesh_config(tmp_path_factory.mktemp("refusing"), "egn")))


def _channel_body(app: TestClient, *, uuid: str | None = None, **leaves: Any) -> dict:
    """A request carrying the channel leaves given, at slot block 120."""
    refs = MeshRefs(app.app.state.context)
    lower, upper = app.app.state.context._slot_range_hz(120, 9)
    values = {
        "central-frequency": (lower + upper) // 2, "channel-output-power": 150,
        "lower-frequency": lower, "upper-frequency": upper,
        "number-of-mc": 1, "number-of-otsi": 1,
    }
    values.update({k.replace("_", "-"): v for k, v in leaves.items()})
    end_points = []
    for local_id, trx in (("a", "trx A"), ("z", "trx C")):
        ep = tapi_end_point(local_id, refs.sip(trx))
        constraint = ep["layer-protocol-constraint"][0]
        constraint[OTSIA_CSEP_SPEC]["number-of-otsi"] = values["number-of-otsi"]
        constraint[OTSIA_CSEP_SPEC]["otsi-config"][0].update({
            "central-frequency": values["central-frequency"],
            "channel-output-power": values["channel-output-power"],
        })
        constraint[MCG_CSEP_SPEC] = {"number-of-mc": values["number-of-mc"], "mc-spectrum-config-pac": [{
            "local-id": "1", "spectrum": {
                "lower-frequency": values["lower-frequency"],
                "upper-frequency": values["upper-frequency"]}}]}
        end_points.append(ep)
    entry: dict[str, Any] = {"end-point": end_points}
    if uuid is not None:
        entry["uuid"] = uuid
    return {SVC: [entry]}


def _channel(service: dict) -> dict[str, Any]:
    constraint = service["end-point"][0]["layer-protocol-constraint"][0]
    otsia, mcg = constraint[OTSIA_CSEP_SPEC], constraint[MCG_CSEP_SPEC]
    band = mcg["mc-spectrum-config-pac"][0]["spectrum"]
    return {
        "central-frequency": otsia["otsi-config"][0]["central-frequency"],
        "channel-output-power": otsia["otsi-config"][0]["channel-output-power"],
        "number-of-otsi": otsia["number-of-otsi"],
        "number-of-mc": mcg["number-of-mc"],
        "lower-frequency": band["lower-frequency"],
        "upper-frequency": band["upper-frequency"],
    }


def _expected(app: TestClient) -> dict[str, str]:
    lower, upper = app.app.state.context._slot_range_hz(120, 9)
    return {
        "central-frequency": str((lower + upper) // 2), "channel-output-power": "150.0",
        "number-of-otsi": "1", "number-of-mc": "1",
        "lower-frequency": str(lower), "upper-frequency": str(upper),
    }


def test_a_frequency_reads_as_its_decimal_string(mesh: TestClient) -> None:
    """The task's example: 191278125000000 Hz is "191278125000000"."""
    resp = mesh.get(f"{DATA}/tapi-common:context/service-interface-point")
    sip = resp.json()["tapi-common:service-interface-point"][0]
    pac = sip["tapi-photonic-media:photonic-media-service-interface-point-spec"][
        "spectrum-capability-pac"]
    band = pac["supportable-spectrum"][0]
    assert band == {
        "upper-frequency": "195496875000000",
        "lower-frequency": "190696875000000",
        "frequency-constraint": band["frequency-constraint"],
    }
    assert '"lower-frequency":"190696875000000"' in resp.text


def _as_strings(app: TestClient) -> dict[str, Any]:
    exp = _expected(app)
    return {**{k: v for k, v in exp.items() if k != "channel-output-power"},
            "channel-output-power": "150"}


@pytest.mark.parametrize("spelling", ["string", "number"])
@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_both_spellings_are_accepted(mesh: TestClient, spelling: str, method: str) -> None:
    """The standard string, and the JSON number as a documented leniency.
    The answer is the strings either way. (PATCH merges only name and the
    states, so no 64-bit leaf is writable there; see the PATCH test.)"""
    leaves = {k.replace("-", "_"): v for k, v in _as_strings(mesh).items()} if spelling == "string" else {}
    uuid = "22222222-2222-3333-4444-555555555555"
    body = _channel_body(mesh, uuid=uuid, **leaves)
    if method == "POST":
        resp = mesh.post(CC, json=body)
    else:
        resp = mesh.put(f"{CC}/connectivity-service={uuid}", json=body)
    assert resp.status_code == 201, resp.text
    assert _channel(resp.json()[SVC][0]) == _expected(mesh)
    # A replacing PUT (204) takes the same spelling.
    resp = mesh.put(f"{CC}/connectivity-service={uuid}", json=body)
    assert resp.status_code == 204, resp.text
    got = mesh.get(f"{CC}/connectivity-service={uuid}").json()[SVC][0]
    assert _channel(got) == _expected(mesh)


def test_a_get_body_goes_back_as_a_put(mesh: TestClient) -> None:
    """The strings a GET returns are a valid PUT body, unchanged."""
    resp = mesh.post(CC, json=_channel_body(mesh))
    assert resp.status_code == 201, resp.text
    service = resp.json()[SVC][0]
    url = f"{CC}/connectivity-service={service['uuid']}"
    before = mesh.get(url).json()
    resp = mesh.put(url, json=before)
    assert resp.status_code == 204, resp.text
    assert _channel(mesh.get(url).json()[SVC][0]) == _channel(before[SVC][0])


def test_a_patch_answers_with_strings(mesh: TestClient) -> None:
    resp = mesh.post(CC, json=_channel_body(mesh))
    uuid = resp.json()[SVC][0]["uuid"]
    resp = mesh.patch(f"{CC}/connectivity-service={uuid}",
                      json={SVC: [{"administrative-state": "LOCKED"}]})
    assert resp.status_code == 200, resp.text
    assert _channel(resp.json()[SVC][0]) == _expected(mesh)


@pytest.mark.parametrize("precise", [("-234.5", "-234.5"), ("-234.5000000", "-234.5"),
                                     ("+100", "100.0"), (-234.5, "-234.5"), ("0", "0.0")])
def test_channel_output_power_is_canonical(mesh: TestClient, precise: tuple[Any, str]) -> None:
    sent, shown = precise
    resp = mesh.post(CC, json=_channel_body(mesh, channel_output_power=sent))
    assert resp.status_code == 201, resp.text
    assert _channel(resp.json()[SVC][0])["channel-output-power"] == shown


_UINT64_REFUSED = [
    pytest.param(1.0, id="float"),
    pytest.param(191306250000000.0, id="integral-float"),
    pytest.param("1e14", id="exponent"),
    pytest.param("191306250000000.0", id="fraction"),
    pytest.param("", id="empty"),
    pytest.param(" 191306250000000", id="blank"),
    pytest.param("0x10", id="hex"),
    pytest.param("１", id="non-ascii-digit"),
    pytest.param(True, id="boolean"),
    pytest.param(-1, id="negative"),
    pytest.param("-1", id="negative-string"),
    pytest.param(2**64, id="too-large"),
    pytest.param("18446744073709551616", id="too-large-string"),
    pytest.param([1], id="list"),
]
_DECIMAL64_REFUSED = [
    pytest.param("1e2", id="exponent"),
    pytest.param("", id="empty"),
    pytest.param("150.", id="no-fraction-digit"),
    pytest.param(".5", id="no-integer-digit"),
    pytest.param("150.12345678", id="eight-fraction-digits"),
    pytest.param(150.12345678, id="eight-fraction-digits-number"),
    pytest.param("NaN", id="nan"),
    pytest.param("Infinity", id="infinity"),
    pytest.param("1_0", id="underscore"),
    pytest.param(True, id="boolean"),
    pytest.param("922337203685.4775808", id="too-large"),
    pytest.param(10**300, id="too-large-number"),
    pytest.param({"v": 1}, id="object"),
]


def _refused(resp) -> None:
    assert resp.status_code == 400, resp.text
    error = resp.json()["ietf-restconf:errors"]["error"][0]
    assert error["error-tag"] == "invalid-value", error


@pytest.mark.parametrize("leaf", ["central-frequency", "lower-frequency", "number-of-mc",
                                  "number-of-otsi"])
@pytest.mark.parametrize("value", _UINT64_REFUSED)
@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_a_bad_uint64_is_invalid_value(
    refusing: TestClient, leaf: str, value: Any, method: str,
) -> None:
    uuid = "33333333-2222-3333-4444-555555555555"
    body = _channel_body(refusing, uuid=uuid, **{leaf.replace("-", "_"): value})
    if method == "POST":
        _refused(refusing.post(CC, json=body))
    else:
        _refused(refusing.put(f"{CC}/connectivity-service={uuid}", json=body))
    assert refusing.get(f"{CC}/connectivity-service").json()[SVC] == []


@pytest.mark.parametrize("value", _DECIMAL64_REFUSED)
@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_a_bad_decimal64_is_invalid_value(refusing: TestClient, value: Any, method: str) -> None:
    uuid = "44444444-2222-3333-4444-555555555555"
    body = _channel_body(refusing, uuid=uuid, channel_output_power=value)
    if method == "POST":
        _refused(refusing.post(CC, json=body))
    else:
        _refused(refusing.put(f"{CC}/connectivity-service={uuid}", json=body))
    assert refusing.get(f"{CC}/connectivity-service").json()[SVC] == []


@pytest.mark.parametrize("value", ["2", 2, "+2"])
def test_an_unsupported_count_is_not_implemented(refusing: TestClient, value: Any) -> None:
    """number-of-otsi = 2 is a valid uint64 the twin does not support: 501,
    in either spelling; "1" and 1 are the same supported value."""
    resp = refusing.post(CC, json=_channel_body(refusing, number_of_otsi=value))
    assert resp.status_code == 501, resp.text
    assert "number-of-otsi" in resp.text


def test_snapshots_keep_numbers_and_restore(tmp_path: Path) -> None:
    """The snapshot is the twin's own format, not YANG-JSON: it keeps the
    numbers it always held, so an earlier version's file restores, and the
    restored service reads back as strings."""
    app = TestClient(create_app(_mesh_config(tmp_path, "egn")))
    resp = app.post(CC, json=_channel_body(app))
    assert resp.status_code == 201, resp.text
    path = tmp_path / "snap.json"
    resp = app.post("/admin/snapshot", json={"path": str(path)})
    assert resp.status_code == 200, resp.text
    snapshot = json.loads(path.read_text())
    cfg = snapshot["services"][0]["end-point"][0]["layer-protocol-constraint"][0][
        OTSIA_CSEP_SPEC]["otsi-config"][0]
    assert isinstance(cfg["central-frequency"], int)
    assert isinstance(cfg["channel-output-power"], float)

    other = TestClient(create_app(_mesh_config(tmp_path / "b", "egn")))
    resp = other.post("/admin/restore", json={"path": str(path)})
    assert resp.status_code == 200, resp.text
    services = other.get(f"{CC}/connectivity-service").json()[SVC]
    assert [_channel(s) for s in services] == [_expected(other)]


def test_the_proprietary_views_keep_numbers(mesh: TestClient) -> None:
    """/internal is not YANG-JSON and keeps its numbers."""
    resp = mesh.post(CC, json=_channel_body(mesh))
    uuid = resp.json()[SVC][0]["uuid"]
    info = mesh.get(f"/internal/services/{uuid}").json()
    assert isinstance(info["central-frequency-thz"], float)


def test_copy_is_not_shared(mesh: TestClient) -> None:
    """Both end-points carry the same spectrum, each encoded once."""
    resp = mesh.post(CC, json=_channel_body(mesh))
    service = copy.deepcopy(resp.json()[SVC][0])
    a, z = (_channel({"end-point": [ep]}) for ep in service["end-point"])
    assert a == z == _expected(mesh)


# ---------------------------------------------------------------------------
# The codec (models/yang_numbers.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "text"), [
    (0.0, "0.0"), (-0.0, "0.0"), (0, "0.0"), (150.0, "150.0"), (150, "150.0"),
    (-234.5, "-234.5"), (1e-7, "0.0000001"), (0.1 + 0.2, "0.3"), (12.34567891, "12.3456789"),
    (Decimal("-0.50"), "-0.5"), (922337203685.4775807, "922337203685.4775"),
])
def test_decimal64_canonical_form(value: Any, text: str) -> None:
    from twinlight.models.yang_numbers import format_decimal64

    assert format_decimal64(value, 7) == text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "1.0", 10.0**12])
def test_decimal64_refuses_what_is_not_one(value: Any) -> None:
    from twinlight.models.yang_numbers import format_decimal64

    with pytest.raises(TypeError):
        format_decimal64(value, 7)


@pytest.mark.parametrize("value", [1.0, "1", -1, 2**64, True, None])
def test_uint64_refuses_what_is_not_one(value: Any) -> None:
    from twinlight.models.yang_numbers import format_uint64

    with pytest.raises(TypeError):
        format_uint64(value)


def test_parse_reads_the_lexical_forms() -> None:
    from twinlight.models.yang_numbers import parse_decimal64, parse_int64, parse_uint64

    assert parse_uint64("+007", "x") == 7  # RFC 7950 §9.2.1: sign and digits
    assert parse_uint64(str(2**64 - 1), "x") == 2**64 - 1
    assert parse_int64("-9223372036854775808", "x") == -(2**63)
    assert parse_decimal64("-0.0000001", 7, "x") == Decimal("-0.0000001")
    assert parse_decimal64(150, 7, "x") == Decimal(150)
    with pytest.raises(ValueError, match="int64 range"):
        parse_int64(str(2**63), "x")
    with pytest.raises(ValueError, match="fraction"):
        parse_decimal64("0.00000001", 7, "x")


def test_the_encoder_copies_and_refuses_a_non_number() -> None:
    from twinlight.models.yang_numbers import encode_64bit_leaves

    shared = {"lower-frequency": 1, "upper-frequency": 2}
    body = {"a": [{"spectrum": shared}, {"spectrum": shared}], "n": 3}
    leaves = {"/r/a/spectrum/lower-frequency": "uint64", "/r/n": "int64"}
    out = encode_64bit_leaves(body, "/r", leaves)
    assert out == {"a": [{"spectrum": {"lower-frequency": "1", "upper-frequency": 2}}] * 2,
                   "n": "3"}
    assert shared == {"lower-frequency": 1, "upper-frequency": 2}
    with pytest.raises(TypeError, match="/r/n"):
        encode_64bit_leaves({"n": "3"}, "/r", leaves)
