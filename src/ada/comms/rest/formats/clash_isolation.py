"""Run the heavy half of a clash job in a killable forked child -- the clash twin of ``convert.py``.

WHY. The clash handlers used to run their computation through ``loop.run_in_executor``, and a
thread cannot be killed: a user who cancelled a long contributed-checker run saw the job flip to
``cancelled`` while the worker kept computing (and then uploaded the orphaned result). The
conversion path solved exactly this with a forked child the parent can SIGTERM/SIGKILL
(``subprocess_convert.run_isolated``), polled against ``audit_log`` for cancellation, with the same
timeout and RSS watchdogs. This module puts the clash jobs on that same machinery rather than a
second copy of it.

WHERE THE I/O HAPPENS. The child inherits the parent's asyncio loop, NATS and asyncpg handles and
the obstore client, none of which survive a fork as working clients -- so the child never touches
them. Everything a handler can fetch up front (the source file, the cached result) is fetched in
the parent before forking. What it cannot -- a provider reading its own published blobs, a group
reading each member's source -- goes through :class:`ChildStorage`, a synchronous facade with the
``_SyncStorageFacade`` surface whose every call is a request the PARENT serves with its real
storage and answers through a file in the child's work dir. That beats prefetching (which blobs a
provider reads is the provider's business; core cannot list them) and beats building a fresh
storage client in the child (obstore's runtime is not fork-safe, and it would duplicate the
deployment's storage configuration). The child writes its outputs into the work dir; the parent
uploads them only after a clean exit, so a cancelled or killed job never publishes a result.

WHERE THERE IS NO FORK (Windows, a dev box, the test suite there) the same ``work`` function runs in
a thread with the parent's ``_SyncStorageFacade``. It cannot be killed mid-run, but a cancellation
that lands while it runs still stops the upload. The queue-less LOCAL transport does not come
through here at all (``local_jobs`` runs the clash functions directly with its ``cancel_event``).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import tempfile
import traceback as tb_module
from typing import Any, Callable

from ada.config import logger

from .. import db as db_module
from .. import subprocess_convert
from ..queue import JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done

__all__ = ["ChildStorage", "run_clash_work", "storage_serve"]

#: ``work(out_dir, storage, progress)`` -- the heavy half of a clash job. Writes its outputs into
#: ``out_dir``; ``storage`` is a synchronous facade (or None when the job asked for none);
#: ``progress(stage, frac)`` reports a stage, and the last one reported is the stage a failure is
#: recorded against.
ClashWork = Callable[[pathlib.Path, Any, Callable[[str, float], None]], None]

#: Exceptions whose message is the whole story -- no traceback on the audit row, same as the
#: in-thread handlers recorded them.
_QUIET_ERRORS = frozenset({"UnsupportedFormat"})


class ChildStorage:
    """``_SyncStorageFacade``'s surface, served by the parent (see the module docstring).

    Bytes cross through files in the child's work dir rather than the pipe: a manifest is small,
    but a member's source can be hundreds of MB and the pipe carries one JSON line per request.
    """

    def __init__(self, child: "subprocess_convert.IsolatedChild") -> None:
        self._child = child
        self._n = 0

    def _scratch(self) -> pathlib.Path:
        self._n += 1
        return self._child.work_dir / f".rpc-{self._n}"

    def list_keys(self, prefix: str = "") -> list[str]:
        return list(self._child.call("list_keys", prefix=prefix) or [])

    def fetch_to_path(self, key: str, dest):
        self._child.call("fetch_to_path", key=key, dest=os.fspath(dest))
        return dest

    def get_bytes(self, key: str) -> bytes:
        tmp = self._scratch()
        try:
            self._child.call("get_bytes", key=key, dest=str(tmp))
            return tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)

    def put_bytes(
        self, key: str, data: bytes, content_encoding: "str | None" = None, pre_compressed: bool = False
    ) -> None:
        tmp = self._scratch()
        try:
            tmp.write_bytes(data)
            self._child.call(
                "put_bytes", key=key, src=str(tmp), content_encoding=content_encoding, pre_compressed=pre_compressed
            )
        finally:
            tmp.unlink(missing_ok=True)


def storage_serve(storage, scope) -> "subprocess_convert.ServeFn":
    """The parent half of :class:`ChildStorage`: each op runs against the real async storage."""

    async def _serve(op: str, args: dict) -> Any:
        if op == "list_keys":
            prefix = str(args.get("prefix") or "")
            return [e.key for e in await storage.list(scope) if e.key.startswith(prefix)]
        if op == "fetch_to_path":
            await storage.stream_to_path(scope, args["key"], pathlib.Path(args["dest"]))
            return args["dest"]
        if op == "get_bytes":
            pathlib.Path(args["dest"]).write_bytes(await storage.get_bytes(scope, args["key"]))
            return args["dest"]
        if op == "put_bytes":
            data = pathlib.Path(args["src"]).read_bytes()
            extra = {"pre_compressed": True} if args.get("pre_compressed") else {}
            await storage.put_bytes(scope, args["key"], data, content_encoding=args.get("content_encoding"), **extra)
            return None
        raise ValueError(f"unknown storage op {op!r}")

    return _serve


def _fork_available() -> bool:
    # A function (not the constant) so a test can choose the path without touching the platform.
    return subprocess_convert.HAVE_POSIX_FORK


async def _report_cancelled(queue, job_id: str, label: str) -> None:
    # The audit row is already 'cancelled' (the cancel endpoint set it, and it is what the
    # watchdog polled) -- mirror convert.py and leave it alone rather than flip it to error/done.
    logger.info("worker: %s %s cancelled by user; no result uploaded", label, job_id)
    try:
        await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error="cancelled by user")
    except Exception:
        logger.debug("worker: cancelled-status update failed for %s", job_id, exc_info=True)


async def run_clash_work(
    work: ClashWork,
    *,
    job: Job,
    queue,
    db_pool,
    started_at: float,
    stage: str,
    storage=None,
    scope=None,
    timeout_s: float | None = None,
    label: str = "clash check",
) -> pathlib.Path | None:
    """Run ``work`` killably and return its output dir, or None once the job's fate is reported.

    On success the caller reads/uploads what ``work`` wrote and then removes the dir
    (``shutil.rmtree``). On None, the queue entry and audit row already say what happened:
    ``cancelled`` (no upload, audit untouched), or ``error`` naming the timeout, the
    out-of-memory kill, the crash signal, or the exception ``work`` raised -- recorded against the
    last stage ``work`` reported, ``stage`` until it reports one.

    ``storage`` + ``scope`` give ``work`` a synchronous storage facade (served by the parent when
    forked); leave them None for work that needs nothing beyond what the caller fetched.
    """
    job_id = job.job_id
    last_stage = stage

    async def _cancel_check() -> bool:
        if db_pool is None:
            return False
        try:
            return await db_module.audit_is_cancelled(db_pool, job_id)
        except Exception:
            return False

    async def _fail(msg: str, trace: str | None, metrics: dict | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=last_stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace, metrics=metrics or None)

    if _fork_available():
        from ..worker.state import _touch_liveness

        async def _on_progress(st: str, frac: float) -> None:
            nonlocal last_stage
            last_stage = st or last_stage
            _touch_liveness()
            try:
                await queue.update(job_id, stage=last_stage, progress=frac)
            except Exception:
                logger.debug("queue.update from clash progress failed", exc_info=True)

        async def _on_sample(_sample) -> None:
            # A long check blocks this job's slot in the pull loop exactly like a conversion does.
            _touch_liveness()

        def _in_child(child) -> None:
            child_storage = ChildStorage(child) if storage is not None else None
            work(child.work_dir, child_storage, child.progress)

        try:
            res = await subprocess_convert.run_isolated(
                _in_child,
                label=label,
                child_name=label,
                crash_hint="(SIGSEGV/SIGABRT typically means a native fault inside the geometry stack).",
                work_prefix="adapy-clash-",
                on_progress=_on_progress,
                on_sample=_on_sample,
                timeout_s=timeout_s,
                cancel_check=_cancel_check,
                serve=storage_serve(storage, scope) if storage is not None else None,
            )
        except Exception as exc:
            # The parent-side machinery failed (fork, pipes): the child never ran or we lost it.
            logger.exception("worker: %s subprocess wrapper failed for job %s", label, job_id)
            await _fail(str(exc), tb_module.format_exc())
            return None

        if res.cancelled:
            res.cleanup()
            await _report_cancelled(queue, job_id, label)
            return None
        if not res.ok:
            metrics = dict(res.final_metrics)
            if res.signal_name == "TIMEOUT" and timeout_s:
                msg = f"{label} timed out after {timeout_s / 60.0:g} min (the configured timeout) and was terminated."
            elif res.error_type is not None:
                # The exception ``work`` raised, worded as the in-thread handlers always recorded it.
                msg = res.error_message or res.error or res.error_type
            else:
                msg = res.error or f"{label} subprocess produced no output"
            if res.signal_name:
                logger.warning("worker: %s child for job %s ended via %s", label, job_id, res.signal_name)
            trace = None if res.error_type in _QUIET_ERRORS else res.traceback
            await _fail(msg, trace, metrics)
            return None
        out_dir = res.work_dir
    else:
        # No fork on this platform: the same work in a thread (see the module docstring).
        from ..worker.source_nodes import _SyncStorageFacade

        loop = asyncio.get_running_loop()
        out_dir = pathlib.Path(tempfile.mkdtemp(prefix="adapy-clash-"))
        sync_storage = _SyncStorageFacade(storage, scope, loop) if storage is not None else None

        def _progress(st: str, frac: float) -> None:
            nonlocal last_stage
            last_stage = st or last_stage
            asyncio.run_coroutine_threadsafe(queue.update(job_id, stage=last_stage, progress=frac), loop)

        try:
            await loop.run_in_executor(None, work, out_dir, sync_storage, _progress)
        except Exception as exc:
            shutil.rmtree(out_dir, ignore_errors=True)
            if type(exc).__name__ not in _QUIET_ERRORS:
                logger.exception("worker: %s failed for job %s", label, job_id)
            trace = None if type(exc).__name__ in _QUIET_ERRORS else tb_module.format_exc()
            await _fail(str(exc), trace)
            return None

    # A cancellation that landed after the last watchdog poll (or at any point, on the thread
    # path, which cannot be interrupted) must still keep the result from being published.
    if await _cancel_check():
        shutil.rmtree(out_dir, ignore_errors=True)
        await _report_cancelled(queue, job_id, label)
        return None
    return out_dir
