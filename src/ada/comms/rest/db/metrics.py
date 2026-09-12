"""Aggregated conversion, view-load and render metrics for the perf dashboard."""

from __future__ import annotations

import json

import asyncpg


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


async def aggregate_view_load_metrics(
    pool: asyncpg.Pool,
    *,
    since_days: int = 30,
) -> list[dict]:
    """Per-file aggregation over recent browser model loads (``action =
    'view'`` rows written by the viewer's opt-in load instrumentation).

    Grouped by ``key`` (the GLB object loaded) because the operational
    question is "which models are slow to load, and why". For each file
    it computes p50/p95 of every load phase so a slow load can be
    attributed to a bottleneck class:

      * IO / backend storage — ``ttfb_ms`` (request -> first byte)
      * network transfer     — ``download_ms``, throughput, wire bytes
      * client CPU           — ``decompress_ms`` + ``parse_ms`` + ``prepare_ms``
      * GPU upload           — ``first_render_ms``

    ``dominant_bound`` labels the row by whichever class holds the
    largest share of the median total — the load-side analogue of the
    conversion dashboard's ``cpu_fraction`` signal. Phase fields are
    pulled out of the ``client_metrics`` JSONB; rows missing a field
    (e.g. cross-origin presigned loads with no Resource Timing split)
    simply don't contribute to that field's percentile.
    """
    days = max(1, min(365, since_days))

    def num(field: str) -> str:
        # NULLIF guards an empty-string JSON value from a bad ::numeric cast.
        return f"NULLIF(client_metrics->>'{field}', '')::numeric"

    sql = f"""
        WITH loads AS (
            SELECT
                key,
                LOWER(SUBSTRING(key FROM '\\.([^.]+)$')) AS source_ext,
                status,
                duration_ms,
                read_bytes,
                write_bytes,
                peak_rss_kb,
                {num('ttfb_ms')}         AS ttfb_ms,
                {num('download_ms')}     AS download_ms,
                {num('decompress_ms')}   AS decompress_ms,
                {num('parse_ms')}        AS parse_ms,
                {num('prepare_ms')}      AS prepare_ms,
                {num('first_render_ms')} AS first_render_ms,
                {num('total_ms')}        AS total_ms,
                {num('throughput_mbps')} AS throughput_mbps,
                {num('transfer_bytes')}  AS transfer_bytes,
                {num('triangles')}       AS triangles,
                {num('vertices')}        AS vertices
            FROM audit_log
            WHERE action = 'view'
              AND client_metrics IS NOT NULL
              AND key IS NOT NULL
              AND ts > NOW() - ($1 * INTERVAL '1 day')
        )
        SELECT
            key,
            MAX(source_ext) AS source_ext,
            COUNT(*) AS sample_count,
            COUNT(*) FILTER (WHERE status IN ('error', 'failed')) AS fail_count,
            COUNT(*) FILTER (WHERE status IN ('ok', 'done')) AS ok_count,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY duration_ms) AS total_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) AS total_ms_p95,
            MAX(duration_ms) AS total_ms_max,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY ttfb_ms) AS ttfb_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ttfb_ms) AS ttfb_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY download_ms) AS download_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY download_ms) AS download_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY decompress_ms) AS decompress_ms_p50,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY parse_ms) AS parse_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY parse_ms) AS parse_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY prepare_ms) AS prepare_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY prepare_ms) AS prepare_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY first_render_ms) AS first_render_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY first_render_ms) AS first_render_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY throughput_mbps) AS throughput_mbps_p50,
            AVG(transfer_bytes)::bigint AS transfer_bytes_avg,
            AVG(read_bytes)::bigint AS read_bytes_avg,
            AVG(write_bytes)::bigint AS write_bytes_avg,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY triangles) AS triangles_p50,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY vertices) AS vertices_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY peak_rss_kb) AS js_heap_kb_p95
        FROM loads
        GROUP BY key
        ORDER BY total_ms_p95 DESC NULLS LAST, key
    """
    rows = await pool.fetch(sql, days)

    def _f(v) -> float | None:
        return float(v) if v is not None else None

    def _i(v) -> int | None:
        return int(v) if v is not None else None

    cells: list[dict] = []
    for r in rows:
        sample_count = r["sample_count"] or 0
        fail_count = r["fail_count"] or 0
        # Bottleneck attribution from the median phases. CPU is the sum
        # of the three main-thread phases; whichever class holds the
        # biggest slice of the median total wins. Fall back to "unknown"
        # when there's no timing at all.
        io = _f(r["ttfb_ms_p50"]) or 0.0
        net = _f(r["download_ms_p50"]) or 0.0
        cpu = (_f(r["decompress_ms_p50"]) or 0.0) + (_f(r["parse_ms_p50"]) or 0.0) + (_f(r["prepare_ms_p50"]) or 0.0)
        gpu = _f(r["first_render_ms_p50"]) or 0.0
        classes = {"io": io, "network": net, "cpu": cpu, "gpu": gpu}
        accounted = sum(classes.values())
        dominant = max(classes, key=classes.get) if accounted > 0 else "unknown"
        cells.append(
            {
                "key": r["key"] or "",
                "source_ext": r["source_ext"] or "",
                "sample_count": sample_count,
                "fail_count": fail_count,
                "ok_count": r["ok_count"] or 0,
                "failure_rate": (fail_count / sample_count if sample_count > 0 else 0.0),
                "total_ms_p50": _i(r["total_ms_p50"]),
                "total_ms_p95": _i(r["total_ms_p95"]),
                "total_ms_max": _i(r["total_ms_max"]),
                "ttfb_ms_p50": _i(r["ttfb_ms_p50"]),
                "ttfb_ms_p95": _i(r["ttfb_ms_p95"]),
                "download_ms_p50": _i(r["download_ms_p50"]),
                "download_ms_p95": _i(r["download_ms_p95"]),
                "decompress_ms_p50": _i(r["decompress_ms_p50"]),
                "parse_ms_p50": _i(r["parse_ms_p50"]),
                "parse_ms_p95": _i(r["parse_ms_p95"]),
                "prepare_ms_p50": _i(r["prepare_ms_p50"]),
                "prepare_ms_p95": _i(r["prepare_ms_p95"]),
                "first_render_ms_p50": _i(r["first_render_ms_p50"]),
                "first_render_ms_p95": _i(r["first_render_ms_p95"]),
                "throughput_mbps_p50": _f(r["throughput_mbps_p50"]),
                "transfer_bytes_avg": _i(r["transfer_bytes_avg"]),
                "read_bytes_avg": _i(r["read_bytes_avg"]),
                "write_bytes_avg": _i(r["write_bytes_avg"]),
                "triangles_p50": _i(r["triangles_p50"]),
                "vertices_p50": _i(r["vertices_p50"]),
                "js_heap_kb_p95": _i(r["js_heap_kb_p95"]),
                # Median-phase bottleneck attribution.
                "io_ms": round(io, 1),
                "network_ms": round(net, 1),
                "cpu_ms": round(cpu, 1),
                "gpu_ms": round(gpu, 1),
                "dominant_bound": dominant,
            }
        )
    return cells


