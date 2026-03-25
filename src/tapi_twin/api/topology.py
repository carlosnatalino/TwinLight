"""TAPI Topology API endpoints.

GET .../tapi-topology:topology-context
GET .../topology={uuid}
GET .../topology={uuid}/node={node_uuid}
GET .../topology={uuid}/link={link_uuid}
GET .../topology={uuid}/node={node_uuid}/owned-node-edge-point={nep_uuid}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

PREFIX = "/data/tapi-common:context/tapi-topology:topology-context"

router = APIRouter(prefix=PREFIX, tags=["tapi-topology"])


@router.get("")
async def get_topology_context(request: Request) -> dict:
    """Return the full topology context."""
    ctx = request.app.state.context
    return {
        "tapi-topology:topology-context": ctx.topology_context.model_dump(
            by_alias=True
        )
    }


@router.get("/topology={uuid}")
async def get_topology(uuid: str, request: Request) -> dict:
    """Return a specific topology."""
    ctx = request.app.state.context
    topo = ctx.get_topology(uuid)
    if topo is None:
        raise HTTPException(status_code=404, detail=f"Topology {uuid} not found")
    return {"tapi-topology:topology": [topo.model_dump(by_alias=True)]}


@router.get("/topology={uuid}/node={node_uuid}")
async def get_node(uuid: str, node_uuid: str, request: Request) -> dict:
    """Return a specific node within a topology."""
    ctx = request.app.state.context
    node = ctx.get_node(uuid, node_uuid)
    if node is None:
        raise HTTPException(
            status_code=404, detail=f"Node {node_uuid} not found in topology {uuid}"
        )
    return {"tapi-topology:node": [node.model_dump(by_alias=True)]}


@router.get("/topology={uuid}/link={link_uuid}")
async def get_link(uuid: str, link_uuid: str, request: Request) -> dict:
    """Return a specific link within a topology."""
    ctx = request.app.state.context
    link = ctx.get_link(uuid, link_uuid)
    if link is None:
        raise HTTPException(
            status_code=404, detail=f"Link {link_uuid} not found in topology {uuid}"
        )
    return {"tapi-topology:link": [link.model_dump(by_alias=True)]}


@router.get("/topology={uuid}/node={node_uuid}/owned-node-edge-point={nep_uuid}")
async def get_nep(uuid: str, node_uuid: str, nep_uuid: str, request: Request) -> dict:
    """Return a specific node edge point."""
    ctx = request.app.state.context
    nep = ctx.get_nep(uuid, node_uuid, nep_uuid)
    if nep is None:
        raise HTTPException(
            status_code=404,
            detail=f"NEP {nep_uuid} not found on node {node_uuid}",
        )
    return {"tapi-topology:owned-node-edge-point": [nep.model_dump(by_alias=True)]}
