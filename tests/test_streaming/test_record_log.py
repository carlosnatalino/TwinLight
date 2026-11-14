"""The truncated record log: append, replay from a time, trim by age."""

from __future__ import annotations

import pytest

from twinlight.streaming.proto import gnmi_pb2
from twinlight.streaming.record_log import InMemoryRecordLog, LogRecord, RecordLog


def _rec(t: int, job: str = "j") -> LogRecord:
    return LogRecord(t, job, gnmi_pb2.SubscribeResponse())


@pytest.fixture
def log() -> InMemoryRecordLog:
    log = InMemoryRecordLog()
    for t in (10, 20, 30):
        log.append([_rec(t, "a"), _rec(t, "b")])
    return log


def test_satisfies_the_protocol(log: InMemoryRecordLog) -> None:
    proto: RecordLog = log
    assert proto.oldest_ns() == 10


def test_replay_is_inclusive_of_start(log: InMemoryRecordLog) -> None:
    assert [(r.logged_ns, r.job_uuid) for r in log.replay(20)] == [
        (20, "a"), (20, "b"), (30, "a"), (30, "b"),
    ]


def test_replay_between_ticks_starts_at_the_next(log: InMemoryRecordLog) -> None:
    assert {r.logged_ns for r in log.replay(21)} == {30}


def test_replay_before_oldest_returns_everything(log: InMemoryRecordLog) -> None:
    assert len(log.replay(0)) == 6


def test_replay_in_the_future_is_empty(log: InMemoryRecordLog) -> None:
    assert log.replay(31) == []


def test_trim_by_age_drops_whole_ticks(log: InMemoryRecordLog) -> None:
    assert log.trim(older_than_ns=25) == 4
    assert log.oldest_ns() == 30
    assert len(log) == 2
    assert log.trim(older_than_ns=25) == 0


def test_empty_log(log: InMemoryRecordLog) -> None:
    log.trim(10**9)
    assert log.oldest_ns() is None
    assert log.replay(0) == []


def test_out_of_order_append_is_rejected(log: InMemoryRecordLog) -> None:
    with pytest.raises(ValueError, match="order"):
        log.append([_rec(5)])
