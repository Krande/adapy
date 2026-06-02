"""Postgres layer for the multi-tenant REST viewer.

Optional. When ``DATABASE_URL`` is empty the pool stays ``None`` and
the API serves in shared-only mode: every authenticated user lands in
the same shared bucket, no projects, no admin panel, no audit log.
This keeps the helm chart genuinely "Postgres-optional" — small
deployments don't need to stand up a database to use the viewer.

Migrations are bundled SQL files under ``migrations/`` and applied at
boot inside an advisory lock so a multi-replica rollout doesn't race.
A row in ``schema_version`` records each applied file by stem name.

Repository helpers expose the small set of queries the rest of the
package needs (project list, membership check, audit log insert).
asyncpg's pool is held on FastAPI's ``app.state.db_pool`` and pulled
via the :func:`get_pool` accessor.
"""

from __future__ import annotations

import importlib.resources
import json
from dataclasses import dataclass
from typing import Optional

import asyncpg

from ada.config import logger

# Postgres advisory-lock id for the migration runner. asyncpg / PG
# treat advisory keys as int8 (signed 64-bit), so the value must fit
# in [-2^63, 2^63). Top nibble cleared so this stays a positive int.
_MIGRATION_LOCK_ID = 0x0ADA0001_ADA00001


@dataclass(frozen=True)
class Project:
    id: str
    slug: str
    name: str
    role: str  # caller's role within this project


async def init_pool(database_url: str) -> Optional[asyncpg.Pool]:
    """Build a connection pool and apply pending migrations.

    Returns ``None`` when ``database_url`` is empty so callers can
    branch into shared-only mode without a try/except.

    Retries connection failures with exponential backoff. The pod
    can come up before kube-dns or before Postgres has finished
    accepting connections, and a one-shot init that gives up on the
    first ``socket.gaierror`` was leaving the API permanently in
    shared-only mode (admin endpoints all returning 503) until
    someone manually rolled the pod. Total budget ~60s — long enough
    to ride out cold-start ordering, short enough that a real
    misconfig still surfaces in the readiness probe.
    """
    if not database_url:
        logger.info("db: DATABASE_URL not set — running in shared-only mode")
        return None
    import asyncio as _asyncio

    delay = 1.0
    deadline = 60.0
    waited = 0.0
    while True:
        try:
            pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=1,
                max_size=10,
                # Kill idle connections after 10 min — Postgres servers behind
                # load balancers (PgBouncer, Garage's PG, managed Postgres) are
                # finicky about long-idle conns.
                max_inactive_connection_lifetime=600.0,
            )
            break
        except (OSError, asyncpg.exceptions.PostgresError) as exc:
            if waited >= deadline:
                logger.error(
                    "db: pool init still failing after %.0fs — giving up: %s",
                    waited,
                    exc,
                )
                raise
            logger.warning(
                "db: pool init failed (%s); retry in %.1fs (waited %.1fs/%.0fs)",
                exc,
                delay,
                waited,
                deadline,
            )
            await _asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.6, 8.0)
    try:
        await _apply_migrations(pool)
    except Exception:
        await pool.close()
        raise
    logger.info("db: pool ready, migrations up-to-date")
    return pool


async def close_pool(pool: Optional[asyncpg.Pool]) -> None:
    if pool is not None:
        await pool.close()


async def _apply_migrations(pool: asyncpg.Pool) -> None:
    """Run any unapplied migrations under a Postgres advisory lock.

    The lock is held only during apply; competing replicas wait, then
    discover the migrations are already applied and become a no-op.
    """
    files = sorted(
        p for p in importlib.resources.files("ada.comms.rest.migrations").iterdir() if p.name.endswith(".sql")
    )

    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version    TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_version")}
            for path in files:
                version = path.stem
                if version in applied:
                    continue
                logger.info("db: applying migration %s", version)
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_version(version) VALUES ($1)", version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)


# ── Repository helpers ────────────────────────────────────────────────


async def upsert_user(pool: asyncpg.Pool, sub: str, email: str, display_name: str) -> None:
    """Lazy user upsert on first authenticated request. Bumps last_seen_at."""
    await pool.execute(
        """
        INSERT INTO users (sub, email, display_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (sub) DO UPDATE SET
            email = EXCLUDED.email,
            display_name = EXCLUDED.display_name,
            last_seen_at = NOW()
        """,
        sub,
        email or None,
        display_name or None,
    )


async def list_user_projects(pool: asyncpg.Pool, user_sub: str) -> list[Project]:
    rows = await pool.fetch(
        """
        SELECT p.id, p.slug, p.name, m.role
        FROM project_members m
        JOIN projects p ON p.id = m.project_id
        WHERE m.user_sub = $1 AND p.archived_at IS NULL
        ORDER BY p.name
        """,
        user_sub,
    )
    return [Project(id=str(r["id"]), slug=r["slug"], name=r["name"], role=r["role"]) for r in rows]


async def is_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str) -> bool:
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM project_members m
        JOIN projects p ON p.id = m.project_id
        WHERE m.user_sub = $1 AND p.id = $2 AND p.archived_at IS NULL
        """,
        user_sub,
        project_id,
    )
    return row is not None


async def insert_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None,
    scope_kind: str,
    scope_id: str | None,
    action: str,
    key: str | None = None,
    target_format: str | None = None,
    status: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    job_id: str | None = None,
    traceback: str | None = None,
    audit_run_id: str | None = None,
) -> None:
    """Insert one audit_log row.

    ``audit_run_id`` links the row back to an admin-triggered
    regression sweep (M1 audit panel). NULL for every user-driven
    convert / upload / download row — only the audit dispatcher
    populates it.

    When the row both carries an ``audit_run_id`` AND lands in a
    terminal status (``done`` / ``ok`` / ``error`` / ``cancelled``
    / ``skipped``), the matching counter on ``audit_runs`` is bumped
    in the same transaction. This is the cached-cell path of the
    dispatcher — derived blobs that already exist get audited as
    ``done`` without enqueueing a job, and the run's ok counter
    needs to advance immediately so the math closes against the
    total.
    """

    counter_col = _AUDIT_RUN_COUNTER_FOR_STATUS.get(status) if audit_run_id is not None and status is not None else None
    if counter_col is None:
        # Hot path — single INSERT, no transaction overhead. Covers
        # every user-driven action and the audit dispatcher's
        # ``status='queued'`` enqueue audit.
        await pool.execute(
            """
            INSERT INTO audit_log
                (user_sub, scope_kind, scope_id, action, key,
                 target_format, status, error, duration_ms, job_id,
                 traceback, audit_run_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            user_sub,
            scope_kind,
            scope_id,
            action,
            key,
            target_format,
            status,
            error,
            duration_ms,
            job_id,
            traceback,
            audit_run_id,
        )
        return

    # Audit-dispatcher cached-cell path: insert + counter bump in one
    # transaction so a crash between the two can't leave a row counted
    # by the audit grid but not by the run total.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO audit_log
                    (user_sub, scope_kind, scope_id, action, key,
                     target_format, status, error, duration_ms, job_id,
                     traceback, audit_run_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                user_sub,
                scope_kind,
                scope_id,
                action,
                key,
                target_format,
                status,
                error,
                duration_ms,
                job_id,
                traceback,
                audit_run_id,
            )
            await _bump_audit_run_counter(conn, audit_run_id, status)


