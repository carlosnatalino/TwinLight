"""HTTP client for gnpy-api, the service that runs GNPy outside the twin.

gnpy-api (``oopt-gnpy-api``, session endpoints) keeps a designed GNPy network
in memory between calls. ``POST /api/v1.0/sessions`` loads and designs a
network and returns a session token; every ``/api/v1.0/session/...`` call
then carries that token in the ``X-Session-Token`` header and acts on that
network: list or change elements, fail fibers, compute paths, propagate,
re-design, export the designed topology.

This module is transport only: status codes are mapped onto the exceptions
the twin already handles (``KeyError`` for an unknown element,
``ParamValidationError`` for a rejected value), plus :class:`SessionLostError`
when the service no longer knows the token, typically because it restarted.
"""

from __future__ import annotations

from typing import Any

import httpx

from twinlight.physics.backend import ParamValidationError

API_PREFIX = "/api/v1.0"
SESSION_TOKEN_HEADER = "X-Session-Token"

# Designing a large topology (CORONET CONUS: 2 000+ elements) takes seconds,
# and longer still when the service runs under CPU emulation.
_TIMEOUT_S = 60.0


class GnpyApiError(RuntimeError):
    """gnpy-api could not be reached, or failed to answer."""


class SessionLostError(GnpyApiError):
    """gnpy-api no longer knows the session token, e.g. after a restart."""


def new_http_client(base_url: str) -> httpx.Client:
    """The HTTP client every :class:`GnpyApiClient` uses; tests swap it out."""
    return httpx.Client(base_url=base_url, timeout=_TIMEOUT_S)


class GnpyApiClient:
    """One gnpy-api session, plus the session-less endpoints."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._http = new_http_client(base_url)
        self.token: str | None = None

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Call ``API_PREFIX + path`` with the session token, return the JSON body."""
        headers = {SESSION_TOKEN_HEADER: self.token} if self.token else {}
        try:
            response = self._http.request(method, API_PREFIX + path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GnpyApiError(f"gnpy-api at {self.base_url} unreachable: {exc}") from exc
        if response.status_code == 401:
            raise SessionLostError(_detail(response))
        if response.status_code == 404:
            raise KeyError(_detail(response))
        if response.status_code in (400, 422):
            raise ParamValidationError(_detail(response))
        if response.is_error:
            raise GnpyApiError(f"gnpy-api answered {response.status_code}: {_detail(response)}")
        return response.json() if response.content else None

    def open_session(self, topology: dict, equipment: dict) -> dict:
        """Load and design a network (GNPy legacy JSON); returns its summary."""
        summary: dict = self.request(
            "POST", "/sessions",
            json={"gnpy-api:topology": topology, "gnpy-api:equipment": equipment},
        )
        self.token = summary.pop("session-token")
        return summary

    def close_session(self) -> None:
        """End the session; a session the service already forgot is not an error."""
        if self.token is None:
            return
        try:
            self.request("DELETE", "/session")
        except SessionLostError:
            pass
        finally:
            self.token = None

    def example_file(self, name: str) -> dict:
        """One JSON file of the GNPy example data shipped with the service."""
        data: dict = self.request("GET", f"/example-data/{name}")
        return data


def designed_topology(base_url: str, topology: dict, equipment: dict) -> dict:
    """GNPy's design of ``topology``, as GNPy topology JSON, from a short-lived session."""
    client = GnpyApiClient(base_url)
    client.open_session(topology, equipment)
    try:
        designed: dict = client.request("GET", "/session/topology")
        return designed
    finally:
        client.close_session()


def _detail(response: httpx.Response) -> str:
    """FastAPI answers ``{"detail"}``; gnpy-api's sample server answers ``{"description"}``."""
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("description") or body)
    return str(body)
