"""The T-API performance-measurement stream: sampler, log and stream state.

One stream, as TR-548 §6.2.2 requires: measurement-details records, whole
entity, periodic, in a truncated log, delivered over gNMI as protobuf.

Every ``streaming.sample_interval_s`` the sampler takes one OPM sample of
every connectivity service — through ``sample_service_opm``, the function
behind ``GET /internal/opm``, at one instant for all services — and appends
one record per OAM job. It runs whether or not anyone is subscribed (the log
is what lets a client resume, TR-548 ST-6.4), and every subscriber is sent
the same logged record objects.

Stream state follows TR-548 ST-1.1 / ST-6.1: ALIGNING from creation until the
first tick has been logged, ACTIVE from then on. No records exist while
ALIGNING, so a client that connects early simply receives nothing yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
import uuid as uuid_lib
from collections.abc import Callable
from enum import Enum
from typing import Any

from twinlight.api import internal
from twinlight.streaming.record_log import InMemoryRecordLog, LogRecord, RecordLog
from twinlight.streaming.records import measurement_details, record_response

logger = logging.getLogger(__name__)

# Name-based, so the identifiers a client discovered survive a restart.
_STREAM_NAMESPACE = uuid_lib.uuid5(
    uuid_lib.NAMESPACE_URL, "urn:twinlight:tapi-streaming"
)
SUPPORTED_STREAM_TYPE_UUID = str(
    uuid_lib.uuid5(_STREAM_NAMESPACE, "supported-stream-type/measurement-details")
)
AVAILABLE_STREAM_UUID = str(
    uuid_lib.uuid5(_STREAM_NAMESPACE, "available-stream/measurement-details/gnmi")
)


class StreamState(str, Enum):
    """tapi-streaming:stream-state identities used by this stream."""

    ALIGNING = "tapi-streaming:STREAM_STATE_ALIGNING"
    ACTIVE = "tapi-streaming:STREAM_STATE_ACTIVE"


class MeasurementStream:
    """Samples the twin into a ``RecordLog`` and wakes subscribers."""

    def __init__(
        self,
        ctx: Any,
        config: Any,
        *,
        log: RecordLog | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._ctx = ctx
        self._config = config
        self.log: RecordLog = log if log is not None else InMemoryRecordLog()
        self._clock_ns = clock_ns
        self.state = StreamState.ALIGNING
        self._last_logged_ns = 0
        # Bumped after every tick; subscribers wait for it to change.
        self._generation = 0
        self._new_records: asyncio.Condition | None = None

    # -- configuration views ----------------------------------------------

    @property
    def sample_interval_s(self) -> float:
        return float(self._config.streaming.sample_interval_s)

    @property
    def retention_ns(self) -> int:
        return int(self._config.streaming.record_retention_min) * 60 * 1_000_000_000

    @property
    def connection_address(self) -> str:
        return self._config.streaming.connection_address(self._config.server.grpc_port)

    def now_ns(self) -> int:
        """The clock logged times come from, in ns since the epoch."""
        return self._clock_ns()

    @property
    def last_logged_ns(self) -> int:
        """Logged time of the newest tick, 0 before the first."""
        return self._last_logged_ns

    def _condition(self) -> asyncio.Condition:
        # Created on first use, inside the event loop that serves gRPC.
        if self._new_records is None:
            self._new_records = asyncio.Condition()
        return self._new_records

    # -- sampling -----------------------------------------------------------

    async def tick(self) -> int:
        """Sample every service once, log the records; return how many."""
        ctx, cfg = self._ctx, self._config
        t = internal.wall_clock()
        interval_ns = round(self.sample_interval_s * 1e9)
        samples: list[tuple[Any, dict[str, Any]]] = []
        for svc in ctx.get_services():
            try:
                sample = await internal.sample_service_opm(ctx, cfg, svc, t)
            except Exception:
                logger.exception("OPM sample of service %s failed", svc.uuid)
                # Encoded as MISSING throughout: a record was due and no
                # measurement was obtained (TR-548 ST-6.3).
                sample = {"service-uuid": svc.uuid, "timestamp": t}
            samples.append((svc, sample))

        # Strictly increasing per tick, so "records after time T" is exact.
        logged_ns = max(self._clock_ns(), self._last_logged_ns + 1)
        records: list[LogRecord] = []
        for svc, sample in samples:
            # Sampling awaits baselines, so a service may have been deleted
            # meanwhile: its job is gone and it gets no record.
            job = ctx.oam_job_for_service(svc.uuid)
            if job is None:
                continue
            details = measurement_details(
                sample,
                native_resource_id=ctx.receiver_gnpy_uid(svc) or "",
                sample_interval_ns=interval_ns,
            )
            records.append(
                LogRecord(logged_ns, job.uuid, record_response(job.uuid, logged_ns, details))
            )

        self.log.append(records)
        self.log.trim(logged_ns - self.retention_ns)
        self._last_logged_ns = logged_ns
        if self.state is StreamState.ALIGNING:
            self.state = StreamState.ACTIVE
            logger.info("Measurement stream ACTIVE (first tick logged)")
        self._generation += 1
        cond = self._condition()
        async with cond:
            cond.notify_all()
        return len(records)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Tick at a fixed rate until ``stop`` is set or the task is cancelled."""
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        logger.info(
            "Measurement stream sampling every %.3g s, retention %d min",
            self.sample_interval_s,
            self._config.streaming.record_retention_min,
        )
        while stop is None or not stop.is_set():
            try:
                await self.tick()
            except Exception:
                logger.exception("Measurement stream tick failed")
            deadline += self.sample_interval_s
            # A tick that overran its slot starts the next one immediately
            # rather than trying to catch up with a burst.
            deadline = max(deadline, loop.time())
            delay = deadline - loop.time()
            if stop is None:
                await asyncio.sleep(delay)
            else:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=delay)

    # -- subscriber side ------------------------------------------------------

    async def records_after(self, after_ns: int) -> list[LogRecord]:
        """Records logged after ``after_ns``, waiting for the next tick if none."""
        while True:
            generation = self._generation
            records = self.log.replay(after_ns + 1)
            if records:
                return records
            cond = self._condition()
            async with cond:
                await cond.wait_for(functools.partial(self._ticked_since, generation))

    def _ticked_since(self, generation: int) -> bool:
        return self._generation != generation
