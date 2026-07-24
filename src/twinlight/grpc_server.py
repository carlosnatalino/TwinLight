"""gRPC server setup for gNMI service."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import grpc
import httpx
from fastapi import FastAPI

from twinlight.streaming.gnmi_service import GnmiServicer
from twinlight.streaming.proto import gnmi_pb2_grpc

logger = logging.getLogger(__name__)

DEFAULT_GRPC_GRACE_S: float = 5.0


async def start_grpc_server(
    app: FastAPI,
    port: int,
    *,
    shutdown_event: Optional[asyncio.Event] = None,
    grace_seconds: float = DEFAULT_GRPC_GRACE_S,
) -> None:
    """Start the gNMI gRPC server.

    Uses httpx.ASGITransport to route gNMI requests to the FastAPI app
    in-process (no TCP round-trip).

    If shutdown_event is set, the server will stop gracefully (stop with
    grace_seconds, then close). Otherwise it runs until wait_for_termination().
    """
    transport = httpx.ASGITransport(app=app)
    http_client = httpx.AsyncClient(
        transport=transport, base_url="http://localhost"
    )

    server = grpc.aio.server()
    servicer = GnmiServicer(http_client)
    gnmi_pb2_grpc.add_gNMIServicer_to_server(servicer, server)

    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)

    logger.info("gNMI gRPC server starting on port %d", port)
    await server.start()
    logger.info("gNMI gRPC server started on port %d", port)

    try:
        if shutdown_event is not None:
            # Race "someone asked us to shut down" against "the server
            # terminated on its own".
            #
            # NB: the termination task must NOT be cancelled when the
            # shutdown event wins. ``grpc.aio``'s wait_for_termination is
            # backed by the server's internal shutdown future, so
            # cancelling it poisons that future and the subsequent
            # ``server.stop()`` raises CancelledError instead of shutting
            # down cleanly. Only the plain Event.wait() is safe to cancel;
            # the termination task is awaited below once we have actually
            # asked the server to stop.
            wait_termination = asyncio.create_task(server.wait_for_termination())
            wait_shutdown = asyncio.create_task(shutdown_event.wait())
            await asyncio.wait(
                [wait_termination, wait_shutdown],
                return_when=asyncio.FIRST_COMPLETED,
            )
            wait_shutdown.cancel()

            if shutdown_event.is_set():
                logger.info(
                    "gNMI gRPC server stopping (grace=%s s)", grace_seconds
                )
                await server.stop(grace_seconds)
            await wait_termination
        else:
            await server.wait_for_termination()
    except asyncio.CancelledError:
        # Cancelled from outside (e.g. the supervisor tearing tasks down):
        # stop the server without a grace period and exit quietly rather
        # than propagating a CancelledError out of the entry point.
        logger.info("gNMI gRPC server cancelled — stopping immediately")
        await server.stop(None)
    finally:
        await http_client.aclose()
        logger.info("gNMI gRPC server stopped")
