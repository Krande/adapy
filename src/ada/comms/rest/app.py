from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ada.config import logger

from . import auth as auth_module
from . import db as db_module
from .auth import User
from .config import Settings, load_settings
from .scope import Scope
from .converter import (
    TARGET_FORMATS,
    UnsupportedFormat,
    derived_key_for,
    is_supported_source,
    supported_targets_for,
)
from .handlers import dispatch
from .queue import JobQueue
from .storage import Storage

# Text-heavy CAD/FEM formats compress 5–10× with gzip; binary mesh
# formats already pack their geometry tightly so we skip them. The
# storage layer transparently decompresses on read; the download
# endpoint forwards Content-Encoding: gzip so browsers handle it on
# the user's machine. ada.from_<format> in the worker sees the original
# bytes via Storage.get_bytes.
_GZIP_UPLOAD_EXTS: frozenset[str] = frozenset(
    {".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis"}
)


def _content_encoding_for(key: str) -> str | None:
    return "gzip" if pathlib.PurePosixPath(key).suffix.lower() in _GZIP_UPLOAD_EXTS else None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage.from_settings(settings)
    queue = JobQueue(settings.queue)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Connect to NATS lazily; a missing URL just disables the queue.
        if queue.enabled:
            try:
                await queue.connect()
                logger.info("queue connected to %s", settings.queue.url)
            except Exception as exc:
                logger.warning("queue connect failed (%s); convert endpoints will return 503", exc)
        # Postgres pool — ``None`` when DATABASE_URL is empty. We don't
        # let DB connect failures abort startup: the API can still
        # serve the shared bucket, and a failed pool surfaces as 503s
        # on the multi-tenant endpoints rather than a crash loop.
        try:
            app.state.db_pool = await db_module.init_pool(settings.database_url)
        except Exception:
            logger.exception("db: pool init failed; running shared-only")
            app.state.db_pool = None
        yield
        if queue.enabled:
            try:
                await queue.close()
            except Exception:
                logger.exception("queue close failed")
        try:
            await db_module.close_pool(app.state.db_pool)
        except Exception:
            logger.exception("db close failed")
        # Release the OIDC JWKS HTTP client (no-op when auth is disabled).
        try:
            await auth_module.aclose(app)
        except Exception:
            logger.exception("auth close failed")

    app = FastAPI(
        title="ada-py viewer API",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    auth_module.install(app, settings.auth)

    @app.get("/healthz")
    async def healthz() -> Response:
        # Public — load balancers + readiness probes hit this.
        return Response(status_code=200)

    # /api/config is *almost* public (the SPA fetches it before it has
    # a token, to learn whether auth is enabled and what the issuer is)
    # — but it never leaks user data, so we serve it unauthenticated.
    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        return JSONResponse({
            "transport": "rest",
            "apiBase": "/api",
            "convertEnabled": queue.enabled,
            "auth": {
                "enabled": settings.auth.enabled,
                "issuer": settings.auth.issuer,
                "clientId": settings.auth.client_id,
                # Audience usually = clientId; expose it so the SPA can
                # request the right token from Azure-style providers.
                "audience": settings.auth.audience,
            },
        })

    # Every /api/* below this line requires a verified user. The dep is
    # attached to the router so individual routes don't have to repeat
    # `Depends(current_user)`. When auth is disabled the dep returns the
    # synthetic local-dev user, so dev / desktop paths see no behavior
    # change beyond an extra (free) function call per request.
    api = APIRouter(prefix="/api", dependencies=[Depends(auth_module.current_user)])

    @api.post("/rpc")
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

    @api.post("/convert")
    async def api_convert(request: Request) -> JSONResponse:
        body = await request.json()
        source_key = (body.get("source_key") or "").strip()
        target_format = (body.get("target_format") or "glb").strip().lower()
        if not source_key:
            raise HTTPException(status_code=400, detail="source_key required")
        if not is_supported_source(source_key):
            raise HTTPException(status_code=415, detail=f"unsupported source format: {source_key}")
        if target_format not in TARGET_FORMATS:
            raise HTTPException(
                status_code=415,
                detail=f"unknown target_format {target_format!r}; allowed: {sorted(TARGET_FORMATS)}",
            )
        viable = supported_targets_for(source_key)
        if target_format not in viable:
            raise HTTPException(
                status_code=415,
                detail=f"target {target_format!r} not viable for source {source_key!r}; allowed: {viable}",
            )
        # Phase 2B: scope is hardcoded to ``shared`` here so existing
        # /api/* routes keep working unchanged. The scope-shaped URLs
        # land in phase 2C.
        if not await storage.exists(Scope.shared(), source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")

        # Cheap fast-path: if the derived blob is already there, skip the
        # queue entirely and report a synthetic done job.
        try:
            derived_key = derived_key_for(source_key, target_format)
        except UnsupportedFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if await storage.exists(Scope.shared(), derived_key):
            return JSONResponse(
                {
                    "job_id": "",
                    "source_key": source_key,
                    "derived_key": derived_key,
                    "target_format": target_format,
                    "status": "done",
                    "progress": 1.0,
                    "stage": "cached",
                    "cached": True,
                }
            )

        try:
            job = await queue.enqueue(source_key, target_format)
        except Exception as exc:
            logger.exception("enqueue failed")
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        payload = asdict(job)
        payload["cached"] = False
        return JSONResponse(payload, status_code=202)

    @api.get("/convert/targets")
    async def api_convert_targets(source_key: str) -> JSONResponse:
        # Lets the frontend render only viable target options for a
        # given source. Cheap and side-effect-free.
        return JSONResponse(
            {"source_key": source_key, "targets": supported_targets_for(source_key)}
        )

    @api.get("/convert/{job_id}")
    async def api_convert_status(job_id: str) -> JSONResponse:
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")
        job = await queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        return JSONResponse(asdict(job))

    @api.get("/blobs/{key:path}")
    async def api_blob(key: str) -> StreamingResponse:
        # Streams raw bytes from storage. Useful for direct GLB fetches
        # outside the RPC envelope (CDN-cacheable, addressable).
        try:
            result = await storage.open_stream(Scope.shared(), key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("blob fetch failed for %s: %s", key, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        headers: dict[str, str] = {}
        if result.content_encoding:
            # Tell the browser to decompress on receipt — keeps the
            # download UX identical (foo.ifc lands uncompressed on disk)
            # while we ship 5–10× less bytes over the wire.
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(
            result.stream, media_type="application/octet-stream", headers=headers
        )

    @api.put("/blobs/{key:path}")
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
            await storage.put_bytes(Scope.shared(), clean, data, content_encoding=_content_encoding_for(clean))
        except Exception as exc:
            logger.exception("blob upload failed for %s", clean)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"key": clean, "size": len(data)}, status_code=201)

    app.include_router(api)

    @app.get("/config.js")
    async def config_js() -> PlainTextResponse:
        # Tiny JS shim the SPA loads before its main bundle. Sets the
        # window globals that comms/index.ts inspects to pick the
        # transport and bootstrap auth. Generated dynamically so a
        # single image targets multiple deployments. Strings are JSON-
        # encoded so any embedded quotes can't break out of the literal.
        import json as _json

        a = settings.auth
        body = (
            'window.COMMS_MODE = "rest";\n'
            'window.API_BASE = "/api";\n'
            f'window.CONVERT_ENABLED = {"true" if queue.enabled else "false"};\n'
            f'window.AUTH_ENABLED = {"true" if a.enabled else "false"};\n'
            f"window.AUTH_ISSUER = {_json.dumps(a.issuer)};\n"
            f"window.AUTH_CLIENT_ID = {_json.dumps(a.client_id)};\n"
            f"window.AUTH_AUDIENCE = {_json.dumps(a.audience)};\n"
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
