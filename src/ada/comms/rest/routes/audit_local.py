"""Client-side audit rows: the in-browser (WASM) conversion lifecycle
(``POST /scopes/{scope}/audit/local`` and ``.../audit/local/{job_id}``) and the
viewer's model-load / render-window samples (``POST /scopes/{scope}/audit/view``).

Needs the app's storage (failure capture) through :class:`~.deps.RestContext`.
Extracted from ``create_app``; see ``routes/__init__`` for the pattern. The three
paths share the ``audit/`` segment but are all fixed after it apart from the
``{job_id}`` leaf, so registering them together is order-safe.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from .. import failure_capture
from ..auth import User
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()

# ── Browser-driven (WASM) conversion audit ────────────────────────
#
# The in-browser pyodide engine runs conversions with no NATS job, so
# it can't ride the worker's queued→running→done audit lifecycle.
# These two routes give it parity: open a 'running' row (returns a
# ``wasm-<uuid>`` job_id), then patch it terminal with metrics. The
# ``wasm-`` job_id prefix + ``wasm:`` image tag let the audit panel
# tell in-browser rows from worker rows; the ``wasm-`` guard on the
# update stops a browser from mutating a worker's row.
_WASM_JOB_PREFIX = "wasm-"


@router.post("/scopes/{scope}/audit/local")
async def api_scope_audit_local_create(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Open an audit row for an in-browser (WASM) conversion.

    Body (JSON): ``{key, target_format, audit_run_id?, image_tag?}``.
    ``audit_run_id`` attaches the row to an admin audit-run sweep
    (section F) and is admin-only. Returns ``{job_id}``.
    """
    import uuid

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="audit requires a database")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    key = (str(body.get("key") or "")).strip().lstrip("/") or None
    target_format = (str(body.get("target_format") or "")).strip().lstrip(".").lower() or None
    image_tag = body.get("image_tag")
    worker_image_tag = image_tag if (isinstance(image_tag, str) and image_tag.startswith("wasm:")) else "wasm:unknown"
    audit_run_id = body.get("audit_run_id")
    if audit_run_id is not None:
        audit_run_id = str(audit_run_id).strip() or None
    if audit_run_id is not None:
        if not getattr(user, "is_admin", False):
            raise HTTPException(status_code=403, detail="audit_run_id requires admin")
        # audit_run_id is a UUID column; a malformed value would raise
        # a DataError deep in asyncpg. Treat any lookup failure as
        # "no such run" so a bad id is a clean 404, not a 500.
        try:
            run = await db_module.get_audit_run(pool, audit_run_id)
        except Exception:
            run = None
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")

    job_id = _WASM_JOB_PREFIX + uuid.uuid4().hex
    try:
        await db_module.insert_audit(
            pool,
            user_sub=user.sub,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            action="convert",
            key=key,
            target_format=target_format,
            status="running",
            job_id=job_id,
            audit_run_id=audit_run_id,
            worker_image_tag=worker_image_tag,
        )
    except Exception as exc:
        logger.exception("audit/local create failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse({"job_id": job_id}, status_code=201)


@router.post("/scopes/{scope}/audit/local/{job_id}")
async def api_scope_audit_local_update(
    job_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Patch a WASM conversion's audit row to its terminal outcome.

    Body (JSON): ``{status, error?, traceback?, duration_ms?,
    read_bytes?, write_bytes?, peak_rss_kb?, metrics_samples?}``.
    ``status`` ∈ {done, ok, error, skipped, cancelled}. Guarded so a
    browser can only patch its own ``wasm-`` rows.
    """
    # Reject a malformed id before touching infra so the guard is
    # independent of DB availability.
    if not job_id.startswith(_WASM_JOB_PREFIX):
        raise HTTPException(status_code=400, detail="job_id must be a wasm- local id")
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="audit requires a database")
    owner = await db_module.get_audit_owner_by_job(pool, job_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="audit row not found")
    # Ownership: the row's creator, or an admin (audit-run sweeps).
    if owner["user_sub"] != user.sub and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    status = (str(body.get("status") or "")).strip().lower()
    _allowed = {"done", "ok", "error", "skipped", "cancelled"}
    if status not in _allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_allowed)}")

    def _int_or_none(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # In-browser conversions patch their row here rather than through _audit
    # or the worker's _audit_done, so failure capture needs its own call: this
    # is the whole WASM pipeline, and without it every browser-side failure
    # would still lose its source the moment the user deletes the file.
    failure_key = None
    if failure_capture.is_failure(status):
        failure_key = await failure_capture.capture_for_job(ctx.storage, pool, db_module, job_id)
    try:
        await db_module.update_audit_by_job(
            pool,
            job_id=job_id,
            status=status,
            error=(str(body["error"]) if body.get("error") is not None else None),
            duration_ms=_int_or_none(body.get("duration_ms")),
            traceback=(str(body["traceback"]) if body.get("traceback") is not None else None),
            peak_rss_kb=_int_or_none(body.get("peak_rss_kb")),
            read_bytes=_int_or_none(body.get("read_bytes")),
            write_bytes=_int_or_none(body.get("write_bytes")),
            failure_key=failure_key,
        )
    except Exception as exc:
        logger.exception("audit/local update failed for %s", job_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Optional heartbeat samples — best-effort, must never fail the call.
    samples = body.get("metrics_samples")
    if isinstance(samples, list):
        for s in samples:
            if not isinstance(s, dict):
                continue
            try:
                await db_module.append_metrics_sample_by_job(pool, job_id=job_id, sample=s)
            except Exception:
                logger.exception("audit/local: metrics sample append failed for %s", job_id)
    return JSONResponse({"ok": True})


@router.post("/scopes/{scope}/audit/view")
async def api_scope_audit_view(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Record one browser model-load (``action = 'view'``) or a
    steady-state render window (``action = 'render'`` — selected by
    ``client_metrics.kind == 'render'``).

    Posted by the viewer's opt-in load/render instrumentation
    (admin-only toggle in the Performance options) once a GLB has
    finished loading into the scene, or per render-sample window.
    Body (JSON):

      ``{key, status, duration_ms?, read_bytes?, write_bytes?,
         peak_rss_kb?, client_metrics?}``

    ``client_metrics`` is the per-phase IO/network/CPU/GPU breakdown
    (see migration 017). Best-effort: a metrics post must never break
    the user's session, so a DB hiccup is logged and swallowed with a
    200. Not admin-gated — any user with scope access may record their
    own loads (the row is owned by ``user.sub``); the collection is
    gated client-side so it only fires for admins.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        # No DB on this deployment — quietly accept so the client
        # doesn't error-toast on a metrics post.
        return JSONResponse({"ok": False, "reason": "no-db"})
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    key = (str(body.get("key") or "")).strip().lstrip("/") or None
    status = (str(body.get("status") or "ok")).strip().lower()
    if status not in {"ok", "done", "error", "failed"}:
        status = "ok"

    def _int_or_none(v):
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    cm = body.get("client_metrics")
    if not isinstance(cm, dict):
        cm = None
    else:
        # Defensive cap: keep the JSONB small and bounded regardless
        # of what a client sends. Scalars pass through; the one allowed
        # nested value is ``profile_frames`` (the JS Self-Profiling top-N
        # self-time table) — bounded to a sane length with scalar-only
        # fields. Any other nested/oversized value is dropped.
        cleaned: dict = {}
        for k, v in list(cm.items())[:64]:
            if not isinstance(k, str):
                continue
            if isinstance(v, (int, float, bool)) or v is None:
                cleaned[k[:64]] = v
            elif isinstance(v, str):
                cleaned[k[:64]] = v[:512]
            elif k == "profile_frames" and isinstance(v, list):
                frames: list = []
                for f in v[:80]:
                    if not isinstance(f, dict):
                        continue
                    fn = f.get("fn")
                    if not isinstance(fn, str):
                        continue
                    frame: dict = {"fn": fn[:200]}
                    for fk in ("self_ms", "total_ms"):
                        fv = f.get(fk)
                        if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                            frame[fk] = fv
                    frames.append(frame)
                if frames:
                    cleaned["profile_frames"] = frames
        cm = cleaned or None

    # Steady-state render-window rows post to the same endpoint but
    # carry ``client_metrics.kind == "render"`` so they land under the
    # 'render' action (the load-time rows use 'view').
    action = "render" if (cm and cm.get("kind") == "render") else "view"

    # A load/render failure is reproducible only from the exact blob that
    # failed: re-deriving it can yield different bytes, so unlike a
    # conversion this captures the derived artifact itself.
    failure_key = None
    if failure_capture.is_failure(status):
        failure_key = await failure_capture.capture(
            ctx.storage, pool, db_module, scope=scope_obj, key=key, action=action
        )

    try:
        await db_module.insert_audit(
            pool,
            user_sub=user.sub,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            action=action,
            key=key,
            target_format=None,
            status=status,
            error=(str(body["error"])[:2000] if body.get("error") is not None else None),
            # Client-side load failures (e.g. a malformed GLB buffer) carry no
            # Python traceback; stash the browser error's JS stack here so the
            # audit Error panel has something actionable to show.
            traceback=(str(body["traceback"])[:8000] if body.get("traceback") is not None else None),
            duration_ms=_int_or_none(body.get("duration_ms")),
            read_bytes=_int_or_none(body.get("read_bytes")),
            write_bytes=_int_or_none(body.get("write_bytes")),
            peak_rss_kb=_int_or_none(body.get("peak_rss_kb")),
            client_metrics=cm,
            failure_key=failure_key,
        )
    except Exception:
        logger.exception("audit/view record failed")
        return JSONResponse({"ok": False, "reason": "insert-failed"})
    return JSONResponse({"ok": True}, status_code=201)
