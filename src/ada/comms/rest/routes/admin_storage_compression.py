"""Admin storage-compression routes: ``POST
/api/admin/storage/{scope}/compress-uncompressed`` (kick off a sweep) and
``GET /api/admin/storage/compression-status`` (poll every sweep's state).

Per-scope sweep state is kept two ways: durably in NATS KV
(``queue.set/get_compress_sweep_state``, so a new session can observe a
sweep started elsewhere) and in :attr:`~.deps.RestContext.compression_state`
— an in-process cache so the BackgroundTask driving one sweep doesn't have
to round-trip KV between its own mutations.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import pathlib
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse

from ada.config import logger

from ..scope import Scope
from .deps import RestContext, content_encoding_for, rest_context, scope_from_path

router = APIRouter()


async def _save_compression_state(ctx: RestContext, scope_label: str) -> None:
    state = ctx.compression_state.get(scope_label)
    if state is None:
        return
    try:
        await ctx.queue.set_compress_sweep_state(scope_label, state)
    except Exception:
        logger.exception("compression sweep: KV write failed (non-fatal)")


async def _compression_sweep(ctx: RestContext, scope_obj: Scope, scope_label: str) -> None:
    import gzip as _gzip
    import shutil as _shutil
    import tempfile as _tempfile

    from ..converter import is_derived_key

    state = ctx.compression_state[scope_label]
    try:
        entries = await ctx.storage.list(scope_obj)
    except Exception as exc:
        state["error"] = f"list failed: {exc}"
        state["completed_at"] = time.time()
        state["last_update"] = time.time()
        await _save_compression_state(ctx, scope_label)
        return
    candidates = [e for e in entries if content_encoding_for(e.key) == "gzip" and not is_derived_key(e.key)]
    state["total"] = len(candidates)
    state["last_update"] = time.time()
    await _save_compression_state(ctx, scope_label)
    for entry in candidates:
        if state.get("cancelled"):
            break
        state["current_key"] = entry.key
        state["last_update"] = time.time()
        await _save_compression_state(ctx, scope_label)
        try:
            # Stream the object to disk so the viewer pod never has
            # to hold the whole payload in RAM — a 900 MB SIF with
            # the default 1 GiB memory limit OOM-kills the process
            # if we try the load-into-bytes path.
            with _tempfile.TemporaryDirectory() as tmpdir:
                raw_path = pathlib.Path(tmpdir) / "raw"
                gz_path = pathlib.Path(tmpdir) / "gz"
                await ctx.storage.stream_to_path_raw(
                    scope_obj,
                    entry.key,
                    raw_path,
                )
                with open(raw_path, "rb") as fh:
                    magic = fh.read(2)
                if magic == b"\x1f\x8b":
                    state["already_gzipped"] += 1
                    continue
                with open(raw_path, "rb") as fin, _gzip.open(gz_path, "wb", compresslevel=6) as fout:
                    _shutil.copyfileobj(fin, fout, length=1 << 20)
                # The gzipped result is typically ~5–10× smaller
                # than the raw payload — safely fits in memory for
                # the put_bytes call. If we ever hit a case where
                # even the compressed size exceeds the pod's RAM
                # limit, switch to a streaming put.
                gzipped = gz_path.read_bytes()
            await ctx.storage.put_bytes(
                scope_obj,
                entry.key,
                gzipped,
                content_encoding="gzip",
                pre_compressed=True,
            )
            state["compressed"] += 1
            state["bytes_before"] += entry.size or 0
            state["bytes_after"] += len(gzipped)
        except Exception as exc:
            logger.exception("compress sweep failed on %s/%s", scope_label, entry.key)
            state["errors"].append({"key": entry.key, "error": str(exc)})
        finally:
            state["processed"] += 1
            state["last_update"] = time.time()
            await _save_compression_state(ctx, scope_label)
    state["completed_at"] = time.time()
    state["current_key"] = None
    state["last_update"] = time.time()
    await _save_compression_state(ctx, scope_label)


@router.post("/storage/{scope}/compress-uncompressed")
async def admin_compress_uncompressed(
    scope: str,
    background_tasks: BackgroundTasks,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Sweep the scope for objects whose extension is in the
    gzip-compressible list but whose stored bytes aren't gzipped,
    and rewrite each as ``Content-Encoding: gzip``.

    Runs in a background task so the request returns immediately;
    progress is reported via the companion
    ``GET /storage/compression-status`` endpoint. Re-triggering
    while a sweep is running for the same scope returns 409.
    """
    scope_label = scope
    current = await ctx.queue.get_compress_sweep_state(scope_label)
    if current and current.get("completed_at") is None:
        # Treat as orphaned if last_update is older than 90 s — the
        # most likely cause is a viewer pod restart that lost the
        # BackgroundTask. Override the stale state with a fresh
        # one rather than 409-blocking forever.
        last_update = current.get("last_update") or current.get("started_at") or 0
        if time.time() - last_update < 90:
            raise HTTPException(
                status_code=409,
                detail=f"sweep already running for {scope_label}",
            )

    ctx.compression_state[scope_label] = {
        "started_at": time.time(),
        "completed_at": None,
        "last_update": time.time(),
        "total": 0,
        "processed": 0,
        "compressed": 0,
        "already_gzipped": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "errors": [],
        "error": None,
        "cancelled": False,
        "current_key": None,
    }
    await _save_compression_state(ctx, scope_label)
    background_tasks.add_task(_compression_sweep, ctx, scope_obj, scope_label)
    return JSONResponse(
        {"scope": scope_label, "status": "started"},
        status_code=202,
    )


@router.get("/storage/compression-status")
async def admin_compression_status(ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Snapshot of every recorded compression sweep keyed by scope.
    State lives in NATS KV so a new session sees in-flight sweeps
    that were started elsewhere; an entry with ``completed_at: null``
    and ``last_update`` older than 90 s indicates the viewer pod
    restarted mid-sweep (the work was lost — re-trigger to resume)."""
    try:
        scopes = await ctx.queue.list_compress_sweep_states()
    except Exception:
        logger.exception("compression status: KV read failed")
        scopes = {}
    # Layer in any in-process state that hasn't been flushed to KV
    # yet (e.g. between mutations within the BackgroundTask).
    for label, state in ctx.compression_state.items():
        scopes[label] = state
    # Tag each entry with an ``orphaned`` flag for the frontend's
    # toast logic — saves the client recomputing the staleness.
    now = time.time()
    for state in scopes.values():
        if state.get("completed_at") is None:
            last = state.get("last_update") or state.get("started_at") or 0
            state["orphaned"] = (now - last) > 90
        else:
            state["orphaned"] = False
    return JSONResponse({"scopes": scopes})