async def aggregate_view_load_hotspots(
    pool: asyncpg.Pool,
    *,
    action: str = "view",
    key: str | None = None,
    since_days: int = 30,
    limit: int = 100,
) -> dict:
    """Function-level hotspots across browser ``view`` loads or ``render``
    windows — the client-side analogue of ``aggregate_profile_hotspots``
    for conversions.

    The viewer's opt-in instrumentation runs the JS Self-Profiling API
    (during a load, or per render window when ``action='render'``) and
    stores the top self-time frames (TypeScript *and* WASM — pyodide/
    adacpp frames surface as ``wasm-function[...]`` or their name-section
    names) under ``client_metrics->'profile_frames'`` as
    ``[{"fn", "self_ms", "total_ms"}, ...]``. This unnests them across
    every matching row (optionally one ``key``) and sums self-time per
    function so the slowest TS/WASM calls float to the top.

    Returns ``{functions: [...], loads_in_window: N}``; an empty
    ``functions`` with ``loads_in_window=0`` means no profiled rows in
    the window (self-profiling unsupported/disabled, or the
    ``Document-Policy: js-profiling`` header isn't being served).
    """
    days = max(1, min(365, since_days))
    lim = max(1, min(1000, limit))
    act = action if action in ("view", "render") else "view"
    args: list = [act, days]
    key_filter = ""
    if key:
        args.append(key)
        key_filter = f" AND key = ${len(args)}"

    # Count profiled rows in the window first (so the UI can distinguish
    # "no data" from "no hotspots").
    loads_in_window = await pool.fetchval(
        f"""
        SELECT COUNT(*) FROM audit_log
        WHERE action = $1
          AND client_metrics ? 'profile_frames'
          AND jsonb_array_length(client_metrics->'profile_frames') > 0
          AND ts > NOW() - ($2 * INTERVAL '1 day')
          {key_filter}
        """,
        *args,
    )

    args.append(lim)
    rows = await pool.fetch(
        f"""
        WITH frames AS (
            SELECT f->>'fn' AS fn,
                   NULLIF(f->>'self_ms', '')::numeric AS self_ms,
                   NULLIF(f->>'total_ms', '')::numeric AS total_ms
            FROM audit_log,
                 LATERAL jsonb_array_elements(client_metrics->'profile_frames') AS f
            WHERE action = $1
              AND client_metrics ? 'profile_frames'
              AND ts > NOW() - ($2 * INTERVAL '1 day')
              {key_filter}
        )
        SELECT fn,
               COUNT(*) AS samples,
               SUM(self_ms)::numeric AS self_ms_sum,
               AVG(self_ms)::numeric AS self_ms_avg,
               MAX(total_ms)::numeric AS total_ms_max
        FROM frames
        WHERE fn IS NOT NULL AND fn != ''
        GROUP BY fn
        ORDER BY self_ms_sum DESC NULLS LAST
        LIMIT ${len(args)}
        """,
        *args,
    )

    def _f(v) -> float | None:
        return float(v) if v is not None else None

    functions = [
        {
            "fn": r["fn"],
            "samples": r["samples"] or 0,
            "self_ms_sum": _f(r["self_ms_sum"]),
            "self_ms_avg": _f(r["self_ms_avg"]),
            "total_ms_max": _f(r["total_ms_max"]),
            "is_wasm": bool(r["fn"]) and ("wasm" in r["fn"].lower()),
        }
        for r in rows
    ]
    return {"functions": functions, "loads_in_window": int(loads_in_window or 0)}


