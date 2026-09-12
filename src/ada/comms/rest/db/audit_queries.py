"""Admin-facing audit-log queries: filtering, listing, summaries and single-row lookups."""

from __future__ import annotations

import datetime
import json
import re

import asyncpg

from ._common import _loads_jsonb

# ── Admin queries ────────────────────────────────────────────────────


_RELATIVE_BOUND = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)


_RELATIVE_UNIT = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_audit_time_bound(value: str | None, *, now: datetime.datetime | None = None) -> datetime.datetime | None:
    """A time bound for the audit filter: ``"6h"``, or an ISO-8601 instant.

    Relative forms are resolved HERE, against the server's clock, rather than
    the browser computing an absolute instant and sending that. A workstation
    whose clock is a few minutes fast would otherwise silently drop rows from a
    "last 5 minutes" view and show a window that never existed — and the
    narrower the range, the worse the error, which is exactly backwards.

    Absolute ISO input stays absolute: for a custom range the operator picked
    two real instants, and skew is irrelevant to what they meant.

    Empty / None means unbounded. Anything unparseable raises ``ValueError`` so
    the caller can answer 400 rather than quietly returning all of history.
    """
    raw = (value or "").strip()
    if not raw:
        return None

    m = _RELATIVE_BOUND.match(raw)
    if m:
        amount, unit = int(m.group(1)), m.group(2).lower()
        base = now or datetime.datetime.now(datetime.timezone.utc)
        return base - datetime.timedelta(**{_RELATIVE_UNIT[unit]: amount})

    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"not a duration (e.g. '6h') or an ISO-8601 instant: {value!r}") from exc
    # A naive instant is taken as UTC: the column is timestamptz, and comparing
    # it against a naive value raises rather than guessing a zone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _audit_predicates(
    *,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    target_format: str | None = None,
    statuses: list[str] | None = None,
    key_like: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    before_id: int | None = None,
    exclude_audit_dispatched: bool = False,
) -> tuple[list[str], list]:
    """Build the shared ``audit_log`` WHERE fragments and their arguments.

    Extracted so ``list_audit`` and ``summarize_audit`` cannot drift: the
    admin Audit tab shows a summary and a log side by side under ONE filter,
    and a predicate honoured by one but not the other reads as a counting bug
    rather than as the mismatch it is. Placeholders are numbered from the
    running length of ``args``, so a caller may append its own (a LIMIT, say)
    afterwards.
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
    if target_format:
        args.append(target_format)
        where.append(f"target_format = ${len(args)}")
    if statuses:
        args.append(statuses)
        where.append(f"status = ANY(${len(args)})")
    if key_like:
        args.append(f"%{key_like}%")
        where.append(f"key ILIKE ${len(args)}")
    # Bounded on ``ts``, which carries a DESC btree index, so narrowing the
    # window makes both the log and the summary cheaper rather than dearer.
    if since is not None:
        args.append(since)
        where.append(f"ts >= ${len(args)}")
    if until is not None:
        args.append(until)
        where.append(f"ts <= ${len(args)}")
    if before_id is not None:
        args.append(before_id)
        where.append(f"id < ${len(args)}")
    if exclude_audit_dispatched:
        where.append("audit_run_id IS NULL")
    return where, args


async def list_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    target_format: str | None = None,
    statuses: list[str] | None = None,
    key_like: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    limit: int = 100,
    before_id: int | None = None,
    exclude_audit_dispatched: bool = False,
) -> list[dict]:
    """Reverse-chronological audit_log scan, optionally filtered.

    ``key_like`` is a case-insensitive substring filter on the source ``key``
    (the filepath/filename), so the admin audit log can be narrowed to one file
    or folder (``%term%`` ILIKE).

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
    where, args = _audit_predicates(
        user_sub=user_sub,
        scope_kind=scope_kind,
        scope_id=scope_id,
        action=action,
        target_format=target_format,
        statuses=statuses,
        key_like=key_like,
        since=since,
        until=until,
        before_id=before_id,
        exclude_audit_dispatched=exclude_audit_dispatched,
    )
    args.append(min(max(limit, 1), 500))
    sql = (
        "SELECT id, ts, started_at, user_sub, scope_kind, scope_id, action, key,"
        " target_format, status, error, duration_ms, traceback,"
        " cpu_user_ms, cpu_sys_ms, peak_rss_kb, read_bytes, write_bytes,"
        " profile_key, log_key, job_id, audit_run_id, worker_image_tag, convert_meta,"
        " issue_bot_status, issue_bot_synced_at, issue_bot_last_error,"
        " client_metrics->>'device_id' AS device_id,"
        " u.email AS user_email, u.display_name AS user_display_name"
        " FROM audit_log LEFT JOIN users u ON u.sub = audit_log.user_sub"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY id DESC LIMIT ${len(args)}"
    rows = await pool.fetch(sql, *args)
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] is not None else None,
            "started_at": r["started_at"].isoformat() if r["started_at"] is not None else None,
            "user_sub": r["user_sub"],
            "user_email": r["user_email"],
            "user_display_name": r["user_display_name"],
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
            "log_key": r["log_key"],
            "job_id": r["job_id"],
            "audit_run_id": str(r["audit_run_id"]) if r["audit_run_id"] else None,
            "worker_image_tag": r["worker_image_tag"],
            "convert_meta": _loads_jsonb(r["convert_meta"]),
            "issue_bot_status": r["issue_bot_status"],
            "issue_bot_synced_at": (r["issue_bot_synced_at"].isoformat() if r["issue_bot_synced_at"] else None),
            "issue_bot_last_error": r["issue_bot_last_error"],
            "device_id": r["device_id"],
        }
        for r in rows
    ]


