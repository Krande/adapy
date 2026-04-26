from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from ada.config import logger

from .config import Settings, load_settings
from .handlers import dispatch
from .storage import Storage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    storage = Storage.from_settings(settings)

    app = FastAPI(title="ada-py viewer API", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/healthz")
    async def healthz() -> Response:
        return Response(status_code=200)

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        # Bootstrap config consumed by the frontend (window.COMMS_MODE etc).
        return JSONResponse({"transport": "rest", "apiBase": "/api"})

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

    return app


app = create_app()
