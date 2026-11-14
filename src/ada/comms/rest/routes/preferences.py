"""The caller's own preference documents: ``GET`` / ``PUT`` / ``DELETE /api/me/preferences/{key}``.

One JSON value per (user, key), stored as given (``db/user_preferences.py``, migration 053). The
viewer keeps its panel layout here in hosted mode, so it follows the user between browsers. Only
the caller's own rows are reachable: the user comes from the token, never from the path.

Without a database (a local viewer) the same values are files in the server's own storage,
``_prefs/<user hash>/<key>.json`` in the shared scope (a hidden prefix). That is what keeps a
local viewer's layout when it is started again on another port: the browser's storage is per
origin, so a new port would start from nothing, while the server's storage stays where it was.
"""

from __future__ import annotations

import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..converters.keys import PREFERENCES_PREFIX
from ..scope import Scope
from .deps import RestContext, rest_context

router = APIRouter()

_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
# Serialized size of one value. A layout document is a few KiB.
MAX_PREFERENCE_BYTES = 256 * 1024


def check_preference_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="preference key must be 1-128 of A-Z a-z 0-9 . _ : -")
    return key


PREFS_PREFIX = PREFERENCES_PREFIX


def preference_file_key(sub: str, key: str) -> str:
    """Where a user's value is kept without a database: by a hash of the user, never the name."""
    user = hashlib.sha256(sub.encode("utf-8")).hexdigest()[:16]
    return f"{PREFS_PREFIX}{user}/{key}.json"


class _Prefs:
    """The caller's preferences: postgres when there is a pool, the storage files when not."""

    def __init__(self, request: Request, ctx: RestContext, user: User) -> None:
        self.pool = getattr(request.app.state, "db_pool", None)
        self.storage = ctx.storage
        self.sub = user.sub

    async def get(self, key: str) -> object | None:
        if self.pool is not None:
            return await db_module.get_user_preference(self.pool, self.sub, key)
        try:
            return json.loads(await self.storage.get_bytes(Scope.shared(), preference_file_key(self.sub, key)))
        except Exception:
            return None

    async def set(self, key: str, value: object) -> None:
        if self.pool is not None:
            await db_module.set_user_preference(self.pool, self.sub, key, value)
            return
        await self.storage.put_bytes(
            Scope.shared(), preference_file_key(self.sub, key), json.dumps(value).encode("utf-8")
        )

    async def delete(self, key: str) -> bool:
        if self.pool is not None:
            return await db_module.delete_user_preference(self.pool, self.sub, key)
        file_key = preference_file_key(self.sub, key)
        if not await self.storage.exists(Scope.shared(), file_key):
            return False
        await self.storage.delete(Scope.shared(), file_key)
        return True


@router.get("/me/preferences/{key}")
async def api_get_preference(
    key: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """``{key, value}``, ``value`` null when the caller has stored nothing under ``key``."""
    check_preference_key(key)
    return JSONResponse({"key": key, "value": await _Prefs(request, ctx, user).get(key)})


@router.put("/me/preferences/{key}")
async def api_put_preference(
    key: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Replace the caller's value under ``key``. Body: ``{"value": <any JSON>}``; a null value
    removes it."""
    check_preference_key(key)
    prefs = _Prefs(request, ctx, user)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(status_code=400, detail="value required")
    value = body["value"]
    if value is None:
        await prefs.delete(key)
        return JSONResponse({"key": key, "value": None})
    if len(json.dumps(value)) > MAX_PREFERENCE_BYTES:
        raise HTTPException(status_code=413, detail=f"a preference is at most {MAX_PREFERENCE_BYTES} bytes")
    await prefs.set(key, value)
    return JSONResponse({"key": key, "value": value})


@router.delete("/me/preferences/{key}")
async def api_delete_preference(
    key: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """``{removed}``: whether the caller had a value under ``key``."""
    check_preference_key(key)
    return JSONResponse({"removed": await _Prefs(request, ctx, user).delete(key)})
