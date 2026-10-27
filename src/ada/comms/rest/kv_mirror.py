"""A live in-memory copy of one NATS KV bucket, kept by ONE long-lived watcher.

Why this exists. Everything the API needs to know about the bucket as a whole
— which workers are online and what they serve, which jobs have finished, which
compression sweeps are recorded — used to be answered by scanning it: list
every key, then fetch the values one by one. A scan replays the whole bucket,
delete markers included, through a fresh server-side consumer. Routing did that
once per enqueued job, so loading a large model meant a full replay per job,
and the consumers each scan left behind are what OOM-killed the server.

A mirror replays the bucket ONCE, when it starts, and from then on only
receives what changes. Readers ask the mirror, which costs no NATS round trip.
The server holds one consumer per mirror for the life of the process.

Delete markers. A KV delete or purge leaves a marker that NATS keeps until it
is removed explicitly, so a bucket that sees a job per conversion accumulates
one marker per job ever run — and every replay sends them all. The mirror sees
every marker arrive, which makes it the natural place to know which ones are
old enough to drop; :meth:`KvMirror.tombstones_older_than` lists them.

Freshness. The watcher is an ordered consumer: nats-py's heartbeat watchdog
recreates it from the last sequence it saw after a disconnect, so the mirror
catches up on its own once the server is back. Until then readers see the
last state the mirror held, which is what a reader that had just scanned
would have seen too.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from ada.config import logger

#: How long the server keeps the mirror's consumer after the process holding it
#: has gone. Long enough to ride out a reconnect, short enough that a crashed
#: API pod does not leave a replaying consumer behind for minutes.
MIRROR_INACTIVE_S = 60.0

#: Wait before re-opening the watcher when it fails outright (rather than
#: being reset by nats-py, which happens without our involvement).
RESTART_BACKOFF_S = 5.0

_DELETE_OPS = ("DEL", "PURGE")


@dataclass(frozen=True)
class MirrorEntry:
    value: bytes
    revision: int


@dataclass(frozen=True)
class Tombstone:
    revision: int
    #: Server time the marker was written, as a unix timestamp.
    created: float


def _timestamp(created) -> float:
    if isinstance(created, datetime):
        return created.timestamp()
    if isinstance(created, (int, float)):
        return float(created)
    return time.time()


class KvMirror:
    """Mirror of one KV bucket. ``start()`` it once; read it any time after
    :attr:`ready` is set. Not ready means "use another way to answer"."""

    def __init__(self, kv, name: str):
        #: The ``KeyValue`` handle this mirrors.
        self.kv = kv
        self.name = name
        self._entries: dict[str, MirrorEntry] = {}
        self._tombstones: dict[str, Tombstone] = {}
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._watcher = None

    @property
    def ready(self) -> bool:
        """True once the initial replay has finished."""
        return self._ready.is_set()

    async def wait_ready(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=f"kv-mirror-{self.name}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # --- reads ------------------------------------------------------------

    def get(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        return entry.value if entry is not None else None

    def items(self, prefix: str = "") -> Iterator[tuple[str, bytes]]:
        """``(key, value)`` for every live key starting with ``prefix``.

        A snapshot: the mirror may change while the caller iterates over the
        result, and the caller never sees a half-applied update.
        """
        return iter([(k, e.value) for k, e in self._entries.items() if k.startswith(prefix)])

    def tombstones_older_than(self, age_s: float, now: float | None = None) -> list[tuple[str, int]]:
        """``(key, revision)`` of every delete marker written more than
        ``age_s`` ago."""
        cutoff = (time.time() if now is None else now) - age_s
        return [(k, t.revision) for k, t in self._tombstones.items() if t.created < cutoff]

    def forget_tombstone(self, key: str, revision: int) -> None:
        """Drop a marker the caller has removed from the server. Only that
        revision: a newer write to the key has already replaced it here."""
        t = self._tombstones.get(key)
        if t is not None and t.revision == revision:
            del self._tombstones[key]

    @property
    def size(self) -> tuple[int, int]:
        """``(live keys, delete markers)``."""
        return len(self._entries), len(self._tombstones)

    # --- the watcher -----------------------------------------------------

    def apply(self, key: str, value: bytes | None, revision: int, operation: str | None, created) -> None:
        """Fold one KV update into the mirror. Public so tests can drive it."""
        current = self._entries.get(key) or self._tombstones.get(key)
        if current is not None and current.revision >= revision:
            return  # a redelivery, or an update older than what we hold
        if operation in _DELETE_OPS:
            self._entries.pop(key, None)
            self._tombstones[key] = Tombstone(revision=revision, created=_timestamp(created))
        else:
            self._tombstones.pop(key, None)
            self._entries[key] = MirrorEntry(value=value or b"", revision=revision)

    async def _run(self) -> None:
        while True:
            try:
                await self._watch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("kv mirror %s: watcher failed; reopening in %.0fs", self.name, RESTART_BACKOFF_S)
            await asyncio.sleep(RESTART_BACKOFF_S)

    async def _watch_once(self) -> None:
        watcher = await self.kv.watch(">", inactive_threshold=MIRROR_INACTIVE_S)
        replaying = True
        seen: set[str] = set()
        try:
            async for entry in watcher:
                if entry is None:
                    # End of the initial replay. Anything we held that the
                    # replay did not mention is gone from the server (it was
                    # removed while a previous watcher was down).
                    if replaying:
                        for key in [k for k in self._entries if k not in seen]:
                            del self._entries[key]
                        for key in [k for k in self._tombstones if k not in seen]:
                            del self._tombstones[key]
                        replaying = False
                        seen = set()
                        live, markers = self.size
                        logger.info("kv mirror %s: ready (%d keys, %d delete markers)", self.name, live, markers)
                        self._ready.set()
                    continue
                if replaying:
                    seen.add(entry.key)
                self.apply(entry.key, entry.value, entry.revision, entry.operation, entry.created)
        finally:
            try:
                await watcher.stop()
            except Exception:
                pass
