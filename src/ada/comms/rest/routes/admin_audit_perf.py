"""Admin audit detail + perf-dashboard + issue-bot-config routes (M5/M6/M7
admin audit panel): issue-target config, manual issue-bot sync, the
cross-conversion / frontend-load / render perf snapshots and hotspots, the
per-audit-row detail reads (source/profile/log/metrics downloads), metrics
cleanup, and the audit log list + summary.

:func:`run_issue_bot_for` and :func:`run_issue_bot_for_conversion` are also
called from the issue-bot poller (``_issue_bot_loop``, still inside
``create_app``) — they take a plain DB pool rather than ``RestContext``
since neither needs storage or the job queue, so ``create_app`` can keep
calling them directly under their old underscore names with no wrapper.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ada.config import logger
from ada.core.file_system import new_temp_path

from .. import auth as auth_module
from .. import db as db_module
from .. import failure_capture
from ..auth import User
from ..scope import Scope
from .deps import RestContext, require_pool, rest_context

router = APIRouter()

# ── Issue target configuration (M5) ───────────────────────────
#
# Tokens are deployed via env vars (typically populated from a
# k8s Secret). The DB stores only the env var name, never the
# raw token. ``GET`` reports whether the configured env var is
# currently set on this API process so the admin sees "token
# configured" vs "token env var missing".

_ISSUE_KIND_KEY = "audit.issue_target.kind"
_ISSUE_REPO_KEY = "audit.issue_target.repo"
_ISSUE_BASE_URL_KEY = "audit.issue_target.base_url"
_ISSUE_TOKEN_ENV_KEY = "audit.issue_target.token_env_name"
_ISSUE_TARGET_KINDS: frozenset[str] = frozenset({"disabled", "github", "forgejo"})


async def load_issue_target_config(pool) -> dict | None:
    """Read the configured issue target from app_settings + the
    token from the named env var. Returns ``None`` when the
    target is disabled / unconfigured / missing the token; the
    caller treats that as ``issue_bot_status='skipped'``.
    """
    kind = await db_module.get_setting(pool, _ISSUE_KIND_KEY)
    if not kind or kind.strip().lower() in ("", "disabled", "off"):
        return None
    repo = await db_module.get_setting(pool, _ISSUE_REPO_KEY)
    if not repo:
        return None
    token_env = await db_module.get_setting(pool, _ISSUE_TOKEN_ENV_KEY)
    if not token_env:
        return None
    token = os.environ.get(token_env.strip())
    if not token:
        logger.warning(
            "issue-bot: token env var %r is not set; skipping sync",
            token_env,
        )
        return None
    base_url = await db_module.get_setting(pool, _ISSUE_BASE_URL_KEY)
    return {
        "kind": kind.strip().lower(),
        "repo": repo.strip(),
        "base_url": (base_url or "").strip() or None,
        "token": token,
        "token_env": token_env.strip(),
    }


async def run_issue_bot_for(pool, run: dict) -> None:
    """Sync one finished audit run against the configured forge.

    Stamps the run's ``issue_bot_status`` to a terminal value
    ('done' / 'skipped' / 'failed'). Catches and records every
    exception so a single bad run can't kill the poller.
    """
    from .. import audit_issue, issue_client

    run_id = run["id"]
    cfg = await load_issue_target_config(pool)
    if cfg is None:
        await db_module.mark_audit_run_issue_bot(
            pool,
            run_id,
            status="skipped",
            error="issue target disabled or token env var unset",
        )
        return

    try:
        failed = await db_module.list_failed_audit_run_jobs(pool, run_id)
    except Exception as exc:
        logger.exception("issue-bot: list_failed_audit_run_jobs failed")
        await db_module.mark_audit_run_issue_bot(
            pool,
            run_id,
            status="failed",
            error=f"db read failed: {exc}",
        )
        return

    # No failures → nothing to publish, but we still rebuild the
    # dashboard so a clean run flips the dashboard back to "no
    # open regressions".
    try:
        client = issue_client.build_client(
            cfg["kind"],
            repo=cfg["repo"],
            token=cfg["token"],
            base_url=cfg["base_url"],
        )
    except Exception as exc:
        await db_module.mark_audit_run_issue_bot(
            pool,
            run_id,
            status="failed",
            error=f"client init failed: {exc}",
        )
        return

    summary: dict
    if failed:
        try:
            summary = await audit_issue.sync_run_issues(
                client,
                run=run,
                failed_jobs=failed,
            )
        except Exception as exc:
            logger.exception("issue-bot: sync_run_issues failed")
            await db_module.mark_audit_run_issue_bot(
                pool,
                run_id,
                status="failed",
                error=f"sync failed: {exc}",
            )
            return
    else:
        summary = {"opened": 0, "commented": 0, "errors": [], "unique_failures": 0}

    try:
        dash = await audit_issue.rebuild_dashboard_issue(client, last_run=run)
    except Exception as exc:
        logger.exception("issue-bot: rebuild_dashboard_issue failed")
        dash = {"updated": False, "error": str(exc)}

    if summary["errors"] or not dash.get("updated", False):
        note_parts: list[str] = []
        if summary["errors"]:
            note_parts.append(f"{len(summary['errors'])} per-issue errors: " + "; ".join(summary["errors"][:3]))
        if not dash.get("updated", False) and dash.get("error"):
            note_parts.append(f"dashboard: {dash['error']}")
        await db_module.mark_audit_run_issue_bot(
            pool,
            run_id,
            status="failed",
            error=" | ".join(note_parts) or "unknown",
        )
        return

    note = f"opened={summary['opened']} commented={summary['commented']} " f"unique={summary['unique_failures']}"
    await db_module.mark_audit_run_issue_bot(
        pool,
        run_id,
        status="done" if failed else "skipped",
        error=None if failed else "no failures to report",
    )
    logger.info("issue-bot: synced run %s — %s", run_id, note)


async def run_issue_bot_for_conversion(pool, row: dict) -> None:
    """Sync ONE user-driven failed conversion against the forge.

    Reuses :func:`sync_run_issues` with a 1-job list and a
    synthetic 'run' wrapper labelled "user conversion" so the
    comment / issue body wording reflects the trigger. Skips
    the dashboard rebuild — that's the responsibility of the
    audit-run bot pass; rebuilding on every single user
    failure would hammer the forge needlessly.
    """
    from .. import audit_issue, issue_client

    audit_id = int(row["id"])
    cfg = await load_issue_target_config(pool)
    if cfg is None:
        await db_module.mark_audit_log_issue_bot(
            pool,
            audit_id,
            status="skipped",
            error="issue target disabled or token env var unset",
        )
        return

    try:
        client = issue_client.build_client(
            cfg["kind"],
            repo=cfg["repo"],
            token=cfg["token"],
            base_url=cfg["base_url"],
        )
    except Exception as exc:
        await db_module.mark_audit_log_issue_bot(
            pool,
            audit_id,
            status="failed",
            error=f"client init failed: {exc}",
        )
        return

    run_wrapper = {
        "id": f"audit-row-{audit_id}",
        "started_at": row.get("ts"),
    }
    try:
        summary = await audit_issue.sync_run_issues(
            client,
            run=run_wrapper,
            failed_jobs=[row],
            source_label="user conversion",
        )
    except Exception as exc:
        logger.exception(
            "issue-bot: sync_run_issues failed for audit row %s",
            audit_id,
        )
        await db_module.mark_audit_log_issue_bot(
            pool,
            audit_id,
            status="failed",
            error=f"sync failed: {exc}",
        )
        return

    if summary["errors"]:
        await db_module.mark_audit_log_issue_bot(
            pool,
            audit_id,
            status="failed",
            error="; ".join(summary["errors"][:3]),
        )
        return

    await db_module.mark_audit_log_issue_bot(
        pool,
        audit_id,
        status="done",
        error=None,
    )
    logger.info(
        "issue-bot: synced user conversion %s — opened=%d commented=%d",
        audit_id,
        summary["opened"],
        summary["commented"],
    )


@router.get("/audit/issue-target")
async def admin_issue_target_get(request: Request) -> JSONResponse:
    pool = require_pool(request)
    kind = await db_module.get_setting(pool, _ISSUE_KIND_KEY) or "disabled"
    repo = await db_module.get_setting(pool, _ISSUE_REPO_KEY) or ""
    base_url = await db_module.get_setting(pool, _ISSUE_BASE_URL_KEY) or ""
    token_env = await db_module.get_setting(pool, _ISSUE_TOKEN_ENV_KEY) or ""
    # ``token_present`` is the truthy-state of the env var on the
    # currently-serving replica. Replicas with different env
    # would disagree here — that's fine, the UI label is "as
    # seen by this API process".
    token_present = bool(token_env and os.environ.get(token_env))
    return JSONResponse(
        {
            "kind": kind,
            "repo": repo,
            "base_url": base_url,
            "token_env_name": token_env,
            "token_present": token_present,
        }
    )


@router.put("/audit/issue-target")
async def admin_issue_target_set(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Overwrite the four issue-target settings atomically.

    Body: ``{"kind": "github"|"forgejo"|"disabled", "repo": "owner/name",
             "base_url": "...", "token_env_name": "..."}``.

    We never accept a raw ``token`` field here — credentials live
    in env vars (sourced from k8s Secrets); the operator changes
    the actual token by rotating the Secret + re-rolling the
    deployment, not via this endpoint.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    kind = (body.get("kind") or "disabled").strip().lower()
    if kind not in _ISSUE_TARGET_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"kind must be one of {sorted(_ISSUE_TARGET_KINDS)}",
        )
    repo = (body.get("repo") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    token_env = (body.get("token_env_name") or "").strip()
    if kind != "disabled":
        if not repo or "/" not in repo:
            raise HTTPException(
                status_code=400,
                detail="repo must be 'owner/name' when kind is not disabled",
            )
        if kind == "forgejo" and not base_url:
            raise HTTPException(
                status_code=400,
                detail=("base_url required for forgejo " "(e.g. https://git.example.com/api/v1)"),
            )
        if not token_env:
            raise HTTPException(
                status_code=400,
                detail="token_env_name required when kind is not disabled",
            )
    await db_module.set_setting(pool, _ISSUE_KIND_KEY, kind, updated_by=user.sub)
    await db_module.set_setting(pool, _ISSUE_REPO_KEY, repo, updated_by=user.sub)
    await db_module.set_setting(pool, _ISSUE_BASE_URL_KEY, base_url, updated_by=user.sub)
    await db_module.set_setting(pool, _ISSUE_TOKEN_ENV_KEY, token_env, updated_by=user.sub)
    token_present = bool(token_env and os.environ.get(token_env))
    return JSONResponse(
        {
            "kind": kind,
            "repo": repo,
            "base_url": base_url,
            "token_env_name": token_env,
            "token_present": token_present,
        }
    )


@router.post("/audit/runs/{run_id}/sync-issues")
async def admin_audit_run_sync_issues(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Manually retry the issue-bot for one run. Clears the
    run's ``issue_bot_status`` so the next poller tick picks it
    up — also kicks off an immediate sync as a BackgroundTask so
    the user doesn't have to wait the full 30 s for the poller."""
    pool = require_pool(request)
    run = await db_module.get_audit_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="audit run not found")
    if run["status"] != "finished":
        raise HTTPException(
            status_code=400,
            detail="run is not finished; sync only meaningful on finished runs",
        )
    ok = await db_module.reset_audit_run_issue_bot(pool, run_id)
    if not ok:
        raise HTTPException(status_code=409, detail="reset failed (race?)")

    # Kick the bot immediately for snappier feedback. The poller
    # would catch it on its next tick anyway, but the user just
    # clicked a button and waiting 30s is unfriendly.
    async def _kick() -> None:
        claimed = await db_module.claim_audit_run_for_issue_bot(pool)
        if claimed is not None:
            await run_issue_bot_for(pool, claimed)

    background_tasks.add_task(_kick)
    return JSONResponse({"id": run_id, "status": "queued"}, status_code=202)


