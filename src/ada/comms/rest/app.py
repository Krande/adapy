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
from .scope import Scope, can_access as scope_can_access
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

    # ── Scope helpers ────────────────────────────────────────────────
    #
    # Scope wire format: a single path segment / header value, one of
    #   shared          — the shared bucket (any auth user)
    #   user:me         — the caller's personal scope (resolved server-
    #                     side to user.sub so URLs are user-agnostic)
    #   project:<id>    — a project the caller is a member of
    #
    # Membership and project existence are checked against the DB; with
    # no DB, project scopes are categorically inaccessible.

    def _parse_scope(s: str, user: User) -> Scope:
        if s == "shared":
            return Scope.shared()
        if s == "user:me":
            return Scope.user(user.sub)
        if s.startswith("user:"):
            # Naming another user explicitly is intentionally not
            # allowed; admins use phase-3 admin endpoints instead.
            raise HTTPException(
                status_code=400,
                detail="use 'user:me' for personal scope",
            )
        if s.startswith("project:"):
            pid = s[len("project:"):].strip()
            if not pid:
                raise HTTPException(status_code=400, detail="missing project id")
            return Scope.project(pid)
        raise HTTPException(status_code=400, detail=f"invalid scope {s!r}")

    async def _scope_from_path(
        scope: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> Scope:
        s = _parse_scope(scope, user)
        if not await scope_can_access(user, s, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")
        return s

    async def _scope_from_header(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> Scope:
        s = _parse_scope(request.headers.get("X-Scope", "shared"), user)
        if not await scope_can_access(user, s, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")
        return s

    @api.post("/rpc")
    async def api_rpc(
        request: Request,
        scope: Scope = Depends(_scope_from_header),
    ) -> Response:
        # FlatBuffer envelope used by the SPA's WebSocket-style flow
        # (LIST_FILE_OBJECTS, VIEW_FILE_OBJECT, ...). The scope rides
        # on an X-Scope header so the existing serializer doesn't need
        # to change.
        payload = await request.body()
        if not payload:
            raise HTTPException(status_code=400, detail="empty body")
        try:
            reply = await dispatch(payload, storage, scope)
        except Exception as exc:
            logger.exception("rpc dispatch failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if reply is None:
            return Response(status_code=204)
        return Response(content=reply, media_type="application/octet-stream")

    # ── /api/me + /api/projects ──────────────────────────────────────

    @api.get("/me")
    async def api_me(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        # Lazy upsert on first authenticated hit so the `users` table
        # tracks who has actually signed in. No-op when DB is off.
        pool = getattr(request.app.state, "db_pool", None)
        projects: list[dict] = []
        if pool is not None:
            await db_module.upsert_user(pool, user.sub, user.email, user.display_name)
            for p in await db_module.list_user_projects(pool, user.sub):
                projects.append({"id": p.id, "slug": p.slug, "name": p.name, "role": p.role})

        # Scopes the caller can pick from in the SPA's project picker.
        # Order matters — first entry is the default landing scope.
        scopes: list[dict] = [
            {"kind": "user", "id": "me", "name": "Personal"},
            {"kind": "shared", "id": None, "name": "Shared"},
        ]
        for p in projects:
            scopes.append({"kind": "project", "id": p["id"], "name": p["name"]})

        return JSONResponse(
            {
                "sub": user.sub,
                "email": user.email,
                "displayName": user.display_name,
                "isAdmin": user.is_admin,
                "scopes": scopes,
                "projects": projects,
            }
        )

    @api.get("/projects")
    async def api_projects(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            return JSONResponse({"projects": []})
        rows = await db_module.list_user_projects(pool, user.sub)
        return JSONResponse(
            {
                "projects": [
                    {"id": p.id, "slug": p.slug, "name": p.name, "role": p.role}
                    for p in rows
                ]
            }
        )

    # ── Scope-shaped storage + conversion routes ─────────────────────

    @api.get("/scopes/{scope}/files")
    async def api_scope_files(scope_obj: Scope = Depends(_scope_from_path)) -> JSONResponse:
        from .converter import is_derived_key

        files = await storage.list(scope_obj)
        # Hide the _derived/ namespace — those blobs are an internal
        # cache, not user files. Convert + download surfaces them
        # explicitly when needed.
        return JSONResponse(
            {
                "files": [
                    {"key": f.key, "size": f.size}
                    for f in files
                    if not is_derived_key(f.key)
                ],
            }
        )

    @api.get("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_get(
        key: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> StreamingResponse:
        try:
            result = await storage.open_stream(scope_obj, key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("blob fetch failed for %s: %s", key, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        headers: dict[str, str] = {}
        if result.content_encoding:
            # See storage.py: gzipped sources/derived round-trip via
            # Content-Encoding so the browser auto-decompresses.
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(
            result.stream, media_type="application/octet-stream", headers=headers
        )

    @api.put("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_put(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
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
            await storage.put_bytes(
                scope_obj,
                clean,
                data,
                content_encoding=_content_encoding_for(clean),
            )
        except Exception as exc:
            logger.exception("blob upload failed for %s", clean)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"key": clean, "size": len(data)}, status_code=201)

    @api.post("/scopes/{scope}/convert")
    async def api_scope_convert(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
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
        if not await storage.exists(scope_obj, source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")

        try:
            derived_key = derived_key_for(source_key, target_format)
        except UnsupportedFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if await storage.exists(scope_obj, derived_key):
            return JSONResponse(
                {
                    "job_id": "",
                    "source_key": source_key,
                    "derived_key": derived_key,
                    "target_format": target_format,
                    "status": "done",
                    "progress": 1.0,
                    "stage": "cached",
                    "scope_kind": scope_obj.kind,
                    "scope_id": scope_obj.id,
                    "cached": True,
                }
            )

        try:
            job = await queue.enqueue(
                source_key,
                target_format,
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
            )
        except Exception as exc:
            logger.exception("enqueue failed")
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        payload = asdict(job)
        payload["cached"] = False
        return JSONResponse(payload, status_code=202)

    @api.get("/scopes/{scope}/convert/targets")
    async def api_scope_convert_targets(
        source_key: str,
        scope_obj: Scope = Depends(_scope_from_path),  # auth + access check
    ) -> JSONResponse:
        return JSONResponse(
            {"source_key": source_key, "targets": supported_targets_for(source_key)}
        )

    @api.get("/convert/{job_id}")
    async def api_convert_status(
        job_id: str,
        user: User = Depends(auth_module.current_user),
        request: Request = ...,
    ) -> JSONResponse:
        # Job status is identified by a globally unique job_id, so the
        # URL doesn't carry a scope. We still enforce that the caller
        # could access the job's recorded scope before returning it.
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")
        job = await queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        job_scope = (
            Scope.shared()
            if job.scope_kind == "shared"
            else Scope(kind=job.scope_kind, id=job.scope_id)  # type: ignore[arg-type]
        )
        if not await scope_can_access(
            user, job_scope, getattr(request.app.state, "db_pool", None)
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        return JSONResponse(asdict(job))

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