async def aggregate_render_metrics(
    pool: asyncpg.Pool,
    *,
    since_days: int = 30,
) -> list[dict]:
    """Per-file aggregation over steady-state render windows (``action =
    'render'`` rows). Each row is one rolling window the viewer sampled
    while a model was on screen; this rolls them up per ``key`` so a
    janky / GPU-bound model is obvious.

    For each file: median/worst FPS, CPU frame time (time between frames),
    GPU frame time (``EXT_disjoint_timer_query_webgl2`` when the client
    had it), draw calls + triangles rendered, and a ``dominant_bound``
    label — ``gpu`` when median GPU ms exceeds median CPU frame ms, else
    ``cpu`` — the steady-state analogue of the load dashboard's bound.
    """
    days = max(1, min(365, since_days))

    def num(field: str) -> str:
        return f"NULLIF(client_metrics->>'{field}', '')::numeric"

    sql = f"""
        WITH r AS (
            SELECT
                key,
                {num('fps_p50')}        AS fps_p50,
                {num('fps_min')}        AS fps_min,
                {num('frame_ms_p50')}   AS frame_ms_p50,
                {num('frame_ms_p95')}   AS frame_ms_p95,
                {num('gpu_ms_p50')}     AS gpu_ms_p50,
                {num('gpu_ms_p95')}     AS gpu_ms_p95,
                {num('draw_calls')}     AS draw_calls,
                {num('triangles')}      AS triangles,
                {num('programs')}       AS programs,
                {num('geometries')}     AS geometries,
                {num('textures')}       AS textures,
                {num('long_frames')}    AS long_frames,
                {num('frame_count')}    AS frame_count
            FROM audit_log
            WHERE action = 'render'
              AND client_metrics IS NOT NULL
              AND key IS NOT NULL
              AND ts > NOW() - ($1 * INTERVAL '1 day')
        )
        SELECT
            key,
            COUNT(*) AS window_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fps_p50) AS fps_p50,
            MIN(fps_min) AS fps_min,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY frame_ms_p50) AS frame_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY frame_ms_p95) AS frame_ms_p95,
            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY gpu_ms_p50) AS gpu_ms_p50,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY gpu_ms_p95) AS gpu_ms_p95,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY draw_calls) AS draw_calls_p50,
            MAX(draw_calls) AS draw_calls_max,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY triangles) AS triangles_p50,
            MAX(programs) AS programs_max,
            MAX(geometries) AS geometries_max,
            MAX(textures) AS textures_max,
            SUM(long_frames) AS long_frames_sum,
            SUM(frame_count) AS frame_count_sum
        FROM r
        GROUP BY key
        ORDER BY fps_p50 ASC NULLS LAST, key
    """
    rows = await pool.fetch(sql, days)

    def _f(v) -> float | None:
        return float(v) if v is not None else None

    def _i(v) -> int | None:
        return int(v) if v is not None else None

    cells: list[dict] = []
    for r in rows:
        cpu_ms = _f(r["frame_ms_p50"])
        gpu_ms = _f(r["gpu_ms_p50"])
        if gpu_ms is not None and cpu_ms is not None:
            dominant = "gpu" if gpu_ms > cpu_ms else "cpu"
        elif gpu_ms is not None:
            dominant = "gpu"
        elif cpu_ms is not None:
            dominant = "cpu"
        else:
            dominant = "unknown"
        cells.append(
            {
                "key": r["key"] or "",
                "window_count": r["window_count"] or 0,
                "fps_p50": _f(r["fps_p50"]),
                "fps_min": _f(r["fps_min"]),
                "frame_ms_p50": _f(r["frame_ms_p50"]),
                "frame_ms_p95": _f(r["frame_ms_p95"]),
                "gpu_ms_p50": _f(r["gpu_ms_p50"]),
                "gpu_ms_p95": _f(r["gpu_ms_p95"]),
                "draw_calls_p50": _i(r["draw_calls_p50"]),
                "draw_calls_max": _i(r["draw_calls_max"]),
                "triangles_p50": _i(r["triangles_p50"]),
                "programs_max": _i(r["programs_max"]),
                "geometries_max": _i(r["geometries_max"]),
                "textures_max": _i(r["textures_max"]),
                "long_frames_sum": _i(r["long_frames_sum"]),
                "frame_count_sum": _i(r["frame_count_sum"]),
                "dominant_bound": dominant,
            }
        )
    return cells


async def get_audit_client_metrics(pool: asyncpg.Pool, audit_id: int) -> dict | None:
    """Return the ``client_metrics`` JSONB for one audit_log row (the
    browser view/render instrumentation payload), or None. Backs the
    admin audit-log detail view so a single load/render event can be
    inspected phase-by-phase."""
    row = await pool.fetchrow("SELECT client_metrics FROM audit_log WHERE id = $1", audit_id)
    if row is None:
        return None
    cm = row["client_metrics"]
    if cm is None:
        return None
    # JSONB comes back as text (no codec set); parse defensively, mirroring
    # the metrics_samples / counts read paths.
    if isinstance(cm, str):
        try:
            cm = json.loads(cm)
        except Exception:
            return None
    return cm if isinstance(cm, dict) else None