@router.post("/audit/{audit_id}/sync-issue")
async def admin_audit_log_sync_issue(
    audit_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Manually retry the issue-bot for ONE failed conversion
    (M5b). Mirror of the per-run sync endpoint; resets the
    row's issue_bot_status and kicks an immediate sync as a
    background task so the operator gets quick feedback."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    if row.get("status") not in ("error", "failed"):
        raise HTTPException(
            status_code=400,
            detail="row is not in a failed state; sync only meaningful on failures",
        )
    ok = await db_module.reset_audit_log_issue_bot(pool, audit_id)
    if not ok:
        raise HTTPException(status_code=409, detail="reset failed (race?)")

    async def _kick() -> None:
        claimed = await db_module.claim_failed_conversion_for_issue_bot(pool)
        if claimed is not None:
            await run_issue_bot_for_conversion(pool, claimed)

    background_tasks.add_task(_kick)
    return JSONResponse({"id": audit_id, "status": "queued"}, status_code=202)


# ── Cross-conversion perf dashboard (M6) ──────────────────────
#
# GET /admin/audit/perf?since=30&trigger=all
#   Aggregates audit_log convert rows over the last N days,
#   returns per-cell metrics + streaming-candidate verdict.
#
# GET /admin/audit/perf/thresholds
# PUT /admin/audit/perf/thresholds
#   Read / update the streaming-classifier thresholds. Defaults
#   ship in audit_perf.DEFAULT_THRESHOLDS; admin overrides land
#   in app_settings under audit.perf.thresholds.<key>.

_PERF_TRIGGERS: frozenset[str] = frozenset({"all", "audit", "user"})


async def load_perf_thresholds(pool) -> dict:
    """Read the admin-overridable thresholds from app_settings,
    layered on top of the ``audit_perf.DEFAULT_THRESHOLDS``. Keys
    live under ``audit.perf.thresholds.<short_name>``; values are
    stored as JSON-encoded floats so a typo'd string can't sneak
    through to the classifier."""
    from .. import audit_perf

    overrides: dict[str, float] = {}
    for key in audit_perf.DEFAULT_THRESHOLDS:
        raw = await db_module.get_setting(
            pool,
            f"audit.perf.thresholds.{key}",
        )
        if raw is None:
            continue
        try:
            overrides[key] = float(raw)
        except (TypeError, ValueError):
            continue
    return audit_perf.merged_thresholds(overrides)


@router.get("/audit/perf")
async def admin_audit_perf(
    request: Request,
    since: int = 30,
    trigger: str = "all",
    audit_run_id: str | None = None,
    worker_image_tag: str | None = None,
) -> JSONResponse:
    """Cross-conversion perf snapshot. ``since`` is days back from
    now; ``trigger`` is one of ``all`` / ``audit`` / ``user``.

    ``audit_run_id`` locks the snapshot to one sweep; pair with
    ``worker_image_tag`` to lock it to one worker build (so an
    upgrade between the same-named runs doesn't smear results).

    Response shape:

    ``{"cells": [...with streaming verdict],
       "thresholds": {...effective},
       "since_days": N,
       "trigger": "...",
       "audit_run_id": ... | None,
       "worker_image_tag": ... | None,
       "generated_at": "ISO-8601"}``

    Every cell in ``cells`` carries a ``streaming`` field
    (``{"is_candidate": bool, "signals": [...]}``) so the UI can
    render the badge without an extra round trip.
    """
    from datetime import datetime, timezone

    from .. import audit_perf

    pool = require_pool(request)
    trig = (trigger or "all").strip().lower()
    if trig not in _PERF_TRIGGERS:
        raise HTTPException(
            status_code=400,
            detail=f"trigger must be one of {sorted(_PERF_TRIGGERS)}",
        )
    run_id = (audit_run_id or "").strip() or None
    worker_tag = (worker_image_tag or "").strip() or None
    cells = await db_module.aggregate_conversion_metrics(
        pool,
        since_days=since,
        trigger=None if trig == "all" else trig,
        audit_run_id=run_id,
        worker_image_tag=worker_tag,
    )
    thresholds = await load_perf_thresholds(pool)
    annotated = audit_perf.annotate(cells, thresholds=thresholds)
    return JSONResponse(
        {
            "cells": annotated,
            "thresholds": thresholds,
            "signal_reasons": audit_perf.SIGNAL_REASONS,
            "since_days": max(1, min(365, since)),
            "trigger": trig,
            "audit_run_id": run_id,
            "worker_image_tag": worker_tag,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/audit/frontend-loads")
async def admin_audit_frontend_loads(
    request: Request,
    since: int = 30,
) -> JSONResponse:
    """Per-file browser model-load perf snapshot (``action = 'view'``).

    One cell per GLB loaded, with p50/p95 of every load phase and a
    ``dominant_bound`` label (io / network / cpu / gpu) so a slow
    load is immediately attributable to a bottleneck class. ``since``
    is days back from now. Drives the admin "Frontend Loads" tab.
    """
    from datetime import datetime, timezone

    pool = require_pool(request)
    cells = await db_module.aggregate_view_load_metrics(pool, since_days=since)
    return JSONResponse(
        {
            "cells": cells,
            "since_days": max(1, min(365, since)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/audit/frontend-loads/hotspots")
async def admin_audit_frontend_loads_hotspots(
    request: Request,
    key: str | None = None,
    since: int = 30,
    limit: int = 100,
    kind: str = "view",
) -> JSONResponse:
    """Function-level hotspots across browser ``view`` loads or
    ``render`` windows (``kind``) — summed JS Self-Profiling self-time
    per TS/WASM frame. Optionally scoped to one ``key`` (GLB file).
    Empty ``functions`` with ``loads_in_window=0`` means no profiled
    rows (self-profiling unsupported/disabled or
    ``Document-Policy: js-profiling`` not served)."""
    pool = require_pool(request)
    key_arg = (key or "").strip() or None
    action = "render" if (kind or "").strip().lower() == "render" else "view"
    out = await db_module.aggregate_view_load_hotspots(pool, action=action, key=key_arg, since_days=since, limit=limit)
    return JSONResponse({**out, "key": key_arg, "kind": action, "since_days": max(1, min(365, since))})


@router.get("/audit/render")
async def admin_audit_render(
    request: Request,
    since: int = 30,
) -> JSONResponse:
    """Per-file steady-state render-performance snapshot
    (``action = 'render'``). One cell per GLB with median/worst FPS,
    CPU vs GPU frame time, draw calls + triangles rendered, and a
    ``dominant_bound`` (cpu / gpu) label."""
    from datetime import datetime, timezone

    pool = require_pool(request)
    cells = await db_module.aggregate_render_metrics(pool, since_days=since)
    return JSONResponse(
        {
            "cells": cells,
            "since_days": max(1, min(365, since)),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/audit/perf/workers")
async def admin_audit_perf_workers(
    request: Request,
    since: int = 90,
) -> JSONResponse:
    """Distinct ``worker_image_tag`` values seen in the perf
    window, with the row count + most recent timestamp for each.
    Drives the PerformanceTab "Worker SHA" picker so the user
    only sees tags that have real data behind them. Sorted by
    ``last_seen`` desc — the freshest build first.
    """
    pool = require_pool(request)
    days = max(1, min(365, since))
    rows = await pool.fetch(
        """
        SELECT worker_image_tag AS tag,
               COUNT(*)        AS samples,
               MAX(ts)         AS last_seen
        FROM audit_log
        WHERE action = 'convert'
          AND worker_image_tag IS NOT NULL
          AND ts > NOW() - ($1 * INTERVAL '1 day')
        GROUP BY worker_image_tag
        ORDER BY last_seen DESC
        """,
        days,
    )
    workers = [
        {
            "tag": r["tag"],
            "samples": int(r["samples"] or 0),
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        }
        for r in rows
    ]
    return JSONResponse({"workers": workers, "since_days": days})


@router.get("/audit/perf/thresholds")
async def admin_perf_thresholds_get(request: Request) -> JSONResponse:
    """Effective streaming-classifier thresholds (defaults +
    admin overrides). Returned alongside the per-key defaults so
    the editor can show "reset to default" deltas."""
    from .. import audit_perf

    pool = require_pool(request)
    return JSONResponse(
        {
            "thresholds": await load_perf_thresholds(pool),
            "defaults": audit_perf.DEFAULT_THRESHOLDS,
        }
    )


@router.put("/audit/perf/thresholds")
async def admin_perf_thresholds_set(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Overwrite thresholds. Body: ``{"<key>": <float>, ...}``.

    Unknown keys are rejected with 400 so a typo doesn't quietly
    disable a signal. Pass ``null`` for a key to clear an
    override (the default takes over). All writes happen against
    the same ``app_settings`` table the rest of the admin
    settings use.
    """
    from .. import audit_perf

    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    unknown = sorted(set(body.keys()) - set(audit_perf.DEFAULT_THRESHOLDS))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown threshold keys: {unknown}",
        )
    for key, raw in body.items():
        setting_key = f"audit.perf.thresholds.{key}"
        if raw is None:
            # Clear → write the empty string; get_setting + float()
            # treat that as "no override" because the float()
            # coercion fails. Cleanest path without adding a
            # dedicated delete helper.
            await db_module.set_setting(
                pool,
                setting_key,
                "",
                updated_by=user.sub,
            )
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"{key}: must be a number ({exc})",
            ) from exc
        await db_module.set_setting(
            pool,
            setting_key,
            str(val),
            updated_by=user.sub,
        )
    return JSONResponse(
        {
            "thresholds": await load_perf_thresholds(pool),
            "defaults": audit_perf.DEFAULT_THRESHOLDS,
        }
    )


@router.get("/audit/perf/hotspots")
async def admin_audit_perf_hotspots(
    request: Request,
    source_ext: str | None = None,
    target_format: str | None = None,
    since: int = 30,
    limit: int = 25,
) -> JSONResponse:
    """Function-level hot paths inside one cell, aggregated across
    every cProfile-tagged conversion in the window.

    ``source_ext`` and ``target_format`` narrow the join to one
    (source × target) cell; omit either to aggregate across all
    cells (useful for "what's slow overall" exploratory views).
    Returns the top N functions by SUMmed cumulative time —
    same shape pstats uses, just rolled up.

    Data only exists once ``profile_conversions=true`` is set on
    the app settings (global) or per-job, AND the background
    profile-parser loop has caught up with the new .prof blobs.
    ``profiles_in_window=0`` flags the "profiling disabled or
    nothing parsed yet" empty state cleanly.
    """
    pool = require_pool(request)
    out = await db_module.aggregate_profile_hotspots(
        pool,
        source_ext=source_ext,
        target_format=target_format,
        since_days=since,
        limit=limit,
    )
    return JSONResponse(
        {
            "source_ext": source_ext,
            "target_format": target_format,
            **out,
        }
    )


def _audit_time_bounds(since: str | None, until: str | None):
    """Parse the shared time window, or 400 with the offending value.

    Relative forms ("6h") resolve against the SERVER clock — see
    db.parse_audit_time_bound for why the browser must not do it. Both the
    log and the summary take the same two parameters so a window set on one
    means the same thing on the other.
    """
    try:
        return (
            db_module.parse_audit_time_bound(since),
            db_module.parse_audit_time_bound(until),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# REGISTERED BEFORE ``/audit/{audit_id}`` ON PURPOSE. Starlette matches in
# declaration order, so a static segment that could also parse as a path
# parameter has to come first — otherwise this lands on the row-detail
# route, which tries to read "summary" as an int and 422s. Moving it below
# is a silent breakage: the URL still exists, it just answers wrong.
@router.get("/audit/summary")
async def admin_audit_summary(
    request: Request,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    target: str | None = None,
    key: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> JSONResponse:
    """Counts behind the Audit tab's Overview, under the log's own filter.

    Takes the same query parameters as ``GET /admin/audit`` and normalises
    them identically, so one filter drives both surfaces. ``status`` is
    accepted-and-ignored by omission: the summary exists to show how a
    population splits across states, and the status tiles are the control
    that sets that filter — honouring it would zero the other tiles the
    moment you clicked one. See ``db.summarize_audit``.

    Counting is done in the database rather than over a page of rows: the
    log is keyset-paginated at 100, so summing what the client happens to
    be holding would report "13 failed" for a sweep with hundreds.
    """
    pool = require_pool(request)
    key_like = (key or "").strip() or None
    target_format = (target or "").strip().lstrip(".").lower() or None
    since_ts, until_ts = _audit_time_bounds(since, until)
    summary = await db_module.summarize_audit(
        pool,
        user_sub=user_sub,
        scope_kind=scope_kind,
        scope_id=scope_id,
        action=action,
        target_format=target_format,
        key_like=key_like,
        since=since_ts,
        until=until_ts,
    )
    return JSONResponse(summary)


@router.get("/audit/{audit_id}")
async def admin_audit_get(
    audit_id: int,
    request: Request,
) -> JSONResponse:
    """Return a single audit row's metadata. The local repro
    tooling reads ``target_format`` + ``key`` from here so it can
    invoke the converter without re-listing the whole audit log."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    return JSONResponse(row)


@router.get("/audit/{audit_id}/source")
async def admin_audit_source(
    audit_id: int,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> StreamingResponse:
    """Download the original source blob referenced by an audit
    row. Mirrors the profile-download pattern but resolves
    ``scope_kind/scope_id + key`` instead of ``profile_key`` —
    useful for reproducing a failed conversion locally without
    having to know the storage scope. 404 when the row is missing
    or the blob is gone (e.g. expired ephemeral storage)."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    key = row.get("key")
    if not key:
        raise HTTPException(status_code=404, detail="audit row has no source key")
    scope = (
        Scope.shared()
        if row["scope_kind"] == "shared"
        else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
    )
    try:
        result = await ctx.storage.open_stream(scope, key)
    except FileNotFoundError as exc:
        # The original is gone — the exact case failure capture exists for.
        # Serve the preserved copy so a row stays reproducible after the
        # user deletes (or replaces) the file that broke.
        failure_key = row.get("failure_key")
        if not failure_key:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            result = await ctx.storage.open_stream(failure_capture.failure_scope(), failure_key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = key.rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if result.content_encoding:
        headers["Content-Encoding"] = result.content_encoding
    return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)


@router.get("/audit/{audit_id}/profile")
async def admin_audit_profile(
    audit_id: int,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> StreamingResponse:
    """Download the cProfile dump attached to an audit row, if
    any. 404 when the row or its profile_key is missing."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    profile_key = row.get("profile_key")
    if not profile_key:
        raise HTTPException(status_code=404, detail="no profile attached to this row")
    scope = (
        Scope.shared()
        if row["scope_kind"] == "shared"
        else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
    )
    try:
        result = await ctx.storage.open_stream(scope, profile_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # .prof is binary cProfile output (marshal-formatted). Browsers
    # download it as-is — snakeviz / speedscope load directly.
    filename = profile_key.rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if result.content_encoding:
        headers["Content-Encoding"] = result.content_encoding
    return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)


@router.get("/audit/{audit_id}/log")
async def admin_audit_log_file(
    audit_id: int,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> StreamingResponse:
    """Download the captured stdout/stderr log for a conversion (every conversion now ships
    one). 404 when the row or its log_key is missing — i.e. a conversion that predates the
    log-capture, not a silent gap."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    log_key = row.get("log_key")
    if not log_key:
        raise HTTPException(status_code=404, detail="no log attached to this row")
    scope = (
        Scope.shared()
        if row["scope_kind"] == "shared"
        else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
    )
    try:
        result = await ctx.storage.open_stream(scope, log_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {"Content-Disposition": f'attachment; filename="{log_key.rsplit("/", 1)[-1]}"'}
    if result.content_encoding:
        headers["Content-Encoding"] = result.content_encoding
    return StreamingResponse(result.stream, media_type="text/plain; charset=utf-8", headers=headers)


@router.get("/audit/{audit_id}/metrics-history")
async def admin_audit_metrics_history(
    audit_id: int,
    request: Request,
) -> JSONResponse:
    """Return the per-heartbeat resource samples captured by the
    worker subprocess wrapper. One sample per ~2 s while the
    convert child was alive — RSS, CPU user/sys, IO bytes, all
    time-aligned by ``elapsed_s``. The SPA renders these as a
    time-series chart in the audit details modal so an operator
    sees memory growth + CPU pressure as the run progresses.

    Empty array when the row pre-dates the subprocess wrapper or
    the worker pod was killed before it could append. ``None``
    from the DB collapses to ``[]`` here so the chart renders an
    explicit "no data" state rather than crashing on null."""
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    samples = row.get("metrics_samples") or []
    return JSONResponse({"audit_id": audit_id, "samples": samples})


@router.get("/audit/{audit_id}/client-metrics")
async def admin_audit_client_metrics(
    audit_id: int,
    request: Request,
) -> JSONResponse:
    """Return the ``client_metrics`` payload for one browser
    view/render audit row — the per-phase IO/network/CPU/GPU split,
    payload + device context, and (when profiling was on) the
    per-function self-time frames. Backs the audit-log detail view's
    Client tab so a single load/render event can be inspected.
    ``null`` when the row isn't a browser-instrumented one."""
    pool = require_pool(request)
    cm = await db_module.get_audit_client_metrics(pool, audit_id)
    return JSONResponse({"audit_id": audit_id, "client_metrics": cm})


@router.get("/audit/{audit_id}/profile/stats")
async def admin_audit_profile_stats(
    audit_id: int,
    request: Request,
    limit: int = 500,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Server-side parse of the .prof for the SPA dashboard. Returns
    a JSON list of per-function rows the table can sort/filter
    without dragging pstats / snakeviz / a marshal parser into the
    browser. ``.prof`` download stays available alongside.

    Each row carries: function name, file:line, ncalls, primitive
    ncalls, total time (excluding sub-calls), per-call total,
    cumulative time (including sub-calls), per-call cumulative.
    """
    pool = require_pool(request)
    row = await db_module.get_audit_by_id(pool, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
    profile_key = row.get("profile_key")
    if not profile_key:
        raise HTTPException(status_code=404, detail="no profile attached to this row")
    scope = (
        Scope.shared()
        if row["scope_kind"] == "shared"
        else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
    )
    # pstats only reads from disk, so stash the bytes in a tempfile.
    try:
        data = await ctx.storage.get_bytes(scope, profile_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    import pstats

    tmp = new_temp_path(suffix=".prof")
    try:
        tmp.write_bytes(data)
        try:
            stats = pstats.Stats(str(tmp))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to parse profile: {exc}") from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    # stats.stats: dict[(filename, lineno, funcname), (cc, nc, tt, ct, callers)].
    rows = []
    total_tt = 0.0
    for (fn, line, name), (cc, nc, tt, ct, _callers) in stats.stats.items():
        total_tt += tt
        rows.append(
            {
                "func": name,
                "file": fn,
                "line": line,
                "ncalls": nc,
                "primitive_calls": cc,
                "tottime": tt,
                "percall_tot": (tt / nc) if nc else 0.0,
                "cumtime": ct,
                "percall_cum": (ct / cc) if cc else 0.0,
            }
        )
    # Default presentation sort: cumtime desc — same as pstats default.
    rows.sort(key=lambda r: r["cumtime"], reverse=True)
    if limit and len(rows) > limit:
        rows = rows[:limit]
    return JSONResponse(
        {
            "audit_id": audit_id,
            "total_tottime": total_tt,
            "row_count": len(rows),
            "rows": rows,
        }
    )


@router.delete("/audit/metrics")
async def admin_clear_metrics(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Wipe all metrics + profile blobs. Audit rows themselves
    stay; only the metrics columns are nulled and the .prof blobs
    deleted from storage. Used to reclaim DB / object-store space
    after a profiling session."""
    pool = require_pool(request)
    result = await db_module.clear_audit_metrics(pool)
    deleted_blobs = 0
    blob_errors: list[str] = []
    for entry in result["profile_keys"]:
        try:
            scope = (
                Scope.shared()
                if entry["scope_kind"] == "shared"
                else Scope(kind=entry["scope_kind"], id=entry["scope_id"])  # type: ignore[arg-type]
            )
            await ctx.storage.delete(scope, entry["profile_key"])
            deleted_blobs += 1
        except FileNotFoundError:
            # Already gone — fine.
            deleted_blobs += 1
        except Exception as exc:
            logger.warning(
                "clear_metrics: failed to delete %s: %s",
                entry["profile_key"],
                exc,
            )
            # Report only the failed key to the client; the exception detail is logged
            # above (avoid leaking backend/stack-trace text in the response).
            blob_errors.append(entry["profile_key"])
    return JSONResponse(
        {
            "rows_cleared": result["rows_cleared"],
            "profiles_deleted": deleted_blobs,
            "errors": blob_errors,
        }
    )


@router.get("/audit")
async def admin_audit(
    request: Request,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    target: str | None = None,
    status: str | None = None,
    key: str | None = None,
    since: str | None = None,
    until: str | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> JSONResponse:
    pool = require_pool(request)
    # ``key`` is a case-insensitive substring filter on the source filepath/
    # filename so the audit log can be narrowed to one file or folder.
    key_like = (key or "").strip() or None
    # ``target`` filters by the conversion's target format (glb / ifc / step / …).
    target_format = (target or "").strip().lstrip(".").lower() or None
    # ``status`` filters by job state (queued / running / done / error).
    status_norm = (status or "").strip().lower() or None
    since_ts, until_ts = _audit_time_bounds(since, until)
    rows = await db_module.list_audit(
        pool,
        user_sub=user_sub,
        scope_kind=scope_kind,
        scope_id=scope_id,
        action=action,
        target_format=target_format,
        statuses=[status_norm] if status_norm else None,
        key_like=key_like,
        since=since_ts,
        until=until_ts,
        limit=limit,
        before_id=before_id,
    )
    # Page cursor: smallest id from this batch. Caller passes it back
    # as ``before_id`` to fetch the next older page.
    next_before = rows[-1]["id"] if len(rows) >= max(1, min(limit, 500)) else None
    return JSONResponse({"entries": rows, "next_before_id": next_before})
