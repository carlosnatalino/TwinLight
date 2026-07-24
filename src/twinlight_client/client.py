"""REST client for the T-API Digital Twin."""

from __future__ import annotations

from typing import Any, Self

import httpx


class TapiClient:
    """Async HTTP client wrapping all T-API REST endpoints."""

    _BASE = "/data/tapi-common:context"
    _TOPO = f"{_BASE}/tapi-topology:topology-context"

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        self._client = httpx.AsyncClient(base_url=base_url)

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
        resp = await self._client.get(self._BASE)
        resp.raise_for_status()
        return resp.json()

    async def get_sips(self) -> list[dict[str, Any]]:
        resp = await self._client.get(f"{self._BASE}/service-interface-point")
        resp.raise_for_status()
        return resp.json()

    async def get_sip(self, uuid: str) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._BASE}/service-interface-point={uuid}"
        )
        resp.raise_for_status()
        return resp.json()

    # -- TAPI Topology --------------------------------------------------------

    async def get_topology_context(self) -> dict[str, Any]:
        resp = await self._client.get(self._TOPO)
        resp.raise_for_status()
        return resp.json()

    async def get_topologies(self) -> list[dict[str, Any]]:
        ctx = await self.get_topology_context()
        return ctx.get("tapi-topology:topology-context", {}).get("topology", [])

    async def get_topology(self, uuid: str) -> dict[str, Any]:
        resp = await self._client.get(f"{self._TOPO}/topology={uuid}")
        resp.raise_for_status()
        return resp.json()

    async def get_node(
        self, topology_uuid: str, node_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._TOPO}/topology={topology_uuid}/node={node_uuid}"
        )
        resp.raise_for_status()
        return resp.json()

    async def get_link(
        self, topology_uuid: str, link_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._TOPO}/topology={topology_uuid}/link={link_uuid}"
        )
        resp.raise_for_status()
        return resp.json()

    async def get_nep(
        self, topology_uuid: str, node_uuid: str, nep_uuid: str
    ) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self._TOPO}/topology={topology_uuid}"
            f"/node={node_uuid}/owned-node-edge-point={nep_uuid}"
        )
        resp.raise_for_status()
        return resp.json()

    # -- TAPI Connectivity ----------------------------------------------------

    async def get_connectivity_services(self) -> list[dict[str, Any]]:
        """Return the list of connectivity services (T-API standard endpoint)."""
        resp = await self._client.get(
            "/data/tapi-connectivity:connectivity-context/connectivity-service"
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("tapi-connectivity:connectivity-context", {}).get(
            "connectivity-service", []
        )

    # -- Internal / OPM -------------------------------------------------------

    async def get_opm(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/internal/opm")
        resp.raise_for_status()
        return resp.json()

    async def get_opm_service(self, service_uuid: str) -> dict[str, Any]:
        resp = await self._client.get(f"/internal/opm/{service_uuid}")
        resp.raise_for_status()
        return resp.json()