async def cancel_audit_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    user_sub: str,
) -> bool:
    """Mark a queued or running job as cancelled. Caller must own it.

    Returns ``True`` if a row was updated, ``False`` otherwise (row
    missing, status already terminal, or owned by someone else).
    Filtering on ``status`` lets the SQL itself enforce the "only
    cancel queued/running" rule so a race between two cancel clicks
    can't mark a done job cancelled retroactively.
    """
    result = await pool.execute(
        """
        UPDATE audit_log
        SET status = 'cancelled',
            error = COALESCE(error, 'cancelled by user')
        WHERE job_id = $1
          AND user_sub = $2
          AND status IN ('queued', 'running')
        """,
        job_id,
        user_sub,
    )
    return result.endswith(" 1")


async def mark_audit_running(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    worker_image_tag: str | None = None,
) -> None:
    """Flip the audit_log row for a job into ``status='running'`` and
    stamp ``ts = now()`` so the admin's "current cell" query has a
    fresh row to surface.

    Without this hop the row goes straight queued → done and the
    audit toast's ORDER BY can't tell which queued cell is actually
    being worked on — so the same one stays on screen for the
    whole sweep. Best-effort: a DB hiccup must never break job
    processing.
    """
    if pool is None:
        return
    try:
        await pool.execute(
            """
            UPDATE audit_log
            SET status = 'running',
                ts = NOW(),
                worker_image_tag = COALESCE($2, worker_image_tag)
            WHERE job_id = $1
              AND status = 'queued'
            """,
            job_id,
            worker_image_tag,
        )
    except Exception:
        logger.exception("worker: mark_audit_running failed for job %s", job_id)


async def update_audit_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
    duration_ms: int | None = None,
    traceback: str | None = None,
    cpu_user_ms: int | None = None,
    cpu_sys_ms: int | None = None,
    peak_rss_kb: int | None = None,
    read_bytes: int | None = None,
    write_bytes: int | None = None,
    profile_key: str | None = None,
    worker_image_tag: str | None = None,
) -> None:
    """Patch the audit row tied to a queue job with its final outcome.

    No-op when the row is missing (job predates the migration, or the
    enqueue-time audit insert failed). COALESCE preserves any existing
    column when the caller passes None.

    When the row carries an ``audit_run_id`` (M1 audit panel), the
    matching counter on ``audit_runs`` (``ok`` / ``failed`` /
    ``skipped``) is bumped in the same transaction. The run flips to
    ``status='finished'`` when ``ok + failed + skipped`` reaches
    ``total``. Regular user-driven jobs (audit_run_id IS NULL) take
    the original single-table path with no overhead.
    """
    # Single transaction so the per-row write + the run-counter bump
    # never split — a worker restart between the two would otherwise
    # leak a job from the counters and the run would never finish.
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE audit_log
                SET status = $2,
                    error = COALESCE($3, error),
                    duration_ms = COALESCE($4, duration_ms),
                    traceback = COALESCE($5, traceback),
                    cpu_user_ms = COALESCE($6, cpu_user_ms),
                    cpu_sys_ms = COALESCE($7, cpu_sys_ms),
                    peak_rss_kb = COALESCE($8, peak_rss_kb),
                    read_bytes = COALESCE($9, read_bytes),
                    write_bytes = COALESCE($10, write_bytes),
                    profile_key = COALESCE($11, profile_key),
                    worker_image_tag = COALESCE($12, worker_image_tag)
                WHERE job_id = $1
                RETURNING audit_run_id
                """,
                job_id,
                status,
                error,
                duration_ms,
                traceback,
                cpu_user_ms,
                cpu_sys_ms,
                peak_rss_kb,
                read_bytes,
                write_bytes,
                profile_key,
                worker_image_tag,
            )
            if updated is None or updated["audit_run_id"] is None:
                return
            await _bump_audit_run_counter(conn, updated["audit_run_id"], status)


# Map terminal job-status values to the audit_runs counter column they
# bump. Statuses outside this set (``running`` / ``queued``) don't
# advance any counter — only terminal transitions do.
_AUDIT_RUN_COUNTER_FOR_STATUS = {
    "done": "ok",
    "ok": "ok",
    "error": "failed",
    "failed": "failed",
    "cancelled": "skipped",
    "skipped": "skipped",
}


async def _bump_audit_run_counter(
    conn: asyncpg.Connection,
    run_id,
    terminal_status: str,
) -> None:
    """Increment one of the run's terminal counters and finish the
    run when all enqueued jobs have landed. Connection-bound (not
    pool-bound) so the caller can run this inside the same
    transaction as the audit_log UPDATE.

    ``status='aborted'`` (an admin pressed Cancel) is preserved —
    late worker completions arriving after the abort still bump
    counters for diagnostics, but the run never auto-flips back to
    ``finished``.
    """
    column = _AUDIT_RUN_COUNTER_FOR_STATUS.get(terminal_status)
    if column is None:
        # Transient state (``running``); nothing to bump yet.
        return
    # The SET clause uses dynamic column interpolation but ``column``
    # is constrained to a closed allowlist above, so f-string here is
    # safe — no caller-supplied SQL surface.
    await conn.execute(
        f"""
        UPDATE audit_runs
        SET {column} = {column} + 1,
            finished_at = CASE
                WHEN status = 'aborted' THEN finished_at
                WHEN ok + failed + skipped + 1 >= total
                  THEN COALESCE(finished_at, NOW())
                ELSE finished_at
            END,
            status = CASE
                WHEN status = 'aborted' THEN 'aborted'
                WHEN ok + failed + skipped + 1 >= total
                  THEN 'finished'
                ELSE status
            END
        WHERE id = $1
        """,
        run_id,
    )


async def active_audit_summary(pool: asyncpg.Pool) -> dict:
    """Aggregate state of currently-``running`` audit runs.

    Used by the bottom-right viewer toast to show an ambient
    "N audit runs · M cells pending" badge that links into the
    admin panel. Plus the most recently-touched in-flight cell
    (status ``running`` preferred, falls back to ``queued``) so
    the operator sees what's actually converting right now —
    handy on a force-rebuild measurement run where every cell
    actually executes.

    Returns:
        {"running_runs": int, "pending_cells": int,
         "current_cell": {key, target_format, status,
                          started_at, elapsed_ms} | None}
    """
    counts_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS running_runs,
            COALESCE(SUM(GREATEST(total - ok - failed - skipped, 0)), 0)
                AS pending_cells
        FROM audit_runs
        WHERE status = 'running'
        """
    )
    # Only surface rows where the worker has actually picked the
    # cell up — i.e. ``audit_log.status = 'running'`` (set by
    # ``mark_audit_running`` at the start of ``_process_one``).
    # Queued rows don't reflect what's converting right now; the
    # dispatcher inserts them all with the same ts at the start of
    # the sweep, so picking the "most recent queued" gives a row
    # that doesn't change as the sweep advances. Caller sees no
    # current_cell between cells (a few hundred ms) — that's the
    # honest answer, and the toast hides the line there.
    current_row = await pool.fetchrow(
        """
        SELECT al.key, al.target_format, al.status, al.ts
        FROM audit_log al
        JOIN audit_runs ar ON ar.id = al.audit_run_id
        WHERE ar.status = 'running'
          AND al.status = 'running'
        ORDER BY al.ts DESC
        LIMIT 1
        """
    )
    current_cell = None
    if current_row is not None:
        ts = current_row["ts"]
        elapsed_ms = None
        if ts is not None:
            from datetime import datetime, timezone

            elapsed_ms = int((datetime.now(timezone.utc) - ts).total_seconds() * 1000)
        current_cell = {
            "key": current_row["key"],
            "target_format": current_row["target_format"],
            "status": current_row["status"],
            "started_at": ts.isoformat() if ts else None,
            "elapsed_ms": elapsed_ms,
        }
    return {
        "running_runs": int(counts_row["running_runs"] or 0),
        "pending_cells": int(counts_row["pending_cells"] or 0),
        "current_cell": current_cell,
    }


