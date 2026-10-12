"""The machinery an in-API "run" is driven with: poll a job to done, heartbeat, cancel, skip.

Two kinds of run share it. A provider SWEEP (``routes/asset_sweeps.py``) fetches and publishes
every collection one after another; a search-result EXPORT (``routes/asset_exports.py``) writes the
hits of each (provider, collection) as IFC files, several at once. Both are a ``*_runs`` row and
one ``*_items`` row per unit of work, both are driven by a task in the API process that started
them, and both need the same three answers from every poll of a job:

* is the run still wanted? -- the driver stamps the run's heartbeat on every poll and gets its
  status back, so a cancel from any replica is seen within one poll interval. A listing reports a
  ``running`` run with an old heartbeat as ``interrupted``: a restart loses the driver, and says so.
* is THIS item still wanted? -- "skip current" stops one item and lets the run carry on. The
  request is stored on the run (``skip_position``) and comes back with the heartbeat, so a skip
  costs no extra round trip per poll. Only the item at that position reacts to it.
* has the job finished, and how long did it wait before a worker took it? -- ``queued_ms`` and the
  run time are measured here, to the poll's resolution, because a run is a measurement first.

A heartbeat answers a :class:`RunBeat`: the run's status AS A STRING (so ``beat != "running"`` reads
the way it always did, and a test fake that answers a plain ``"running"`` still works) carrying the
pending skip request as attributes.

Cancelling a job is best effort on every transport (the queue's cancel only nudges the record), so
an item that is skipped or cancelled is recorded as such at once, whatever the job then does.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from typing import Any, Awaitable, Callable, Coroutine

from ada.config import logger

__all__ = [
    "ItemSkipped",
    "POLL_INTERVAL_S",
    "RunBeat",
    "RunCancelled",
    "json_of",
    "ms",
    "spawn",
    "user_label",
    "wait_for_job",
]

POLL_INTERVAL_S = 3.0

#: Drivers in flight in this process. Held so the event loop's weak reference is not the only one.
_DRIVERS: set[asyncio.Task] = set()


class RunCancelled(Exception):
    """The run is no longer ``running`` (cancelled, or finished elsewhere): stop driving it."""


class ItemSkipped(Exception):
    """Someone asked for THIS item to be skipped; its job has been asked to stop."""

    def __init__(self, by: str | None = None) -> None:
        super().__init__(f"skipped by {by or 'a user'}")
        self.by = by


class RunBeat(str):
    """A heartbeat's answer: the run's status, with the pending skip request riding along.

    A ``str`` so every caller that compared the old plain status keeps working unchanged.
    """

    skip_position: int | None = None
    skip_by: str | None = None

    def __new__(cls, status: str, skip_position: int | None = None, skip_by: str | None = None) -> "RunBeat":
        beat = super().__new__(cls, status)
        beat.skip_position = skip_position
        beat.skip_by = skip_by
        return beat


def ms(seconds: float) -> int:
    return round(seconds * 1000)


def user_label(user: Any) -> str | None:
    """Who a skip is recorded as: the name a person would recognise, else the subject."""
    for attr in ("display_name", "email", "sub"):
        value = getattr(user, attr, None)
        if value:
            return str(value)
    return None


def json_of(data: bytes) -> Any:
    """A JSON document from the store. Summaries are stored gzip-at-rest, and a backend may or
    may not hand them back inflated."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data)


def spawn(coro: Coroutine[Any, Any, None], *, name: str) -> asyncio.Task:
    """Start a driver task and keep a strong reference to it until it ends."""
    task = asyncio.create_task(coro, name=name)
    _DRIVERS.add(task)
    task.add_done_callback(_DRIVERS.discard)
    return task


async def _cancel_job(ctx, job_id: str, label: str) -> None:
    try:
        await ctx.jobs.cancel(job_id)
    except Exception:  # noqa: BLE001 - best effort; the run/item is recorded as stopped either way
        logger.warning("%s: could not cancel job %s", label, job_id, exc_info=True)


async def wait_for_job(
    ctx,
    job_id: str,
    *,
    timeout_s: float,
    beat: Callable[[], Awaitable[Any]],
    position: int | None = None,
    poll_s: float = POLL_INTERVAL_S,
    label: str = "run",
) -> tuple[int, int]:
    """Poll ``job_id`` to done. Returns ``(queued_ms, ran_ms)``.

    ``beat()`` stamps the run's heartbeat and answers its status (a :class:`RunBeat` or a plain
    string). Raises :class:`RunCancelled` (after cancelling the job) when the run is no longer
    running, :class:`ItemSkipped` (after cancelling the job) when the beat names ``position`` as
    the item to skip, and RuntimeError when the job failed or outlived ``timeout_s``.
    """
    t0 = time.monotonic()
    started: float | None = None
    while True:
        status = await beat()
        if status != "running":
            await _cancel_job(ctx, job_id, label)
            raise RunCancelled
        if position is not None and getattr(status, "skip_position", None) == position:
            await _cancel_job(ctx, job_id, label)
            raise ItemSkipped(getattr(status, "skip_by", None))
        snap = await ctx.jobs.status(job_id)
        state = (snap.status if snap is not None else "").lower()
        now = time.monotonic()
        if started is None and state not in ("", "queued"):
            started = now
        if state == "done":
            started = started if started is not None else now
            return ms(started - t0), ms(now - started)
        if state in ("error", "cancelled"):
            detail = (snap.payload or {}).get("error") if snap is not None else None
            raise RuntimeError(str(detail or f"the job was {state}"))
        if snap is None and now - t0 > 60:
            raise RuntimeError("the job is no longer known to the queue")
        if now - t0 > timeout_s:
            raise RuntimeError(f"still {state or 'unknown'} after {round(timeout_s / 60)} min (not cancelled)")
        await asyncio.sleep(poll_s)
