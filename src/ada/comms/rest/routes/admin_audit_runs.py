"""Admin audit-run routes (M1 admin audit panel): kick off / list / inspect /
cancel / re-dispatch / rerun-cell / validate / delete a regression sweep,
plus the cell-history and per-run cell-matrix reads.

:func:`audit_dispatch`, :func:`audit_dispatch_wasm`, :func:`audit_run_list_cells`
and :func:`audit_cells_for_files` are also called from three places still
inside ``create_app``: the audit-schedule cron tick (``_scheduler_fire``),
the auto-validate poller (``_dispatch_auto_validation``), and the admin
audit-schedules "fire now" route (``routes/admin_audit_schedules.py``, next
slice of the split). That is why they take :class:`~.deps.RestContext`
explicitly rather than closing over the app's queue/storage: ``create_app``
keeps calling them under their old underscore names, bound to its own
``RestContext``, until every caller is extracted. Same shape as
``routes/plugin_jobs.py``'s ``enqueue_plugin_job``.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from ..scope import can_access as scope_can_access
from .deps import (
    RestContext,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
)

router = APIRouter()

#: Synthetic worker_pool value that routes an audit run to the in-browser
#: WASM engine instead of a NATS worker pool.
WASM_POOL = "wasm"


def audit_cells_for_files(files, validate_only: bool) -> list[tuple[str, str]]:
    """Pure cell enumeration over an already-fetched file listing —
    lets the dispatcher derive both the conversion grid and the
    parity-cell reservation from ONE listing, so the two counts
    can't disagree on what files existed at dispatch time."""
    from ..converter import ConverterRegistry, is_hidden_key, is_supported_source

    cells: list[tuple[str, str]] = []
    for f in files:
        # Skip ALL internal namespaces, not just _derived/: _overlays/ (utility previews) and
        # _reconvert/ (gallery throwaway re-conversions) are not corpus sources and must never
        # become audit cells. is_hidden_key covers all three; is_derived_key did not match
        # _reconvert/ (deliberately not a derived product), so it leaked into runs.
        if is_hidden_key(f.key):
            continue
        if not is_supported_source(f.key):
            continue
        ext = pathlib.PurePosixPath(f.key).suffix.lower()
        targets = ConverterRegistry.targets_for(ext)
        if validate_only:
            # A validation run does cross-format visual-parity only (no conversion
            # grid), and only when the source can produce a structure-preserving
            # format to compare against. Parity is a validation concern — full
            # conversion runs do not emit parity cells.
            if any(t in ("ifc", "xml", "step") for t in targets):
                cells.append((f.key, "parity"))
        else:
            for target_format in targets:
                cells.append((f.key, target_format))
    return cells


async def audit_run_list_cells(ctx: RestContext, scope_obj: Scope, validate_only: bool) -> list[tuple[str, str]]:
    """Enumerate the (source_key, target_format) cells for an audit
    run over ``scope_obj`` — the scope's non-derived, supported
    source files crossed with the converter matrix. ``validate_only``
    emits only per-source ``parity`` cells. Shared by the NATS
    dispatcher, the WASM dispatcher, and the cells endpoint so the
    three never disagree on what a run covers. May raise on a scope
    listing failure (caller decides how to surface it)."""
    files = await ctx.storage.list(scope_obj)
    return audit_cells_for_files(files, validate_only)


