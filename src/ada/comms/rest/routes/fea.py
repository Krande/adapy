"""FEA artefact + result routes: browser-baked artefact upload
(``POST /scopes/{scope}/fea/artefacts`` and .../fea/artefact``), and the two
cached-or-enqueue readers (``GET /scopes/{scope}/result-meta``,
``GET /scopes/{scope}/fea/manifest``).

Needs the app's storage + job queue (through :class:`~.deps.RestContext`)
and the worker-advertised-extensions snapshot (``routes/deps.py``'s
``worker_advertised_exts``, which reads ``ctx.worker_registry`` — the same
cached snapshot ``create_app``'s ``_worker_advertised_exts`` alias reads for
the routes still in that closure).

Extracted from ``create_app``; see ``routes/__init__`` for the pattern. Not
contiguous in the old closure (upload/convert routes were interleaved
between them), but none of the four paths here share a literal prefix with
each other or with a parameterised sibling, so registering them together at
one ``include_router`` call is order-safe.
"""

from __future__ import annotations

import io
import json
import pathlib
import posixpath
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import pending_uploads
from ..auth import User
from ..converter import (
    fea_artefact_manifest_key_for,
    fea_artefact_prefix_for,
    fea_manifest_stale_reason,
    fea_meta_key_for,
    is_fea_artefact_source,
    is_fea_result_key,
)
from ..job_transport import JobRequest
from ..scope import Scope
from .deps import (
    DIRECT_UPLOAD_THRESHOLD_BYTES,
    RestContext,
    pending_upload_detail,
    rest_context,
    scope_from_path,
    worker_advertised_exts,
)

router = APIRouter()


@router.post("/scopes/{scope}/fea/artefacts")
async def api_scope_fea_artefacts_upload(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Upload a browser-baked FEA artefact tree (section D).

    The pyodide FEM stack runs ``bake_fea_artefacts_from_source`` in
    the browser and zips the output dir; the body is that raw zip.
    Each entry (``fea.manifest.json``, ``fea.mesh.glb``,
    ``fea.<field>.bin``, ...) is written under the canonical
    ``_derived/<source>.fea/`` prefix with the *same* gzip policy the
    worker uses (``storage.put_bytes`` compresses ``.json``/``.bin``;
    the mesh GLB is stored as-is), so the existing streaming-FEA
    reader consumes it unchanged.

    Query: ``source`` (existing source key in the scope).
    """
    source = (request.query_params.get("source") or "").strip().lstrip("/")
    if not source:
        raise HTTPException(status_code=400, detail="source query param required")
    # Gate on the FEA-artefact source set (.rmed/.sif/...), the same
    # predicate the GET /fea/manifest worker route uses — not the
    # general convert-source check, since these sources have no
    # convert-registry target and the browser path runs worker-free.
    if not is_fea_artefact_source(source):
        raise HTTPException(status_code=415, detail=f"not a FEA artefact source: {source}")
    try:
        source_exists = await ctx.storage.exists(scope_obj, source)
    except Exception:
        source_exists = False
    if not source_exists:
        raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")

    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            announced = int(cl)
        except ValueError:
            announced = -1
        if announced > DIRECT_UPLOAD_THRESHOLD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"fea artefact upload exceeds {DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
            )

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"body is not a valid zip: {exc}") from exc

    # Each entry must be a bare ``fea.*`` filename — no subdirs, no
    # path traversal — so a crafted zip can't escape the per-source
    # prefix and write arbitrary keys.
    entries = [n for n in zf.namelist() if not n.endswith("/")]
    for n in entries:
        base = posixpath.basename(n)
        if base != n or not base or base.startswith(".") or not base.startswith("fea."):
            raise HTTPException(status_code=400, detail=f"illegal artefact entry: {n!r}")
    names = {posixpath.basename(n) for n in entries}
    if "fea.manifest.json" not in names:
        raise HTTPException(status_code=400, detail="zip missing fea.manifest.json")

    prefix = fea_artefact_prefix_for(source)
    written = 0
    try:
        for n in entries:
            base = posixpath.basename(n)
            payload = zf.read(n)
            # Mirror the worker's compression policy exactly: gzip only
            # the manifest JSON; store .bin blobs (and the mesh GLB)
            # identity so the viewer can HTTP-Range a single field step.
            content_encoding = "gzip" if base.lower().endswith(".json") else None
            await ctx.storage.put_bytes(
                scope_obj,
                prefix + base,
                payload,
                content_encoding=content_encoding,
            )
            written += 1
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("fea artefact upload failed for %s", source)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(
        {"manifest_key": fea_artefact_manifest_key_for(source), "count": written},
        status_code=201,
    )


@router.post("/scopes/{scope}/fea/artefact")
async def api_scope_fea_artefact_upload_one(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Upload a *single* browser-baked FEA artefact file (section D).

    The per-file counterpart of ``POST /fea/artefacts`` (zip): the
    in-browser bake ships each ``fea.*`` file as it lands instead of
    accumulating the whole tree and zipping it, so neither the browser
    (output tree + zip) nor this endpoint (whole zip in memory, capped
    by the direct-upload threshold) has to hold the entire artefact set
    at once. Same prefix, same per-extension gzip policy as the zip
    route, so the streaming-FEA reader consumes the result unchanged.

    Query: ``source`` (existing source key) + ``name`` (the bare
    ``fea.*`` filename). Body: the raw file bytes.
    """
    source = (request.query_params.get("source") or "").strip().lstrip("/")
    if not source:
        raise HTTPException(status_code=400, detail="source query param required")
    if not is_fea_artefact_source(source):
        raise HTTPException(status_code=415, detail=f"not a FEA artefact source: {source}")

    name = (request.query_params.get("name") or "").strip()
    base = posixpath.basename(name)
    # Same guard as the zip route: a bare ``fea.*`` filename only — no
    # subdirs, no traversal, so a request can't escape the per-source
    # prefix and write an arbitrary key.
    if base != name or not base or base.startswith(".") or not base.startswith("fea."):
        raise HTTPException(status_code=400, detail=f"illegal artefact name: {name!r}")

    try:
        source_exists = await ctx.storage.exists(scope_obj, source)
    except Exception:
        source_exists = False
    if not source_exists:
        raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")

    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            announced = int(cl)
        except ValueError:
            announced = -1
        if announced > DIRECT_UPLOAD_THRESHOLD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"fea artefact file exceeds {DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
            )

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")

    prefix = fea_artefact_prefix_for(source)
    # gzip only the manifest JSON; .bin blobs stay identity so the
    # viewer can HTTP-Range a single field step (see the blobs route).
    content_encoding = "gzip" if base.lower().endswith(".json") else None
    try:
        await ctx.storage.put_bytes(scope_obj, prefix + base, data, content_encoding=content_encoding)
    except Exception as exc:
        logger.exception("fea artefact file upload failed for %s/%s", source, base)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({"key": prefix + base, "name": base}, status_code=201)