async def abort_audit_run(
    pool: asyncpg.Pool,
    run_id: str,
) -> dict | None:
    """Stop a running audit. Sets the run's status to ``'aborted'``
    and cancels every queued / running child audit_log row in the
    same transaction so the per-cell grid shows where the run was
    when it died (the rows that already finished keep their
    terminal status — we don't rewrite history)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """
                UPDATE audit_runs
                SET status = 'aborted',
                    finished_at = NOW()
                WHERE id = $1
                  AND status = 'running'
                RETURNING id, scope, worker_pool, trigger, started_at,
                          finished_at, status, note, total, ok, failed,
                          skipped, created_by, issue_bot_status,
                          issue_bot_last_error, issue_bot_synced_at
                """,
                run_id,
            )
            if run is None:
                return None
            # Cancel still-queued / still-running children. Counts
            # them as ``skipped`` so the run's ok+failed+skipped
            # matches total once late completions stop landing.
            cancelled_rows = await conn.fetch(
                """
                UPDATE audit_log
                SET status = 'cancelled',
                    error = COALESCE(error, 'audit run aborted')
                WHERE audit_run_id = $1
                  AND status IN ('queued', 'running')
                RETURNING id
                """,
                run_id,
            )
            n_cancel = len(cancelled_rows)
            if n_cancel > 0:
                await conn.execute(
                    """
                    UPDATE audit_runs
                    SET skipped = skipped + $2
                    WHERE id = $1
                    """,
                    run_id,
                    n_cancel,
                )
    return _audit_run_row(run)


async def append_metrics_sample_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    sample: dict,
) -> None:
    """Append one heartbeat sample to ``audit_log.metrics_samples``.

    The column is JSONB initialised to NULL when the row is created;
    we coalesce to ``[]`` before appending so the first heartbeat
    on a fresh row still produces a valid array. Best-effort —
    callers swallow exceptions because losing one heartbeat shouldn't
    fail the conversion.
    """
    await pool.execute(
        """
        UPDATE audit_log
        SET metrics_samples = COALESCE(metrics_samples, '[]'::jsonb) || $2::jsonb
        WHERE job_id = $1
        """,
        job_id,
        json.dumps(sample),
    )


# ── Admin queries ────────────────────────────────────────────────────


async def list_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    statuses: list[str] | None = None,
    limit: int = 100,
    before_id: int | None = None,
    exclude_audit_dispatched: bool = False,
) -> list[dict]:
    """Reverse-chronological audit_log scan, optionally filtered.

    Pagination is keyset-style on ``id`` (the BIGSERIAL primary key) —
    pass the smallest id from the previous page as ``before_id``. id
    monotonicity matches ``ts`` ordering and avoids the offset-based
    "page drift" surprise when new rows arrive between requests.

    ``statuses`` filters by the job's terminal/transient state (e.g.
    ``["queued", "running"]`` for the user-facing "my in-flight jobs"
    view). Empty list / None disables the filter.

    ``exclude_audit_dispatched`` filters out cells emitted by the
    admin audit sweep (``audit_run_id IS NOT NULL``). The user-
    facing /my-jobs view sets this so a 453-cell sweep doesn't
    flood the bottom-right toast — the Audit Runs admin tab is
    the proper surface for that work.
    """
    where: list[str] = []
    args: list = []
    if user_sub:
        args.append(user_sub)
        where.append(f"user_sub = ${len(args)}")
    if scope_kind:
        args.append(scope_kind)
        where.append(f"scope_kind = ${len(args)}")
    if scope_id:
        args.append(scope_id)
        where.append(f"scope_id = ${len(args)}")
    if action:
        args.append(action)
        where.append(f"action = ${len(args)}")
    if statuses:
        args.append(statuses)
        where.append(f"status = ANY(${len(args)})")
    if before_id is not None:
        args.append(before_id)
        where.append(f"id < ${len(args)}")
    if exclude_audit_dispatched:
        where.append("audit_run_id IS NULL")
    args.append(min(max(limit, 1), 500))
    sql = (
        "SELECT id, ts, user_sub, scope_kind, scope_id, action, key,"
        " target_format, status, error, duration_ms, traceback,"
        " cpu_user_ms, cpu_sys_ms, peak_rss_kb, read_bytes, write_bytes,"
        " profile_key, job_id, audit_run_id, worker_image_tag,"
        " issue_bot_status, issue_bot_synced_at, issue_bot_last_error"
        " FROM audit_log"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY id DESC LIMIT ${len(args)}"
    rows = await pool.fetch(sql, *args)
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] is not None else None,
            "user_sub": r["user_sub"],
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "action": r["action"],
            "key": r["key"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
            "traceback": r["traceback"],
            "cpu_user_ms": r["cpu_user_ms"],
            "cpu_sys_ms": r["cpu_sys_ms"],
            "peak_rss_kb": r["peak_rss_kb"],
            "read_bytes": r["read_bytes"],
            "write_bytes": r["write_bytes"],
            "profile_key": r["profile_key"],
            "job_id": r["job_id"],
            "audit_run_id": str(r["audit_run_id"]) if r["audit_run_id"] else None,
            "worker_image_tag": r["worker_image_tag"],
            "issue_bot_status": r["issue_bot_status"],
            "issue_bot_synced_at": (r["issue_bot_synced_at"].isoformat() if r["issue_bot_synced_at"] else None),
            "issue_bot_last_error": r["issue_bot_last_error"],
        }
        for r in rows
    ]


async def get_audit_by_id(pool: asyncpg.Pool, audit_id: int) -> dict | None:
    """Fetch a single audit row by id. Used by the profile-download
    endpoint to look up the blob key + scope without re-listing, and
    by the local repro tooling to recover ``target_format`` + the
    error context for a failed conversion."""
    row = await pool.fetchrow(
        """
        SELECT id, ts, user_sub, scope_kind, scope_id, profile_key, key,
               action, target_format, status, error, traceback,
               duration_ms, job_id, metrics_samples, audit_run_id,
               issue_bot_status, issue_bot_synced_at, issue_bot_last_error
        FROM audit_log WHERE id = $1
        """,
        audit_id,
    )
    if row is None:
        return None
    samples_raw = row["metrics_samples"]
    # asyncpg returns JSONB as a Python str by default; parse defensively.
    if isinstance(samples_raw, str):
        try:
            samples = json.loads(samples_raw)
        except (ValueError, TypeError):
            samples = None
    else:
        samples = samples_raw
    return {
        "id": row["id"],
        "ts": row["ts"].isoformat() if row["ts"] is not None else None,
        "user_sub": row["user_sub"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
        "profile_key": row["profile_key"],
        "key": row["key"],
        "action": row["action"],
        "target_format": row["target_format"],
        "status": row["status"],
        "error": row["error"],
        "traceback": row["traceback"],
        "duration_ms": row["duration_ms"],
        "job_id": row["job_id"],
        "metrics_samples": samples,
        "audit_run_id": str(row["audit_run_id"]) if row["audit_run_id"] else None,
        "issue_bot_status": row["issue_bot_status"],
        "issue_bot_synced_at": (row["issue_bot_synced_at"].isoformat() if row["issue_bot_synced_at"] else None),
        "issue_bot_last_error": row["issue_bot_last_error"],
    }


# ── App settings ─────────────────────────────────────────────────────


async def get_setting(pool: asyncpg.Pool, key: str) -> str | None:
    row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
    return row["value"] if row else None


async def set_setting(pool: asyncpg.Pool, key: str, value: str, *, updated_by: str | None) -> None:
    """Upsert a setting. ``value`` is a string — caller serializes
    booleans / numbers as appropriate (we keep this small and avoid
    type-tagging columns)."""
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        key,
        value,
        updated_by,
    )


async def clear_audit_metrics(pool: asyncpg.Pool) -> dict:
    """Null out the metrics columns on every audit row, returning
    counts of rows touched and profile_keys that need blob cleanup.

    The audit rows themselves are left intact — only the metrics
    payload is wiped. Caller is responsible for deleting the actual
    profile blobs from storage (we return the keys to make that
    feasible without a second pass over the table).
    """
    profile_rows = await pool.fetch(
        """
        SELECT scope_kind, scope_id, profile_key
        FROM audit_log
        WHERE profile_key IS NOT NULL
        """
    )
    profile_keys = [
        {
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "profile_key": r["profile_key"],
        }
        for r in profile_rows
    ]
    result = await pool.execute(
        """
        UPDATE audit_log
        SET cpu_user_ms = NULL,
            cpu_sys_ms = NULL,
            peak_rss_kb = NULL,
            read_bytes = NULL,
            write_bytes = NULL,
            profile_key = NULL
        WHERE cpu_user_ms IS NOT NULL
           OR cpu_sys_ms IS NOT NULL
           OR peak_rss_kb IS NOT NULL
           OR read_bytes IS NOT NULL
           OR write_bytes IS NOT NULL
           OR profile_key IS NOT NULL
        """
    )
    # asyncpg returns "UPDATE N" — pull out the integer.
    rows_cleared = 0
    if isinstance(result, str) and result.startswith("UPDATE "):
        try:
            rows_cleared = int(result.split()[1])
        except (IndexError, ValueError):
            pass
    return {"rows_cleared": rows_cleared, "profile_keys": profile_keys}


async def list_all_projects(pool: asyncpg.Pool) -> list[dict]:
    """Admin view: every project (including archived). Member count too."""
    rows = await pool.fetch(
        """
        SELECT p.id, p.slug, p.name, p.created_at, p.archived_at,
               COUNT(m.user_sub) AS member_count
        FROM projects p
        LEFT JOIN project_members m ON m.project_id = p.id
        GROUP BY p.id
        ORDER BY p.archived_at IS NOT NULL, p.name
        """
    )
    return [
        {
            "id": str(r["id"]),
            "slug": r["slug"],
            "name": r["name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "archived_at": r["archived_at"].isoformat() if r["archived_at"] else None,
            "member_count": int(r["member_count"]),
        }
        for r in rows
    ]


async def create_project(pool: asyncpg.Pool, slug: str, name: str) -> dict:
    """Insert a project and return it. Slug is unique; conflicts → ValueError."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO projects (slug, name) VALUES ($1, $2)
            RETURNING id, slug, name, created_at, archived_at
            """,
            slug,
            name,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ValueError(f"slug {slug!r} already exists") from exc
    assert row is not None
    return {
        "id": str(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "archived_at": row["archived_at"].isoformat() if row["archived_at"] else None,
        "member_count": 0,
    }


async def archive_project(pool: asyncpg.Pool, project_id: str) -> bool:
    """Soft-delete: stamp archived_at. Returns False when not found.

    Soft delete preserves audit_log scope_id references and lets us
    un-archive without orphaning. Hard-delete is intentionally not
    exposed via the admin API.
    """
    row = await pool.fetchrow(
        "UPDATE projects SET archived_at = NOW() WHERE id = $1 AND archived_at IS NULL RETURNING id",
        project_id,
    )
    return row is not None


async def list_project_members(pool: asyncpg.Pool, project_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT m.user_sub, m.role, m.added_at,
               u.email, u.display_name, u.last_seen_at
        FROM project_members m
        LEFT JOIN users u ON u.sub = m.user_sub
        WHERE m.project_id = $1
        ORDER BY u.display_name, m.user_sub
        """,
        project_id,
    )
    return [
        {
            "user_sub": r["user_sub"],
            "role": r["role"],
            "added_at": r["added_at"].isoformat() if r["added_at"] else None,
            "email": r["email"],
            "display_name": r["display_name"],
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        }
        for r in rows
    ]


