"""The API's background loops, started by ``create_app``'s lifespan and cancelled at
shutdown: the worker-registry snapshot refresh, completed-job KV cleanup, stale-worker
pruning, the audit and plugin-job cron schedulers, the profile hotspot parser and the
issue-bot poller (which also dispatches auto-validation passes).

Each loop is defensive: one failed tick is logged and the loop carries on; only
``asyncio.CancelledError`` (shutdown) exits. What a loop used to capture from the
``create_app`` closure is an explicit parameter: the queue, the storage, or the app's
:class:`~.routes.deps.RestContext`.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException

from ada.config import logger
from ada.core.file_system import new_temp_path

from . import db as db_module
from .queue import JobQueue
from .routes.admin_audit_perf import run_issue_bot_for, run_issue_bot_for_conversion
from .routes.admin_audit_runs import audit_dispatch
from .routes.admin_plugin_jobs import plugin_schedule_fire
from .routes.deps import (
    RestContext,
    SystemUser,
    next_fire,
    parse_scope,
    refresh_worker_registry,
    resolve_project_scope,
)
from .scope import Scope
from .storage import Storage


async def worker_registry_refresh_loop(queue: JobQueue, worker_registry: dict) -> None:
    """Background task: refresh the cached worker-registry snapshot
    (``worker_registry``) every ``REFRESH_INTERVAL_S`` so the
    config endpoints never call ``list_workers()`` (an N+1 over NATS
    KV) on the request path. Defensive — a failed tick keeps the last
    good snapshot; only ``asyncio.CancelledError`` exits (shutdown)."""
    # 15s (was 3s): the refresh does a KV keys() scan, and worker
    # capabilities/tags only change on a worker's ~15s heartbeat, so a
    # tighter interval bought nothing but scan traffic. Cheap now that the
    # KV is kept lean (see job_cleanup_loop), but no reason to over-poll.
    REFRESH_INTERVAL_S = 15.0
    logger.info("worker registry refresh: starting (every %ss)", REFRESH_INTERVAL_S)
    try:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL_S)
            await refresh_worker_registry(queue, worker_registry)
    except asyncio.CancelledError:
        logger.info("worker registry refresh: stopped")
        raise


async def job_cleanup_loop(q) -> None:
    """Background task: periodically drop completed job entries from the KV
    so the shared bucket stays small. The KV is a transient in-flight
    progress cache only — the durable record lives in Postgres (audit) and
    S3 (blobs/profiles). Without this, terminal job entries piled up
    (observed: 47k), so every worker-registry keys() scan replayed the whole
    bucket and stormed NATS. Grace keeps a completed job readable long
    enough for a polling client to see its final state. Defensive per tick;
    only CancelledError exits."""
    CLEANUP_INTERVAL_S = 300.0
    GRACE_S = 900.0  # keep terminal jobs ~15min so the frontend's final poll still resolves
    logger.info("job KV cleanup: starting (every %ss, grace %ss)", CLEANUP_INTERVAL_S, GRACE_S)
    try:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_S)
            try:
                await q.purge_completed_jobs(grace_s=GRACE_S)
            except Exception:
                logger.exception("job KV cleanup: tick failed")
            # Purging a job leaves a delete marker NATS keeps forever;
            # without this the bucket grows by one per job ever run.
            try:
                await q.purge_old_tombstones()
            except Exception:
                logger.exception("job KV cleanup: delete-marker purge failed")
    except asyncio.CancelledError:
        logger.info("job KV cleanup: stopped")
        raise


async def worker_prune_loop(q) -> None:
    """Background task: hourly, hard-prune worker registry entries unseen for
    ``WORKER_PRUNE_AFTER_S`` (2 days). Defensive — one failed tick is logged, not fatal; only
    ``asyncio.CancelledError`` exits cleanly (shutdown)."""
    PRUNE_INTERVAL_S = 3600.0
    logger.info("worker prune: starting (every %ss, horizon %ss)", PRUNE_INTERVAL_S, q.WORKER_PRUNE_AFTER_S)
    try:
        while True:
            await asyncio.sleep(PRUNE_INTERVAL_S)
            try:
                n = await q.prune_stale_workers()
                if n:
                    logger.info("worker prune: removed %d stale registration(s)", n)
            except Exception:
                logger.exception("worker prune: tick failed")
    except asyncio.CancelledError:
        logger.info("worker prune: stopped")
        raise


async def scheduler_loop(ctx: RestContext, pool) -> None:
    """Background task: tick every 30s, claim due schedules, fire
    the same dispatch code path as ``POST /admin/audit/runs``.

    The loop is defensive — exceptions inside one tick are logged
    but don't kill the loop, so a transient DB blip or a
    malformed schedule row never silently disables the whole
    scheduler. Only ``asyncio.CancelledError`` exits cleanly (the
    shutdown path).

    Cross-replica safety: ``claim_due_audit_schedule`` is the only
    operation that mutates a schedule's ``last_fired_at`` /
    ``next_fire_at``, and it does so under
    ``FOR UPDATE SKIP LOCKED`` so two replicas ticking the same
    row produce at most one fire.
    """
    from datetime import datetime, timezone

    # One tick is "claim and dispatch until no more rows are due,
    # then sleep". The inner while-loop ensures a backlog (e.g.
    # after a deploy with several pending schedules) drains in
    # one tick instead of one-per-30s.
    TICK_INTERVAL_S = 30.0
    logger.info("audit scheduler: starting (tick every %ss)", TICK_INTERVAL_S)
    try:
        while True:
            try:
                now = datetime.now(timezone.utc)
                while True:
                    try:
                        # Compute provisional next_fire_at from
                        # the CURRENT time, not from the row's
                        # cron_expr — we don't know cron_expr
                        # until we've claimed the row. We claim
                        # with a placeholder, then immediately
                        # follow up to set the correct
                        # next_fire_at based on the row's expr.
                        placeholder_next = now  # overwritten below
                        row = await db_module.claim_due_audit_schedule(
                            pool,
                            now=now,
                            next_fire_at=placeholder_next,
                        )
                    except Exception:
                        logger.exception("audit scheduler: claim failed")
                        break
                    if row is None:
                        break
                    # Update next_fire_at to the real value
                    # computed from this row's cron expression.
                    # If parsing fails (shouldn't — we validated
                    # on insert/update) leave the schedule alone
                    # and disable it via a skip reason.
                    try:
                        real_next = next_fire(row["cron_expr"], after=now)
                        await db_module.update_audit_schedule(
                            pool,
                            row["id"],
                            next_fire_at=real_next,
                            next_fire_at_set=True,
                        )
                    except Exception as exc:
                        logger.exception(
                            "audit scheduler: cron parse failed for %s",
                            row["id"],
                        )
                        await db_module.set_audit_schedule_skip_reason(
                            pool,
                            row["id"],
                            f"cron parse failed: {exc}",
                        )
                        continue

                    await scheduler_fire(ctx, pool, row)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("audit scheduler: tick failed")
            await asyncio.sleep(TICK_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("audit scheduler: stopped")
        raise


async def scheduler_fire(ctx: RestContext, pool, schedule_row: dict) -> None:
    """One claimed-row's dispatch. Resolves scope, runs the
    concurrent-fire guard, creates the audit_run, and kicks off
    ``audit_dispatch`` in a fresh task (mirroring the
    BackgroundTask path used by the manual route)."""
    sched_id = schedule_row["id"]
    scope_str = schedule_row["scope"]
    worker_pool = schedule_row["worker_pool"]
    try:
        s = parse_scope(scope_str, SystemUser())
        s = await resolve_project_scope(pool, s)
    except HTTPException as exc:
        await db_module.set_audit_schedule_skip_reason(
            pool,
            sched_id,
            f"scope resolution failed ({exc.status_code}): {exc.detail}",
        )
        return
    except Exception as exc:
        logger.exception("audit scheduler: scope resolution crashed")
        await db_module.set_audit_schedule_skip_reason(
            pool,
            sched_id,
            f"scope resolution crashed: {exc}",
        )
        return

    # Concurrent-fire guard: a previous run with the same
    # (scope, worker_pool) is still in-flight. Skipping is the
    # safe choice — overlapping audits would double the worker
    # pool load and confuse the per-cell grid.
    try:
        in_flight = await db_module.audit_run_exists_for_key(
            pool,
            scope_str,
            worker_pool,
        )
    except Exception:
        logger.exception("audit scheduler: concurrent-fire check failed")
        return
    if in_flight:
        await db_module.set_audit_schedule_skip_reason(
            pool,
            sched_id,
            "skipped: previous audit run still in-flight",
        )
        return

    try:
        run = await db_module.create_audit_run(
            pool,
            scope=scope_str,
            worker_pool=worker_pool,
            trigger="cron",
            note=f"scheduled: {schedule_row['name']}",
            created_by="system",
        )
    except Exception as exc:
        logger.exception("audit scheduler: create_audit_run failed")
        await db_module.set_audit_schedule_skip_reason(
            pool,
            sched_id,
            f"create_audit_run failed: {exc}",
        )
        return

    # Mirror the manual path: dispatch runs as a fire-and-forget
    # task so the scheduler tick stays responsive. The task takes
    # over emitting audit_log rows + bumping run counters.
    asyncio.create_task(
        audit_dispatch(ctx, run["id"], s, worker_pool, "system", pool),
        name=f"audit-dispatch-{run['id']}",
    )


async def plugin_schedule_loop(ctx: RestContext, pool) -> None:
    """Tick every 30 s, claim due schedules, enqueue them as plugin jobs.

    A deliberate mirror of ``scheduler_loop`` above: same interval, same
    drain-the-backlog inner loop, same "one tick's exception must not kill the
    loop" posture. The two are separate because their payloads are, not
    because they disagree about how ticking works.
    """
    from datetime import datetime, timezone

    TICK_INTERVAL_S = 30.0
    logger.info("plugin-job scheduler: starting (tick every %ss)", TICK_INTERVAL_S)
    try:
        while True:
            try:
                now = datetime.now(timezone.utc)
                while True:
                    try:
                        # Claimed with a placeholder next_fire_at and
                        # corrected below, once this row's cron_expr is known.
                        row = await db_module.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now)
                    except Exception:
                        logger.exception("plugin-job scheduler: claim failed")
                        break
                    if row is None:
                        break
                    try:
                        await db_module.update_plugin_job_schedule(
                            pool,
                            row["id"],
                            next_fire_at=next_fire(row["cron_expr"], after=now),
                        )
                    except Exception as exc:
                        # next_fire_at is now `now`, so this row would be
                        # claimed again on the next tick and spin. Disable it
                        # and say why rather than let it hammer the queue.
                        logger.exception("plugin-job scheduler: could not advance %s", row["id"])
                        await db_module.update_plugin_job_schedule(pool, row["id"], enabled=False)
                        await db_module.set_plugin_job_schedule_skip_reason(
                            pool,
                            row["id"],
                            f"disabled: could not compute the next firing from {row['cron_expr']!r}: {exc}",
                        )
                        continue

                    await plugin_schedule_fire(ctx, pool, row, fired_at=now)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("plugin-job scheduler: tick failed")
            await asyncio.sleep(TICK_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("plugin-job scheduler: stopped")
        raise


PROFILE_TOP_K = 50


async def parse_one_profile(storage: Storage, pool, claimed: dict) -> None:
    """Download one .prof blob, extract top-K functions by
    cumtime, and write rows into ``profile_function_stats``.
    Failures get stamped on the audit_log row's
    ``profile_stats_error`` so the operator can debug, but the
    loop continues — one bad blob mustn't stop the queue."""
    import pstats

    audit_id = int(claimed["id"])
    try:
        scope = (
            Scope.shared()
            if claimed["scope_kind"] == "shared"
            else Scope(kind=claimed["scope_kind"], id=claimed["scope_id"])  # type: ignore[arg-type]
        )
        data = await storage.get_bytes(scope, claimed["profile_key"])
    except Exception as exc:
        logger.warning(
            "profile parser: storage read failed for audit %s: %s",
            audit_id,
            exc,
        )
        await db_module.mark_profile_stats_failed(
            pool,
            audit_id,
            f"storage read failed: {exc}",
        )
        return

    # pstats only reads from disk — stash bytes in a tempfile
    # rather than threading a BytesIO through it.
    tmp_path = new_temp_path(suffix=".prof")
    try:
        tmp_path.write_bytes(data)
        try:
            stats = pstats.Stats(str(tmp_path))
        except Exception as exc:
            logger.warning(
                "profile parser: pstats failed for audit %s: %s",
                audit_id,
                exc,
            )
            await db_module.mark_profile_stats_failed(
                pool,
                audit_id,
                f"pstats parse failed: {exc}",
            )
            return
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    rows: list[dict] = []
    for (fn, line, name), (cc, nc, tt, ct, _callers) in stats.stats.items():
        rows.append(
            {
                "func": name or "",
                "file": fn or "",
                "line": int(line) if line is not None else 0,
                "ncalls": int(nc),
                "primitive_calls": int(cc),
                "tottime": float(tt),
                "cumtime": float(ct),
            }
        )
    rows.sort(key=lambda r: r["cumtime"], reverse=True)
    rows = rows[:PROFILE_TOP_K]

    try:
        await db_module.insert_profile_function_stats(
            pool,
            audit_id,
            rows,
        )
    except Exception as exc:
        logger.exception("profile parser: insert failed for audit %s", audit_id)
        await db_module.mark_profile_stats_failed(
            pool,
            audit_id,
            f"insert failed: {exc}",
        )