@router.get("/scopes/{scope}/result-meta")
async def api_scope_result_meta(
    request: Request,
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Return the (steps, fields) inventory for a FEA result file.

    Cache hit: 200 with the parsed JSON.
    Cache miss: 202 with ``{"job_id": ..., "status": "queued"}``;
    frontend polls ``/api/convert/{job_id}`` until done, then
    re-fetches this endpoint to get the body.

    SIF parsing on multi-hundred-MB decks takes 30 s+ and the
    slim API container doesn't carry ada.fem at all — both
    reasons push the work into the worker queue. Same shape as
    the streaming-viewer manifest endpoint.

    404 if the source is missing; 415 if the source isn't a FEA
    result file.
    """
    storage = ctx.storage

    source_key = (key or "").strip().lstrip("/")
    if not source_key:
        raise HTTPException(status_code=400, detail="key required")
    if not is_fea_result_key(source_key):
        raise HTTPException(
            status_code=415,
            detail=f"result-meta only applies to FEA result files; got {source_key!r}",
        )
    if not await storage.exists(scope_obj, source_key):
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")

    meta_key = fea_meta_key_for(source_key)
    try:
        cached = await storage.get_bytes(scope_obj, meta_key)
    except FileNotFoundError:
        cached = None
    except Exception:
        # Treat any cache-read hiccup as a miss; rebuild is the
        # safer path than handing the user a 500 because of a stale
        # half-written meta blob.
        logger.exception("result-meta: cache read failed for %s", meta_key)
        cached = None
    if cached:
        try:
            return JSONResponse(json.loads(cached.decode("utf-8")))
        except Exception:
            logger.exception("result-meta: cache parse failed for %s; rebuilding", meta_key)

    # Cache miss — enqueue a worker job and return 202. Frontend
    # polls /convert/{job_id} until done, then re-fetches this
    # endpoint.
    ctx.jobs.require("result_meta")
    try:
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=source_key,
                target_format="fea_meta",
                scope=scope_obj,
                feature="result_meta",
                derived_key=meta_key,
            )
        )
    except Exception as exc:
        logger.exception("result-meta: enqueue failed for %s", source_key)
        await ctx.audit(
            request,
            user,
            scope_obj,
            "fea_meta",
            key=source_key,
            status="error",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

    await ctx.audit(
        request,
        user,
        scope_obj,
        "fea_meta",
        key=source_key,
        status="queued",
        job_id=job.job_id,
    )
    return JSONResponse(
        {
            "job_id": job.job_id,
            "source_key": source_key,
            "meta_key": meta_key,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
        },
        status_code=202,
    )


@router.get("/scopes/{scope}/fea/manifest")
async def api_scope_fea_manifest(
    request: Request,
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Return the streaming-viewer manifest for a FEA source.

    Cache hit: 200 with the parsed manifest JSON.
    Cache miss: 202 with ``{"job_id": ..., "status": "queued"}``;
    frontend polls ``/api/convert/{job_id}`` until done, then
    re-fetches this endpoint.

    The bake itself runs in the worker container (which has the
    full ada.fem stack); the API container is intentionally slim
    and can't import ada.fem at all.
    """
    storage = ctx.storage
    # Still the queue, not the transport: the advertised-extension check below
    # reads the worker registry snapshot, which is not a job.
    queue = ctx.queue

    source_key = (key or "").strip().lstrip("/")
    if not source_key:
        raise HTTPException(status_code=400, detail="key required")
    pending = pending_uploads.get(scope_obj, source_key)
    if pending is not None:
        raise HTTPException(status_code=409, detail=pending_upload_detail(source_key, pending))
    if not is_fea_artefact_source(source_key):
        # adapy ships built-in stream readers for .rmed and .sif;
        # capability workers register additional ones at startup
        # (e.g. abaqus → .odb / .sqlite) and publish the set into
        # the worker registry. Honour those here so a worker plug-in
        # doesn't have to also patch the API gate. The worker-side
        # bake still re-validates via its own ``make_stream_reader``
        # registry, so an extension the API accepted but no worker
        # actually handles surfaces as a clear bake error rather
        # than getting silently dropped.
        ext = pathlib.PurePosixPath(source_key).suffix.lower()
        if ext not in await worker_advertised_exts(queue, ctx.worker_registry):
            raise HTTPException(
                status_code=415,
                detail=(
                    f"streaming FEA viewer only supports .rmed / .sif / .sin "
                    f"or worker-advertised stream readers; got {source_key!r}"
                ),
            )
    if not await storage.exists(scope_obj, source_key):
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")

    manifest_key = fea_artefact_manifest_key_for(source_key)
    try:
        cached = await storage.get_bytes(scope_obj, manifest_key)
    except FileNotFoundError:
        cached = None
    except Exception:
        logger.exception("fea-manifest: cache read failed for %s", manifest_key)
        cached = None
    force_rebake = False
    if cached:
        manifest: dict | None = None
        try:
            manifest = json.loads(cached.decode("utf-8"))
        except Exception:
            logger.exception("fea-manifest: cache parse failed for %s; rebuilding", manifest_key)
        stale: str | None = None
        if manifest is not None:
            # Freshness, not just existence: a bake made before the
            # current bake output (bake_version) or before the source's
            # last re-upload (a deck is routinely re-solved in place under
            # the same name) must be rebuilt, not served forever.
            try:
                src_head = await storage.head(scope_obj, source_key)
                man_head = await storage.head(scope_obj, manifest_key)
            except Exception:
                logger.exception("fea-manifest: head failed for %s", source_key)
                src_head = man_head = None
            stale = fea_manifest_stale_reason(manifest, src_head, man_head)
            if stale is None:
                return JSONResponse(manifest)
        # A cached entry exists but is stale or unusable. The worker's
        # already-cached short-circuit keys on the manifest's existence,
        # so a plain enqueue would no-op straight back here; force the
        # rebake through it.
        force_rebake = True
        if not ctx.jobs.supports("bake") and manifest is not None:
            # No worker to rebake with. A stale manifest still describes
            # real (older) results; serving it beats a 503 — log so the
            # operator sees why the deck lacks the newer bake output.
            logger.warning(
                "fea-manifest: serving stale bake for %s (%s) — bake queue disabled",
                source_key,
                stale,
            )
            return JSONResponse(manifest)
        logger.info(
            "fea-manifest: cached bake for %s is stale (%s) — re-baking",
            source_key,
            stale or "unparsable manifest",
        )

    # Cache miss (or stale hit) — enqueue a worker bake and return 202.
    # Frontend polls /convert/{job_id} via the existing route and
    # re-fetches this endpoint when the job hits status=done.
    ctx.jobs.require("bake")
    try:
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=source_key,
                target_format="fea_artefacts",
                scope=scope_obj,
                feature="bake",
                # derived_key is the manifest path so the worker's
                # "already cached?" short-circuit lines up with this
                # endpoint's cache check.
                derived_key=manifest_key,
                # A stale/unusable cached manifest EXISTS, so that
                # short-circuit must be bypassed for the rebake to happen.
                force_rebuild=force_rebake,
            )
        )
    except Exception as exc:
        logger.exception("fea-manifest: enqueue failed for %s", source_key)
        await ctx.audit(
            request,
            user,
            scope_obj,
            "fea_bake",
            key=source_key,
            status="error",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

    await ctx.audit(
        request,
        user,
        scope_obj,
        "fea_bake",
        key=source_key,
        status="queued",
        job_id=job.job_id,
    )
    return JSONResponse(
        {
            "job_id": job.job_id,
            "source_key": source_key,
            "manifest_key": manifest_key,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
        },
        status_code=202,
    )
