"""The caller's own preference documents: ``GET`` / ``PUT`` / ``DELETE /api/me/preferences/{key}``.

One JSON value per (user, key), stored as given (``db/user_preferences.py``, migration 053). The
viewer keeps its panel layout here in hosted mode, so it follows the user between browsers. Only
the caller's own rows are reachable: the user comes from the token, never from the path.

Needs the DB pool: without one every route answers 503, and the viewer keeps its preferences in
the browser instead.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import require_pool

router = APIRouter()

_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
# Serialized size of one value. A layout document is a few KiB.
MAX_PREFERENCE_BYTES = 256 * 1024


def check_preference_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="preference key must be 1-128 of A-Z a-z 0-9 . _ : -")
    return key


@router.get("/me/preferences/{key}")
async def api_get_preference(
    key: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """``{key, value}``, ``value`` null when the caller has stored nothing under ``key``."""
    check_preference_key(key)
    pool = require_pool(request)
    return JSONResponse({"key": key, "value": await db_module.get_user_preference(pool, user.sub, key)})


@router.put("/me/preferences/{key}")
async def api_put_preference(
    key: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Replace the caller's value under ``key``. Body: ``{"value": <any JSON>}``; a null value
    removes it."""
    check_preference_key(key)
    pool = require_pool(request)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    if not isinstance(body, dict) or "value" not in body:
        raise HTTPException(status_code=400, detail="value required")
    value = body["value"]
    if value is None:
        await db_module.delete_user_preference(pool, user.sub, key)
        return JSONResponse({"key": key, "value": None})
    if len(json.dumps(value)) > MAX_PREFERENCE_BYTES:
        raise HTTPException(status_code=413, detail=f"a preference is at most {MAX_PREFERENCE_BYTES} bytes")
    await db_module.set_user_preference(pool, user.sub, key, value)
    return JSONResponse({"key": key, "value": value})


@router.delete("/me/preferences/{key}")
async def api_delete_preference(
    key: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """``{removed}``: whether the caller had a value under ``key``."""
    check_preference_key(key)
    pool = require_pool(request)
    return JSONResponse({"removed": await db_module.delete_user_preference(pool, user.sub, key)})
