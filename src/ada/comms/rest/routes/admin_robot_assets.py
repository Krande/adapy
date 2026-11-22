"""Admin -> Robot bundles: the robot assets of the provider store, listed, exported and imported as
one ``.adabots`` file (``ada/comms/rest/robot_bundles.py``; ``docs/architecture/robot_asset_bundles.md``).

``GET  /api/admin/robot-assets``                       every robot id with its stored versions, newest
                                                       first, the scopes that hold the ``robots`` grant,
                                                       and the bundle limits
``GET  /api/admin/robot-assets/export?ref=..&all=1``   the bundle of the named versions (``id@version``,
``POST /api/admin/robot-assets/export``  ``{refs|all}``   or ``id`` for all of an id's versions), streamed
``POST /api/admin/robot-assets/import/preview``        the bundle as the raw body: validated, staged, and
                                                       what an import would do per robot (nothing written
                                                       to the assets)
``POST /api/admin/robot-assets/import/{upload_id}``    ``{add_older?, robots?: {ref: {include?,
                                                       add_older?}}}``: import the staged bundle
``DELETE /api/admin/robot-assets/import/{upload_id}``  drop a staged bundle without importing it

Every route is under ``/api/admin`` and so is an administrator's (``require_admin``): the robot assets
are provider data, which only an administrator writes, and the provider store is a scope only an
administrator opens. An import writes into the provider store only -- never into another scope -- and
changes no grant: a scope that holds the ``robots`` grant reads the imported versions at once.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from .. import robot_bundles as rb
from ..auth import User
from .deps import RestContext, optional_pool, rest_context
from .provider_store import clear_store_cache

router = APIRouter()


async def _grants(request: Request) -> list[dict]:
    """The scopes that read the robot assets: grants of the collection and provider-wide ones."""
    pool = optional_pool(request)
    if pool is None:
        return []
    try:
        rows = await db_module.list_provider_grants(pool, provider=rb.PROVIDER_ID, collection=rb.COLLECTION)
    except Exception as exc:  # noqa: BLE001 - the list still answers without them
        logger.warning("robot assets: could not read the grants: %s", exc)
        return []
    return [
        {"target_scope": g["target_scope"], "collection": g.get("collection"), "geometry": bool(g.get("geometry"))}
        for g in rows
    ]


@router.get("/robot-assets")
async def admin_robot_assets(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    store = await rb.list_store(ctx.storage)
    return JSONResponse(
        {
            "store": "providers",
            "provider": rb.PROVIDER_ID,
            "collection": rb.COLLECTION,
            "robots": rb.listing(store),
            "grants": await _grants(request),
            "bundle": {
                "schema": rb.BUNDLE_SCHEMA,
                "suffix": rb.BUNDLE_SUFFIX,
                "limits": rb.Limits.from_env().as_dict(),
            },
        }
    )


async def _export(request: Request, ctx: RestContext, user: User, refs: list[str] | None) -> StreamingResponse:
    store = await rb.list_store(ctx.storage)
    try:
        chosen = rb.select(store, refs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"not stored: {exc.args[0]}") from exc
    if not chosen:
        raise HTTPException(status_code=404, detail="there are no robot assets to export")
    exported_at = rb.exported_at_now()
    filename = rb.bundle_filename(chosen, exported_at)
    await ctx.audit(request, user, rb.STORE, "robot-bundle-export", key=filename, status="ok")
    logger.info("robot bundle export by %s: %d version(s)", user.sub, len(chosen))
    return StreamingResponse(
        rb.export_stream(ctx.storage, chosen, store, exported_at=exported_at),
        media_type=rb.BUNDLE_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Robot-Bundle-Robots": str(len(chosen)),
            "Cache-Control": "no-store",
        },
    )


@router.get("/robot-assets/export")
async def admin_robot_assets_export_get(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> StreamingResponse:
    refs = request.query_params.getlist("ref")
    all_ = request.query_params.get("all", "").lower() in ("1", "true", "yes")
    if not refs and not all_:
        raise HTTPException(status_code=400, detail="name the robots (?ref=<id>@<version>, repeated) or ?all=1")
    return await _export(request, ctx, user, None if all_ else refs)


@router.post("/robot-assets/export")
async def admin_robot_assets_export_post(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> StreamingResponse:
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    refs = body.get("refs")
    if body.get("all") is True:
        return await _export(request, ctx, user, None)
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) for r in refs):
        raise HTTPException(status_code=400, detail="'refs' must be a non-empty list of refs, or 'all': true")
    return await _export(request, ctx, user, refs)


class _TooLarge(Exception):
    """The body passed ``max_bundle_bytes`` while it streamed."""


def _too_large(limits: rb.Limits) -> HTTPException:
    return HTTPException(status_code=413, detail=f"the bundle is larger than {limits.max_bundle_bytes} bytes")


async def _stage(request: Request, ctx: RestContext, key: str, limits: rb.Limits) -> int:
    """Stream the raw body straight into the staging object ``key`` (a multipart upload: no local
    copy, a few parts in memory). Refused with 413 before a byte is read when Content-Length says
    it is too large, and as soon as the body passes the limit otherwise -- the upload is then
    aborted, so nothing is left at ``key``. An empty body is a 400 and stages nothing. Returns the
    bundle's size."""
    announced = request.headers.get("content-length")
    if announced and announced.isdigit() and int(announced) > limits.max_bundle_bytes:
        raise _too_large(limits)
    body = request.stream().__aiter__()
    first = b""
    async for chunk in body:
        if chunk:
            first = chunk
            break
    if not first:
        raise HTTPException(status_code=400, detail="empty body: send the bundle file as the request body")
    n = 0

    async def chunks():
        nonlocal n
        n = len(first)
        if n > limits.max_bundle_bytes:
            raise _TooLarge()
        yield first
        async for chunk in body:
            n += len(chunk)
            if n > limits.max_bundle_bytes:
                raise _TooLarge()
            if chunk:
                yield chunk

    try:
        await ctx.storage.put_stream(rb.STORE, key, chunks())
    except _TooLarge:
        await _drop(ctx, key)  # the upload was aborted; this only makes sure
        raise _too_large(limits) from None
    return n


