"""Profile-hotspot ingestion and aggregation (cProfile function stats)."""

from __future__ import annotations

import asyncpg

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
