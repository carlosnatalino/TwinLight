"""The truncated record log behind the T-API measurement stream.

TR-548 §3.8.1.2 requires a TRUNCATED log for measurement data: recent records
only, older ones discarded by age. §6.1.5 and use case ST-6.4 use it to let a
client that lost its connection resume, without loss, from the logged time of
the last record it processed.

``RecordLog`` is the whole contract the gNMI service depends on, so a shared
backend (Valkey Streams, keyed by logged time) can replace the in-process
``InMemoryRecordLog`` without touching the streaming code.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from twinlight.streaming.proto import gnmi_pb2


@dataclass(frozen=True)
class LogRecord:
    """One logged measurement record.

    ``logged_ns`` is when the record was appended, in nanoseconds since the
    epoch: the time the history extension's ``range.start`` is compared with
    (TR-548 §6.3.4) and the gNMI notification timestamp. Every record of one
    sampling tick shares it.
    """

    logged_ns: int
    job_uuid: str
    response: gnmi_pb2.SubscribeResponse


class RecordLog(Protocol):
    """Append, replay from a time, trim by age."""

    def append(self, records: Sequence[LogRecord]) -> None:
        """Append records, whose ``logged_ns`` must not precede the newest."""

    def replay(self, start_ns: int) -> list[LogRecord]:
        """Every record with ``logged_ns >= start_ns``, in log order."""

    def trim(self, older_than_ns: int) -> int:
        """Drop records with ``logged_ns < older_than_ns``; return how many."""

    def oldest_ns(self) -> int | None:
        """``logged_ns`` of the oldest retained record, or None when empty."""


class InMemoryRecordLog:
    """``RecordLog`` in a deque. Single event loop, no locking needed."""

    def __init__(self) -> None:
        self._records: deque[LogRecord] = deque()

    def __len__(self) -> int:
        return len(self._records)

    def append(self, records: Sequence[LogRecord]) -> None:
        if records and self._records and records[0].logged_ns < self._records[-1].logged_ns:
            raise ValueError("records must be appended in logged-time order")
        self._records.extend(records)

    def replay(self, start_ns: int) -> list[LogRecord]:
        # Scan from the newest end: a live subscriber asks only for the last
        # tick, so this costs the size of the answer, not of the log.
        out: list[LogRecord] = []
        for record in reversed(self._records):
            if record.logged_ns < start_ns:
                break
            out.append(record)
        out.reverse()
        return out

    def trim(self, older_than_ns: int) -> int:
        dropped = 0
        while self._records and self._records[0].logged_ns < older_than_ns:
            self._records.popleft()
            dropped += 1
        return dropped

    def oldest_ns(self) -> int | None:
        return self._records[0].logged_ns if self._records else None