async def summarize_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    target_format: str | None = None,
    key_like: str | None = None,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    reason_limit: int = 10,
) -> dict:
    """Aggregate counts for the Audit tab's Overview, under the same filter
    the log uses.

    NOTE THE MISSING ``statuses`` PARAMETER — it is deliberate, not an
    oversight. Overview's whole job is to show how a filtered population
    splits ACROSS states, and the tiles double as the control that sets the
    status filter. Honouring that filter here would mean clicking "Failed"
    zeroes the other three tiles, i.e. the act of drilling in destroys the
    context you drilled in from. So every other predicate applies and status
    does not; the caller renders the active status as a selection instead.

    Returns ``by_status`` (every state present, plus explicit zeros for the
    four the queue writes, so the UI never has to invent a missing key),
    ``by_target`` (per target format, split by state — this is what makes
    "glb is fine, step is failing" visible at a glance), and
    ``top_errors`` (the most common failure messages, so the usual next
    question — *which* failure — is answered without opening a single row).
    """
    where, args = _audit_predicates(
        user_sub=user_sub,
        scope_kind=scope_kind,
        scope_id=scope_id,
        action=action,
        target_format=target_format,
        key_like=key_like,
        since=since,
        until=until,
    )
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    # One pass yields both breakdowns: summing over target gives the status
    # totals, so the tiles and the per-target table can never disagree.
    grid = await pool.fetch(
        "SELECT status, target_format, count(*) AS n FROM audit_log" + clause + " GROUP BY status, target_format",
        *args,
    )

    by_status: dict[str, int] = {"queued": 0, "running": 0, "done": 0, "error": 0}
    by_target: dict[str, dict[str, int]] = {}
    total = 0
    for r in grid:
        status = r["status"] or "unknown"
        n = int(r["n"])
        total += n
        by_status[status] = by_status.get(status, 0) + n
        tgt = r["target_format"] or "—"
        bucket = by_target.setdefault(tgt, {})
        bucket[status] = bucket.get(status, 0) + n

    # CONGESTION. ``ts`` is the ENQUEUE time — insert_audit writes the row with
    # status='queued' before a worker can see the job, and the completion update
    # never rewrites it. So for a row still queued, ``now() - ts`` is exactly how
    # long that job has been waiting, which is the question "are we backed up?"
    #
    # What this deliberately does NOT report is the wait of jobs that already
    # ran. Nothing records when a worker picked a job up: the row carries
    # enqueue time and processing duration, and the instant between them is
    # simply not stored. A historical average would have to be invented, and an
    # invented latency is worse than an absent one. Adding a ``started_at``
    # column is the honest way to get it.
    queue_row = await pool.fetchrow(
        "SELECT count(*) AS n,"
        " EXTRACT(EPOCH FROM max(now() - ts)) AS oldest_s,"
        " EXTRACT(EPOCH FROM avg(now() - ts)) AS mean_s,"
        " EXTRACT(EPOCH FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY now() - ts)) AS median_s"
        " FROM audit_log" + (clause + " AND " if clause else " WHERE ") + "status = 'queued'",
        *args,
    )

    # HISTORICAL wait, now that started_at survives the hop that used to destroy
    # it. Rows predating migration 027 have started_at NULL and are excluded
    # rather than backfilled: giving them ts would report every historical job
    # as having waited zero and flatten the very trend this measures.
    served_row = await pool.fetchrow(
        "SELECT count(*) AS n,"
        " EXTRACT(EPOCH FROM avg(started_at - ts)) AS mean_s,"
        " EXTRACT(EPOCH FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY started_at - ts)) AS median_s,"
        " EXTRACT(EPOCH FROM percentile_cont(0.95) WITHIN GROUP (ORDER BY started_at - ts)) AS p95_s,"
        " EXTRACT(EPOCH FROM max(started_at - ts)) AS max_s"
        " FROM audit_log" + (clause + " AND " if clause else " WHERE ") + "started_at IS NOT NULL",
        *args,
    )

    err_where = list(where) + ["status = 'error'", "error IS NOT NULL"]
    err_args = list(args)
    err_args.append(min(max(reason_limit, 1), 50))
    top_errors = await pool.fetch(
        "SELECT error, count(*) AS n FROM audit_log WHERE "
        + " AND ".join(err_where)
        + f" GROUP BY error ORDER BY n DESC, error ASC LIMIT ${len(err_args)}",
        *err_args,
    )

    def _sec(v):
        return round(float(v), 1) if v is not None else None

    return {
        "total": total,
        "by_status": by_status,
        "congestion": {
            "queued": int(queue_row["n"] or 0),
            "running": by_status.get("running", 0),
            # Ages of the jobs waiting RIGHT NOW, in seconds. None when the
            # queue is empty — which is not the same as zero, and the UI says so.
            "oldest_wait_s": _sec(queue_row["oldest_s"]),
            "mean_wait_s": _sec(queue_row["mean_s"]),
            "median_wait_s": _sec(queue_row["median_s"]),
            # How long jobs that DID run waited before a worker took them.
            # ``served`` is how many rows carry the measurement at all — rows
            # from before migration 027 do not, and a median over three rows
            # deserves less trust than one over three thousand, so the count
            # travels with the numbers.
            "served": int(served_row["n"] or 0),
            "served_mean_wait_s": _sec(served_row["mean_s"]),
            "served_median_wait_s": _sec(served_row["median_s"]),
            "served_p95_wait_s": _sec(served_row["p95_s"]),
            "served_max_wait_s": _sec(served_row["max_s"]),
        },
        "by_target": [
            {"target": tgt, "counts": counts, "total": sum(counts.values())}
            for tgt, counts in sorted(by_target.items(), key=lambda kv: -sum(kv[1].values()))
        ],
        "top_errors": [{"error": r["error"], "count": int(r["n"])} for r in top_errors],
    }


async def get_audit_by_job(pool: asyncpg.Pool, job_id: str) -> dict | None:
    """Scope + source key for a queued job's audit row, newest first.

    The worker patches its audit row by ``job_id`` and its error paths never
    carry the scope, so failure capture reads it back from here rather than
    threading a source key through every one of them. Narrow on purpose — this
    runs on the failure path and only needs enough to address the blob.
    """
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, key, action, status
        FROM audit_log WHERE job_id = $1 ORDER BY id DESC LIMIT 1
        """,
        job_id,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
        "key": row["key"],
        "action": row["action"],
        "status": row["status"],
    }


async def get_audit_by_id(pool: asyncpg.Pool, audit_id: int) -> dict | None:
    """Fetch a single audit row by id. Used by the profile-download
    endpoint to look up the blob key + scope without re-listing, and
    by the local repro tooling to recover ``target_format`` + the
    error context for a failed conversion."""
    row = await pool.fetchrow(
        """
        SELECT id, ts, user_sub, scope_kind, scope_id, profile_key, log_key, failure_key, key,
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
        "log_key": row["log_key"],
        "failure_key": row["failure_key"],
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
