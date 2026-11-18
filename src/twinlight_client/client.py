"""REST client for the T-API Digital Twin."""

from __future__ import annotations

import asyncio
from typing import Any, Self

import httpx

from twinlight_client.credentials import bearer_headers
from twinlight_client.restconf import discover_root


class TapiClient:
    """Async HTTP client wrapping all T-API REST endpoints."""

    _CONTEXT = "/data/tapi-common:context"
    _TOPO = f"{_CONTEXT}/tapi-topology:topology-context"

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        token: str | None = None,
        restconf_root: str | None = None,
    ) -> None:
        """``token`` is sent as ``Authorization: Bearer <token>`` on every
        request; leave it None for a twin with authentication disabled.

        ``restconf_root`` is where the twin serves T-API (``{root}/data``).
        Leave it None to read it from ``/.well-known/host-meta`` before the
        first T-API request (RFC 8040 §3.1; see ``twinlight_client.restconf``)."""
        headers = bearer_headers(token)
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers)
        self._root = None if restconf_root is None else restconf_root.rstrip("/")
        self._root_lock = asyncio.Lock()

    async def restconf_root(self) -> str:
        """The twin's RESTCONF root, discovered once and then reused."""
        if self._root is None:
            async with self._root_lock:
                if self._root is None:
                    self._root = await discover_root(self._client)
        return self._root

    async def _tapi(self, path: str) -> str:
        """``path`` (``/data/tapi-common:context/…``) under the RESTCONF root."""
        return f"{await self.restconf_root()}{path}"

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- Health ---------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        resp = await self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    # -- TAPI Common ----------------------------------------------------------

    async def get_context(self) -> dict[str, Any]:
        resp = await self._client.get(await self._tapi(self._CONTEXT))
        resp.raise_for_status()
        return resp.json()

    async def get_sips(self) -> list[dict[str, Any]]:
        resp = await self._client.get(
            await self._tapi(f"{self._CONTEXT}/service-interface-point")
        )
        resp.raise_for_status()
        return resp.json()

    async def get_sip(self, uuid: str) -> dict[str, Any]:
        resp = await self._client.get(
            await self._tapi(f"{self._CONTEXT}/service-interface-point={uuid}")
        )
        resp.raise_for_status()
        return resp.json()

    # -- TAPI Topology --------------------------------------------------------

    async def get_topology_context(self) -> dict[str, Any]:
        resp = await self._client.get(await self._tapi(self._TOPO))
        resp.raise_for_status()
        return resp.json()

    async def get_topologies(self) -> list[dict[str, Any]]:
        ctx = await self.get_topology_context()
        return ctx.get("tapi-topology:topology-context", {}).get("topology", [])

    async def get_topology(self, uuid: str) -> dict[str, Any]:
        resp = await self._client.get(await self._tapi(f"{self._TOPO}/topology={uuid}"))
        resp.raise_for_status()
        return resp.json()

    async def get_node(
        self, topology_uuid: str, node_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            await self._tapi(f"{self._TOPO}/topology={topology_uuid}/node={node_uuid}")
        )
        resp.raise_for_status()
        return resp.json()

    async def get_link(
        self, topology_uuid: str, link_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            await self._tapi(f"{self._TOPO}/topology={topology_uuid}/link={link_uuid}")
        )
        resp.raise_for_status()
        return resp.json()

    async def get_nep(
        self, topology_uuid: str, node_uuid: str, nep_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            await self._tapi(
                f"{self._TOPO}/topology={topology_uuid}"
                f"/node={node_uuid}/owned-node-edge-point={nep_uuid}"
            )
        )
        resp.raise_for_status()
        return resp.json()

    # -- TAPI Connectivity ----------------------------------------------------

    async def get_connectivity_services(self) -> list[dict[str, Any]]:
        """Return the list of connectivity services (T-API standard endpoint)."""
        resp = await self._client.get(
            await self._tapi(
                f"{self._CONTEXT}/tapi-connectivity:connectivity-context/connectivity-service"
            )
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("tapi-connectivity:connectivity-context", {}).get(
            "connectivity-service", []
        )

    # -- TAPI Streaming and OAM (TR-548 discovery) ---------------------------

    async def get_stream_context(self) -> dict[str, Any]:
        """The tapi-streaming:stream-context (supported and available streams)."""
        resp = await self._client.get(
            await self._tapi(f"{self._CONTEXT}/tapi-streaming:stream-context")
        )
        resp.raise_for_status()
        return resp.json()["tapi-streaming:stream-context"]

    async def get_oam_jobs(self) -> list[dict[str, Any]]:
        """Every tapi-oam:oam-job; the twin keeps one per connectivity service."""
        resp = await self._client.get(
            await self._tapi(f"{self._CONTEXT}/tapi-oam:oam-context/oam-job")
        )
        resp.raise_for_status()
        return resp.json().get("tapi-oam:oam-job", [])

    # -- Internal / OPM -------------------------------------------------------

    async def get_opm(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/internal/opm")
        resp.raise_for_status()
        return resp.json()

    async def get_opm_service(self, service_uuid: str) -> dict[str, Any]:
        resp = await self._client.get(f"/internal/opm/{service_uuid}")
        resp.raise_for_status()
        return resp.json()