async def _drop(ctx: RestContext, key: str) -> None:
    try:
        await ctx.storage.delete(rb.STORE, key)
    except Exception:  # noqa: BLE001 - missing already, or swept within a day anyway
        pass


def _refused(exc: rb.BundleError) -> HTTPException:
    return HTTPException(status_code=400, detail={"message": str(exc), "problems": exc.problems})


async def _plan(ctx: RestContext, source: rb.BundleSource, limits: rb.Limits):
    try:
        bundle = await asyncio.to_thread(rb.validate_bundle, source, limits)
    except rb.BundleError as exc:
        raise _refused(exc) from exc
    store = await rb.list_store(ctx.storage)
    plans = await rb.plan_import(ctx.storage, bundle, store)
    return bundle, store, plans


@router.post("/robot-assets/import/preview")
async def admin_robot_assets_import_preview(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Validate an uploaded bundle and say what an import would do. Nothing is written to the
    robot assets; the bundle is kept in the provider store's staging area until it is imported or
    dropped (or for a day). The body streams into the staging object and is validated there, in
    place: the API keeps no local copy of it. A refused bundle is removed from staging again."""
    limits = rb.Limits.from_env()
    upload_id = uuid.uuid4().hex
    key = rb.staging_key(upload_id)
    await _stage(request, ctx, key, limits)
    try:
        fh = await rb.open_staged(ctx.storage, key)
        try:
            bundle, _store, plans = await _plan(ctx, fh, limits)
        finally:
            fh.close()
    except BaseException:
        await _drop(ctx, key)
        raise
    try:
        await rb.sweep_staging(ctx.storage)
    except Exception as exc:  # noqa: BLE001 - an old staged file is not worth failing a preview for
        logger.warning("robot bundles: staging sweep failed: %s", exc)
    logger.info("robot bundle preview by %s: %d robot(s)", user.sub, len(plans))
    return JSONResponse(
        {
            "upload_id": upload_id,
            "bundle": bundle.summary(),
            "robots": [p.as_dict() for p in plans],
            "counts": rb.status_counts(plans),
            "grants": await _grants(request),
        }
    )


def _upload_key(upload_id: str) -> str:
    try:
        return rb.staging_key(upload_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/robot-assets/import/{upload_id}")
async def admin_robot_assets_import(
    upload_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Import a staged bundle. It is validated and compared with the store again (the store may have
    changed since the preview), then each robot is written on its own. The staged object is read in
    place and each file streams from it into its own object: nothing goes to local disk."""
    key = _upload_key(upload_id)
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    try:
        choices = rb.Choices.from_body(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not await ctx.storage.exists(rb.STORE, key):
        raise HTTPException(status_code=404, detail="no such upload: preview the bundle again")
    limits = rb.Limits.from_env()
    try:
        fh = await rb.open_staged(ctx.storage, key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no such upload: preview the bundle again") from exc
    try:
        bundle, store, plans = await _plan(ctx, fh, limits)
        results = await rb.apply_import(ctx.storage, fh, bundle, plans, choices, store)
        wrote = any(r["result"] in (rb.ADDED, rb.ADDED_OLDER) for r in results)
        if wrote:
            await rb.write_index(ctx.storage)
            clear_store_cache()
    finally:
        fh.close()
    await _drop(ctx, key)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    failed = counts.get(rb.FAILED, 0)
    await ctx.audit(
        request,
        user,
        rb.STORE,
        "robot-bundle-import",
        key=rb.STORE_PREFIX,
        status="ok" if not failed else "error",
        error=f"{failed} robot(s) not imported" if failed else None,
    )
    logger.info("robot bundle import by %s: %s", user.sub, counts)
    return JSONResponse({"results": results, "counts": counts, "index_written": wrote})


@router.delete("/robot-assets/import/{upload_id}")
async def admin_robot_assets_import_discard(upload_id: str, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    key = _upload_key(upload_id)
    existed = await ctx.storage.exists(rb.STORE, key)
    if existed:
        await ctx.storage.delete(rb.STORE, key)
    return JSONResponse({"removed": existed})