async def add_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str, role: str = "member") -> bool:
    """Idempotent membership add. Returns True on insert, False on duplicate.

    Inserts a placeholder ``users`` row when the sub hasn't been seen
    yet so the FK holds — the row gets enriched (email, display_name)
    on the user's first authenticated request.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING",
                user_sub,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO project_members (project_id, user_sub, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (project_id, user_sub) DO NOTHING
                RETURNING user_sub
                """,
                project_id,
                user_sub,
                role,
            )
    return row is not None


async def remove_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str) -> bool:
    row = await pool.fetchrow(
        "DELETE FROM project_members WHERE project_id = $1 AND user_sub = $2 RETURNING user_sub",
        project_id,
        user_sub,
    )
    return row is not None


async def project_exists(pool: asyncpg.Pool, project_id: str) -> bool:
    row = await pool.fetchrow("SELECT 1 FROM projects WHERE id = $1", project_id)
    return row is not None


async def project_id_from_slug(pool: asyncpg.Pool, slug: str) -> str | None:
    """Resolve a project slug to its UUID. Returns None if no match.

    Slugs are URL-safe and stable across renames of the human-readable
    name; UUIDs are the FK-stable id. The scope URL parser uses this to
    accept ``project:<slug>`` as a friendlier alternative to
    ``project:<uuid>``.
    """
    row = await pool.fetchrow(
        "SELECT id FROM projects WHERE slug = $1 AND archived_at IS NULL",
        slug,
    )
    return str(row["id"]) if row else None


# ── Corpora (M3 admin audit panel) ──────────────────────────────────


def _corpus_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "slug": r["slug"],
        "name": r["name"],
        "description": r["description"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "created_by": r["created_by"],
        "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
    }