async def profile_parser_loop(storage: Storage, pool) -> None:
    """Background task: pull unprocessed audit_log rows with a
    ``profile_key``, parse the .prof, persist top-K function
    stats. Idle when profiling is disabled (no rows match).

    Batch-size limit per tick keeps the parser from monopolising
    the event loop after a big audit run lands hundreds of new
    .prof blobs at once.
    """
    TICK_INTERVAL_S = 30.0
    BATCH_PER_TICK = 5
    logger.info(
        "profile parser: starting (tick every %ss, batch %d)",
        TICK_INTERVAL_S,
        BATCH_PER_TICK,
    )
    try:
        while True:
            try:
                for _ in range(BATCH_PER_TICK):
                    claimed = await db_module.claim_unprocessed_profile_row(pool)
                    if claimed is None:
                        break
                    await parse_one_profile(storage, pool, claimed)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("profile parser: tick failed")
            await asyncio.sleep(TICK_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("profile parser: stopped")
        raise


async def dispatch_auto_validation(ctx: RestContext, pool, parent: dict) -> None:
    """Dispatch the validation (cross-format parity) pass of an
    ``auto_validate`` conversion run — *into the same run*, not a new one.
    Runs dispatched with an upfront reservation (``validate_total > 0``)
    already count these cells in their total, so dispatch consumes the
    reservation; pre-reservation runs get their total extended (and
    reopen from ``finished``) as before. The claim already stamped the
    parent so this runs once; failures are logged but never break the
    poller tick."""
    try:
        s = parse_scope(parent["scope"], SystemUser())
        s = await resolve_project_scope(pool, s)
    except Exception:
        logger.exception("auto-validate: scope resolution failed for run %s", parent["id"])
        if (parent.get("validate_total") or 0) > 0:
            # Release the reservation — no parity cells will ever be
            # enqueued, so the run must not hang 'running' on them.
            await db_module.consume_audit_run_validation_reserve(pool, parent["id"], 0)
        return
    # Awaited (not fire-and-forget) so the run holds its parity cells
    # before the issue-bot drain in the same tick can claim it.
    try:
        await audit_dispatch(
            ctx,
            parent["id"],
            s,
            parent["worker_pool"],
            "system",
            pool,
            False,
            validate_only=True,
            extend=True,
            consume_reserve=(parent.get("validate_total") or 0) > 0,
        )
        logger.info("auto-validate: appended validation cells to run %s", parent["id"])
    except Exception:
        logger.exception("auto-validate: dispatch failed for run %s", parent["id"])


async def issue_bot_loop(ctx: RestContext, pool) -> None:
    """Background task: drain (1) finished audit runs + (2)
    failed user-driven conversions per tick. Defensive —
    exceptions in one tick don't kill the loop.

    User-conversion failures are processed individually but
    rate-limited per tick (``USER_BATCH_PER_TICK``) so a burst
    of failures doesn't flood the forge API. Audit-run sweeps
    batch all of a run's failures into one sync, so they're
    not rate-limited the same way.
    """
    TICK_INTERVAL_S = 30.0
    USER_BATCH_PER_TICK = 10
    logger.info(
        "issue-bot poller: starting (tick every %ss, user batch %d)",
        TICK_INTERVAL_S,
        USER_BATCH_PER_TICK,
    )
    try:
        while True:
            try:
                # Auto-validate first: an auto_validate run whose conversion
                # cells have landed gets its parity cells dispatched (the
                # run stays 'running' on its upfront-reserved total; legacy
                # finished runs reopen), so the issue-bot below only claims
                # a run once it's *truly* done — conversions and validation
                # together. Claimed once (the claim stamps
                # auto_validate_dispatched_at).
                while True:
                    parent = await db_module.claim_audit_run_for_auto_validate(pool)
                    if parent is None:
                        break
                    await dispatch_auto_validation(ctx, pool, parent)
                # Drain finished audit runs (each represents many
                # failures batched into one sync, more valuable
                # to keep current).
                while True:
                    run = await db_module.claim_audit_run_for_issue_bot(pool)
                    if run is None:
                        break
                    await run_issue_bot_for(pool, run)
                # Then user-conversion failures, capped per tick.
                for _ in range(USER_BATCH_PER_TICK):
                    conv = await db_module.claim_failed_conversion_for_issue_bot(pool)
                    if conv is None:
                        break
                    await run_issue_bot_for_conversion(pool, conv)
                # Link failed rows to their issue fingerprint (audit_log.issue_fp) so a
                # recheck can find the cells behind an issue. One batch per tick: old
                # rows are caught up gradually, new ones within a tick of failing.
                await db_module.backfill_audit_log_issue_fps(pool)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("issue-bot poller: tick failed")
            await asyncio.sleep(TICK_INTERVAL_S)
    except asyncio.CancelledError:
        logger.info("issue-bot poller: stopped")
        raise
