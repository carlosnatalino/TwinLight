"""The transparent optical domain controller (``/transparent``).

A RESTCONF-shaped interface (RFC 8040, with NMDA datastores per RFC 8527,
YANG Patch per RFC 8072 and with-defaults per RFC 6243) onto the twin's
whole physical model, addressed by ns-3-style paths rather than T-API
objects:

====== ============================================ =============================
Method Path                                         Purpose
====== ============================================ =============================
GET    /transparent                                 Root resource (discovery)
GET    /transparent/schema                          Every node and leaf, with
                                                    type, unit, bounds, config
GET    /transparent/data[/<path>]                   Operational view (config +
                                                    state); ``content``,
                                                    ``depth``, ``with-defaults``,
                                                    ``format``
PUT    /transparent/data/<path>                     Replace: set what the body
                                                    names, revert the rest
PATCH  /transparent/data[/<path>]                   Merge, or YANG Patch with
                                                    ``application/yang-patch+json``
DELETE /transparent/data[/<path>]                   Revert to startup values
\\*     /transparent/ds/<datastore>[/<path>]          The same, per datastore:
                                                    running, candidate (writable);
                                                    startup, operational (read)
GET    /transparent/operations                      The RPCs below
POST   /transparent/operations/<rpc>                commit, discard-changes,
                                                    validate, compare, redesign
====== ============================================ =============================

``/data`` writes go straight to *running*; ``/ds/candidate`` stages them for
a later ``commit``. The running datastore's ETag changes with every
configuration change made through *any* interface, and ``If-Match`` makes
a write or commit conditional on it. Writes answer ``204`` unless the
client sends ``Prefer: return=representation`` (RFC 7240), which returns
the applied changes and the services whose baselines they invalidated.

This is a proprietary interface on its own prefix; the T-API routers are
untouched (see docs/ARCHITECTURE.md). The logic lives in
``twinlight/transparent/``; this module is the HTTP skin.
"""

from __future__ import annotations

import json
from email.utils import format_datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from twinlight.api.middleware import RESTCONF_MEDIA_TYPE
from twinlight.transparent.datastore import (
    CONVENTIONAL,
    DATASTORES,
    ChangeError,
    RenderOptions,
    apply_changes,
    build_edit,
    compare,
    datastore_name,
    describe_changes,
    effective_changes,
    preview_changes,
    render_leaves,
    render_tree,
    source_for,
    validate_changes,
)
from twinlight.transparent.path import (
    PathSyntaxError,
    Segment,
    join,
    parse_url_path,
)
from twinlight.transparent.tree import (
    ELEMENT_KINDS,
    LINK_LEAVES,
    MODULATION_LEAVES,
    MODULE,
    QOT_LEAVES,
    SCHEMA_REVISION,
    SERVICE_LEAVES,
    LeafKey,
    TreeBuilder,
    TreeError,
    config_leaf_spec,
    element_leaf_specs,
    resolve,
)

ROOT = "/transparent"
YANG_PATCH_MEDIA_TYPE = "application/yang-patch+json"

router = APIRouter(prefix=ROOT, tags=["transparent"])

OPERATIONS: dict[str, str] = {
    "commit": "Apply the candidate datastore to running, atomically",
    "discard-changes": "Drop every edit staged in the candidate datastore",
    "validate": "Check the candidate (or an inline YANG Patch) against "
                "running; optionally predict each affected service's QoT",
    "compare": "The YANG Patch that turns one datastore into another",
    "redesign": "Re-run the backend's network design (GNPy designed_network)",
}


# -- Plumbing ------------------------------------------------------------------


def _ctx(request: Request) -> Any:
    return request.app.state.context


def _qualified(name: str) -> str:
    return f"{MODULE}:{name}"


def _errors_response(errors: list[TreeError], status: int | None = None) -> JSONResponse:
    body = {"ietf-restconf:errors": {"error": [
        {
            "error-type": "application",
            "error-tag": e.tag,
            **({"error-path": e.path} if e.path else {}),
            "error-message": str(e),
        }
        for e in errors
    ]}}
    return JSONResponse(
        status_code=status or max(e.status for e in errors),
        content=body, media_type=RESTCONF_MEDIA_TYPE,
    )


def _error(message: str, tag: str, status: int) -> JSONResponse:
    return _errors_response([TreeError(message, tag=tag, status=status)])