async def list_corpora(
    pool: asyncpg.Pool,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Newest-first corpora list. Archived rows hidden by default; the
    admin panel never needs them and the audit-form picker would
    confuse the operator by listing slugs that storage will reject."""
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = await pool.fetch(
        f"""
        SELECT id, slug, name, description, created_at,
               created_by, archived_at
        FROM corpora
        {where}
        ORDER BY created_at DESC
        """
    )
    return [_corpus_row(r) for r in rows]


async def create_corpus(
    pool: asyncpg.Pool,
    *,
    slug: str,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Insert one new corpus. ``slug`` is the human-readable id used
    on the wire (``corpus:<slug>``); enforce uniqueness against live
    rows via the partial unique index. Archived rows holding the
    same slug are no obstacle."""
    row = await pool.fetchrow(
        """
        INSERT INTO corpora (slug, name, description, created_by)
        VALUES ($1, $2, $3, $4)
        RETURNING id, slug, name, description, created_at,
                  created_by, archived_at
        """,
        slug,
        name,
        description,
        created_by,
    )
    return _corpus_row(row)


async def get_corpus_by_slug(
    pool: asyncpg.Pool,
    slug: str,
) -> dict | None:
    """Look up one corpus by its public slug. Returns the live row
    only; archived corpora are treated as absent."""
    row = await pool.fetchrow(
        """
        SELECT id, slug, name, description, created_at,
               created_by, archived_at
        FROM corpora
        WHERE slug = $1 AND archived_at IS NULL
        """,
        slug,
    )
    return _corpus_row(row) if row else None


async def archive_corpus(pool: asyncpg.Pool, slug: str) -> bool:
    """Soft-delete a corpus. Storage bytes stay in the bucket — the
    operator wipes those separately if disk pressure matters. The
    slug becomes available for re-use immediately because the
    uniqueness index is partial-on-live."""
    result = await pool.execute(
        """
        UPDATE corpora
        SET archived_at = NOW()
        WHERE slug = $1 AND archived_at IS NULL
        """,
        slug,
    )
    return result.endswith(" 1")


# ── Audit runs (M1 admin audit panel) ──────────────────────────────


def _audit_run_row(r) -> dict:
    """Project an audit_runs row to its JSON-ready dict shape.

    Includes the M5 issue-bot fields when the underlying row has
    them (it always does post-migration 009, but the helper tolerates
    rows from a SELECT that omits those columns by falling back to
    ``None``)."""

    def _opt(col: str):
        try:
            return r[col]
        except (KeyError, TypeError):
            return None

    issue_bot_synced_at = _opt("issue_bot_synced_at")
    return {
        "id": str(r["id"]),
        "scope": r["scope"],
        "worker_pool": r["worker_pool"],
        "trigger": r["trigger"],
        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        "status": r["status"],
        "note": r["note"],
        "total": r["total"],
        "ok": r["ok"],
        "failed": r["failed"],
        "skipped": r["skipped"],
        "created_by": r["created_by"],
        "force_rebuild": _opt("force_rebuild") or False,
        "issue_bot_status": _opt("issue_bot_status"),
        "issue_bot_last_error": _opt("issue_bot_last_error"),
        "issue_bot_synced_at": (issue_bot_synced_at.isoformat() if issue_bot_synced_at else None),
    }


async def create_audit_run(
    pool: asyncpg.Pool,
    *,
    scope: str,
    worker_pool: str | None,
    trigger: str = "manual",
    note: str | None = None,
    created_by: str | None = None,
    force_rebuild: bool = False,
) -> dict:
    """Open a new audit_runs row in ``status='running'``. Returns the
    fresh row (including its server-generated UUID + started_at) so
    the dispatcher can stamp the jobs it enqueues. ``total`` starts
    at 0 — :func:`set_audit_run_total` finalises it once dispatch
    knows how many jobs landed.

    ``force_rebuild`` (M7+) bypasses the dispatcher's cached-cell
    short-circuit so every viable cell actually re-converts.
    Persisted on the row so the admin UI can show "this was a
    force-rebuild" badge.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO audit_runs (scope, worker_pool, trigger, note, created_by, force_rebuild)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        scope,
        worker_pool,
        trigger,
        note,
        created_by,
        force_rebuild,
    )
    return _audit_run_row(row)


async def set_audit_run_total(
    pool: asyncpg.Pool,
    run_id: str,
    total: int,
) -> None:
    """Set the dispatched-job count after enqueue completes. If
    ``total`` is 0 (no jobs to run — empty scope or no viable cells),
    the run is marked ``finished`` immediately so the UI doesn't show
    a perpetually-running row."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET total = $2,
            status = CASE WHEN $2 = 0 THEN 'finished' ELSE status END,
            finished_at = CASE WHEN $2 = 0 THEN NOW() ELSE finished_at END
        WHERE id = $1
        """,
        run_id,
        total,
    )


async def list_audit_runs(
    pool: asyncpg.Pool,
    *,
    limit: int = 50,
    before_started_at: str | None = None,
) -> list[dict]:
    """Reverse-chronological audit_runs scan. Keyset paginated on
    ``started_at`` so new runs landing between requests don't shift
    the page boundary the way an offset would."""
    args: list = []
    where = ""
    if before_started_at:
        args.append(before_started_at)
        where = f" WHERE started_at < ${len(args)}::timestamptz"
    args.append(min(max(limit, 1), 200))
    rows = await pool.fetch(
        f"""
        SELECT id, scope, worker_pool, trigger, started_at, finished_at,
               status, note, total, ok, failed, skipped, created_by,
               force_rebuild,
               issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        FROM audit_runs
        {where}
        ORDER BY started_at DESC
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [_audit_run_row(r) for r in rows]


async def get_audit_run(pool: asyncpg.Pool, run_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, scope, worker_pool, trigger, started_at, finished_at,
               status, note, total, ok, failed, skipped, created_by,
               force_rebuild,
               issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        FROM audit_runs WHERE id = $1
        """,
        run_id,
    )
    return _audit_run_row(row) if row else None


async def list_audit_run_jobs(
    pool: asyncpg.Pool,
    run_id: str,
) -> list[dict]:
    """Every audit_log row tied to one audit_run. Returned in
    insert order (ascending id) so the per-run grid in the admin
    panel can render rows in the deterministic order the
    dispatcher emitted them."""
    rows = await pool.fetch(
        """
        SELECT id, ts, key, target_format, status, error,
               duration_ms, cpu_user_ms, cpu_sys_ms, peak_rss_kb,
               read_bytes, write_bytes, job_id, worker_image_tag
        FROM audit_log
        WHERE audit_run_id = $1
        ORDER BY id ASC
        """,
        run_id,
    )
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "key": r["key"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
            "cpu_user_ms": r["cpu_user_ms"],
            "cpu_sys_ms": r["cpu_sys_ms"],
            "peak_rss_kb": r["peak_rss_kb"],
            "read_bytes": r["read_bytes"],
            "write_bytes": r["write_bytes"],
            "job_id": r["job_id"],
            "worker_image_tag": r["worker_image_tag"],
        }
        for r in rows
    ]


async def audit_run_exists_for_key(
    pool: asyncpg.Pool,
    scope: str,
    worker_pool: str | None,
) -> bool:
    """Concurrent-fire guard for the scheduler tick (M4). Returns True
    when an ``audit_runs`` row with the same (scope, worker_pool) is
    still ``status='running'``. ``worker_pool=None`` is matched against
    SQL NULL explicitly so the "any pool" tag doesn't collide with a
    schedule that pins a specific pool."""
    if worker_pool is None:
        row = await pool.fetchrow(
            """
            SELECT 1 FROM audit_runs
            WHERE status = 'running'
              AND scope = $1
              AND worker_pool IS NULL
            LIMIT 1
            """,
            scope,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT 1 FROM audit_runs
            WHERE status = 'running'
              AND scope = $1
              AND worker_pool = $2
            LIMIT 1
            """,
            scope,
            worker_pool,
        )
    return row is not None


# ── Audit schedules (M4 admin audit panel) ─────────────────────────


def _audit_schedule_row(r) -> dict:
    """Project an audit_schedules row to its JSON-ready dict shape."""
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "cron_expr": r["cron_expr"],
        "scope": r["scope"],
        "worker_pool": r["worker_pool"],
        "enabled": r["enabled"],
        "last_fired_at": (r["last_fired_at"].isoformat() if r["last_fired_at"] else None),
        "next_fire_at": (r["next_fire_at"].isoformat() if r["next_fire_at"] else None),
        "last_skipped_reason": r["last_skipped_reason"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "created_by": r["created_by"],
        "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
    }


_SCHEDULE_COLS = (
    "id, name, cron_expr, scope, worker_pool, enabled, "
    "last_fired_at, next_fire_at, last_skipped_reason, "
    "created_at, created_by, archived_at"
)


async def list_audit_schedules(
    pool: asyncpg.Pool,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Newest-first audit_schedules listing. Archived rows hidden by
    default — the admin panel's primary view should only see live
    schedules."""
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = await pool.fetch(f"SELECT {_SCHEDULE_COLS} FROM audit_schedules{where} " "ORDER BY created_at DESC")
    return [_audit_schedule_row(r) for r in rows]


async def get_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        f"SELECT {_SCHEDULE_COLS} FROM audit_schedules WHERE id = $1",
        schedule_id,
    )
    return _audit_schedule_row(row) if row else None


async def create_audit_schedule(
    pool: asyncpg.Pool,
    *,
    name: str,
    cron_expr: str,
    scope: str,
    worker_pool: str | None,
    next_fire_at,
    enabled: bool = True,
    created_by: str | None = None,
) -> dict:
    """Insert one new audit_schedule. ``next_fire_at`` is pre-computed
    by the caller via croniter — keeping the cron parsing in the
    route handler means the DB layer stays library-free and the 400
    path for malformed expressions is sharper."""
    row = await pool.fetchrow(
        f"""
        INSERT INTO audit_schedules
            (name, cron_expr, scope, worker_pool, enabled, next_fire_at, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_SCHEDULE_COLS}
        """,
        name,
        cron_expr,
        scope,
        worker_pool,
        enabled,
        next_fire_at,
        created_by,
    )
    return _audit_schedule_row(row)


async def update_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
    *,
    name: str | None = None,
    cron_expr: str | None = None,
    scope: str | None = None,
    worker_pool: str | None = None,
    worker_pool_set: bool = False,
    enabled: bool | None = None,
    next_fire_at=None,
    next_fire_at_set: bool = False,
) -> dict | None:
    """Partial update. Only fields with non-None values (and the
    ``*_set`` toggles for nullable columns where ``None`` is a
    meaningful payload value) are written; the rest are left at
    whatever the row already holds."""
    sets: list[str] = []
    args: list = []
    if name is not None:
        args.append(name)
        sets.append(f"name = ${len(args)}")
    if cron_expr is not None:
        args.append(cron_expr)
        sets.append(f"cron_expr = ${len(args)}")
    if scope is not None:
        args.append(scope)
        sets.append(f"scope = ${len(args)}")
    if worker_pool_set:
        args.append(worker_pool)
        sets.append(f"worker_pool = ${len(args)}")
    if enabled is not None:
        args.append(enabled)
        sets.append(f"enabled = ${len(args)}")
    if next_fire_at_set:
        args.append(next_fire_at)
        sets.append(f"next_fire_at = ${len(args)}")
    if not sets:
        return await get_audit_schedule(pool, schedule_id)
    args.append(schedule_id)
    row = await pool.fetchrow(
        f"""
        UPDATE audit_schedules
        SET {', '.join(sets)}
        WHERE id = ${len(args)} AND archived_at IS NULL
        RETURNING {_SCHEDULE_COLS}
        """,
        *args,
    )
    return _audit_schedule_row(row) if row else None


async def archive_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
) -> bool:
    """Soft-delete one schedule. The tick query filters on
    ``archived_at IS NULL`` so the row stops firing immediately."""
    result = await pool.execute(
        "UPDATE audit_schedules SET archived_at = NOW() " "WHERE id = $1 AND archived_at IS NULL",
        schedule_id,
    )
    return result.endswith(" 1")


async def claim_due_audit_schedule(
    pool: asyncpg.Pool,
    *,
    now,
    next_fire_at,
):
    """Atomically claim ONE due schedule.

    Returns the claimed row (as a dict) or ``None`` if nothing was
    due. The UPDATE pins ``last_fired_at`` to the provided ``now``,
    advances ``next_fire_at`` to the supplied value (computed by the
    caller via croniter from ``now``), and clears
    ``last_skipped_reason`` — the caller can re-set the reason via
    :func:`set_audit_schedule_skip_reason` if dispatch ends up not
    firing.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple API replicas can tick
    in parallel without ever firing the same schedule twice.
    """
    row = await pool.fetchrow(
        f"""
        UPDATE audit_schedules
        SET last_fired_at = $1,
            next_fire_at = $2,
            last_skipped_reason = NULL
        WHERE id = (
            SELECT id FROM audit_schedules
            WHERE enabled
              AND archived_at IS NULL
              AND next_fire_at IS NOT NULL
              AND next_fire_at <= $1
            ORDER BY next_fire_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING {_SCHEDULE_COLS}
        """,
        now,
        next_fire_at,
    )
    return _audit_schedule_row(row) if row else None


async def set_audit_schedule_skip_reason(
    pool: asyncpg.Pool,
    schedule_id: str,
    reason: str,
) -> None:
    """Record why a tick decided not to dispatch (concurrent-fire
    guard, scope resolution failure, etc.). Stamped after the atomic
    claim so the operator sees the latest reason without unwinding
    the ``last_fired_at`` bump — the schedule still advances to the
    next slot rather than re-trying the missed one."""
    await pool.execute(
        "UPDATE audit_schedules SET last_skipped_reason = $2 WHERE id = $1",
        schedule_id,
        reason,
    )


# ── Audit issue-bot (M5 admin audit panel) ─────────────────────────


async def claim_audit_run_for_issue_bot(
    pool: asyncpg.Pool,
):
    """Atomically claim the oldest finished audit_run that hasn't
    been issue-synced yet. Sets ``issue_bot_status='syncing'`` so
    other replicas / retries skip it.

    Returns the claimed row (dict shaped like the public audit_run
    projection) or ``None`` if nothing is pending. The caller is
    expected to call :func:`mark_audit_run_issue_bot` with a
    terminal state once the sync completes (or fails).
    """
    row = await pool.fetchrow(
        """
        UPDATE audit_runs
        SET issue_bot_status = 'syncing'
        WHERE id = (
            SELECT id FROM audit_runs
            WHERE status = 'finished'
              AND issue_bot_status IS NULL
            ORDER BY finished_at ASC NULLS LAST
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, scope, worker_pool, trigger, started_at, finished_at,
                  status, note, total, ok, failed, skipped, created_by,
                  force_rebuild,
                  issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        """,
    )
    return _audit_run_row(row) if row else None


async def mark_audit_run_issue_bot(
    pool: asyncpg.Pool,
    run_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Stamp a terminal issue-bot status on the run. ``status`` is
    'done' (issues synced), 'skipped' (no failures to sync / bot
    disabled), or 'failed' (raised mid-sync — ``error`` carries the
    summary). All three set ``issue_bot_synced_at`` to NOW() so the
    UI can show how recently the bot ran."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET issue_bot_status = $2,
            issue_bot_last_error = $3,
            issue_bot_synced_at = NOW()
        WHERE id = $1
        """,
        run_id,
        status,
        error,
    )


async def reset_audit_run_issue_bot(
    pool: asyncpg.Pool,
    run_id: str,
) -> bool:
    """Clear the issue-bot status so the next tick picks the run up
    again. Used by the admin "retry sync" button. Returns True on a
    real reset, False when the run wasn't found or wasn't finished
    (in which case retrying makes no sense)."""
    result = await pool.execute(
        """
        UPDATE audit_runs
        SET issue_bot_status = NULL,
            issue_bot_last_error = NULL,
            issue_bot_synced_at = NULL
        WHERE id = $1 AND status = 'finished'
        """,
        run_id,
    )
    return result.endswith(" 1")


# ── Single-conversion issue-bot (M5b) ──────────────────────────────


async def claim_failed_conversion_for_issue_bot(
    pool: asyncpg.Pool,
) -> dict | None:
    """Atomically claim the oldest failed user-driven conversion
    that hasn't been synced yet.

    Restricted to ``audit_run_id IS NULL`` rows so audit-sweep
    failures keep going through the parent run's bot pass — that
    path batches all of a run's failures into one sync, which is
    much friendlier on the forge API than firing N times.
    """
    row = await pool.fetchrow(
        """
        UPDATE audit_log
        SET issue_bot_status = 'syncing'
        WHERE id = (
            SELECT id FROM audit_log
            WHERE status IN ('error', 'failed')
              AND audit_run_id IS NULL
              AND issue_bot_status IS NULL
            ORDER BY id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, ts, user_sub, scope_kind, scope_id, action,
                  key, target_format, status, error, traceback
        """,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "ts": row["ts"].isoformat() if row["ts"] else None,
        "user_sub": row["user_sub"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
        "action": row["action"],
        "key": row["key"],
        "target_format": row["target_format"],
        "status": row["status"],
        "error": row["error"],
        "traceback": row["traceback"],
    }


async def mark_audit_log_issue_bot(
    pool: asyncpg.Pool,
    audit_id: int,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Stamp a terminal issue-bot status on one audit_log row. Mirrors
    :func:`mark_audit_run_issue_bot` for the per-row case."""
    await pool.execute(
        """
        UPDATE audit_log
        SET issue_bot_status = $2,
            issue_bot_last_error = $3,
            issue_bot_synced_at = NOW()
        WHERE id = $1
        """,
        audit_id,
        status,
        error,
    )


async def reset_audit_log_issue_bot(
    pool: asyncpg.Pool,
    audit_id: int,
) -> bool:
    """Clear the bot status on one audit_log row so the next tick
    re-syncs it. Used by the admin "retry sync" button. Returns
    True on a real reset, False when the row wasn't found or
    isn't in a terminal failure state (queued/running rows don't
    have anything to sync)."""
    result = await pool.execute(
        """
        UPDATE audit_log
        SET issue_bot_status = NULL,
            issue_bot_last_error = NULL,
            issue_bot_synced_at = NULL
        WHERE id = $1 AND status IN ('error', 'failed')
        """,
        audit_id,
    )
    return result.endswith(" 1")


async def aggregate_conversion_metrics(
    pool: asyncpg.Pool,
    *,
    since_days: int = 30,
    trigger: str | None = None,
    audit_run_id: str | None = None,
    worker_image_tag: str | None = None,
) -> list[dict]:
    """Per-cell (``source_ext`` × ``target_format``) aggregation over
    the recent convert jobs (M6 cross-conversion dashboard).

    Computes p50 / p95 / max for duration, peak RSS, RSS per source MB,
    and write bytes. Failure rate is ``fail_count / sample_count`` —
    a float in ``[0, 1]``. ``source_size_mb`` is derived from
    ``read_bytes`` (the storage bytes the worker pulled in); rows with
    a NULL read_bytes contribute to sample/duration metrics but not
    to RSS-per-MB.

    ``trigger`` filters the underlying rows:
      * ``None`` / ``'all'`` — every convert job (default)
      * ``'audit'`` — only jobs tied to an audit run (M1+ sweeps)
      * ``'user'`` — only direct user-driven convert jobs

    ``audit_run_id`` narrows to a single sweep; combined with a
    ``worker_image_tag`` filter that's the way to lock the dashboard
    to one set of measurements taken with one worker build so an
    old/cached row from a different image doesn't dilute the
    numbers. ``since_days`` is clamped to ``[1, 365]`` so a typo'd
    multi-year range can't accidentally pin the DB; the admin UI
    exposes a fixed picker (24h / 7d / 30d / 90d).
    """
    days = max(1, min(365, since_days))
    where_extra = ""
    args: list = []
    if trigger == "audit":
        where_extra += " AND audit_run_id IS NOT NULL"
    elif trigger == "user":
        where_extra += " AND audit_run_id IS NULL"
    # Otherwise no trigger filter ("all").
    args.append(days)
    if audit_run_id is not None:
        args.append(audit_run_id)
        where_extra += f" AND audit_run_id = ${len(args)}"
    if worker_image_tag is not None:
        args.append(worker_image_tag)
        where_extra += f" AND worker_image_tag = ${len(args)}"
    sql = f"""
        WITH convert_jobs AS (
            SELECT
                LOWER(SUBSTRING(key FROM '\\.([^.]+)$')) AS source_ext,
                target_format,
                status,
                duration_ms,
                peak_rss_kb,
                read_bytes,
                write_bytes,
                cpu_user_ms,
                cpu_sys_ms,
                -- Effective source size in MB. Floor at 0.001 so
                -- division by zero can't happen for tiny / unknown
                -- inputs; the resulting RSS/MB inflation only kicks
                -- in for files <1 KB which are useless data points
                -- anyway.
                GREATEST(COALESCE(read_bytes, 0) / 1048576.0, 0.001) AS source_mb
            FROM audit_log
            WHERE action = 'convert'
              -- ``$1`` is an int (clamped days); multiply against
              -- the interval literal so we don't need to round-
              -- trip through a string concat (which asyncpg
              -- rejects with ``expected str, got int`` because
              -- ``||`` is the SQL string-concat operator).
              AND ts > NOW() - ($1 * INTERVAL '1 day')
              AND target_format IS NOT NULL
              AND key IS NOT NULL
              {where_extra}
        )
        SELECT
            source_ext,
            target_format,
            COUNT(*) AS sample_count,
            COUNT(*) FILTER (WHERE status IN ('error', 'failed')) AS fail_count,
            COUNT(*) FILTER (WHERE status IN ('ok', 'done')) AS ok_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) AS duration_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS duration_ms_p95,
            MAX(duration_ms) AS duration_ms_max,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY peak_rss_kb) AS peak_rss_kb_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY peak_rss_kb) AS peak_rss_kb_p95,
            MAX(peak_rss_kb) AS peak_rss_max_kb,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY peak_rss_kb / source_mb)
                FILTER (WHERE peak_rss_kb IS NOT NULL AND read_bytes > 0)
                AS peak_rss_per_source_mb_p95,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY write_bytes) AS write_bytes_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY write_bytes) AS write_bytes_p95,
            AVG(read_bytes)::bigint AS read_bytes_avg,
            -- IO/CPU split: how much of wall-clock is spent in CPU
            -- vs blocked on IO. Computed as SUM(cpu_user_ms +
            -- cpu_sys_ms) / SUM(duration_ms). Values close to 1
            -- mean the converter is CPU-bound; values < ~0.3 mean
            -- most of the wall-clock is spent waiting (S3 reads,
            -- presigned-URL handshakes, OCC tessellation IO, etc.)
            -- — those are the "consider streaming or async IO"
            -- candidates. NULL when no rows had timing.
            CASE
                WHEN SUM(duration_ms) > 0
                  THEN SUM(COALESCE(cpu_user_ms, 0) + COALESCE(cpu_sys_ms, 0))::float
                       / SUM(duration_ms)::float
                ELSE NULL
            END AS cpu_fraction
        FROM convert_jobs
        WHERE source_ext IS NOT NULL
          AND source_ext != ''
          AND target_format != ''
        GROUP BY source_ext, target_format
        ORDER BY source_ext, target_format
    """
    rows = await pool.fetch(sql, *args)

    def _f(v) -> float | None:
        # PERCENTILE_CONT returns NUMERIC which asyncpg gives as
        # Decimal; convert to float for the JSON layer. None stays
        # None so the frontend can detect "no data" cleanly.
        if v is None:
            return None
        return float(v)

    def _i(v) -> int | None:
        if v is None:
            return None
        return int(v)

    cells: list[dict] = []
    for r in rows:
        sample_count = r["sample_count"] or 0
        fail_count = r["fail_count"] or 0
        cells.append(
            {
                "source_ext": r["source_ext"] or "",
                "target_format": r["target_format"] or "",
                "sample_count": sample_count,
                "fail_count": fail_count,
                "ok_count": r["ok_count"] or 0,
                "failure_rate": (fail_count / sample_count if sample_count > 0 else 0.0),
                "duration_ms_p50": _i(r["duration_ms_p50"]),
                "duration_ms_p95": _i(r["duration_ms_p95"]),
                "duration_ms_max": _i(r["duration_ms_max"]),
                "peak_rss_kb_p50": _i(r["peak_rss_kb_p50"]),
                "peak_rss_kb_p95": _i(r["peak_rss_kb_p95"]),
                "peak_rss_max_kb": _i(r["peak_rss_max_kb"]),
                "peak_rss_per_source_mb_p95": _f(r["peak_rss_per_source_mb_p95"]),
                "write_bytes_p50": _i(r["write_bytes_p50"]),
                "write_bytes_p95": _i(r["write_bytes_p95"]),
                "read_bytes_avg": _i(r["read_bytes_avg"]),
                "cpu_fraction": _f(r["cpu_fraction"]),
            }
        )
    return cells


# ── Profile hotspots (M7 perf dashboard) ────────────────────────────


async def claim_unprocessed_profile_row(
    pool: asyncpg.Pool,
) -> dict | None:
    """Atomically claim the oldest audit_log row whose ``.prof`` has
    not been processed yet by the profile-hotspots background loop.

    Stamps ``profile_stats_processed_at`` so a concurrent replica
    skips the row; the caller is expected to either insert its
    parsed function stats or (on failure) overwrite the same
    timestamp via :func:`mark_profile_stats_processed` with an
    error message. Returns the audit_log row's
    ``id`` / ``key`` / ``target_format`` / ``profile_key`` /
    ``scope_kind`` / ``scope_id`` so the parser can fetch the blob
    without a second lookup.

    Limits to ``status IN ('ok', 'done')`` — only completed
    conversions have a meaningful profile; queued / failed cells
    either never produced one or produced a partial dump we don't
    want polluting the aggregates.
    """
    row = await pool.fetchrow(
        """
        UPDATE audit_log
        SET profile_stats_processed_at = NOW()
        WHERE id = (
            SELECT id FROM audit_log
            WHERE profile_key IS NOT NULL
              AND profile_stats_processed_at IS NULL
              AND status IN ('ok', 'done')
            ORDER BY id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, key, target_format, profile_key, scope_kind, scope_id
        """,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "key": row["key"],
        "target_format": row["target_format"],
        "profile_key": row["profile_key"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
    }


async def insert_profile_function_stats(
    pool: asyncpg.Pool,
    audit_id: int,
    rows: list[dict],
) -> None:
    """Insert the parsed top-K function stats for one audit row.

    ``rows`` is the output of pstats parsing already truncated to
    K=50 (or whatever the caller picked) and sorted by ``cumtime``
    desc. ``rank`` is the row's index in that order. Uses a single
    executemany so the K inserts don't open K transactions.
    """
    if not rows:
        return
    values = [
        (
            audit_id,
            idx,
            r["func"],
            r["file"],
            r["line"],
            int(r["ncalls"]),
            int(r["primitive_calls"]),
            float(r["tottime"]),
            float(r["cumtime"]),
        )
        for idx, r in enumerate(rows)
    ]
    await pool.executemany(
        """
        INSERT INTO profile_function_stats
            (audit_id, rank, func, file, line, ncalls,
             primitive_calls, tottime, cumtime)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        values,
    )


