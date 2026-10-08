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
    fea_case_prefix_for,
    fea_case_stale_reason,
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


# ── Lazy load combinations ──────────────────────────────────────────────────
#
# A base bake (bake_version >= 4) of a deck that leaves its load combinations as
# recipes holds the stored cases only and lists the rest as
# ``combination_steps``. These two routes are how a viewer gets one: the
# materialised case (``fea_case``), or one field's envelope over all of them
# (``fea_envelope``). Same contract as the manifest: 200 with the cached JSON,
# else 202 with a job to poll on /convert/{job_id}, then ask again.


async def _fresh_base_manifest(ctx: RestContext, scope_obj: Scope, source_key: str) -> tuple[dict, dict | None]:
    """The base manifest of ``source_key`` and its head, or an HTTPException.

    409 when there is no fresh base bake to combine from: the viewer then asks
    for the manifest, which (re)bakes it."""
    storage = ctx.storage
    pending = pending_uploads.get(scope_obj, source_key)
    if pending is not None:
        raise HTTPException(status_code=409, detail=pending_upload_detail(source_key, pending))
    if not is_fea_artefact_source(source_key):
        raise HTTPException(status_code=415, detail=f"not a FEA artefact source: {source_key!r}")
    if not await storage.exists(scope_obj, source_key):
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    manifest_key = fea_artefact_manifest_key_for(source_key)
    try:
        manifest = json.loads((await storage.get_bytes(scope_obj, manifest_key)).decode("utf-8"))
    except FileNotFoundError:
        raise HTTPException(status_code=409, detail="no base bake yet; request the manifest first") from None
    except Exception as exc:
        logger.exception("fea-case: base manifest unreadable for %s", source_key)
        raise HTTPException(status_code=409, detail="base bake unreadable; request the manifest again") from exc
    try:
        src_head = await storage.head(scope_obj, source_key)
        man_head = await storage.head(scope_obj, manifest_key)
    except Exception:
        logger.exception("fea-case: head failed for %s", source_key)
        src_head = man_head = None
    stale = fea_manifest_stale_reason(manifest, src_head, man_head)
    if stale is not None:
        raise HTTPException(status_code=409, detail=f"base bake is stale ({stale}); request the manifest again")
    return manifest, {"source": src_head, "manifest": man_head}


async def _cached_json(ctx: RestContext, scope_obj: Scope, key: str) -> tuple[dict | None, dict | None]:
    try:
        raw = await ctx.storage.get_bytes(scope_obj, key)
    except FileNotFoundError:
        return None, None
    except Exception:
        logger.exception("fea-case: cache read failed for %s", key)
        return None, None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception:
        logger.exception("fea-case: cache parse failed for %s; rebuilding", key)
        return None, None
    try:
        head = await ctx.storage.head(scope_obj, key)
    except Exception:
        head = None
    return doc, head


async def _submit_lazy_job(
    request: Request,
    ctx: RestContext,
    user: User,
    scope_obj: Scope,
    *,
    source_key: str,
    target_format: str,
    derived_key: str,
    force: bool,
    step: int | None = None,
    field_name: str | None = None,
) -> dict:
    ctx.jobs.require("bake")
    try:
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=source_key,
                target_format=target_format,
                scope=scope_obj,
                feature="bake",
                derived_key=derived_key,
                step=step,
                field_name=field_name,
                force_rebuild=force,
            )
        )
    except Exception as exc:
        logger.exception("%s: enqueue failed for %s", target_format, source_key)
        await ctx.audit(request, user, scope_obj, target_format, key=source_key, status="error", error=str(exc))
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc
    await ctx.audit(request, user, scope_obj, target_format, key=source_key, status="queued", job_id=job.job_id)
    return {"job_id": job.job_id, "status": job.status, "progress": job.progress, "stage": job.stage}