def _etag(ctx: Any, ds: str) -> str:
    """Entity-tag of a datastore (RFC 8040 §3.4.1.2).

    Running and operational share the running revision — the time-varying
    measurements are not configuration and do not change it. The candidate
    view depends on both its own edits and the running values beneath them.
    """
    if ds == "startup":
        return '"startup"'
    if ds == "candidate":
        return f'"c{ctx.candidate.revision}-r{ctx.revision}"'
    return f'"r{ctx.revision}"'


def _headers(ctx: Any, ds: str) -> dict[str, str]:
    modified = ctx.last_modified
    if ds == "candidate":
        modified = max(modified, ctx.candidate.last_modified)
    return {
        "ETag": _etag(ctx, ds),
        "Last-Modified": format_datetime(modified, usegmt=True),
    }


def _matches_etag(header: str, etag: str) -> bool:
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or etag in tags or etag in [t.removeprefix("W/") for t in tags]


def _precondition(request: Request, ctx: Any, ds: str) -> JSONResponse | None:
    """``If-Match`` on a write: refuse it if the datastore has moved on."""
    header = request.headers.get("if-match")
    if header and not _matches_etag(header, _etag(ctx, ds)):
        return _error(
            f"The {ds} datastore changed since the entity-tag you sent "
            f"(now {_etag(ctx, ds)}); re-read it and retry",
            "operation-failed", 412,
        )
    return None


def _wants_representation(request: Request) -> bool:
    return "return=representation" in request.headers.get("prefer", "").replace(" ", "")


def _segments(request: Request, depth: int) -> list[Segment]:
    """Parse the path below the first ``depth`` URL segments.

    Read from the *undecoded* request path, so that ``%2F`` stays inside its
    segment (a UID such as ``fiber (A → B)-_(1/4)``). The prefix segments
    (``transparent``, ``data`` or ``ds``, the datastore name) never hold an
    encoded slash, so counting literal slashes to skip them is exact.
    """
    raw = request.scope.get("raw_path")
    if isinstance(raw, bytes):
        text = raw.decode("ascii", errors="replace")
    else:  # no raw path: re-encode the decoded one so it parses the same
        from urllib.parse import quote

        text = quote(request.url.path, safe="/")
    parts = text.split("/")
    return parse_url_path("/".join(parts[depth + 1:]))


async def _json_body(request: Request) -> Any:
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TreeError(f"Malformed JSON body: {exc}", tag="malformed-message") from exc


def _retrieval_options(request: Request, ctx: Any, ds: str) -> tuple[RenderOptions, str]:
    q = request.query_params
    content = q.get("content", "all")
    if content not in ("all", "config", "nonconfig"):
        raise TreeError(f"content={content!r}: use all, config or nonconfig")
    if ds in CONVENTIONAL:
        # Conventional datastores hold configuration only (RFC 8342 §5.1).
        if content == "nonconfig":
            raise TreeError(f"The {ds} datastore holds no state data")
        content = "config"
    depth_raw = q.get("depth", "unbounded")
    depth: int | None = None
    if depth_raw != "unbounded":
        try:
            depth = int(depth_raw)
        except ValueError:
            depth = 0
        if not 1 <= depth <= 65535:
            raise TreeError(f"depth={depth_raw!r}: use 1..65535 or 'unbounded'")
    with_defaults = q.get("with-defaults", "report-all")
    if with_defaults not in ("report-all", "explicit", "trim"):
        raise TreeError(
            f"with-defaults={with_defaults!r}: use report-all, explicit or trim"
        )
    fmt = q.get("format", "tree")
    if fmt not in ("tree", "leaves"):
        raise TreeError(f"format={fmt!r}: use tree or leaves")
    return RenderOptions(
        source=source_for(ctx, ds), depth=depth, content=content,
        with_defaults=with_defaults,
    ), fmt


# -- Root, schema, operations list -----------------------------------------------


@router.get("")
async def root_resource(request: Request) -> JSONResponse:
    """RESTCONF-style root resource: what this controller serves."""
    ctx = _ctx(request)
    body = {_qualified("restconf"): {
        "data": {},
        "operations": {name: {} for name in OPERATIONS},
        "datastores": [f"ietf-datastores:{d}" for d in DATASTORES],
        "schema": f"{ROOT}/schema",
        "schema-revision": SCHEMA_REVISION,
        "backend": ctx._backend.name,
        "backend-available": bool(ctx._backend.available),
        "candidate-pending-edits": len(ctx.candidate.edits),
    }}
    return JSONResponse(body, media_type=RESTCONF_MEDIA_TYPE,
                        headers=_headers(ctx, "running"))


