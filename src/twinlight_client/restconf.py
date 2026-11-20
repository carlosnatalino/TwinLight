"""Finding a twin's RESTCONF root (RFC 8040 §3.1).

The twin serves T-API under ``{root}/data``, where ``root`` is its configured
``server.restconf_root`` (``/restconf`` by default), and advertises the root
in ``/.well-known/host-meta``. A client reads it from there rather than
assuming ``/restconf``. host-meta needs a token on a twin with
authentication on, like every path except ``/health``.

A twin with an empty root advertises it as the absolute URI the request
reached, ``http://host:port``, whose path is empty (RFC 3986 §3.3), so the
root is ``""`` and T-API is at ``/data``. A twin that answers host-meta with
404 predates the RESTCONF root and serves T-API at ``/data`` too, so a 404
also means ``""``. Any other failure (401, a twin that is down) is raised.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx

HOST_META = "/.well-known/host-meta"

_XRD_LINK = "{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link"


def parse_host_meta(xrd: str) -> str:
    """The ``restconf`` link of an RFC 6415 XRD, without a trailing slash.

    ``""`` when the document has no such link. An absolute ``href`` is
    reduced to its path: requests go to the base URL the client was given.
    An empty root is advertised as ``http://host:port``, path ``""``.
    """
    for link in ET.fromstring(xrd).iter(_XRD_LINK):
        if link.get("rel") == "restconf":
            href = link.get("href", "")
            if urlsplit(href).scheme:
                href = urlsplit(href).path
            return href.rstrip("/")
    return ""


def _root_from(resp: httpx.Response) -> str:
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return parse_host_meta(resp.text)


async def discover_root(http: httpx.AsyncClient) -> str:
    """The RESTCONF root of the twin ``http`` points at (see the module doc)."""
    return _root_from(await http.get(HOST_META))


def discover_root_sync(http: httpx.Client) -> str:
    """:func:`discover_root` for a synchronous client."""
    return _root_from(http.get(HOST_META))
