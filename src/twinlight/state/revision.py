"""Validators of the T-API datastore and the gate that serialises guarded writes.

RFC 8040 §3.4.1 gives the datastore resource an entity-tag and a
last-modified timestamp, and RFC 9110 §13 says how a server evaluates the
conditional request header fields against them. The state behind them lives
here, in the state layer, so that every mutator of ``TapiContext`` moves them
and a write route added later inherits them without knowing they exist.

Two validators, because a T-API body mixes configuration with state the twin
derives (operational states, spectrum occupancy, stream state):

- ``config`` changes only when a ``config true`` node of the T-API YANG
  changes (a connectivity-service, a path-comp-service, an OAM job, a
  restore). It is RFC 8040's datastore entity-tag, which "is only affected by
  configuration data resources and MUST NOT be updated for changes to
  non-configuration data" (§3.4.1.2), and it validates the ``content=config``
  representation.
- ``full`` changes with *anything* a T-API body shows: every configuration
  change, and every change of derived state (an element parameter, a failed
  link, a twin setting, the measurement stream turning ACTIVE). It validates
  the default (``content=all``) and the ``content=nonconfig``
  representations, which carry that state; the configuration tag on them
  would let a client keep a stale operational-state through a 304.

Each entity-tag starts with a nonce drawn once per process, so a tag from
before a restart never matches one after it, whatever the counters say. The
nonce is not part of the physics path (CLAUDE.md constraint 8 concerns
per-element randomness); it only has to differ between runs.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Validator:
    """A revision counter with its last-modified time.

    ``ambiguous`` is True while the whole second of ``modified`` has held
    more than one state, or is the second the process started in (a client
    may hold that second from the previous run). An HTTP-date has one-second
    resolution (RFC 9110 §5.6.7), so a client presenting that second cannot
    be told apart from one that saw an earlier state in it: RFC 9110
    §8.8.2.2 treats such a date as a weak validator, and the twin then
    answers as if the resource had been modified (no 304, a 412 on
    ``If-Unmodified-Since``).
    """

    revision: int = 0
    modified: datetime = field(default_factory=lambda: datetime.now(UTC))
    ambiguous: bool = True

    def bump(self, now: datetime) -> None:
        self.ambiguous = int(now.timestamp()) == int(self.modified.timestamp())
        self.revision += 1
        self.modified = now

    def modified_after(self, date: datetime) -> bool:
        """Whether this state is newer than an HTTP-date (whole seconds)."""
        second = int(self.modified.timestamp())
        limit = int(date.timestamp())
        return second > limit or (second == limit and self.ambiguous)


class DatastoreRevision:
    """The ``config`` and ``full`` validators of the T-API datastore."""

    def __init__(self) -> None:
        self.nonce = secrets.token_hex(6)
        self.config = Validator()
        self.full = Validator(modified=self.config.modified)

    def changed(self, *, config: bool) -> None:
        """Record a change: of configuration (``config``) or of state only."""
        now = datetime.now(UTC)
        self.full.bump(now)
        if config:
            self.config.bump(now)

    def etag(self, kind: str) -> str:
        """The strong entity-tag of the ``config`` or ``full`` representation."""
        validator = self.config if kind == "config" else self.full
        return f'"{self.nonce}-{kind[0]}{validator.revision}"'

    def validator(self, kind: str) -> Validator:
        return self.config if kind == "config" else self.full


class WriteGate:
    """Shared/exclusive gate over every write the HTTP interface accepts.

    A conditional RESTCONF write holds it exclusively from the moment its
    preconditions are evaluated until its response is sent; every other
    write (through any interface: T-API, ``/config``, ``/transparent``,
    ``/admin``) holds it shared. So no client write can land between an
    ``If-Match`` check and the write it guards, while unconditional writes
    still run concurrently with each other as before (their own locks, such
    as the admission lock, still order them). Writer-preferring: once an
    exclusive holder waits, new shared holders queue behind it.
    """

    def __init__(self) -> None:
        self._condition: asyncio.Condition | None = None
        self._shared = 0
        self._exclusive = False
        self._waiting_exclusive = 0

    def _cond(self) -> asyncio.Condition:
        # Created on first use, inside the event loop that serves requests.
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    @asynccontextmanager
    async def shared(self) -> AsyncIterator[None]:
        cond = self._cond()
        async with cond:
            await cond.wait_for(lambda: not self._exclusive and not self._waiting_exclusive)
            self._shared += 1
        try:
            yield
        finally:
            async with cond:
                self._shared -= 1
                cond.notify_all()

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        cond = self._cond()
        async with cond:
            self._waiting_exclusive += 1
            try:
                await cond.wait_for(lambda: not self._exclusive and self._shared == 0)
            finally:
                self._waiting_exclusive -= 1
                cond.notify_all()  # shared waiters held back by this one
            self._exclusive = True
        try:
            yield
        finally:
            async with cond:
                self._exclusive = False
                cond.notify_all()