@router.get("/operations")
async def list_operations() -> JSONResponse:
    return JSONResponse(
        {_qualified("operations"): OPERATIONS}, media_type=RESTCONF_MEDIA_TYPE,
    )


@router.get("/schema")
async def schema(request: Request) -> JSONResponse:
    """Every node type and leaf of the tree, generated from the same specs
    the resolver uses — so the schema cannot drift from the data."""
    from twinlight.config import TwinConfig
    from twinlight.physics.backend import element_kind_name
    from twinlight.state.twin_overrides import section_model

    ctx = _ctx(request)
    backend = ctx._backend
    present = sorted({
        element_kind_name(el) for el in (backend.uid_map if backend.available else {}).values()
    }, key=lambda k: ELEMENT_KINDS.index(k) if k in ELEMENT_KINDS else 99)

    def leaves(specs: Any) -> dict[str, Any]:
        return {s.name: s.describe() for s in specs}

    def config_tree(model: Any, prefix: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, info in model.model_fields.items():
            if info.exclude:
                continue
            key = f"{prefix}.{name}" if prefix else name
            sub = section_model(model, name)
            out[name] = (
                config_tree(sub, key) if sub is not None
                else config_leaf_spec(key, info).describe()
            )
        return out

    element_entry = {kind: leaves(element_leaf_specs(ctx, kind)) for kind in present}
    body = {_qualified("schema"): {
        "module": MODULE,
        "revision": SCHEMA_REVISION,
        "backend": backend.name,
        "tree": {
            "ElementList": {
                "node": "list", "key": "uid", "order": "sorted by uid",
                "casts": [f"${k}" for k in ELEMENT_KINDS],
                "entry-by-kind": element_entry,
            },
            "NodeList": {
                "node": "list", "key": "uid", "order": "sorted by uid",
                "entry": "the ElementList entries of every Roadm and Transceiver",
            },
            "LinkList": {
                "node": "list", "key": ["from", "to"], "order": "sorted by from, then to",
                "entry": {
                    **leaves(LINK_LEAVES),
                    "ElementList": "the link's inline elements, in propagation order",
                },
            },
            "ServiceList": {
                "node": "list", "key": "uuid (or service-name)", "order": "creation",
                "entry": {
                    **leaves(SERVICE_LEAVES),
                    "qot": leaves(QOT_LEAVES),
                    "ElementList": "the elements of the allocated path, in order",
                },
            },
            "ModulationList": {
                "node": "list", "key": "format", "entry": leaves(MODULATION_LEAVES),
            },
            "Config": config_tree(TwinConfig, ""),
        },
    }}
    return JSONResponse(body, media_type=RESTCONF_MEDIA_TYPE)


# -- Data and datastores ----------------------------------------------------------


async def _get(request: Request, ds: str, depth: int) -> Response:
    ctx = _ctx(request)
    try:
        opts, fmt = _retrieval_options(request, ctx, ds)
        segments = _segments(request, depth)
        nodes = resolve(TreeBuilder(ctx).root(), segments)
    except (TreeError, PathSyntaxError) as exc:
        return _errors_response([_as_tree_error(exc)])
    if not nodes:
        return _error(
            f"{_path_text(segments)} matches nothing in the {ds} datastore",
            "invalid-value", 404,
        )
    headers = _headers(ctx, ds)
    inm = request.headers.get("if-none-match")
    if inm and _matches_etag(inm, headers["ETag"]):
        return Response(status_code=304, headers=headers)
    if fmt == "leaves":
        body: dict[str, Any] = {_qualified("leaves"): await render_leaves(nodes, opts)}
    else:
        body = {_qualified("data"): await render_tree(nodes, opts)}
    return JSONResponse(body, media_type=RESTCONF_MEDIA_TYPE, headers=headers)


def _path_text(segments: list[Segment]) -> str:
    return "/" + "/".join(str(s) for s in segments)


def _as_tree_error(exc: Exception) -> TreeError:
    if isinstance(exc, TreeError):
        return exc
    return TreeError(str(exc), tag="invalid-value")


async def _write(request: Request, ds: str, depth: int, operation: str) -> Response:
    ctx = _ctx(request)
    if ds not in ("running", "candidate"):
        return _error(
            f"The {ds} datastore is read-only; write running (or /data) or candidate",
            "operation-not-supported", 405,
        )
    try:
        segments = _segments(request, depth)
        builder = TreeBuilder(ctx)
        content_type = request.headers.get("content-type", "")
        if operation == "merge" and content_type.startswith(YANG_PATCH_MEDIA_TYPE):
            return await _yang_patch(request, ctx, ds, builder, segments)
        nodes = resolve(builder.root(), segments)
        if not nodes:
            return _error(
                f"{_path_text(segments)} matches nothing", "invalid-value", 404,
            )
        body = None if operation == "remove" else await _json_body(request)
        if body is None and operation != "remove":
            return _error("A body is required", "missing-element", 400)
        changes = build_edit(nodes, operation, body)
    except (TreeError, PathSyntaxError) as exc:
        return _errors_response([_as_tree_error(exc)])
    refused = _precondition(request, ctx, ds)
    if refused is not None:
        return refused
    return await _commit_or_stage(request, ctx, ds, changes)


async def _commit_or_stage(
    request: Request, ctx: Any, ds: str, changes: dict[LeafKey, Any],
) -> Response:
    changes = effective_changes(ctx, changes, ds)
    if ds == "candidate":
        errors = validate_changes(ctx, {**ctx.candidate.edits, **changes})
        if errors:
            return _errors_response(errors)
        described = await describe_changes(ctx, changes)
        ctx.candidate.stage(changes, ctx.revision)
        invalidated: list[str] = []
    else:
        described = await describe_changes(ctx, changes)
        try:
            invalidated = apply_changes(ctx, changes)
        except ChangeError as exc:
            return _errors_response(exc.errors)
    headers = _headers(ctx, ds)
    if not _wants_representation(request):
        return Response(status_code=204, headers=headers)
    headers["Preference-Applied"] = "return=representation"
    body = {_qualified("edit-result"): {
        "datastore": f"ietf-datastores:{ds}",
        "changes": described,
        "invalidated-services": invalidated,
    }}
    return JSONResponse(body, media_type=RESTCONF_MEDIA_TYPE, headers=headers)


async def _yang_patch(
    request: Request, ctx: Any, ds: str, builder: TreeBuilder,
    base: list[Segment],
) -> Response:
    """RFC 8072: an ordered list of edits applied as one transaction."""
    body = await _json_body(request)
    patch = None
    if isinstance(body, dict):
        patch = body.get("ietf-yang-patch:yang-patch", body.get("yang-patch"))
    if not isinstance(patch, dict) or not isinstance(patch.get("edit"), list):
        return _error(
            'A YANG Patch body is {"ietf-yang-patch:yang-patch": '
            '{"patch-id": "…", "edit": [{"edit-id", "operation", "target", "value"}]}}',
            "malformed-message", 400,
        )
    patch_id = str(patch.get("patch-id", ""))
    changes, owner, edit_errors = _patch_changes(builder, base, patch["edit"])
    if not edit_errors:
        validation = validate_changes(
            ctx, {**ctx.candidate.edits, **changes} if ds == "candidate" else changes,
        )
        for err in validation:
            edit_id = next(
                (owner[k] for k in changes if err.path == k.path), None,
            )
            edit_errors.setdefault(edit_id, []).append(err)
    if edit_errors:
        return _patch_status(patch_id, edit_errors)
    refused = _precondition(request, ctx, ds)
    if refused is not None:
        return refused
    response = await _commit_or_stage(request, ctx, ds, changes)
    if response.status_code >= 400:
        return response
    status: dict[str, Any] = {"patch-id": patch_id, "ok": [None]}
    if isinstance(response, JSONResponse):
        status[_qualified("edit-result")] = json.loads(bytes(response.body))[
            _qualified("edit-result")
        ]
    return JSONResponse(
        {"ietf-yang-patch:yang-patch-status": status},
        media_type=RESTCONF_MEDIA_TYPE,
        headers={k: v for k, v in response.headers.items()
                 if k.lower() in ("etag", "last-modified", "preference-applied")},
    )


def _patch_changes(
    builder: TreeBuilder, base: list[Segment], edits: list[Any],
) -> tuple[dict[LeafKey, Any], dict[LeafKey, str], dict[str | None, list[TreeError]]]:
    """Fold the edits, in order, into one change set (later edits win)."""
    from twinlight.transparent.path import PathSyntaxError as _PSE

    changes: dict[LeafKey, Any] = {}
    owner: dict[LeafKey, str] = {}
    errors: dict[str | None, list[TreeError]] = {}
    for i, edit in enumerate(edits):
        edit_id = str(edit.get("edit-id", i)) if isinstance(edit, dict) else str(i)
        try:
            if not isinstance(edit, dict):
                raise TreeError("an edit is a JSON object", tag="malformed-message")
            operation = edit.get("operation")
            target = edit.get("target")
            if not isinstance(target, str):
                raise TreeError("'target' is required", tag="missing-element")
            nodes = resolve(builder.root(), join(base, target))
            if not nodes:
                raise TreeError(f"{target} matches nothing", tag="invalid-value", status=404)
            if operation in ("merge", "replace") and "value" not in edit:
                raise TreeError(f"'{operation}' needs a 'value'", tag="missing-element")
            sub = build_edit(nodes, str(operation), edit.get("value"))
        except (TreeError, _PSE) as exc:
            errors.setdefault(edit_id, []).append(_as_tree_error(exc))
            continue
        for key, value in sub.items():
            changes.pop(key, None)
            changes[key] = value
            owner[key] = edit_id
    return changes, owner, errors


def _patch_status(
    patch_id: str, errors: dict[str | None, list[TreeError]],
) -> JSONResponse:
    def body(errs: list[TreeError]) -> dict[str, Any]:
        return {"error": [
            {"error-type": "application", "error-tag": e.tag,
             **({"error-path": e.path} if e.path else {}),
             "error-message": str(e)}
            for e in errs
        ]}

    status: dict[str, Any] = {"patch-id": patch_id}
    per_edit = [
        {"edit-id": edit_id, "errors": body(errs)}
        for edit_id, errs in errors.items() if edit_id is not None
    ]
    if per_edit:
        status["edit-status"] = {"edit": per_edit}
    if None in errors:
        status["errors"] = body(errors[None])
    code = max(e.status for errs in errors.values() for e in errs)
    return JSONResponse(
        {"ietf-yang-patch:yang-patch-status": status},
        status_code=code, media_type=RESTCONF_MEDIA_TYPE,
    )


# URL segments before the tree path: /transparent/data and /transparent/ds/<name>.
_DATA = 2
_DS = 3


@router.get("/data")
@router.get("/data/{path:path}")
async def get_data(request: Request) -> Response:
    """Read the operational view of any path."""
    return await _get(request, "operational", _DATA)


@router.put("/data")
@router.put("/data/{path:path}")
async def put_data(request: Request) -> Response:
    """Replace the target in running: set what the body names, revert the rest."""
    return await _write(request, "running", _DATA, "replace")


@router.patch("/data")
@router.patch("/data/{path:path}")
async def patch_data(request: Request) -> Response:
    """Merge into running — or apply a YANG Patch (``application/yang-patch+json``)."""
    return await _write(request, "running", _DATA, "merge")


@router.delete("/data")
@router.delete("/data/{path:path}")
async def delete_data(request: Request) -> Response:
    """Put every config leaf under the target back to its startup value."""
    return await _write(request, "running", _DATA, "remove")


@router.post("/data")
@router.post("/data/{path:path}")
async def post_data() -> Response:
    return _error(
        "Nothing can be created in this tree: its lists mirror the network. "
        "Use PUT or PATCH to change values, and T-API to create services",
        "operation-not-supported", 405,
    )


async def _ds(request: Request, method: str) -> Response:
    try:
        ds = datastore_name(request.path_params["datastore"])
    except TreeError as exc:
        return _errors_response([exc])
    if method == "get":
        return await _get(request, ds, _DS)
    return await _write(request, ds, _DS, method)


@router.get("/ds/{datastore}")
@router.get("/ds/{datastore}/{path:path}")
async def get_ds(request: Request) -> Response:
    """Read one datastore: running, candidate, startup or operational."""
    return await _ds(request, "get")


@router.put("/ds/{datastore}")
@router.put("/ds/{datastore}/{path:path}")
async def put_ds(request: Request) -> Response:
    return await _ds(request, "replace")


@router.patch("/ds/{datastore}")
@router.patch("/ds/{datastore}/{path:path}")
async def patch_ds(request: Request) -> Response:
    return await _ds(request, "merge")


@router.delete("/ds/{datastore}")
@router.delete("/ds/{datastore}/{path:path}")
async def delete_ds(request: Request) -> Response:
    return await _ds(request, "remove")


# -- Operations ------------------------------------------------------------------------


def _rpc_input(body: Any) -> dict[str, Any]:
    if body is None:
        return {}
    if isinstance(body, dict):
        for key in (_qualified("input"), "input"):
            if key in body and isinstance(body[key], dict):
                return dict(body[key])
        return dict(body)
    raise TreeError("RPC input is a JSON object", tag="malformed-message")


def _rpc_output(ctx: Any, output: dict[str, Any], ds: str = "running") -> JSONResponse:
    return JSONResponse(
        {_qualified("output"): output}, media_type=RESTCONF_MEDIA_TYPE,
        headers=_headers(ctx, ds),
    )


@router.post("/operations/{name}")
async def run_operation(name: str, request: Request) -> Response:
    ctx = _ctx(request)
    if name not in OPERATIONS:
        return _error(
            f"Unknown operation {name!r}; known: {sorted(OPERATIONS)}",
            "operation-not-supported", 404,
        )
    try:
        args = _rpc_input(await _json_body(request))
        if name == "commit":
            return await _rpc_commit(request, ctx)
        if name == "discard-changes":
            dropped = len(ctx.candidate.edits)
            ctx.candidate.discard()
            return _rpc_output(ctx, {"discarded-edits": dropped}, "candidate")
        if name == "validate":
            return await _rpc_validate(ctx, args)
        if name == "compare":
            source = datastore_name(str(args.get("source", "running")))
            target = datastore_name(str(args.get("target", "candidate")))
            edits = await compare(ctx, source, target)
            return _rpc_output(ctx, {"differences": {"ietf-yang-patch:yang-patch": {
                "patch-id": f"compare-{source}-{target}",
                "comment": f"Applying these edits to {source} yields {target}",
                "edit": edits,
            }}})
        return _rpc_redesign(request, ctx)
    except (TreeError, PathSyntaxError) as exc:
        return _errors_response([_as_tree_error(exc)])


async def _rpc_commit(request: Request, ctx: Any) -> Response:
    refused = _precondition(request, ctx, "running")
    if refused is not None:
        return refused
    candidate = ctx.candidate
    changes = effective_changes(ctx, dict(candidate.edits))
    moved = candidate.base_revision is not None and candidate.base_revision != ctx.revision
    described = await describe_changes(ctx, changes)
    try:
        invalidated = apply_changes(ctx, changes)
    except ChangeError as exc:
        return _errors_response(exc.errors)
    candidate.discard()
    return _rpc_output(ctx, {
        "committed-edits": len(changes),
        "changes": described,
        "invalidated-services": invalidated,
        # The candidate's values are absolute, so committing them over a
        # running that moved is well-defined; the flag says it happened.
        "running-changed-since-staged": moved,
    })


async def _rpc_validate(ctx: Any, args: dict[str, Any]) -> Response:
    """Validate the candidate, or an inline YANG Patch against running,
    and optionally predict the QoT of every service it touches."""
    patch = args.get("ietf-yang-patch:yang-patch", args.get("yang-patch"))
    if patch is not None:
        if not isinstance(patch, dict) or not isinstance(patch.get("edit"), list):
            raise TreeError("'yang-patch' needs an 'edit' list", tag="malformed-message")
        changes, _owner, errors = _patch_changes(TreeBuilder(ctx), [], patch["edit"])
        flat = [e for errs in errors.values() for e in errs]
        if flat:
            return _errors_response(flat)
        source = "inline yang-patch"
    else:
        source = datastore_name(str(args.get("source", "candidate")))
        if source != "candidate":
            raise TreeError("validate checks 'candidate' or an inline 'yang-patch'")
        changes = dict(ctx.candidate.edits)
    changes = effective_changes(ctx, changes)
    errors_ = validate_changes(ctx, changes)
    if errors_:
        return _errors_response(errors_)
    output: dict[str, Any] = {
        "source": source,
        "valid": True,
        "changes": await describe_changes(ctx, changes),
    }
    if args.get("with-qot-impact"):
        output["qot-impact"] = await preview_changes(ctx, changes)
    return _rpc_output(ctx, output)


def _rpc_redesign(request: Request, ctx: Any) -> Response:
    refused = _precondition(request, ctx, "running")
    if refused is not None:
        return refused
    if not ctx._backend.available:
        return _error("No physical-layer backend is loaded", "operation-failed", 503)
    try:
        invalidated = ctx.apply_element_overrides({}, redesign=True)
    except NotImplementedError as exc:
        return _error(str(exc), "operation-not-supported", 501)
    services = sorted(set().union(*invalidated.values())) if invalidated else []
    return _rpc_output(ctx, {"invalidated-services": services})
