"""RESTCONF (RFC 8040) helpers shared by the T-API modules."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request


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
