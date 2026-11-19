"""The RESTCONF protocol's own resources (RFC 8040 §3.3, §9, §10).

GET {root}/data                                                 the datastore
GET {root}/data/ietf-yang-library:yang-library                  RFC 8525
GET {root}/data/ietf-yang-library:modules-state                 RFC 7895, deprecated in RFC 8525
GET {root}/data/ietf-restconf-monitoring:restconf-state         RFC 8040 §9
GET {root}/data/ietf-restconf-monitoring:restconf-state/capabilities
GET {root}/operations                                           RFC 8040 §3.3.2

Not T-API, and not proprietary either: these are the IETF modules every
RESTCONF server implements, which is why they live here rather than in the
T-API routers (CLAUDE.md, hard constraint 1). Nothing here is writable; the
datastore's POST, PUT and PATCH answer 405 (docs/TAPI_COMPLIANCE.md records
it).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request

from twinlight.api.common import get_context

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
