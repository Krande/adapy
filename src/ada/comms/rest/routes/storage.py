"""User-facing storage/asset routes: the file listing, overlay listing, blob
GET/PUT/DELETE (with byte-range support on GET), key move/rename, the
pyodide-driven derived-blob PUT, and the presigned upload/download flow
(``upload-url``, ``upload-complete``, ``upload-progress``, ``download-url``).

Needs storage + the job queue (through :class:`~.deps.RestContext`).
:func:`rename_with_status` is also called from the admin key-rename route
in :mod:`~.admin_storage` (``POST /api/admin/scopes/{scope}/keys/rename``),
which is why it is a plain function taking ``storage`` explicitly rather
than a route-local closure.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ada.config import logger

from .. import auth as auth_module
from .. import pending_uploads
from ..auth import User
from ..scope import Scope
from ..storage import Storage
from ..storage_ops import (
    delete_blob_cascade,
    derived_source_of,
    move_keys_to_folder,
    rename_key_cascade,
)
from .deps import (
    DIRECT_UPLOAD_THRESHOLD_BYTES,
    UPLOAD_URL_TTL_SECONDS,
    RestContext,
    content_encoding_for,
    format_label,
    is_accepted_source,
    parse_move_body,
    parse_rename_body,
    rest_context,
    scope_from_path,
)

router = APIRouter()


@router.get("/scopes/{scope}/files")
async def api_scope_files(
    scope_obj: Scope = Depends(scope_from_path),
    include_derived: bool = False,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..converter import (
        HIDDEN_PREFIXES,
        is_derived_key,
        is_hidden_key,
        supported_targets_for,
    )

    storage = ctx.storage

    if not include_derived:
        # Default — hide internal namespaces (_derived/ convert cache + _overlays/
        # auto-disposed utility overlays). Those blobs aren't user files. Convert +
        # download surfaces the derived ones explicitly when needed.
        #
        # Skip them in the STORAGE layer rather than filtering here: an audited scope holds
        # far more derived blobs than files (the corpus: ~28k against 141), so filtering after
        # a full listing made this endpoint O(audit history) — ~1.6 s to return 9 KB, against
        # 30 ms for an unaudited scope with the same file count. is_hidden_key still runs; it
        # is now cheap agreement rather than the mechanism.
        files = await storage.list(scope_obj, skip_prefixes=HIDDEN_PREFIXES)
        rows = {f.key: {"key": f.key, "size": f.size} for f in files if not is_hidden_key(f.key)}
        # A key with a pending upload gets the "uploading" fields whether or
        # not it already appears above — present-but-pending means the PUT
        # landed and /upload-complete simply hasn't run yet; absent-but-
        # pending means it either has not landed or the store does not hide
        # a partial object from a concurrent listing. Either way the caller
        # should not treat it as ready.
        for key, pending in pending_uploads.list_for_scope(scope_obj).items():
            fields = pending.as_dict()
            if key in rows:
                # Real bytes are already listed with a real size — a
                # missing size_hint must not clobber it back to 0.
                fields.pop("size", None)
                rows[key].update(fields)
            else:
                rows[key] = {"key": key, **fields}
        return JSONResponse({"files": list(rows.values())})

    files = await storage.list(scope_obj)

    # Convert-page mode — group every derived blob under its
    # source so the page can list pre-existing conversions next
    # to fresh upload rows. Same grouping the admin storage list
    # builds; same helpers reused (``derived_source_of`` parses
    # the full derived-key zoo including the streaming-FEA tree
    # and SIF step/field picks). Orphans (derived without a
    # source in this scope) are dropped here — the admin tab is
    # where you go to clean those up.
    sources: dict[str, dict] = {}
    derived_index: dict[str, list[dict]] = {}
    for f in files:
        if is_derived_key(f.key):
            parsed = derived_source_of(f.key)
            if parsed is None:
                continue
            src_key, target = parsed
            derived_index.setdefault(src_key, []).append(
                {
                    "format": target,
                    "key": f.key,
                    "size": f.size,
                    "last_modified": f.last_modified,
                }
            )
        else:
            sources[f.key] = {
                "key": f.key,
                "size": f.size,
                "last_modified": f.last_modified,
                "format": format_label(f.key),
                "available_targets": supported_targets_for(f.key),
                "derived": [],
            }
    for src_key, derived_list in derived_index.items():
        entry = sources.get(src_key)
        if entry is not None:
            entry["derived"] = derived_list
    # Same merge as the plain listing above, in this richer shape. A key
    # already in ``sources`` (the object landed; only /upload-complete
    # hasn't run) keeps its real size/format and gains the upload fields;
    # one that isn't there yet is added as a synthetic row so the convert
    # page shows "uploading" instead of the file simply not existing.
    for key, pending in pending_uploads.list_for_scope(scope_obj).items():
        fields = pending.as_dict()
        entry = sources.get(key)
        if entry is None:
            sources[key] = {
                "key": key,
                "last_modified": None,
                "format": format_label(key),
                "available_targets": supported_targets_for(key),
                "derived": [],
                **fields,
            }
        else:
            # Real bytes are already listed with a real size — a missing
            # size_hint must not clobber it back to 0.
            fields.pop("size", None)
            entry.update(fields)
    out = sorted(
        sources.values(),
        key=lambda e: e.get("last_modified") or "",
        reverse=True,
    )
    return JSONResponse({"files": out})


@router.get("/scopes/{scope}/overlays")
async def api_scope_overlays(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    # Saved utility overlays (_overlays/<model-stem>.<utility>.glb) so the utils menu can
    # offer previously-generated merge/diff overlays for the loaded model. The client
    # filters by model stem — an overlay generated on MyModel only shows when
    # MyModel is loaded. Excluded from the normal file list (is_hidden_key).
    files = await ctx.storage.list(scope_obj)
    overlays = [
        {"key": f.key, "size": f.size, "last_modified": f.last_modified}
        for f in files
        if f.key.lstrip("/").startswith("_overlays/")
    ]
    overlays.sort(key=lambda o: o.get("last_modified") or "", reverse=True)
    return JSONResponse({"overlays": overlays})


async def _serve_blob_range(
    storage: Storage, request: Request, scope_obj: Scope, key: str, range_header: str
) -> Response | None:
    """Serve a single byte range of an identity-stored object as 206.

    Returns ``None`` (caller serves the whole object) when the range
    can't be honoured: a gzip-at-rest object, a malformed/multi-range
    header, or an unsatisfiable window. Only a single ``bytes=a-b``
    range is supported — that's all the FEA per-step fetch needs."""
    spec = range_header.strip()
    if not spec.lower().startswith("bytes=") or "," in spec:
        return None  # only single-range bytes= supported
    rng = spec[len("bytes=") :].strip()
    if "-" not in rng:
        return None
    start_s, end_s = rng.split("-", 1)
    try:
        # Ranges over a gzipped body would hand back compressed bytes;
        # let the caller serve the whole object instead.
        if await storage.is_gzip_stored(scope_obj, key):
            return None
        meta = await storage.head(scope_obj, key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"not found: {key}")
    if meta is None:
        raise HTTPException(status_code=404, detail=f"not found: {key}")
    size = int(meta["size"])
    try:
        if start_s == "":
            # suffix range: bytes=-N → last N bytes
            n = int(end_s)
            start = max(0, size - n)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s != "" else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        # 416 Range Not Satisfiable
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)
    length = end - start + 1
    try:
        chunk = await storage.get_range(scope_obj, key, start, length)
    except Exception as exc:
        logger.warning("blob range fetch failed for %s [%d,%d]: %s", key, start, end, exc)
        return None
    # Content-Length is left to Starlette (derived from the body) so it
    # can never disagree with the payload — a mismatch makes some
    # reverse proxies emit a broken response ("Failed to fetch").
    return Response(
        content=chunk,
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{size}",
        },
    )


