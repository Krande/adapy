"""``/api/dev/connections/*``: the connection library ``ada dev connections`` serves.

Mounted only when ``ADA_DEV_CONNECTIONS`` names a library (see :mod:`..dev_connections`):

* ``GET  /dev/connections``         — the library, its load status, generation and specs
* ``POST /dev/connections/build``   — ``{spec_name, inputs}`` → the built GLB, stored in the
  caller's personal scope; returns ``{scope, key, inputs, log, counts, generation}``. A builder
  that raises is a 422 ``{error, traceback, declined}`` (``declined``: it raised NotApplicable)
* ``POST /dev/connections/reload``  — reload now (the watcher does this on every change)
* ``POST /dev/connections/restart`` — a fresh library host, re-reading ``ada_connections.toml``
* ``GET  /dev/connections/events``  — server-sent ``state`` events, one per status change
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ada.config import logger

from .. import auth as auth_module
from ..auth import User
from ..dev_connections import DevConnections, HostError, HostUnavailable
from ..scope import Scope
from .deps import RestContext, rest_context

router = APIRouter()

#: The scope a build lands in, as the studio names it.
BUILD_SCOPE = "user:me"
_HEARTBEAT_S = 15.0


def _dev(request: Request) -> DevConnections:
    dev = getattr(request.app.state, "dev_connections", None)
    if dev is None:
        raise HTTPException(status_code=404, detail="no connection library is being served")
    return dev


@router.get("/dev/connections")
async def api_dev_connections(request: Request) -> JSONResponse:
    return JSONResponse(_dev(request).snapshot())


@router.post("/dev/connections/build")
async def api_dev_connections_build(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    dev = _dev(request)
    body = await request.json()
    spec_name = body.get("spec_name")
    if not isinstance(spec_name, str) or not spec_name:
        raise HTTPException(status_code=400, detail="spec_name (str) is required")
    inputs = body.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise HTTPException(status_code=400, detail="inputs must be an object")

    t0 = time.perf_counter()
    try:
        glb, report = await run_in_threadpool(dev.build, spec_name, inputs)
    except HostUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HostError as e:
        return JSONResponse(
            status_code=422,
            content={
                "spec_name": spec_name,
                "error": e.error,
                "traceback": e.traceback,
                # The builder raised NotApplicable: it refused these members (not a crash).
                "declined": bool(e.extra.get("declined")),
                "generation": dev.generation,
            },
        )
    build_ms = round((time.perf_counter() - t0) * 1000)

    digest = hashlib.sha256(
        json.dumps({"g": report.get("generation"), "i": report.get("inputs")}, sort_keys=True).encode()
    ).hexdigest()[:16]
    key = f"_dev/connections/{spec_name}/{digest}.glb"
    scope = Scope.user(user.sub)
    await ctx.storage.put_bytes(scope, key, glb)
    previous = dev.remember_key(spec_name, key)
    if previous:
        try:
            await ctx.storage.delete(scope, previous)
        except Exception:  # noqa: BLE001 - a stale preview left behind is harmless
            logger.debug("dev-connections: could not delete %s", previous)

    return JSONResponse(
        {
            "spec_name": spec_name,
            "scope": BUILD_SCOPE,
            "key": key,
            "inputs": report.get("inputs"),
            "log": report.get("log") or [],
            "counts": report.get("counts") or {},
            "properties": report.get("properties") or {"members": [], "welds": []},
            # The joint's measured contact as a convex-contact run over THIS GLB (its member ids are
            # the GLB's element ids); null where it could not be measured, `clash_note` saying why.
            "clash_run": report.get("clash_run"),
            "clash_note": report.get("clash_note"),
            "generation": dev.generation,
            "build_ms": build_ms,
        }
    )


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
            yield _sse({"type": "state", "reason": "subscribe", **dev.snapshot()})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event)
        finally:
            dev.unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
