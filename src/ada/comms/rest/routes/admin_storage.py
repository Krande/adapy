"""Admin storage-view routes: ``GET /api/admin/scopes/{scope}/files``
(enriched per-scope listing with format + derived-blob info), ``DELETE
/api/admin/scopes/{scope}/blobs/{key}`` (cascade delete), and the
key-management trio ``keys/move-to-folder``, ``keys/rename`` and
``keys/copy-from``.

Scoped via the same ``scope_from_path`` dependency as the user-facing
storage routes (``routes/storage.py``) — admins still need scope access
(member of the project, owner of the user scope, etc.); shared scope is
open to any authed user. ``keys/copy-from`` is what lets an admin populate
a corpus scope from another one.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from ..auth import User
from ..scope import Scope
from ..scope import can_access as scope_can_access
from ..storage_ops import delete_blob_cascade, derived_source_of, move_keys_to_folder
from .deps import (
    RestContext,
    format_label,
    parse_move_body,
    parse_rename_body,
    parse_scope,
    resolve_project_scope,
    rest_context,
    scope_from_path,
)
from .storage import rename_with_status

router = APIRouter()


@router.get("/scopes/{scope}/files")
async def admin_storage_list(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    from ..converter import is_derived_key, supported_targets_for

    files = await ctx.storage.list(scope_obj)
    sources: dict[str, dict] = {}
    derived_index: dict[str, list[dict]] = {}

    for f in files:
        if f.key.lstrip("/").startswith("_overlays/"):
            continue  # auto-disposed utility overlays (merge-preview/diff) — not user files
        if is_derived_key(f.key):
            parsed = derived_source_of(f.key)
            if parsed is None:
                continue  # malformed derived key — ignore quietly
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
        if entry is None:
            # Orphan — derived blob without its source. Surface it
            # as a synthetic entry so the admin can clean it up.
            sources[src_key] = {
                "key": src_key,
                "size": 0,
                "last_modified": None,
                "format": format_label(src_key),
                "available_targets": [],
                "orphan": True,
                "derived": derived_list,
            }
        else:
            entry["derived"] = derived_list

    out = sorted(
        sources.values(),
        key=lambda e: e.get("last_modified") or "",
        reverse=True,
    )
    return JSONResponse({"files": out})


@router.delete("/scopes/{scope}/blobs/{key:path}")
async def admin_storage_delete(
    key: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    result = await delete_blob_cascade(ctx.storage, scope_obj, key)
    await ctx.audit(
        request,
        user,
        scope_obj,
        "delete",
        key=key.lstrip("/"),
        status="ok",
        error="; ".join(result["errors"]) or None,
    )
    return JSONResponse(result)


@router.post("/scopes/{scope}/keys/move-to-folder")
async def admin_keys_move_to_folder(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Batch-move source keys to a destination folder prefix.

    Body: ``{"keys": [...], "folder": "..."}``. Each source key
    is renamed to ``<folder>/<basename(src_key)>`` within the
    same scope, with derived siblings cascading (see
    storage_ops.move_keys_to_folder). Per-key failures don't
    abort the batch — the caller gets ``{moved, failed}``.
    """

    keys, folder = await parse_move_body(request)
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
async def admin_keys_rename(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Rename a single source key (derived siblings cascade)."""
    old_key, new_key = await parse_rename_body(request)
    # routes/storage.py's rename_with_status, shared with the user-facing
    # rename route — see that module's docstring.
    result = await rename_with_status(ctx.storage, scope_obj, old_key, new_key)
    await ctx.audit(request, user, scope_obj, "rename", key=old_key, status="ok")
    return JSONResponse(result)


@router.post("/scopes/{scope}/keys/copy-from")
async def admin_keys_copy_from(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),  # destination scope (e.g. a corpus)
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Server-side copy source keys from another scope into this one.

    Body: ``{"src_scope": "user:me", "keys": [...]}``. Each key is copied
    (Garage / S3 CopyObject — no download/reupload) from ``src_scope`` to the
    same key in the path scope. The caller must be able to read ``src_scope``.
    Per-key reporting: ``{copied, skipped, failed}`` — a key that already
    exists in the destination is reported under ``skipped`` (a no-op, not an
    error, so recursive folder copies tolerate partial overlap); a missing
    source, derived-key reject, or backend error lands in ``failed``. Nothing
    aborts the batch.
    """
    from ..converter import is_derived_key

    pool = getattr(request.app.state, "db_pool", None)
    body = await request.json()
    src_raw = body.get("src_scope")
    raw_keys = body.get("keys")
    if not isinstance(src_raw, str) or not src_raw.strip():
        raise HTTPException(status_code=400, detail="src_scope required")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise HTTPException(status_code=400, detail="keys must be a non-empty list")
    if any(not isinstance(k, str) or not k.strip() for k in raw_keys):
        raise HTTPException(status_code=400, detail="every key must be a non-empty string")

    src_scope = await resolve_project_scope(pool, parse_scope(src_raw.strip(), user))
    if not await scope_can_access(user, src_scope, pool):
        raise HTTPException(status_code=403, detail="forbidden: source scope")
    if src_scope.prefix() == scope_obj.prefix():
        raise HTTPException(status_code=400, detail="source and destination scope are the same")

    # Dedup while preserving order.
    seen: set[str] = set()
    keys: list[str] = []
    for raw in raw_keys:
        cleaned = raw.strip().lstrip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            keys.append(cleaned)

    # Snapshot destination keys so we can skip collisions without a HEAD per file.
    dst_keys = {f.key for f in await ctx.storage.list(scope_obj)}

    copied: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    for key in keys:
        if is_derived_key(key):
            failed.append({"key": key, "reason": "cannot copy derived blobs"})
            continue
        if key in dst_keys:
            skipped.append({"key": key, "reason": "already in corpus"})
            continue
        try:
            # overwrite=True for the same S3 reason as rename above (the safe
            # default raises ``copy-if-not-exists not supported``); the
            # application-layer dst_keys pre-check is the real collision guard.
            await ctx.storage.copy(src_scope, key, scope_obj, key, overwrite=True)
        except Exception:
            # Full detail is logged; return a generic reason so backend/stack-trace
            # text isn't exposed in the response (CodeQL py/stack-trace-exposure).
            logger.exception("admin: copy failed for %s (%s -> %s)", key, src_raw, scope_obj.prefix())
            failed.append({"key": key, "reason": "copy failed"})
            continue
        dst_keys.add(key)
        copied.append({"key": key})
        await ctx.audit(request, user, scope_obj, "copy", key=key, status="ok")

    return JSONResponse({"copied": copied, "skipped": skipped, "failed": failed})