@router.get("/scopes/{scope}/blobs/{key:path}")
async def api_scope_blob_get(
    key: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> Response:
    from ..converter import is_derived_key

    storage = ctx.storage

    # Range support — lets the FEA viewer pull a single step out of a
    # multi-step field blob instead of downloading the whole stack of
    # steps. Only valid for identity-stored objects; gzip-at-rest blobs
    # (manifest JSON, legacy field blobs) are served whole with
    # Content-Encoding so the browser auto-decompresses.
    #
    # The range can arrive two ways: the standard ``Range`` header, or
    # ``?range_start=&range_end=`` query params. The query-param form is
    # proxy-proof — some ingresses/CDNs (notably on the mobile path)
    # strip the Range *header*, which would silently fall back to a
    # whole-blob download; a query string always survives.
    qp = request.query_params
    range_header = request.headers.get("range")
    if "range_start" in qp:
        rs = (qp.get("range_start") or "").strip()
        re_ = (qp.get("range_end") or "").strip()
        if rs:
            range_header = f"bytes={rs}-{re_}"
    if range_header:
        served = await _serve_blob_range(storage, request, scope_obj, key, range_header)
        if served is not None:
            if not is_derived_key(key):
                await ctx.audit(request, user, scope_obj, "download", key=key, status="ok")
            return served
        # else: fall through to whole-object stream (gzipped/un-rangeable)

    try:
        result = await storage.open_stream(scope_obj, key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("blob fetch failed for %s: %s", key, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    # Audit user-driven downloads, not derived blob fetches — the
    # latter happen for every /api/rpc VIEW_FILE_OBJECT cycle and
    # would drown the log.
    if not is_derived_key(key):
        await ctx.audit(request, user, scope_obj, "download", key=key, status="ok")
    headers: dict[str, str] = {}
    if result.content_encoding:
        # See storage.py: gzipped sources/derived round-trip via
        # Content-Encoding so the browser auto-decompresses. A gzip-at-rest
        # object CANNOT be byte-ranged (a range would hand back compressed
        # bytes — see _serve_blob_range), so advertise no-range honestly:
        # otherwise a client/CDN trusts ``Accept-Ranges: bytes`` and a Range
        # GET against the presigned S3 object returns corrupt partial gzip.
        headers["Content-Encoding"] = result.content_encoding
        headers["Accept-Ranges"] = "none"
    else:
        headers["Accept-Ranges"] = "bytes"
    return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)


@router.put("/scopes/{scope}/blobs/{key:path}")
async def api_scope_blob_put(
    key: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..converter import (
        is_derived_key,
        is_published_asset_key,
        is_versions_artefact_key,
    )

    storage = ctx.storage

    clean = key.lstrip("/")
    if not clean:
        raise HTTPException(status_code=400, detail="empty key")
    if is_derived_key(clean):
        raise HTTPException(status_code=403, detail="cannot write to _derived/")
    # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
    # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
    # to them. Two predicates rather than one because they part company on deletability — see
    # is_published_asset_key. The is_derived_key guard above applies to both.
    if (
        not is_versions_artefact_key(clean)
        and not is_published_asset_key(clean)
        and not await is_accepted_source(ctx.queue, ctx.worker_registry, clean)
    ):
        raise HTTPException(status_code=415, detail=f"unsupported file type: {clean}")

    # Reject before reading the body so a multi-GB upload doesn't
    # buffer through Python first. Browsers always send
    # Content-Length on form/file uploads; if it's missing we still
    # fall through and the body read will succeed only for small
    # payloads.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            announced = int(cl)
        except ValueError:
            announced = -1
        if announced > DIRECT_UPLOAD_THRESHOLD_BYTES:
            if storage.supports_presigned_uploads:
                detail = (
                    f"upload exceeds {DIRECT_UPLOAD_THRESHOLD_BYTES} bytes; "
                    "request a presigned URL via POST /api/scopes/{scope}/upload-url "
                    "and PUT directly at the object store"
                )
            else:
                detail = (
                    f"upload exceeds {DIRECT_UPLOAD_THRESHOLD_BYTES} bytes and "
                    "the local-storage backend cannot accept direct uploads; "
                    "deploy with an S3-compatible backend to use larger files"
                )
            raise HTTPException(status_code=413, detail=detail)

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    try:
        await storage.put_bytes(
            scope_obj,
            clean,
            data,
            content_encoding=content_encoding_for(clean),
        )
    except Exception as exc:
        logger.exception("blob upload failed for %s", clean)
        await ctx.audit(request, user, scope_obj, "upload", key=clean, status="error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await ctx.audit(request, user, scope_obj, "upload", key=clean, status="ok")
    return JSONResponse({"key": clean, "size": len(data)}, status_code=201)


# ── User-level file management (personal scope only) ────────────
# Regular users manage their own files; shared/project scopes stay
# admin-managed (project scopes mix the CI versions/ tree with
# regular files), with one carve-out: a project member may DELETE a
# published dataset blob under ``assets/`` in that project, because
# such a publish targets the project scope and would otherwise be
# irreversible for everyone but an admin. See _member_may_delete.
# CI version blobs and the bake cache are protected even inside the
# personal scope — deleting a source still cascades its derived
# blobs via the shared storage_ops helpers.


def _require_personal(scope_obj: Scope) -> None:
    if scope_obj.kind != "user":
        raise HTTPException(
            status_code=403,
            detail="file management is personal-scope only",
        )


def _reject_protected_key(key: str) -> None:
    # Note the absence of is_published_asset_key here, and keep it absent.
    # ``assets/`` blobs are exempt from the upload extension whitelist the same way
    # ``versions/`` blobs are, but they are user-published rather than CI-pushed, so
    # whoever wrote one has to be able to remove it. Adding the prefix here would make
    # every published dataset a one-way door: writable once, never deletable, not even
    # by its publisher in their own personal scope.
    from ..converter import is_derived_key, is_versions_artefact_key

    if is_derived_key(key) or is_versions_artefact_key(key):
        raise HTTPException(
            status_code=400,
            detail="versions/ and _derived/ keys are admin-managed",
        )


def _member_may_delete(scope_obj: Scope, key: str) -> bool:
    """The one carve-out from "non-personal scopes are admin-managed".

    A published dataset is written into the scope it describes, which is
    usually a shared project scope rather than the publisher's personal one.
    Without this, a project-scoped publish is irreversible for everyone
    except an admin, which is the exact failure mode ``assets/`` exists to
    avoid. Membership is not re-checked here: ``scope_from_path`` has
    already run ``scope.can_access``, which for a project scope is a
    ``project_members`` lookup, so reaching this code at all means the
    caller is a member of this project.

    Delete only — deliberately NOT extended to rename / move-to-folder.
    Those take a *destination* key, so allowing them would let a member move
    bytes out of ``assets/`` and land arbitrary content at an unvalidated
    key in a shared scope, bypassing the upload extension gate that only
    exempts the prefix itself. They also rewrite the key, and the key is the
    published dataset's identity and provenance; the correct fix for a wrong
    key is to delete and publish again, which this allows.
    """
    from ..converter import is_published_asset_key

    return scope_obj.kind == "project" and is_published_asset_key(key)


@router.delete("/scopes/{scope}/blobs/{key:path}")
async def api_scope_blob_delete(
    key: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    clean = key.lstrip("/")
    if not _member_may_delete(scope_obj, clean):
        _require_personal(scope_obj)
    if not clean:
        raise HTTPException(status_code=400, detail="empty key")
    _reject_protected_key(clean)
    result = await delete_blob_cascade(ctx.storage, scope_obj, clean)
    await ctx.audit(
        request,
        user,
        scope_obj,
        "delete",
        key=clean,
        status="ok",
        error="; ".join(result["errors"]) or None,
    )
    return JSONResponse(result)


@router.post("/scopes/{scope}/keys/move-to-folder")
async def api_scope_keys_move_to_folder(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    _require_personal(scope_obj)
    keys, folder = await parse_move_body(request)
    _reject_protected_key(folder + "/")
    for k in keys:
        _reject_protected_key(k.strip().lstrip("/"))
    result = await move_keys_to_folder(ctx.storage, scope_obj, keys, folder)
    for entry in result["moved"]:
        await ctx.audit(
            request,
            user,
            scope_obj,
            "move",
            key=entry["old"],
            status="ok",
            error="; ".join(entry["siblings_failed"]) or None,
        )
    return JSONResponse(result)


@router.post("/scopes/{scope}/keys/rename")
async def api_scope_keys_rename(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    _require_personal(scope_obj)
    old_key, new_key = await parse_rename_body(request)
    _reject_protected_key(old_key)
    _reject_protected_key(new_key)
    result = await rename_with_status(ctx.storage, scope_obj, old_key, new_key)
    await ctx.audit(request, user, scope_obj, "rename", key=old_key, status="ok")
    return JSONResponse(result)


async def rename_with_status(storage: Storage, scope_obj: Scope, old_key: str, new_key: str) -> dict:
    """Run a single cascade rename, mapping helper failures to HTTP errors.

    Shared with the (still-closure) admin rename route — see the module
    docstring.
    """
    live_keys = {f.key for f in await storage.list(scope_obj)}
    result = await rename_key_cascade(storage, scope_obj, old_key, new_key, live_keys)
    if "reason" in result:
        reason = result["reason"]
        if reason == "source not found":
            raise HTTPException(status_code=404, detail=reason)
        if reason.startswith("target already exists"):
            raise HTTPException(status_code=409, detail=reason)
        raise HTTPException(status_code=400, detail=reason)
    return result


@router.put("/scopes/{scope}/derived")
async def api_scope_derived_put(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Upload a derived blob produced by the in-browser pyodide
    converter.

    Why a dedicated endpoint: the regular ``PUT /scopes/{scope}/blobs/{key}``
    rejects writes to ``_derived/`` because that namespace is the
    server worker's domain. The pyodide pipeline produces the same
    kind of derived GLB though, just in the browser, and needs to
    land at the same key the rest of the viewer reads from
    (``_derived/<source>.<target>``). This route takes the
    (source, target) pair, derives the canonical key via the same
    ``derived_key_for`` helper the worker uses, and writes the body
    bytes there.

    Body: raw bytes of the derived blob.
    Query: ``source`` (existing source key in the scope), ``target``
    (default ``glb``).
    """
    from ..converter import derived_key_for

    storage = ctx.storage

    source = (request.query_params.get("source") or "").strip().lstrip("/")
    target = (request.query_params.get("target") or "glb").strip().lstrip(".").lower()
    # When the caller drives its own audit lifecycle via the
    # ``audit/local`` endpoints (the WASM pipeline, which records a
    # metrics-rich two-phase row), suppress the auto-audit here so a
    # single conversion doesn't produce two audit_log rows.
    managed_audit = (request.query_params.get("managed_audit") or "").strip().lower() in ("1", "true", "yes")
    if not source:
        raise HTTPException(status_code=400, detail="source query param required")
    if not await is_accepted_source(ctx.queue, ctx.worker_registry, source):
        raise HTTPException(status_code=415, detail=f"unsupported source: {source}")
    # Confirm the source exists in this scope before writing the
    # derived — otherwise the SPA could pollute the cache for a
    # source that isn't visible to the user.
    try:
        source_exists = await storage.exists(scope_obj, source)
    except Exception:
        source_exists = False
    if not source_exists:
        raise HTTPException(
            status_code=404,
            detail=f"source not found in scope: {source}",
        )

    # Same direct-upload guardrail as the source PUT path.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            announced = int(cl)
        except ValueError:
            announced = -1
        if announced > DIRECT_UPLOAD_THRESHOLD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"derived upload exceeds {DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
            )

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")

    try:
        derived_key = derived_key_for(source, target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await storage.put_bytes(scope_obj, derived_key, data)
    except Exception as exc:
        logger.exception("derived upload failed for %s", derived_key)
        if not managed_audit:
            await ctx.audit(
                request,
                user,
                scope_obj,
                "convert",
                key=source,
                target_format=target,
                status="error",
                error=str(exc),
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not managed_audit:
        await ctx.audit(
            request,
            user,
            scope_obj,
            "convert",
            key=source,
            target_format=target,
            status="done",
        )
    return JSONResponse({"key": derived_key, "size": len(data)}, status_code=201)


@router.post("/scopes/{scope}/upload-url")
async def api_scope_upload_url(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Mint a presigned PUT URL for a too-large-to-buffer upload.

    The browser PUTs the raw file directly to the object store
    and then calls /upload-complete. We don't compress on the way
    in (the URL is opaque to us once issued), so the file lands
    uncompressed; the read path still works because get_bytes /
    stream_to_path sniff the gzip magic, not the metadata.

    Returns 503 on local-backed deployments — operator must provide
    an S3-compatible backend with CORS configured for browser PUTs.
    """
    from ..converter import (
        is_derived_key,
        is_published_asset_key,
        is_versions_artefact_key,
    )

    storage = ctx.storage

    if not storage.supports_presigned_uploads:
        raise HTTPException(
            status_code=503,
            detail="presigned uploads require an S3-compatible backend",
        )
    body = await request.json()
    key = (body.get("key") or "").strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    if is_derived_key(key):
        raise HTTPException(status_code=403, detail="cannot write to _derived/")
    # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
    # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
    # to them. Two predicates rather than one because they part company on deletability — see
    # is_published_asset_key. The is_derived_key guard above applies to both.
    if (
        not is_versions_artefact_key(key)
        and not is_published_asset_key(key)
        and not await is_accepted_source(ctx.queue, ctx.worker_registry, key)
    ):
        raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
    try:
        url = await storage.presigned_put_url(scope_obj, key, expires_in_seconds=UPLOAD_URL_TTL_SECONDS)
    except Exception as exc:
        logger.exception("presign failed for %s", key)
        raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
    # Size is a client-supplied hint, not verified against anything — it only
    # ever reaches a progress bar (as "0 / size_hint" until a heartbeat says
    # otherwise) or a 409 body, never a decision. See pending_uploads.
    raw_size = body.get("size")
    size_hint = raw_size if isinstance(raw_size, int) and raw_size >= 0 else None
    pending_uploads.mark_pending(
        scope_obj, key, user_id=user.sub, size_hint=size_hint, ttl_seconds=UPLOAD_URL_TTL_SECONDS
    )
    # Hint that the client should gzip + send Content-Encoding=gzip
    # when this key's extension is in the compressible list. The
    # encoding header is *not* signed into the presigned URL —
    # SigV4 treats unsigned request headers as opaque metadata, so
    # the browser can attach Content-Encoding without breaking the
    # signature. The object store records the header on the object,
    # the read path's get_bytes/stream_to_path sniffs the gzip
    # magic anyway, and a browser without CompressionStream falls
    # back to raw PUT (the sweep job picks it up later).
    return JSONResponse(
        {
            "url": url,
            "key": key,
            "method": "PUT",
            "expires_in_seconds": UPLOAD_URL_TTL_SECONDS,
            "content_encoding": content_encoding_for(key),
        }
    )


@router.post("/scopes/{scope}/upload-complete")
async def api_scope_upload_complete(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Finalise a presigned-URL upload: confirm the object exists,
    write the audit row, and (best-effort) enqueue auto-conversion.

    Mirrors the post-upload behavior of the regular PUT endpoint —
    if you change one, change the other. The browser is responsible
    for calling this once the direct PUT to the object store
    succeeds; if it doesn't, the file lands but no audit / convert
    happens (storage list still surfaces it).
    """
    from ..converter import (
        is_derived_key,
        is_published_asset_key,
        is_versions_artefact_key,
    )

    body = await request.json()
    key = (body.get("key") or "").strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    if is_derived_key(key):
        raise HTTPException(status_code=403, detail="cannot write to _derived/")
    # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
    # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
    # to them. Two predicates rather than one because they part company on deletability — see
    # is_published_asset_key. The is_derived_key guard above applies to both.
    if (
        not is_versions_artefact_key(key)
        and not is_published_asset_key(key)
        and not await is_accepted_source(ctx.queue, ctx.worker_registry, key)
    ):
        raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
    meta = await ctx.storage.head(scope_obj, key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"object not found at {key}; was the PUT successful?")
    # Only now — head() confirms the object is there, which is the closest
    # this process gets to "the PUT actually finished". Left pending on a
    # 404 above: an upload that failed or is still mid-flight must keep
    # blocking jobs against this key, not clear the gate on the strength of
    # a failed finalise call.
    pending_uploads.mark_complete(scope_obj, key)
    await ctx.audit(request, user, scope_obj, "upload", key=key, status="ok")
    return JSONResponse({"key": key, "size": meta["size"]}, status_code=201)


@router.post("/scopes/{scope}/upload-progress")
async def api_scope_upload_progress(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> Response:
    """Heartbeat from the browser's own upload-progress events.

    Body: ``{key, loaded, total}``. The API cannot observe a direct
    browser→object-store PUT itself — that is the whole point of a
    presigned URL — so the only source of real progress is the XHR
    ``upload`` progress event already wired in ``putToPresignedUrl``. This
    endpoint exists so that progress reaches somewhere OTHER than the tab
    doing the upload: a second tab, a second viewer, or the same tab after
    a reload, all read it back via ``GET /scopes/{scope}/files``.

    Best-effort like ``ctx.audit``: a call for a key with no pending upload
    (already completed, expired, or never registered — e.g. the browser
    raced its own ``/upload-complete``) is not an error, just nothing to
    update. 204 either way; the body only needs to distinguish "keep
    heartbeating" from "stop", which the caller gets from whether a later
    ``GET /files`` still shows this key as uploading.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = (str(body.get("key") or "")).strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    try:
        loaded = int(body.get("loaded"))
        total = int(body.get("total"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="loaded and total must be integers") from None
    pending_uploads.heartbeat(scope_obj, key, loaded=loaded, total=total)
    return Response(status_code=204)


@router.post("/scopes/{scope}/download-url")
async def api_scope_download_url(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Mint a presigned GET URL for direct download from the object
    store. Mirrors /upload-url — same auth surface, same fallback
    semantics for local-backed deployments.

    Streaming via GET /blobs/{key} still works for clients that
    prefer the API-tunneled path; this endpoint exists so the CLI
    and other automated consumers can avoid pinning a worker
    thread for the entire transfer of large artefacts.
    """
    from ..converter import is_derived_key

    storage = ctx.storage

    if not storage.supports_presigned_uploads:
        raise HTTPException(
            status_code=503,
            detail="presigned downloads require an S3-compatible backend",
        )
    body = await request.json()
    key = (body.get("key") or "").strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    meta = await storage.head(scope_obj, key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"object not found at {key}")
    ttl = 15 * 60
    try:
        url = await storage.presigned_get_url(scope_obj, key, expires_in_seconds=ttl)
    except Exception as exc:
        logger.exception("presign GET failed for %s", key)
        raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
    # Audit the URL minting, not the eventual GET — the object
    # store does the transfer outside our request path, so this is
    # the last hook we have on the event.
    if not is_derived_key(key):
        await ctx.audit(request, user, scope_obj, "download", key=key, status="presigned")
    return JSONResponse(
        {
            "url": url,
            "key": key,
            "method": "GET",
            "expires_in_seconds": ttl,
            "size": meta["size"],
        }
    )