async def audit_dispatch(
    ctx: RestContext,
    run_id: str,
    scope_obj: Scope,
    worker_pool: str | None,
    user_sub: str,
    pool,
    force_rebuild: bool = False,
    validate_only: bool = False,
    extend: bool = False,
    reserve_validation: bool = False,
    consume_reserve: bool = False,
) -> None:
    """Enumerate the scope's files × the converter matrix and
    enqueue one regular convert job per cell. Cached cells
    (derived blob already present) are audited as ``done``
    immediately. Runs in a BackgroundTask so the request returns
    202 immediately; the operator polls the run row for progress.

    ``force_rebuild`` skips the cached-blob short-circuit so
    every cell is re-converted from source. Used for perf
    measurement runs where a 4-hour audit re-run mustn't
    short-circuit 80% of cells against prior outputs.

    ``reserve_validation`` (auto-validate runs) counts the parity cells
    into the run's total upfront — as ``validate_total`` — so the total
    is complete from the very start instead of growing when the
    validation pass begins. The parity cells themselves are enqueued
    later by the auto-validate poller, which fires once the conversion
    cells alone have landed.

    ``extend`` *appends* the enumerated cells to an existing run instead
    of setting the total from scratch — used by the validation pass.
    With ``consume_reserve`` (a run dispatched with
    ``reserve_validation``) the reserved count is swapped for the actual
    parity cell count, so the total only moves by scope drift between
    the two enumerations. Without it (manual Validate on a finished run,
    or pre-reservation rows) the total grows by the parity cell count
    and the run reopens; an empty cell set is then a no-op.

    Errors during enumeration / enqueue surface as a ``failed``
    audit row on the cell that tripped them — the run still
    finishes when the rest of the jobs complete.
    """
    from ..converter import derived_key_for

    storage = ctx.storage

    synthetic_user = type("AdminAuditUser", (), {"sub": user_sub})()
    # Collect viable cells before enqueueing so the total is
    # exact — set_audit_run_total flips the row to 'finished'
    # if it gets zero, so a typo'd scope shows up immediately in
    # the UI rather than as a perpetually-running ghost.
    try:
        files = await storage.list(scope_obj)
    except Exception:
        logger.exception("audit run %s: scope listing failed", run_id)
        if extend:
            if consume_reserve:
                # Release the reservation so the run can finish instead of
                # hanging forever on cells that will never be enqueued.
                await db_module.consume_audit_run_validation_reserve(pool, run_id, 0)
            return
        await db_module.set_audit_run_total(pool, run_id, 0)
        return
    cells = audit_cells_for_files(files, validate_only)

    if extend:
        if consume_reserve:
            # Swap the upfront reservation for the actual parity cell
            # count (finishes the run right here when that count is 0).
            await db_module.consume_audit_run_validation_reserve(pool, run_id, len(cells))
            if not cells:
                return
        else:
            if not cells:
                return  # nothing to append; leave the finished run untouched
            await db_module.extend_audit_run_total(pool, run_id, len(cells))
    else:
        # Reserve the auto-validation parity cells in the total now — the
        # counter-bump finish check compares against the full total, so
        # the run stays 'running' through the gap between the last
        # conversion cell and the poller dispatching the parity cells.
        reserved = len(audit_cells_for_files(files, True)) if reserve_validation else 0
        await db_module.set_audit_run_total(pool, run_id, len(cells) + reserved, validate_total=reserved)
        if not cells:
            return

    for source_key, target_format in cells:
        # Parity cells produce no derived blob, so there is nothing to cache
        # against — always enqueue, and audit under action="validate".
        if target_format == "parity":
            try:
                job = await ctx.jobs.submit(
                    JobRequest(
                        source_key=source_key,
                        target_format="parity",
                        scope=scope_obj,
                        feature="conversion",
                        target_capability=worker_pool,
                        force_rebuild=force_rebuild,
                        # Parity produces no derived blob — pass an explicit derived_key so
                        # enqueue doesn't route through derived_key_for(), which rejects the
                        # "parity" pseudo-format (not in TARGET_FORMATS). Same pattern the
                        # fea_artefacts flow uses for its manifest key.
                        derived_key=f"_derived/{source_key}.parity",
                    )
                )
            except Exception as exc:
                logger.exception("audit run %s: parity enqueue failed for %s", run_id, source_key)
                await ctx.audit(
                    None,
                    synthetic_user,
                    scope_obj,
                    "validate",
                    key=source_key,
                    target_format="parity",
                    status="error",
                    error=str(exc),
                    audit_run_id=run_id,
                    pool=pool,
                )
                continue
            await ctx.audit(
                None,
                synthetic_user,
                scope_obj,
                "validate",
                key=source_key,
                target_format="parity",
                status="queued",
                job_id=job.job_id,
                audit_run_id=run_id,
                pool=pool,
            )
            continue

        try:
            derived_key = derived_key_for(source_key, target_format)
        except Exception as exc:
            # Should never trigger — targets_for already filtered
            # to viable targets — but record the failure so the
            # grid surfaces it instead of silently shrinking the
            # cell count.
            await ctx.audit(
                None,
                synthetic_user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="error",
                error=str(exc),
                audit_run_id=run_id,
                pool=pool,
            )
            continue

        if force_rebuild:
            cached = False
        else:
            try:
                cached = await storage.exists(scope_obj, derived_key)
            except Exception:
                logger.exception(
                    "audit run %s: storage.exists failed for %s",
                    run_id,
                    derived_key,
                )
                cached = False

        if cached:
            # Cached cell — count as ``done`` without enqueueing.
            # The audit row carries the run id; insert_audit bumps
            # the run's ok counter inline (see db.insert_audit).
            await ctx.audit(
                None,
                synthetic_user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="done",
                audit_run_id=run_id,
                pool=pool,
            )
            continue

        try:
            job = await ctx.jobs.submit(
                JobRequest(
                    source_key=source_key,
                    target_format=target_format,
                    scope=scope_obj,
                    feature="conversion",
                    target_capability=worker_pool,
                    force_rebuild=force_rebuild,
                )
            )
        except Exception as exc:
            logger.exception(
                "audit run %s: enqueue failed for %s -> %s",
                run_id,
                source_key,
                target_format,
            )
            await ctx.audit(
                None,
                synthetic_user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="error",
                error=str(exc),
                audit_run_id=run_id,
                pool=pool,
            )
            continue

        await ctx.audit(
            None,
            synthetic_user,
            scope_obj,
            "convert",
            key=source_key,
            target_format=target_format,
            status="queued",
            job_id=job.job_id,
            audit_run_id=run_id,
            pool=pool,
        )


