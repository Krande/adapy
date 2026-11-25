"""``/api/dev/connections/*``: the connection library ``ada dev connections`` serves.

Mounted only when ``ADA_DEV_CONNECTIONS`` names a library (see :mod:`..dev_connections`):

* ``GET  /dev/connections``         — the library, its load status, generation and specs
* ``POST /dev/connections/build``   — ``{spec_name, inputs}`` → the built GLB, stored in the
  caller's personal scope; returns ``{scope, key, inputs, log, counts, generation}``. A builder
  that raises is a 422 ``{error, traceback, declined}`` (``declined``: it raised NotApplicable)
* ``POST /dev/connections/subject`` — the same request: the detail as a simulation subject (a weld
  assembly) stored in the caller's scope; returns ``{scope, key, kind, welds, label}``; 409 when the
  detail has no welds
* ``POST /dev/connections/reload`` — reload now (the watcher does this on every change)
* ``POST /dev/connections/restart`` — a fresh library host, re-reading ``ada_connections.toml``
* ``GET  /dev/connections/events``  — server-sent ``state`` events, one per status change

The answers are made in :mod:`..dev_connections` (shared with the standard-library server
``ada dev connections`` runs, :mod:`..dev_connections_server`); this module adds the storage.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ada.config import logger

from .. import auth as auth_module
from ..auth import User
from ..dev_connections import (
    BUILD_SCOPE,
    SSE_HEARTBEAT_S,
    DevConnections,
    DevReply,
    DevRequestError,
    build_reply,
    sse,
    subject_reply,
)
from ..scope import Scope
from .deps import RestContext, rest_context

router = APIRouter()

__all__ = ["BUILD_SCOPE", "router"]


def _dev(request: Request) -> DevConnections:
    dev = getattr(request.app.state, "dev_connections", None)
    if dev is None:
        raise HTTPException(status_code=404, detail="no connection library is being served")
    return dev


async def _answer(reply: DevReply, ctx: RestContext, user: User) -> JSONResponse:
    """Store what the reply carries in the caller's scope, drop what it replaces, answer."""
    scope = Scope.user(user.sub)
    if reply.store is not None:
        key, data, encoding = reply.store
        await ctx.storage.put_bytes(scope, key, data, content_encoding=encoding)
    for stale in reply.delete:
        try:
            await ctx.storage.delete(scope, stale)
        except Exception:  # noqa: BLE001 - a stale file left behind is harmless
            logger.debug("dev-connections: could not delete %s", stale)
    return JSONResponse(status_code=reply.status, content=reply.body)


async def _reply(fn, dev: DevConnections, request: Request) -> DevReply:
    body = await request.json()
    try:
        return await run_in_threadpool(fn, dev, body)
    except DevRequestError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/dev/connections")
async def api_dev_connections(request: Request) -> JSONResponse:
    return JSONResponse(_dev(request).snapshot())


@router.post("/dev/connections/build")
async def api_dev_connections_build(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    return await _answer(await _reply(build_reply, _dev(request), request), ctx, user)


@router.post("/dev/connections/subject")
async def api_dev_connections_subject(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Offer the detail as a simulation subject: a weld assembly stored in the caller's scope."""
    return await _answer(await _reply(subject_reply, _dev(request), request), ctx, user)


@router.post("/dev/connections/reload")
async def api_dev_connections_reload(request: Request) -> JSONResponse:
    dev = _dev(request)
    await run_in_threadpool(dev.reload, ["(manual reload)"])
    return JSONResponse(dev.snapshot())


@router.post("/dev/connections/restart")
async def api_dev_connections_restart(request: Request) -> JSONResponse:
    dev = _dev(request)
    await run_in_threadpool(dev.restart)
    return JSONResponse(dev.snapshot())


@router.get("/dev/connections/events")
async def api_dev_connections_events(request: Request) -> StreamingResponse:
    dev = _dev(request)

    async def stream():
        q = dev.subscribe()
        try:
            yield sse({"type": "state", "reason": "subscribe", **dev.snapshot()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield sse(event)
        finally:
            dev.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