async def mark_profile_stats_failed(
    pool: asyncpg.Pool,
    audit_id: int,
    error: str,
) -> None:
    """Stamp a parse failure on the audit_log row. The timestamp is
    already set by :func:`claim_unprocessed_profile_row` (the
    claim doubles as a "we touched this row" marker so it doesn't
    re-claim on the next tick); we just attach the error message
    so the admin UI can show what went wrong."""
    await pool.execute(
        "UPDATE audit_log SET profile_stats_error = $2 WHERE id = $1",
        audit_id,
        error,
    )


async def aggregate_profile_hotspots(
    pool: asyncpg.Pool,
    *,
    source_ext: str | None = None,
    target_format: str | None = None,
    since_days: int = 30,
    limit: int = 25,
) -> dict:
    """Cross-run hotspot aggregation for one (source_ext, target_format)
    cell.

    Joins ``profile_function_stats`` against ``audit_log`` to filter
    by source-extension + target + time window, GROUPs by
    ``(func, file, line)`` and SUMs cumulative time + call count
    across every profile that landed in the window.

    Returns:
        {"functions": [{"func", "file", "line", "agg_cumtime",
                        "agg_ncalls", "profiles_seen"}],
         "profiles_in_window": N,
         "total_cumtime_in_window": T,
         "since_days": N}
    """
    days = max(1, min(365, since_days))
    where = [
        "al.action = 'convert'",
        "al.ts > NOW() - ($1 * INTERVAL '1 day')",
    ]
    args: list = [days]
    if source_ext:
        ext = source_ext.lower()
        # Strip leading dot if present; we match against the SUBSTRING
        # pattern that already excludes it.
        ext_no_dot = ext.lstrip(".")
        args.append(ext_no_dot)
        where.append(f"LOWER(SUBSTRING(al.key FROM '\\.([^.]+)$')) = ${len(args)}")
    if target_format:
        args.append(target_format.lower())
        where.append(f"LOWER(al.target_format) = ${len(args)}")
    args.append(max(1, min(500, limit)))
    sql = f"""
        WITH cell_rows AS (
            SELECT al.id, al.duration_ms
            FROM audit_log al
            WHERE {' AND '.join(where)}
              AND al.profile_key IS NOT NULL
              AND al.profile_stats_processed_at IS NOT NULL
        ),
        agg AS (
            SELECT
                pfs.func, pfs.file, pfs.line,
                SUM(pfs.cumtime) AS agg_cumtime,
                SUM(pfs.ncalls) AS agg_ncalls,
                COUNT(DISTINCT pfs.audit_id) AS profiles_seen
            FROM profile_function_stats pfs
            JOIN cell_rows c ON c.id = pfs.audit_id
            GROUP BY pfs.func, pfs.file, pfs.line
        )
        SELECT * FROM agg
        ORDER BY agg_cumtime DESC
        LIMIT ${len(args)}
    """
    rows = await pool.fetch(sql, *args)

    # Count profiles + window total cumtime separately so the UI
    # can show "top N of M profiles" without scanning the join.
    counts = await pool.fetchrow(
        f"""
        SELECT
            COUNT(DISTINCT pfs.audit_id) AS profiles_in_window,
            COALESCE(SUM(pfs.cumtime) FILTER (WHERE pfs.rank = 0), 0)
                AS total_top_cumtime_in_window
        FROM profile_function_stats pfs
        JOIN audit_log al ON al.id = pfs.audit_id
        WHERE {' AND '.join(where)}
          AND al.profile_stats_processed_at IS NOT NULL
        """,
        *args[:-1],  # drop the limit param — counts query doesn't use it
    )

    return {
        "functions": [
            {
                "func": r["func"],
                "file": r["file"],
                "line": r["line"],
                "agg_cumtime": float(r["agg_cumtime"]),
                "agg_ncalls": int(r["agg_ncalls"]),
                "profiles_seen": int(r["profiles_seen"]),
            }
            for r in rows
        ],
        "profiles_in_window": int(counts["profiles_in_window"] or 0),
        "total_top_cumtime_in_window": float(counts["total_top_cumtime_in_window"] or 0.0),
        "since_days": days,
    }


async def list_failed_audit_run_jobs(
    pool: asyncpg.Pool,
    run_id: str,
) -> list[dict]:
    """All ``audit_log`` rows in ``run_id`` whose status indicates a
    failure ('error' or 'failed'). Returns the columns the issue-bot
    needs to fingerprint + describe the failure — key, target,
    error message, traceback excerpt. Cached cells (status='done')
    and queued cells that never resolved aren't included; the bot
    only opens issues for real failures."""
    rows = await pool.fetch(
        """
        SELECT id, key, scope_kind, scope_id, target_format,
               status, error, traceback
        FROM audit_log
        WHERE audit_run_id = $1
          AND status IN ('error', 'failed')
        ORDER BY id ASC
        """,
        run_id,
    )
    return [
        {
            "id": r["id"],
            "key": r["key"],
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "traceback": r["traceback"],
        }
        for r in rows
    ]