async def audit_dispatch_wasm(
    ctx: RestContext,
    run_id: str,
    scope_obj: Scope,
    pool,
    validate_only: bool = False,
) -> None:
    """WASM audit run: enumerate cells + set the run total, but do
    NOT enqueue anything. The browser fetches the cell matrix via
    ``GET /admin/audit/runs/{id}/cells`` and runs each cell in
    pyodide, writing its audit row through the ``audit/local``
    endpoints (which carry the ``audit_run_id`` and bump the run
    counters). Mirrors the zero-cell short-circuit so a typo'd scope
    finishes immediately rather than hanging as a ghost run."""
    try:
        cells = await audit_run_list_cells(ctx, scope_obj, validate_only)
    except Exception:
        logger.exception("wasm audit run %s: scope listing failed", run_id)
        await db_module.set_audit_run_total(pool, run_id, 0)
        return
    await db_module.set_audit_run_total(pool, run_id, len(cells))


@router.post("/audit/runs")
async def admin_audit_run_create(
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Kick off a regression sweep across one scope.

    Body: ``{"scope": "shared" | "user:me" | "project:<id>",
             "worker_pool": "audit" | "wasm" | null,
             "note": "...",
             "force_rebuild": false }``.

    ``worker_pool="wasm"`` runs the sweep in the browser (no NATS):
    the run is created and its cell total computed, but nothing is
    enqueued — the SPA drives the cells via the WASM engine. Every
    other pool routes to a NATS worker as before.

    ``force_rebuild`` skips the cached-cell short-circuit so
    every cell actually re-converts. Default false — daily
    regression sweeps want the fast cached path.

    Returns 202 with the new run id; client polls
    ``GET /admin/audit/runs/{id}`` for progress.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    scope_str = (body.get("scope") or "shared").strip()
    worker_pool = body.get("worker_pool") or None
    is_wasm = isinstance(worker_pool, str) and worker_pool.strip().lower() == WASM_POOL
    # The browser engine needs no NATS; only worker-pool runs do.
    if not is_wasm:
        ctx.jobs.require("conversion")
    note = body.get("note") or None
    force_rebuild = bool(body.get("force_rebuild") or False)
    # validate_only: a validation-phase run — enqueue only the per-source
    # cross-format parity cells, skipping the conversion grid. The parity job
    # re-derives from source, so it needs no prior conversion outputs.
    validate_only = bool(body.get("validate_only") or False)
    # auto_validate: once this conversion run finishes, the finished-run
    # poller fires a follow-up validate_only run for the same scope. Only
    # meaningful for a worker-pool conversion run (a validation run / a
    # browser run has nothing to chain).
    auto_validate = bool(body.get("auto_validate") or False) and not validate_only and not is_wasm

    s = parse_scope(scope_str, user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")

    run = await db_module.create_audit_run(
        pool,
        scope=scope_str,
        worker_pool=(WASM_POOL if is_wasm else worker_pool),
        trigger="manual",
        note=note,
        created_by=user.sub,
        force_rebuild=force_rebuild,
        auto_validate=auto_validate,
    )
    if is_wasm:
        # Parity cells are a worker-only concern (no browser parity
        # engine), so a WASM run is always the full conversion grid —
        # validate_only is ignored here and in the cells endpoint so
        # the run total and the browser's cell list always agree.
        background_tasks.add_task(
            audit_dispatch_wasm,
            ctx,
            run["id"],
            s,
            pool,
        )
    else:
        background_tasks.add_task(
            audit_dispatch,
            ctx,
            run["id"],
            s,
            worker_pool,
            user.sub,
            pool,
            force_rebuild,
            validate_only,
            # Count the auto-validation parity cells into the run total
            # from the start (dispatched later by the poller).
            reserve_validation=auto_validate,
        )
    return JSONResponse(run, status_code=202)


@router.get("/audit/active")
async def admin_audit_active(request: Request) -> JSONResponse:
    """Lightweight summary of running audit sweeps. Powers the
    ambient bottom-right badge that links into the Audit Runs
    admin tab; the badge polls this on a 15s cadence, so the
    query needs to stay cheap (one indexed aggregate on the
    ``audit_runs_running_idx`` partial index)."""
    pool = require_pool(request)
    return JSONResponse(await db_module.active_audit_summary(pool))


@router.get("/audit/runs")
async def admin_audit_runs_list(
    request: Request,
    limit: int = 50,
    before_started_at: str | None = None,
) -> JSONResponse:
    pool = require_pool(request)
    runs = await db_module.list_audit_runs(
        pool,
        limit=limit,
        before_started_at=before_started_at,
    )
    next_before = runs[-1]["started_at"] if len(runs) >= max(1, min(limit, 200)) else None
    return JSONResponse({"runs": runs, "next_before_started_at": next_before})


@router.get("/audit/runs/{run_id}")
async def admin_audit_run_get(
    run_id: str,
    request: Request,
) -> JSONResponse:
    pool = require_pool(request)
    run = await db_module.get_audit_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="audit run not found")
    jobs = await db_module.list_audit_run_jobs(pool, run_id)
    return JSONResponse({"run": run, "jobs": jobs})


@router.get("/audit/runs/{run_id}/parity")
async def admin_audit_run_parity(
    run_id: str,
    request: Request,
) -> JSONResponse:
    pool = require_pool(request)
    rows = await db_module.list_audit_run_parity(pool, run_id)
    return JSONResponse({"run_id": run_id, "parity": rows})


@router.post("/audit/runs/{run_id}/cancel")
async def admin_audit_run_cancel(
    run_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Abort a running audit. Flips ``status='aborted'`` and
    cancels every queued / running child cell. No-op (404) if
    the run is already terminal — re-cancelling a finished or
    already-aborted run isn't useful.

    Late worker completions arriving after the abort still bump
    counters (so the per-cell grid keeps growing), but the run
    won't auto-flip back to ``finished``."""
    pool = require_pool(request)
    run = await db_module.abort_audit_run(pool, run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="audit run not found or not in running state",
        )
    # Deep-clean the cancelled cells' still-queued JetStream messages so the
    # worker never pulls a doomed conversion (wasted download/convert/hang).
    purge_ids = run.pop("cancelled_job_ids", []) or []
    if purge_ids and ctx.queue is not None:
        try:
            await ctx.queue.purge_jobs(purge_ids)
        except Exception:
            logger.exception("audit cancel: queue purge failed for run %s", run_id)
    return JSONResponse(run)


@router.post("/audit/runs/{run_id}/re-dispatch")
async def admin_audit_run_re_dispatch(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Re-run a prior audit against the same scope / pool / settings.

    Creates a fresh run that mirrors the prior one's ``scope``,
    ``worker_pool``, ``force_rebuild`` and ``auto_validate`` (linked via
    ``parent_run_id``), then dispatches it the same way the original was
    (NATS workers, or the browser WASM engine for a ``wasm`` pool). The
    cell set is re-enumerated from the scope at dispatch time, so a
    re-dispatch reflects the scope's current files — not a frozen copy."""
    pool = require_pool(request)
    prior = await db_module.get_audit_run(pool, run_id)
    if prior is None:
        raise HTTPException(status_code=404, detail="audit run not found")

    scope_str = prior["scope"]
    worker_pool = prior["worker_pool"]
    is_wasm = isinstance(worker_pool, str) and worker_pool.strip().lower() == WASM_POOL
    if not is_wasm:
        ctx.jobs.require("conversion")

    s = parse_scope(scope_str, user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")

    run = await db_module.create_audit_run(
        pool,
        scope=scope_str,
        worker_pool=worker_pool,
        trigger="re-dispatch",
        note=f"re-run of {run_id[:8]}",
        created_by=user.sub,
        force_rebuild=prior["force_rebuild"],
        auto_validate=prior["auto_validate"],
        parent_run_id=run_id,
    )
    if is_wasm:
        background_tasks.add_task(audit_dispatch_wasm, ctx, run["id"], s, pool)
    else:
        background_tasks.add_task(
            audit_dispatch,
            ctx,
            run["id"],
            s,
            worker_pool,
            user.sub,
            pool,
            prior["force_rebuild"],
            False,
            reserve_validation=prior["auto_validate"],
        )
    return JSONResponse(run, status_code=202)


@router.post("/audit/runs/{run_id}/rerun-cell")
async def admin_audit_run_rerun_cell(
    run_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Re-run one cell of an existing run in place (right-click → Rerun).

    Enqueues a single force-rebuild conversion for ``{key, target}`` against
    the run's own scope/pool, re-points the cell's audit row at the new job
    and reopens the run (``db.reset_audit_cell_for_rerun``). The worker's
    normal completion path updates the row and re-finishes the run, so the
    counters, the sum-of-cells runtime and the grid cell all reflect the
    fresh result — no full re-dispatch of the other 900+ cells."""
    from ..converter import derived_key_for

    body = await request.json()
    key = (body or {}).get("key")
    target = (body or {}).get("target")
    if not key or not target:
        raise HTTPException(status_code=400, detail="key and target are required")
    if target == "parity":
        raise HTTPException(
            status_code=400,
            detail="parity cells have no derived product — use Re-validate on the run instead",
        )

    pool = require_pool(request)
    prior = await db_module.get_audit_run(pool, run_id)
    if prior is None:
        raise HTTPException(status_code=404, detail="audit run not found")
    worker_pool = prior["worker_pool"]
    if isinstance(worker_pool, str) and worker_pool.strip().lower() == WASM_POOL:
        raise HTTPException(status_code=400, detail="cannot re-run a single cell of a wasm run from the server")
    ctx.jobs.require("conversion")

    s = parse_scope(prior["scope"], user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        derived_key_for(key, target)  # validate the target is convertible for this source
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"not a convertible cell: {exc}") from exc

    try:
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=key,
                target_format=target,
                scope=s,
                feature="conversion",
                target_capability=worker_pool,
                force_rebuild=True,
            )
        )
    except Exception as exc:
        logger.exception("rerun-cell enqueue failed for %s -> %s", key, target)
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

    found = await db_module.reset_audit_cell_for_rerun(pool, run_id, key, target, job.job_id)
    if not found:
        raise HTTPException(status_code=404, detail="cell not found in this run")
    run = await db_module.get_audit_run(pool, run_id)
    return JSONResponse(run, status_code=202)


@router.post("/audit/runs/{run_id}/validate")
async def admin_audit_run_validate(
    run_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Append a validation (cross-format parity) pass to a finished run —
    the manual counterpart to the auto-validate toggle. Grows the run's
    total + reopens it, then enqueues the parity cells under the same run
    id. 409 if the run isn't finished or has already been validated (the
    pass runs at most once per run; re-run the audit for a fresh one)."""
    pool = require_pool(request)
    run = await db_module.get_audit_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="audit run not found")

    s = parse_scope(run["scope"], user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")
    if run["worker_pool"] != WASM_POOL:
        ctx.jobs.require("conversion")

    claimed = await db_module.claim_run_for_validation(pool, run_id)
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail="run is not finished, or its validation pass has already been dispatched",
        )
    background_tasks.add_task(
        audit_dispatch,
        ctx,
        run_id,
        s,
        run["worker_pool"],
        user.sub,
        pool,
        False,  # force_rebuild
        True,  # validate_only
        True,  # extend — append into the existing run
    )
    return JSONResponse(claimed, status_code=202)


@router.delete("/audit/runs/{run_id}")
async def admin_audit_run_delete(
    run_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Delete an audit run and its audit_log rows (parity rows cascade).
    Refuses a still-running run — cancel it first — so an in-flight sweep
    can't be deleted out from under its workers."""
    pool = require_pool(request)
    run = await db_module.get_audit_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="audit run not found")
    if run["status"] == "running":
        raise HTTPException(status_code=409, detail="cancel the run before deleting it")
    deleted, queued_job_ids = await db_module.delete_audit_run(pool, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="audit run not found")
    # The rows are gone, so the worker's cancel check can't catch these — purge
    # their still-queued JetStream messages so they aren't pulled + processed.
    if queued_job_ids and ctx.queue is not None:
        try:
            await ctx.queue.purge_jobs(queued_job_ids)
        except Exception:
            logger.exception("audit delete: queue purge failed for run %s", run_id)
    return JSONResponse({"deleted": run_id})


@router.get("/audit/cell-history")
async def admin_audit_cell_history(
    request: Request,
    key: str,
    target: str,
    limit: int = 50,
) -> JSONResponse:
    """Historic results for one ``(source key, target_format)`` cell across
    every run — newest first. Drives the grid's right-click 'show history'
    table so an operator can see how one conversion has trended."""
    pool = require_pool(request)
    rows = await db_module.audit_log_history_for_cell(pool, key, target, limit=limit)
    return JSONResponse({"key": key, "target_format": target, "history": rows})


@router.get("/audit/runs/{run_id}/cells")
async def admin_audit_run_cells(
    run_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Cell matrix for an audit run — drives the browser (WASM)
    sweep executor (section F).

    Returns ``{run_id, scope, cells: [{source_key, target_format,
    done}]}`` where ``done`` flags cells that already have a terminal
    audit row for this run, so a reload resumes (skips finished cells)
    instead of re-running them. Cells come from the same enumeration
    the dispatcher used, so the list matches the run's total.
    """
    pool = require_pool(request)
    # get_audit_run takes a UUID column; a malformed id would raise
    # deep in asyncpg — treat any lookup miss as 404.
    try:
        run = await db_module.get_audit_run(pool, run_id)
    except Exception:
        run = None
    if run is None:
        raise HTTPException(status_code=404, detail="audit run not found")

    scope_str = run["scope"]
    s = parse_scope(scope_str, user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        cells = await audit_run_list_cells(ctx, s, validate_only=False)
    except Exception as exc:
        logger.exception("audit run %s: cell enumeration failed", run_id)
        raise HTTPException(status_code=503, detail=f"scope listing failed: {exc}") from exc

    jobs = await db_module.list_audit_run_jobs(pool, run_id)
    _terminal = {"done", "ok", "error", "skipped", "cancelled"}
    done_set = {(j["key"], j["target_format"]) for j in jobs if j["status"] in _terminal}
    out = [{"source_key": k, "target_format": t, "done": (k, t) in done_set} for (k, t) in cells]
    return JSONResponse({"run_id": run_id, "scope": scope_str, "cells": out})