@router.get("/scopes/{scope}/fea/case")
async def api_scope_fea_case(
    request: Request,
    key: str,
    case: int,
    field: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One lazy load combination of a base bake, materialised.

    200: the case overlay (``fea.case.json``) plus ``prefix``, the storage
    prefix its single-step blobs live under (``_derived/<src>.fea/cases/<n>-<recipeHash8>/``;
    the overlay's blob urls are relative to it). 202: ``{job_id, source_key,
    case, case_key, status, progress, stage}`` -- poll /convert/{job_id}, then ask
    again. 404: ``case`` is not a lazy combination of this bake. 409: there is no
    fresh base bake to combine from (ask for the manifest).

    ``field`` is accepted and checked against the bake, but a job materialises
    every field of the case: one superposition pass costs little more than one
    field's, and the next field asked for is then already there.
    """
    source_key = (key or "").strip().lstrip("/")
    if not source_key:
        raise HTTPException(status_code=400, detail="key required")
    manifest, heads = await _fresh_base_manifest(ctx, scope_obj, source_key)
    entry = next((e for e in manifest.get("combination_steps") or [] if int(e.get("n", -1)) == int(case)), None)
    if entry is None or not entry.get("recipe_hash"):
        raise HTTPException(status_code=404, detail=f"case {case} is not a lazy combination of {source_key}")
    if field is not None and field not in {f.get("name_canonical") for f in manifest.get("fields") or []}:
        raise HTTPException(status_code=404, detail=f"no field {field!r} in {source_key}")

    prefix = fea_case_prefix_for(source_key, f"{int(entry['n'])}-{str(entry['recipe_hash'])[:8]}")
    case_key = prefix + "fea.case.json"
    cached, case_head = await _cached_json(ctx, scope_obj, case_key)
    force = False
    if cached is not None:
        stale = fea_case_stale_reason(cached, heads["source"], case_head, heads["manifest"])
        if stale is None and (cached.get("case") or {}).get("recipe_hash") == entry["recipe_hash"]:
            return JSONResponse({**cached, "prefix": prefix})
        logger.info("fea-case: cached case %s of %s is stale (%s) -- rebuilding", case, source_key, stale)
        force = True
    job = await _submit_lazy_job(
        request,
        ctx,
        user,
        scope_obj,
        source_key=source_key,
        target_format="fea_case",
        derived_key=case_key,
        force=force,
        step=int(entry["n"]),
    )
    return JSONResponse(
        {**job, "source_key": source_key, "case": int(entry["n"]), "case_key": case_key}, status_code=202
    )


def _envelope_dir(field: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in field)


@router.get("/scopes/{scope}/fea/envelope")
async def api_scope_fea_envelope(
    request: Request,
    key: str,
    field: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One field's max / min over the lazy load combinations of a base bake.

    200: ``fea.envelope.json`` (``field``, ``components``, ``cases`` covered,
    ``skipped`` -- the combinations that need the raw records and are not in it
    --, ``scalar_range``, per-blob max/min blob + governing indices) plus
    ``prefix``. 202: a job, as for a case. 404: no such field or no lazy
    combinations. 409: no fresh base bake.
    """
    source_key = (key or "").strip().lstrip("/")
    if not source_key:
        raise HTTPException(status_code=400, detail="key required")
    manifest, heads = await _fresh_base_manifest(ctx, scope_obj, source_key)
    if not manifest.get("combination_steps"):
        raise HTTPException(status_code=404, detail=f"{source_key} has no lazy combinations")
    if field not in {f.get("name_canonical") for f in manifest.get("fields") or []}:
        raise HTTPException(status_code=404, detail=f"no field {field!r} in {source_key}")
    prefix = f"{fea_artefact_prefix_for(source_key)}envelopes/{_envelope_dir(field)}/"
    env_key = prefix + "fea.envelope.json"
    cached, env_head = await _cached_json(ctx, scope_obj, env_key)
    force = False
    if cached is not None:
        stale = fea_case_stale_reason(cached, heads["source"], env_head, heads["manifest"])
        if stale is None:
            return JSONResponse({**cached, "prefix": prefix})
        force = True
    job = await _submit_lazy_job(
        request,
        ctx,
        user,
        scope_obj,
        source_key=source_key,
        target_format="fea_envelope",
        derived_key=env_key,
        force=force,
        field_name=field,
    )
    return JSONResponse({**job, "source_key": source_key, "field": field, "envelope_key": env_key}, status_code=202)
