"""Entry point: starts both FastAPI (uvicorn) and gRPC servers."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn

from twinlight.app import create_app
from twinlight.cli import load_config
from twinlight.config import TwinConfig
from twinlight.grpc_server import start_grpc_server

logger = logging.getLogger(__name__)


def configure_logging(config: TwinConfig) -> None:
    """Set up Python logging from the validated config."""
    logging.basicConfig(
        level=config.logging.level.value,
        format=config.logging.format,
        force=True,
    )


async def async_main() -> None:
    config = load_config()
    configure_logging(config)

    log = logging.getLogger(__name__)
    log.info("Starting TwinLight")
    log.info("Topology: %s", config.gnpy.topology)

    if config.restore_path is not None:
        log.info("Restoring from snapshot: %s", config.restore_path)

    app = create_app(config)

    if config.restore_path is not None:
        from pathlib import Path
        app.state.context.restore_from(Path(config.restore_path))

    log.info("REST server: %s:%d", config.server.rest_host, config.server.rest_port)
    log.info("gRPC server port: %d", config.server.grpc_port)

    uvicorn_config = uvicorn.Config(
        app,
        host=config.server.rest_host,
        port=config.server.rest_port,
        log_level=config.logging.level.value.lower(),
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    shutdown_event: asyncio.Event = asyncio.Event()

    def _request_shutdown() -> None:
        if shutdown_event.is_set():
            return
        shutdown_event.set()
        log.info("Shutdown requested (Ctrl+C or SIGTERM)")

    task_uvicorn = asyncio.create_task(uvicorn_server.serve())
    task_grpc = asyncio.create_task(
        start_grpc_server(
            app,
            config.server.grpc_port,
            shutdown_event=shutdown_event,
        )
    )
    # Let uvicorn enter serve() and install its signal handlers, then we overwrite with ours
    # so that both REST and gRPC shut down gracefully (uvicorn uses signal.signal() in serve()).
    await asyncio.sleep(0.05)
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _request_shutdown)
        loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
    except OSError:
        # add_signal_handler not supported (e.g. Windows)
        pass

    async def supervisor() -> None:
        await shutdown_event.wait()
        log.info("Shutting down servers...")
        # Ask uvicorn to leave its own serve() loop rather than calling
        # shutdown() underneath it: serve() then runs the full graceful
        # path itself (stop accepting, drain connections, close the ASGI
        # lifespan). Calling shutdown() directly races that loop and
        # cancels the lifespan receive mid-flight, which surfaces as a
        # CancelledError traceback on an otherwise clean stop.
        uvicorn_server.should_exit = True
        await asyncio.gather(task_uvicorn, task_grpc, return_exceptions=True)
        log.info("Shutdown complete")

    task_supervisor = asyncio.create_task(supervisor())

    # return_exceptions so that one server failing cannot cancel the other
    # mid-shutdown; genuine failures are re-raised below, while the
    # CancelledErrors that accompany a normal stop are ignored.
    results = await asyncio.gather(
        task_uvicorn, task_grpc, task_supervisor, return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException) and not isinstance(
            result, asyncio.CancelledError
        ):
            raise result


def main() -> None:
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Ctrl+C before the signal handlers are installed, or a stray
        # cancellation during teardown. Either way this is a requested
        # stop, not a failure — exit 0 so `docker compose down` and
        # process supervisors don't report a crash.
        if logger.isEnabledFor(logging.INFO):
            logger.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
