"""Client for TwinLight's transparent optical domain controller.

``TransparentClient`` wraps ``/transparent``: reading any path of the
twin's data tree, writing running or the candidate, YANG Patch
transactions, and the commit / validate / compare RPCs. Paths are the
controller's ns-3-style paths, written as plain strings; the client turns
them into URLs (a quoted UID such as ``"fiber (A → B)-F_(1/2)"`` keeps its
slash).

It is synchronous and takes an injectable ``httpx.Client``, so a script, a
notebook or a test (FastAPI's ``TestClient`` is an ``httpx.Client``) can
drive a twin the same way::

    with TransparentClient("http://localhost:8080") as odc:
        odc.set("/ElementList/*/$Fiber/con_in", 1.0, datastore="candidate")
        report = odc.validate(with_qot_impact=True)
        odc.commit(if_match=odc.etag())

This module deliberately does not import the server package.
"""

from __future__ import annotations

import json
from typing import Any, Self
from urllib.parse import quote

import httpx

MODULE = "twinlight-transparent"
ROOT = "/transparent"
_YANG_PATCH = "application/yang-patch+json"
_PREFER = {"Prefer": "return=representation"}


class TransparentError(Exception):
    """The controller refused a request; ``errors`` is its RESTCONF list."""

    def __init__(self, status: int, errors: list[dict[str, Any]], body: Any = None) -> None:
        self.status = status
        self.errors = errors
        self.body = body
        lines = [
            f"{e.get('error-tag', '?')}: {e.get('error-message', '')}" for e in errors
        ] or [str(body)]
        super().__init__(f"HTTP {status}: " + "; ".join(lines))


def _split(path: str) -> list[str]:
    """Split a path on ``/`` outside double quotes (escapes respected)."""
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    while i < len(path):
        ch = path[i]
        if in_quotes and ch == "\\" and i + 1 < len(path):
            buf.append(path[i : i + 2])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
        if ch == "/" and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p]


def path_url(path: str, datastore: str | None = None) -> str:
    """The URL of ``path`` in ``datastore`` (``None``: the ``/data`` view).

    Each segment is percent-encoded whole, so a slash inside a quoted UID
    travels as ``%2F`` and globs, ``|`` and ``$`` survive any proxy.
    """
    base = f"{ROOT}/data" if datastore is None else f"{ROOT}/ds/{datastore}"
    segments = [quote(s, safe="") for s in _split(path)]
    return base + ("/" + "/".join(segments) if segments else "")


def _errors(response: httpx.Response) -> TransparentError:
    try:
        body = response.json()
    except ValueError:
        return TransparentError(response.status_code, [], response.text)
    errors = body.get("ietf-restconf:errors", {}).get("error", [])
    status = body.get("ietf-yang-patch:yang-patch-status")
    if status:
        for edit in status.get("edit-status", {}).get("edit", []):
            for e in edit.get("errors", {}).get("error", []):
                errors.append({**e, "edit-id": edit.get("edit-id")})
        errors += status.get("errors", {}).get("error", [])
    return TransparentError(response.status_code, errors, body)


