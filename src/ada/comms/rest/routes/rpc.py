"""``POST /rpc``: the FlatBuffer envelope the SPA's WebSocket-style flow uses over
REST (LIST_FILE_OBJECTS, VIEW_FILE_OBJECT, ...), handed to :func:`..handlers.dispatch`.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ada.config import logger

from ..handlers import dispatch
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_header, worker_advertised_exts

router = APIRouter()


@router.post("/rpc")
async def api_rpc(
    request: Request,
    scope: Scope = Depends(scope_from_header),
    ctx: RestContext = Depends(rest_context),
) -> Response:
    # FlatBuffer envelope used by the SPA's WebSocket-style flow
    # (LIST_FILE_OBJECTS, VIEW_FILE_OBJECT, ...). The scope rides
    # on an X-Scope header so the existing serializer doesn't need
    # to change.
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=400, detail="empty body")
    # Hand dispatch a lazy provider for the worker-advertised extension
    # set (used only by the file lister, to flag plug-in formats like
    # .odb when an abaqus-capability worker is online). Passing it lazily
    # keeps the worker-registry read off every non-LIST_FILE command —
    # server-info, view-file, etc. no longer pay it — so worker-listing
    # never gates file-finding. The read itself is a cached in-memory
    # lookup (see RestContext.worker_registry) that degrades to the static list.
    try:
        reply = await dispatch(
            payload,
            ctx.storage,
            scope,
            exts_provider=lambda: worker_advertised_exts(ctx.queue, ctx.worker_registry),
        )
    except Exception as exc:
        logger.exception("rpc dispatch failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if reply is None:
        return Response(status_code=204)
    return Response(content=reply, media_type="application/octet-stream")
