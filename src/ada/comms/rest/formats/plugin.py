"""A plugin's on-demand backend job (``plugin_job``).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
import threading
import time
import traceback as tb_module

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.memory import _read_self_proc_io, _read_self_rusage, _read_self_vmhwm_kb
from ..worker.source_nodes import _source_nodes_recorder, _SyncStorageFacade
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_plugin_job(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Run a plugin's on-demand backend job — the generic dispatch (core names no
    plugin). Synthetic: no source file; the plugin fetches whatever it needs via
    the scope-bound storage facade.

    ``conversion_options`` carries ``{"plugin_id": str, "options": dict,
    "derived_prefix": str | None}``. The worker resolves the plugin's advertised
    ``job_entrypoint`` (``"module:callable"``) from the backend registry the pool
    preloaded (``ADA_WORKER_PRELOAD`` / ``ada.plugins``), calls it in an executor
    with the sync storage facade + a progress bridge + the derived-blob prefix,
    and stores the returned summary dict (JSON, gzipped) at ``job.derived_key``.
    The plugin owns writing its own sidecar bundle under its reserved prefix.
    """
    import importlib
    import json

    from ada.plugins import plugin_backend_spec

    job_id = job.job_id
    opts = job.conversion_options or {}
    plugin_id = opts.get("plugin_id")
    if not plugin_id:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="plugin",
            error="conversion_options.plugin_id is required for a plugin_job",
        )
        await _audit_done(db_pool, job_id, "error", "missing plugin_id", started_at)
        return

    spec = plugin_backend_spec(plugin_id)
    entry = (spec or {}).get("job_entrypoint")
    if not spec or not entry:
        msg = (
            f"plugin {plugin_id!r} is not registered on this worker or advertises no "
            f"job_entrypoint — is its backend on ADA_WORKER_PRELOAD / an ada.plugins entry point?"
        )
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    try:
        mod_name, _, attr = entry.partition(":")
        fn = getattr(importlib.import_module(mod_name), attr)
    except Exception as exc:
        logger.exception("worker: plugin_job entrypoint %s import failed", entry)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=f"entrypoint import failed: {exc}")
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    async def _aprog(stage: str, frac: float) -> None:
        await queue.update(job_id, stage=stage, progress=max(0.1, min(0.95, float(frac))))

    def _sync_on_progress(stage: str, frac: float) -> None:
        # The plugin calls this synchronously from the executor thread; hop it
        # onto the loop so stage/progress land in the job row. A hiccup here must
        # never sink the job.
        try:
            asyncio.run_coroutine_threadsafe(_aprog(stage, frac), loop)
        except Exception:  # noqa: BLE001
            pass

    # --- Mid-run cooperative cancellation -----------------------------------
    # The plugin entrypoint runs in a worker THREAD (run_in_executor), not the
    # SIGKILL-watchdog subprocess the convert path uses, so a running plugin job
    # can't be reaped by killing a child. Instead we poll the audit row (the
    # cancel endpoint's source of truth) and set a threading.Event the plugin can
    # observe cooperatively between units of work. A pure-CPU plugin that ignores
    # the event runs to completion unchanged (fully backward-compatible).
    cancel_event = threading.Event()

    async def _cancel_poller() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                if await db_module.audit_is_cancelled(db_pool, job_id):
                    logger.info("worker: plugin_job %s cancel requested mid-run", job_id)
                    cancel_event.set()
                    return
            except Exception:
                logger.debug("worker: plugin_job cancel poll failed for %s", job_id, exc_info=True)

    poller: "asyncio.Task | None" = None
    if db_pool is not None:
        poller = asyncio.create_task(_cancel_poller())

    # Only pass cancel_event to plugins that actually accept it, so an older
    # entrypoint whose signature predates the kwarg keeps working untouched.
    import inspect

    try:
        _sig = inspect.signature(fn)
        _has_varkw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in _sig.parameters.values())
        _accepts_cancel = "cancel_event" in _sig.parameters or _has_varkw
        # Same negotiation, same reason: an entrypoint written before this kwarg
        # existed keeps working untouched rather than dying on an unexpected
        # keyword.
        _accepts_source_nodes = "source_nodes" in _sig.parameters or _has_varkw
    except (TypeError, ValueError):
        _accepts_cancel = False
        _accepts_source_nodes = False

    # --- In-process profiling harness (toggle + per-task filter gated) ------
    # Read the same admin toggle the convert path reads (`profile_conversions`)
    # plus the per-task filter (`profile_task_types`, comma-separated list of
    # target_format values; empty = all tasks). A plugin_job runs in an executor
    # THREAD and spawns its own trace subprocesses, so the fork-child cProfile /
    # rusage harness can't reach it — we run an in-process harness around the
    # call instead. NOTE: resource.getrusage(RUSAGE_SELF) + /proc/self are
    # WHOLE-PROCESS (the executor model can't isolate a single thread's counters),
    # so metrics include any concurrent work on this worker.
    profile_enabled = False
    if db_pool is not None:
        try:
            _pv = await db_module.get_setting(db_pool, "profile_conversions")
            _profile_on = (_pv or "").strip().lower() in {"1", "true", "yes", "on"}
            _tt = await db_module.get_setting(db_pool, "profile_task_types")
            _allowed_types = {t.strip() for t in (_tt or "").split(",") if t.strip()}
            profile_enabled = _profile_on and (not _allowed_types or "plugin_job" in _allowed_types)
        except Exception:
            logger.exception("worker: failed to read profile settings for plugin_job %s", job_id)

    prof_holder: dict[str, object] = {}

    def _invoke() -> dict:
        kwargs: dict = dict(
            storage=sync_storage,
            scope=scope,
            on_progress=_sync_on_progress,
            derived_prefix=opts.get("derived_prefix"),
        )
        if _accepts_cancel:
            kwargs["cancel_event"] = cancel_event
        # A pool where there is one, the API where there is not, and the kwarg
        # left ABSENT when neither is configured. Passing a recorder that
        # cannot reach anything would turn a supported deployment into a
        # plugin that fails at its first write instead of one that knows it
        # cannot record -- which is a distinction plugins are written against.
        if _accepts_source_nodes:
            _recorder = _source_nodes_recorder(db_pool, scope, loop)
            if _recorder is not None:
                kwargs["source_nodes"] = _recorder
        if not profile_enabled:
            return fn(opts.get("options") or {}, **kwargs)

        # Harness: cProfile + rusage/VmHWM/proc-io deltas around the call. All
        # readings are whole-process (see note above), and every one of them is
        # best-effort — cProfile is the only part that works everywhere, and a
        # counter this platform cannot produce must cost a measurement, not the
        # job that was being measured.
        import cProfile

        prof = cProfile.Profile()
        ru0 = _read_self_rusage()
        rd0, wr0 = _read_self_proc_io()
        prof.enable()
        try:
            result = fn(opts.get("options") or {}, **kwargs)
        finally:
            prof.disable()
            ru1 = _read_self_rusage()
            rd1, wr1 = _read_self_proc_io()
            metrics: dict = {
                "read_bytes": max(0, rd1 - rd0),
                "write_bytes": max(0, wr1 - wr0),
            }
            # VmHWM is a monotonic high-water mark; ru_maxrss (kB on Linux) is
            # the fallback when /proc is unavailable.
            peak_rss_kb = _read_self_vmhwm_kb() or (ru1[2] if ru1 else 0)
            if peak_rss_kb:
                metrics["peak_rss_kb"] = peak_rss_kb
            if ru0 is not None and ru1 is not None:
                # Omitted rather than zeroed where rusage is unavailable. A
                # zero would be indistinguishable from a job that genuinely
                # burned no CPU, and the audit panel reads exactly that ratio
                # to decide a task is "mostly waiting on IO".
                metrics["cpu_user_ms"] = int((ru1[0] - ru0[0]) * 1000)
                metrics["cpu_sys_ms"] = int((ru1[1] - ru0[1]) * 1000)
            prof_bytes: bytes | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".prof", delete=False) as tf:
                    _prof_path = tf.name
                prof.dump_stats(_prof_path)
                prof_bytes = pathlib.Path(_prof_path).read_bytes()
                os.unlink(_prof_path)
            except Exception:
                logger.debug("worker: plugin_job profile dump failed for %s", job_id, exc_info=True)
            prof_holder["metrics"] = metrics
            prof_holder["profile_bytes"] = prof_bytes
        return result

    async def _plugin_metrics() -> dict:
        """Assemble the audit metrics dict from the harness output, uploading the
        .prof via the same helper the convert path uses. No-op when profiling off."""
        metrics = dict(prof_holder.get("metrics") or {})  # type: ignore[arg-type]
        prof_bytes = prof_holder.get("profile_bytes")
        if prof_bytes:
            try:
                profile_key = f"_derived/{job.source_key}.{job_id}.prof"
                await storage.put_bytes(scope, profile_key, prof_bytes)  # type: ignore[arg-type]
                metrics["profile_key"] = profile_key
            except Exception:
                logger.exception("worker: plugin_job profile upload failed for %s", job_id)
        return metrics

    try:
        await queue.update(job_id, stage="plugin", progress=0.10)
        summary = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        # Distinguish a user-cancel (poller tripped the event, plugin bailed
        # cooperatively) from a genuine error: on cancel, mark cancelled (NOT
        # error) and return without the error path — mirrors the convert path's
        # CANCELLED branch.
        if cancel_event.is_set():
            logger.info("worker: plugin_job %s cancelled by user mid-run", job_id)
            try:
                await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error=None)
            except Exception:
                pass
            await _audit_done(
                db_pool, job_id, "cancelled", "cancelled by user", started_at, metrics=await _plugin_metrics()
            )
            return
        logger.exception("worker: plugin_job %s failed for job %s", plugin_id, job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="plugin", error=str(exc))
        await _audit_done(
            db_pool, job_id, "error", str(exc), started_at, traceback=trace, metrics=await _plugin_metrics()
        )
        return
    finally:
        # Stop the cancel poller in all paths (cancelling an already-finished
        # task is harmless).
        if poller is not None:
            poller.cancel()

    try:
        await queue.update(job_id, stage="upload", progress=0.95)
        payload = summary if isinstance(summary, dict) else {"result": summary}
        await storage.put_bytes(scope, job.derived_key, json.dumps(payload).encode("utf-8"), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: plugin_job %s summary upload failed for job %s", plugin_id, job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    # Success was the ONE outcome that logged nothing. Cancel, failure and a
    # failed summary upload each say so, so a job that simply worked was the
    # only case where "picked up job X" stayed the last word on it -- which
    # reads exactly like a worker that died still holding it. Logged at the
    # same level as the pickup it answers, so the two read as a pair.
    logger.info(
        "worker: plugin_job %s finished job %s in %.1fs",
        plugin_id,
        job_id,
        time.monotonic() - started_at,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at, metrics=await _plugin_metrics())


class PluginJobHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "plugin_job"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_plugin_job(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