class TransparentClient:
    """Synchronous client for ``/transparent``."""

    def __init__(
        self, base_url: str = "http://localhost:8080",
        client: httpx.Client | None = None, timeout: float = 30.0,
    ) -> None:
        self._own = client is None
        self._http = client or httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._own:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, url, **kwargs)
        if response.status_code >= 400:
            raise _errors(response)
        return response

    @staticmethod
    def _conditional(if_match: str | None) -> dict[str, str]:
        return {"If-Match": if_match} if if_match else {}

    def _rpc(self, name: str, payload: dict[str, Any] | None = None,
             if_match: str | None = None) -> dict[str, Any]:
        r = self._request(
            "POST", f"{ROOT}/operations/{name}",
            json={"input": payload or {}}, headers=self._conditional(if_match),
        )
        out: dict[str, Any] = r.json()[f"{MODULE}:output"]
        return out

    # -- discovery --------------------------------------------------------

    def root(self) -> dict[str, Any]:
        out: dict[str, Any] = self._request("GET", ROOT).json()[f"{MODULE}:restconf"]
        return out

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = self._request("GET", f"{ROOT}/schema").json()[f"{MODULE}:schema"]
        return out

    def etag(self, datastore: str = "running") -> str:
        """Current entity-tag of a datastore, for ``if_match``."""
        url = path_url("/Config/rmsa", datastore if datastore != "operational" else None)
        return self._request("GET", url, params={"depth": 1}).headers["etag"]

    # -- reading ----------------------------------------------------------

    def get(
        self, path: str = "/", *, datastore: str | None = None,
        depth: int | None = None, content: str | None = None,
        with_defaults: str | None = None,
    ) -> dict[str, Any]:
        """The subtree(s) ``path`` selects, as a tree rooted at ``/``."""
        params = {k: v for k, v in {
            "depth": depth, "content": content, "with-defaults": with_defaults,
        }.items() if v is not None}
        r = self._request("GET", path_url(path, datastore), params=params)
        data: dict[str, Any] = r.json()[f"{MODULE}:data"]
        return data

    def leaves(
        self, path: str = "/", *, datastore: str | None = None,
        content: str | None = None, with_defaults: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every leaf ``path`` selects, flat, with unit, bounds and the
        canonical path to write it by."""
        params = {k: v for k, v in {
            "format": "leaves", "content": content, "with-defaults": with_defaults,
        }.items() if v is not None}
        r = self._request("GET", path_url(path, datastore), params=params)
        out: list[dict[str, Any]] = r.json()[f"{MODULE}:leaves"]
        return out

    def value(self, path: str, *, datastore: str | None = None) -> Any:
        """The value of the single leaf ``path`` names."""
        found = self.leaves(path, datastore=datastore)
        if len(found) != 1:
            raise ValueError(f"{path} selects {len(found)} leaves, not one")
        return found[0]["value"]

    # -- writing ----------------------------------------------------------

    def _write(self, method: str, path: str, body: Any, datastore: str,
               if_match: str | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"headers": {**_PREFER, **self._conditional(if_match)}}
        if body is not None:
            kwargs["content"] = json.dumps(body)
            kwargs["headers"]["Content-Type"] = "application/yang-data+json"
        r = self._request(method, path_url(path, datastore), **kwargs)
        result: dict[str, Any] = r.json()[f"{MODULE}:edit-result"]
        return result

    def set(self, path: str, value: Any, *, datastore: str = "running",
            if_match: str | None = None) -> dict[str, Any]:
        """Give every leaf ``path`` selects the value ``value`` (merge)."""
        return self._write("PATCH", path, value, datastore, if_match)

    def merge(self, path: str, body: dict[str, Any], *, datastore: str = "running",
              if_match: str | None = None) -> dict[str, Any]:
        """Merge a subtree into each node ``path`` selects (RESTCONF PATCH)."""
        return self._write("PATCH", path, body, datastore, if_match)

    def replace(self, path: str, body: Any, *, datastore: str = "running",
                if_match: str | None = None) -> dict[str, Any]:
        """Replace each selected node: set what ``body`` names, revert the
        rest of its config to startup (RESTCONF PUT)."""
        return self._write("PUT", path, body, datastore, if_match)

    def revert(self, path: str = "/", *, datastore: str = "running",
               if_match: str | None = None) -> dict[str, Any]:
        """Put every config leaf under ``path`` back to startup (DELETE)."""
        return self._write("DELETE", path, None, datastore, if_match)

    def yang_patch(
        self, edits: list[dict[str, Any]], *, patch_id: str = "twinlight-client",
        datastore: str = "running", if_match: str | None = None,
    ) -> dict[str, Any]:
        """Apply ordered edits as one transaction (RFC 8072).

        ``edits`` are ``{"operation", "target", "value"?}``; ``edit-id`` is
        filled in when missing.
        """
        numbered = [
            {"edit-id": str(i + 1), **edit} for i, edit in enumerate(edits)
        ]
        body = {"ietf-yang-patch:yang-patch": {"patch-id": patch_id, "edit": numbered}}
        url = f"{ROOT}/data" if datastore == "running" else f"{ROOT}/ds/{datastore}"
        r = self._request(
            "PATCH", url, content=json.dumps(body),
            headers={"Content-Type": _YANG_PATCH, **_PREFER, **self._conditional(if_match)},
        )
        status: dict[str, Any] = r.json()["ietf-yang-patch:yang-patch-status"]
        return status

    # -- operations -------------------------------------------------------

    def commit(self, *, if_match: str | None = None) -> dict[str, Any]:
        return self._rpc("commit", if_match=if_match)

    def discard(self) -> dict[str, Any]:
        return self._rpc("discard-changes")

    def validate(
        self, *, with_qot_impact: bool = False,
        edits: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Validate the candidate — or ``edits`` against running, without
        staging them — and optionally predict their QoT impact."""
        payload: dict[str, Any] = {"with-qot-impact": with_qot_impact}
        if edits is not None:
            payload["yang-patch"] = {"edit": [
                {"edit-id": str(i + 1), **e} for i, e in enumerate(edits)
            ]}
        return self._rpc("validate", payload)

    def compare(self, source: str = "running", target: str = "candidate") -> list[dict[str, Any]]:
        """The YANG Patch edits that turn ``source`` into ``target``."""
        out = self._rpc("compare", {"source": source, "target": target})
        edits: list[dict[str, Any]] = out["differences"]["ietf-yang-patch:yang-patch"]["edit"]
        return edits

    def redesign(self, *, if_match: str | None = None) -> dict[str, Any]:
        return self._rpc("redesign", if_match=if_match)
