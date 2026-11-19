"""The RESTCONF protocol's own resources (RFC 8040 §3.3, §9, §10).

GET {root}/data                                                 the datastore
PUT {root}/data                                                 replace its contents
GET {root}/data/ietf-yang-library:yang-library                  RFC 8525
GET {root}/data/ietf-yang-library:modules-state                 RFC 7895, deprecated in RFC 8525
GET {root}/data/ietf-restconf-monitoring:restconf-state         RFC 8040 §9
GET {root}/data/ietf-restconf-monitoring:restconf-state/capabilities
GET {root}/operations                                           RFC 8040 §3.3.2

Not T-API, and not proprietary either: these are the IETF modules every
RESTCONF server implements, which is why they live here rather than in the
T-API routers (CLAUDE.md, hard constraint 1). The datastore takes PUT, which
replaces the T-API context in it; its POST and PATCH answer 405, and the IETF
modules' data is state (docs/TAPI_COMPLIANCE.md records both).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request, Response, status

from twinlight.api.common import get_context, replace_context
from twinlight.api.restconf import RestconfError, target_member
from twinlight.models.tapi_schema_tree import MODULES

router = APIRouter(tags=["restconf"])

#: The revision of ietf-yang-library the twin implements, which the API
#: resource's yang-library-version reports (RFC 8040 §3.3.3).
YANG_LIBRARY_REVISION = "2019-01-04"

_TAPI_REVISION = "2025-03-31"  # every T-API v2.6.0 module but tapi-notification


def _tapi(name: str, revision: str = _TAPI_REVISION) -> dict[str, Any]:
    return {"name": name, "revision": revision, "namespace": f"urn:onf:otcc:yang:{name}"}


def _ietf(name: str, revision: str) -> dict[str, Any]:
    return {"name": name, "revision": revision, "namespace": f"urn:ietf:params:xml:ns:yang:{name}"}


#: Modules whose data nodes the twin serves (conformance-type "implement").
#: tapi-photonic-media augments the SIP and the connectivity-service
#: end-point; tapi-gnmi-streaming defines the measurement records of the
#: gNMI stream and the stream-type-content identity the twin advertises.
#: tapi-streaming's data nodes are under its feature "tapi-streaming", so the
#: feature is listed (RFC 7950 §7.20.1: without it they do not exist).
IMPLEMENTED: list[dict[str, Any]] = [
    _tapi("tapi-common"),
    _tapi("tapi-topology"),
    _tapi("tapi-connectivity"),
    _tapi("tapi-path-computation"),
    _tapi("tapi-equipment"),
    _tapi("tapi-oam"),
    {**_tapi("tapi-streaming"), "feature": ["tapi-streaming"]},
    _tapi("tapi-gnmi-streaming"),
    _tapi("tapi-photonic-media"),
    _ietf("ietf-yang-library", YANG_LIBRARY_REVISION),
    _ietf("ietf-restconf-monitoring", "2017-01-26"),
]

#: Modules used only for their imports (conformance-type "import").
#: tapi-notification is imported by most T-API modules; the twin offers no
#: notification service (docs/TAPI_COMPLIANCE.md § 2.1).
IMPORT_ONLY: list[dict[str, Any]] = [
    _tapi("tapi-notification", "2025-05-31"),
    _ietf("ietf-restconf", "2017-01-26"),
    _ietf("ietf-datastores", "2018-02-14"),
    _ietf("ietf-yang-types", "2013-07-15"),
    _ietf("ietf-inet-types", "2013-07-15"),
]

_MODULE_SET = "twinlight"
_SCHEMA = "twinlight-schema"

#: Changes whenever the module lists do, as RFC 8525's content-id and RFC
#: 7895's module-set-id must; they never change while the twin runs.
CONTENT_ID = hashlib.sha1(
    json.dumps([IMPLEMENTED, IMPORT_ONLY], sort_keys=True).encode()
).hexdigest()


def yang_library() -> dict[str, Any]:
    """``ietf-yang-library:yang-library`` (RFC 8525 §3).

    One module set, one schema, used by the conventional ``running`` and the
    ``operational`` datastore alike: the twin keeps no separate candidate or
    startup T-API configuration.
    """
    return {
        "module-set": [{
            "name": _MODULE_SET,
            "module": IMPLEMENTED,
            "import-only-module": IMPORT_ONLY,
        }],
        "schema": [{"name": _SCHEMA, "module-set": [_MODULE_SET]}],
        "datastore": [
            {"name": "ietf-datastores:running", "schema": _SCHEMA},
            {"name": "ietf-datastores:operational", "schema": _SCHEMA},
        ],
        "content-id": CONTENT_ID,
    }


def modules_state() -> dict[str, Any]:
    """``ietf-yang-library:modules-state``: the RFC 7895 list RFC 8040 §10.1
    names ("There MUST be an instance of this list for every YANG module
    that is used by the server"), kept by RFC 8525 for older clients."""
    return {
        "module-set-id": CONTENT_ID,
        "module": [
            *({**m, "conformance-type": "implement"} for m in IMPLEMENTED),
            *({**m, "conformance-type": "import"} for m in IMPORT_ONLY),
        ],
    }


#: RFC 8040 §9.1.2: the "defaults" capability is mandatory and names the
#: server's basic-mode. The twin reports the leaves it or the client set and
#: omits defaults nobody set, which is RFC 6243's "explicit". It supports no
#: optional query parameter, so §9.1.1 adds no further capability.
CAPABILITIES = ["urn:ietf:params:restconf:capability:defaults:1.0?basic-mode=explicit"]


def restconf_state() -> dict[str, Any]:
    """``ietf-restconf-monitoring:restconf-state`` (RFC 8040 §9). No
    ``streams``: the twin offers no RESTCONF event stream (§9.2 lets a
    server omit the container)."""
    return {"capabilities": {"capability": CAPABILITIES}}


@router.get("/data")
async def get_datastore(request: Request) -> dict:
    """The datastore resource (RFC 8040 §3.4): "the datastore and its
    contents ... represented by a node named 'data' in the 'ietf-restconf'
    module namespace", each top-level node module-qualified."""
    context = await get_context(request)
    return {
        "ietf-restconf:data": {
            **context,
            "ietf-yang-library:yang-library": yang_library(),
            "ietf-yang-library:modules-state": modules_state(),
            "ietf-restconf-monitoring:restconf-state": restconf_state(),
        }
    }


#: The datastore's top-level members besides the T-API context: config false
#: in their modules (RFC 8525, RFC 7895, RFC 8040 §9), so a PUT ignores them.
_IETF_STATE = frozenset({
    "ietf-yang-library:yang-library",
    "ietf-yang-library:modules-state",
    "ietf-restconf-monitoring:restconf-state",
})


@router.put("/data", status_code=status.HTTP_204_NO_CONTENT)
async def put_datastore(body: dict, request: Request) -> Response:
    """Replace the datastore's contents (RFC 8040 §4.5: "A PUT on the
    datastore resource is used to replace the entire contents of the
    datastore"; Appendix B.2.4: "Any child nodes not present in the <data>
    element but present in the server will be deleted").

    Its one configuration is the T-API context, replaced as a PUT of
    ``tapi-common:context`` replaces it (``common.replace_context``): the
    services and path-comp-services the body lists, all or nothing, with the
    configuration the twin derives repeated unchanged. A body without the
    context would delete it, which the twin refuses (403). The IETF state
    members a GET also returns are ignored.
    """
    data = target_member(body, "ietf-restconf:data")
    if not isinstance(data, dict):
        raise RestconfError(400, "bad-element", "ietf-restconf:data must be a JSON object",
                            error_path="/")
    for member in data:
        if member == "tapi-common:context" or member in _IETF_STATE:
            continue
        prefix, _, _ = member.rpartition(":")
        known = prefix in MODULES or prefix in ("ietf-yang-library", "ietf-restconf-monitoring")
        raise RestconfError(
            400, "unknown-element" if known or not prefix else "unknown-namespace",
            f"{member!r} is not a top-level data node of this server",
            error_path="/",
            error_info=(
                {"bad-element": member} if known or not prefix
                else {"bad-element": member, "bad-namespace": prefix}
            ),
        )
    await replace_context(request, data.get("tapi-common:context", {}))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/data/ietf-yang-library:yang-library")
async def get_yang_library() -> dict:
    return {"ietf-yang-library:yang-library": yang_library()}


@router.get("/data/ietf-yang-library:modules-state")
async def get_modules_state() -> dict:
    return {"ietf-yang-library:modules-state": modules_state()}


@router.get("/data/ietf-restconf-monitoring:restconf-state")
async def get_restconf_state() -> dict:
    return {"ietf-restconf-monitoring:restconf-state": restconf_state()}


@router.get("/data/ietf-restconf-monitoring:restconf-state/capabilities")
async def get_capabilities() -> dict:
    return {"ietf-restconf-monitoring:capabilities": restconf_state()["capabilities"]}


#: Served only under a non-empty root, beside the API resource it belongs to.
operations_router = APIRouter(tags=["restconf"])


@operations_router.get("/operations")
async def get_operations() -> dict:
    """RFC 8040 §3.3.2: the twin's modules define no RPC, so the container
    is empty; it exists because the API resource lists it."""
    return {"ietf-restconf:operations": {}}
