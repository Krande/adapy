from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ada.config import logger

from .config import Settings, load_settings
from .converter import derived_key_for, is_supported_source
from .handlers import dispatch
from .queue import JobQueue
from .storage import Storage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Connect to NATS lazily; a missing URL just disables the queue.
        if queue.enabled:
            try:
                await queue.connect()
                logger.info("queue connected to %s", settings.queue.url)
            except Exception as exc:
                logger.warning("queue connect failed (%s); convert endpoints will return 503", exc)
        yield
        if queue.enabled:
            try:
                await queue.close()
            except Exception:
                logger.exception("queue close failed")

    app = FastAPI(
        title="ada-py viewer API",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> Response:
        return Response(status_code=200)

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        # Bootstrap config consumed by the frontend (window.COMMS_MODE etc).
        return JSONResponse({
            "transport": "rest",
            "apiBase": "/api",
            "convertEnabled": queue.enabled,
        })

    @app.post("/api/rpc")
    async def api_rpc(request: Request) -> Response:
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="empty body")
        try:
            reply = await dispatch(payload, storage)
        except Exception as exc:
            logger.exception("rpc dispatch failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if reply is None:
            return Response(status_code=204)
        return Response(content=reply, media_type="application/octet-stream")

    @app.post("/api/convert")
    async def api_convert(request: Request) -> JSONResponse:
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")
        body = await request.json()
        source_key = (body.get("source_key") or "").strip()
        if not source_key:
            raise HTTPException(status_code=400, detail="source_key required")
        if not is_supported_source(source_key):
            raise HTTPException(status_code=415, detail=f"unsupported source format: {source_key}")
        if not await storage.exists(source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")

        # Cheap fast-path: if the derived blob is already there, skip the
        # queue entirely and report a synthetic done job.
        derived_key = derived_key_for(source_key)
        if await storage.exists(derived_key):
            return JSONResponse(
                {
                    "job_id": "",
                    "source_key": source_key,
                    "derived_key": derived_key,
                    "status": "done",
                    "progress": 1.0,
                    "stage": "cached",
                    "cached": True,
                }
            )

        try:
            job = await queue.enqueue(source_key)
        except Exception as exc:
            logger.exception("enqueue failed")
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        payload = asdict(job)
        payload["cached"] = False
        return JSONResponse(payload, status_code=202)

    @app.get("/api/convert/{job_id}")
    async def api_convert_status(job_id: str) -> JSONResponse:
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")
        job = await queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        return JSONResponse(asdict(job))

    @app.get("/api/blobs/{key:path}")
    async def api_blob(key: str) -> StreamingResponse:
        # Streams raw bytes from storage. Useful for direct GLB fetches
        # outside the RPC envelope (CDN-cacheable, addressable).
        try:
            stream = await storage.open_stream(key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("blob fetch failed for %s: %s", key, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return StreamingResponse(stream, media_type="application/octet-stream")

    @app.put("/api/blobs/{key:path}")
    async def api_blob_put(key: str, request: Request) -> JSONResponse:
        # Upload raw file bytes. Frontend uses this from the upload
        # context menu; key is the user-visible filename. Writing to
        # _derived/* is forbidden so users can't poison the cache.
        from .converter import is_derived_key, is_supported_source

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        if is_derived_key(clean):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_supported_source(clean):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {clean}")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")
        try:
            await storage.put_bytes(clean, data)
        except Exception as exc:
            logger.exception("blob upload failed for %s", clean)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"key": clean, "size": len(data)}, status_code=201)

    @app.get("/config.js")
    async def config_js() -> PlainTextResponse:
        # Tiny JS shim the SPA loads before its main bundle. Sets the
        # window globals that comms/index.ts inspects to pick the
        # transport. Generated dynamically so a single image targets
        # multiple deployments.
        body = (
            'window.COMMS_MODE = "rest";\n'
            'window.API_BASE = "/api";\n'
            f'window.CONVERT_ENABLED = {"true" if queue.enabled else "false"};\n'
        )
        return PlainTextResponse(body, media_type="application/javascript")

    if settings.static_path:
        static_dir = pathlib.Path(settings.static_path)
        if not static_dir.is_dir():
            logger.warning("ADA_VIEWER_STATIC_PATH=%s is not a directory; skipping", static_dir)
        else:
            # Mount last so /api/* and /config.js win over the static fallback.
            # html=True makes / serve index.html and SPA routes fall back to it.
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="spa")

    return app


app = create_app()
