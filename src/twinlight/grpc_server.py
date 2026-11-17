"""gRPC server for the T-API measurement stream over gNMI (TR-548 §6.3)."""

from __future__ import annotations

import asyncio
import logging

import grpc
from fastapi import FastAPI

from twinlight.auth import TokenAuthenticator
from twinlight.streaming.gnmi_service import TapiGnmiServicer
from twinlight.streaming.interceptors import TokenAuthInterceptor
from twinlight.streaming.proto import gnmi_pb2_grpc

logger = logging.getLogger(__name__)

DEFAULT_GRPC_GRACE_S: float = 5.0


def build_grpc_server(app: FastAPI) -> grpc.aio.Server:
    """A gRPC server serving gNMI for ``app``, not yet bound or started.

    The token interceptor uses the authenticator ``create_app`` attached to
    ``app.state``, so gNMI accepts exactly the tokens REST does. Without
    tokens no interceptor is installed and calls go straight to the servicer.
    """
    authenticator: TokenAuthenticator = app.state.authenticator
    interceptors = [TokenAuthInterceptor(authenticator)] if authenticator.enabled else []
    server = grpc.aio.server(interceptors=interceptors)
    gnmi_pb2_grpc.add_gNMIServicer_to_server(
        TapiGnmiServicer(app.state.measurement_stream), server
    )
    return server


async def start_grpc_server(
    app: FastAPI,
    port: int,
    *,
    shutdown_event: asyncio.Event | None = None,
    grace_seconds: float = DEFAULT_GRPC_GRACE_S,
) -> None:
    """Start the gNMI gRPC server for ``app``'s measurement stream.

    The servicer reads the stream's record log directly: values never pass
    through REST. When API tokens are configured, every call must carry one
    as a bearer token.

    If shutdown_event is set, the server will stop gracefully (stop with
    grace_seconds, then close). Otherwise it runs until wait_for_termination().
    """
    server = build_grpc_server(app)
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
        logger.info("gNMI gRPC server stopped")
