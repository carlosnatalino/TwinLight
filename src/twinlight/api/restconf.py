"""RESTCONF (RFC 8040) helpers shared by the T-API modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request

from twinlight.models.tapi_schema import SchemaNode, unknown_members


def created_location(request: Request, container: str, entry: str, key: str) -> str:
    """The ``Location`` of a list entry that a POST on its parent created.

    RFC 8040 §4.4.1 has a creating POST answer 201 with a ``Location``
    header identifying the new child resource: the entry's data-resource
    path, ``{restconf_root}{container}/{entry}={key}``, with the key
    percent-encoded as §3.5.3 requires. It is a path-absolute reference
    (RFC 9110 §10.2.2 allows one), so it stays right behind a proxy that
    serves the twin under another host name.
    """
    root = request.app.state.config.server.restconf_root
    return f"{root}{container}/{entry}={quote(key, safe='')}"


def is_restconf_path(path: str, root: str) -> bool:
    """Whether ``path`` is a RESTCONF resource of a twin rooted at ``root``.

    With a root, everything at or below it: the API resource, its
    ``yang-library-version`` leaf and the datastore. With an empty root the
    datastore is ``/data`` (RFC 8040 §3.3.1), so that subtree is RESTCONF.
    The proprietary endpoints (``/internal``, ``/admin``, ``/config``,
    ``/transparent``, ``/metrics``, ``/health``) are never under it.
    """
    base = root if root else "/data"
    return path == base or path.startswith(base + "/")


class RestconfError(HTTPException):
    """An error with the RFC 8040 §7 ``error-tag`` the condition calls for.

    The exception handler in ``app.py`` otherwise derives the tag from the
    status code, which cannot tell, for one 400, a body that does not parse
    (``malformed-message``) from a missing leaf (``missing-element``) or an
    unacceptable value (``invalid-value``).

    ``error_app_tag`` and ``error_path`` are the optional ``error-app-tag``
    and ``error-path`` of the ``yang-errors`` template (RFC 8040 §8), for
    the errors RFC 7950 §15 defines with them; ``error_info`` is its
    ``error-info``, for the tags RFC 6241 Appendix A gives one (the
    ``bad-element`` of an ``unknown-element``, say).
    """

    def __init__(
        self,
        status_code: int,
        error_tag: str,
        detail: str,
        *,
        error_app_tag: str | None = None,
        error_path: str | None = None,
        error_info: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_tag = error_tag
        self.error_app_tag = error_app_tag
        self.error_path = error_path
        self.error_info = dict(error_info) if error_info else None


def error_type(tag: str) -> str:
    """The ``error-type`` of an error with ``tag``.

    RFC 6241 Appendix A lists the error-types each error-tag may carry.
    Every tag the twin sends may be ``application`` except
    ``malformed-message``, whose only error-type is ``rpc``: the message
    itself failed to parse.
    """
    return "rpc" if tag == "malformed-message" else "application"


def validation_error_tag(errors: Sequence[Mapping[str, Any]]) -> str:
    """The RFC 8040 §7 error-tag for a request body pydantic refused.

    RFC 6241 Appendix A defines the tags; the RFC 8040 §7 table pairs each
    with 400 (``missing-element`` by its verified erratum 7311). The first
    error decides, as the message lists them in that order:

    - ``json_invalid``: the body did not parse — ``malformed-message``
      ("failed to be parsed correctly");
    - ``missing`` of the body itself: RFC 8040 §4.5 requires a PUT to carry
      a message-body, "or the server MUST return a '400 Bad Request'
      status-line. The error-tag value 'invalid-value' is used in this
      case", and the same holds for a POST and a plain patch (§4.4.1,
      §4.6.1). Any other ``missing`` is a mandatory leaf left out —
      ``missing-element`` ("An expected element is missing");
    - ``extra_forbidden``: ``unknown-element`` ("An unexpected element is
      present");
    - a value of the wrong JSON type (``*_type``, ``*_parsing``):
      ``bad-element`` ("An element value is not correct; e.g., wrong
      type");
    - anything else, a value of the right type the leaf does not admit (an
      enumeration outside its set, a range, a check of the twin's own):
      ``invalid-value`` ("The request specifies an unacceptable value for
      one or more parameters").
    """
    if not errors:
        return "invalid-value"
    first = errors[0]
    kind = str(first.get("type", ""))
    if kind == "json_invalid":
        return "malformed-message"
    if kind == "missing":
        return "invalid-value" if tuple(first.get("loc", ())) == ("body",) else "missing-element"
    if kind == "extra_forbidden":
        return "unknown-element"
    if kind.endswith(("_type", "_parsing")):
        return "bad-element"
    return "invalid-value"


def refuse_unknown_members(
    value: Any, schema: SchemaNode, path: str, hints: Mapping[str, str] | None = None,
) -> None:
    """Refuse a request body with a member the schema does not admit.

    RFC 8040 §1.4 maps a RESTCONF edit onto NETCONF's ``<edit-config>``,
    which refuses a member the schema does not define: ``unknown-element``,
    "An unexpected element is present", with the member as ``bad-element``
    in ``error-info`` (RFC 6241 Appendix A). A member qualified with a module
    the server does not implement is ``unknown-namespace``, with the module
    as ``bad-namespace``; a defined member spelled against RFC 7951 §4
    (qualified where "the simple form ... MUST be used") is a message that
    does not follow its encoding, ``malformed-message``. RFC 8040 §7 pairs
    all three with 400. The ``error-path`` is the node holding the member,
    since the member itself names no data node; the message names every
    offending member with its path, the error the first. ``hints`` maps a
    member name clients are known to send (an earlier release's spelling,
    say) to the advice the message gives for it.

    Raises:
        RestconfError: 400 with the tag of the first offending member.
    """
    errors = unknown_members(value, schema, path)
    if not errors:
        return
    first = errors[0]
    info: dict[str, str] | None = None
    if first.error_tag == "unknown-element":
        info = {"bad-element": first.member}
    elif first.error_tag == "unknown-namespace":
        info = {"bad-element": first.member, "bad-namespace": first.namespace or ""}
    raise RestconfError(
        400, first.error_tag,
        "the request carries members T-API v2.6.0 does not define here: "
        + "; ".join(f"{e.path} ({(hints or {}).get(e.member, e.reason)})" for e in errors),
        error_path=first.parent_path,
        error_info=info,
    )


def target_member(body: Any, member: str) -> Any:
    """The target resource of a ``PUT``, out of its body.

    RFC 8040 §4.5: "A request message-body MUST be present, representing
    the new data resource, or the server MUST return a '400 Bad Request'
    status-line. The error-tag value 'invalid-value' is used in this case."
    The representation is the target as its GET returns it: one member,
    named with its module (RFC 7951 §4), so a body naming anything else, or
    more, does not represent it.

    Raises:
        RestconfError: 400 ``invalid-value``.
    """
    if not isinstance(body, dict) or set(body) != {member}:
        got = ", ".join(sorted(body)) if isinstance(body, dict) else type(body).__name__
        raise RestconfError(
            400, "invalid-value",
            f"the body of a PUT here is the target resource, {member!r}, as its one "
            f"member; got {got or 'an empty object'}",
        )
    return body[member]
