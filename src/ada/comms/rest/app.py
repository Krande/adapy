from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

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
    {".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis", ".sif"}
)

# Hard cap on the regular API-buffered upload path. Above this we make
# the client request a presigned URL and PUT directly at the object
# store, so the API process never sees the bytes. 200 MB is high enough
# for typical IFC/Genie XML/STEP work and low enough that buffering it
# in Python doesn't blow the worker's RAM budget.
_DIRECT_UPLOAD_THRESHOLD_BYTES: int = 200 * 1024 * 1024


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

    async def _audit(
        request: Request,
        user: User,
        scope: Scope,
        action: str,
        *,
        key: str | None = None,
        target_format: str | None = None,
        status: str | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
        job_id: str | None = None,
    ) -> None:
        """Best-effort audit row insert. No-ops without DB; never raises.

        Audit failures must not break user requests — a missing log line
        is preferable to a 500 on a successful upload.
        """
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            return
        try:
            await db_module.insert_audit(
                pool,
                user_sub=user.sub,
                scope_kind=scope.kind,
                scope_id=scope.id,
                action=action,
                key=key,
                target_format=target_format,
                status=status,
                error=error,
                duration_ms=duration_ms,
                job_id=job_id,
            )
        except Exception:
            logger.exception("audit insert failed (action=%s)", action)

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
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> StreamingResponse:
        try:
            result = await storage.open_stream(scope_obj, key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("blob fetch failed for %s: %s", key, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        # Audit user-driven downloads, not derived blob fetches — the
        # latter happen for every /api/rpc VIEW_FILE_OBJECT cycle and
        # would drown the log.
        from .converter import is_derived_key
        if not is_derived_key(key):
            await _audit(request, user, scope_obj, "download", key=key, status="ok")
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
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .converter import is_derived_key, is_supported_source

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        if is_derived_key(clean):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_supported_source(clean):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {clean}")

        # Reject before reading the body so a multi-GB upload doesn't
        # buffer through Python first. Browsers always send
        # Content-Length on form/file uploads; if it's missing we still
        # fall through and the body read will succeed only for small
        # payloads.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                announced = int(cl)
            except ValueError:
                announced = -1
            if announced > _DIRECT_UPLOAD_THRESHOLD_BYTES:
                if storage.supports_presigned_uploads:
                    detail = (
                        f"upload exceeds {_DIRECT_UPLOAD_THRESHOLD_BYTES} bytes; "
                        "request a presigned URL via POST /api/scopes/{scope}/upload-url "
                        "and PUT directly at the object store"
                    )
                else:
                    detail = (
                        f"upload exceeds {_DIRECT_UPLOAD_THRESHOLD_BYTES} bytes and "
                        "the local-storage backend cannot accept direct uploads; "
                        "deploy with an S3-compatible backend to use larger files"
                    )
                raise HTTPException(status_code=413, detail=detail)

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
            await _audit(request, user, scope_obj, "upload", key=clean, status="error", error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        await _audit(request, user, scope_obj, "upload", key=clean, status="ok")
        return JSONResponse({"key": clean, "size": len(data)}, status_code=201)

    @api.post("/scopes/{scope}/upload-url")
    async def api_scope_upload_url(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Mint a presigned PUT URL for a too-large-to-buffer upload.

        The browser PUTs the raw file directly to the object store
        and then calls /upload-complete. We don't compress on the way
        in (the URL is opaque to us once issued), so the file lands
        uncompressed; the read path still works because get_bytes /
        stream_to_path sniff the gzip magic, not the metadata.

        Returns 503 on local-backed deployments — operator must provide
        an S3-compatible backend with CORS configured for browser PUTs.
        """
        from .converter import is_derived_key, is_supported_source

        if not storage.supports_presigned_uploads:
            raise HTTPException(
                status_code=503,
                detail="presigned uploads require an S3-compatible backend",
            )
        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if is_derived_key(key):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_supported_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        try:
            url = await storage.presigned_put_url(scope_obj, key, expires_in_seconds=3600)
        except Exception as exc:
            logger.exception("presign failed for %s", key)
            raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
        return JSONResponse(
            {
                "url": url,
                "key": key,
                "method": "PUT",
                "expires_in_seconds": 3600,
            }
        )

    @api.post("/scopes/{scope}/upload-complete")
    async def api_scope_upload_complete(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Finalise a presigned-URL upload: confirm the object exists,
        write the audit row, and (best-effort) enqueue auto-conversion.

        Mirrors the post-upload behavior of the regular PUT endpoint —
        if you change one, change the other. The browser is responsible
        for calling this once the direct PUT to the object store
        succeeds; if it doesn't, the file lands but no audit / convert
        happens (storage list still surfaces it).
        """
        from .converter import is_derived_key, is_supported_source

        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if is_derived_key(key):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_supported_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        meta = await storage.head(scope_obj, key)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"object not found at {key}; was the PUT successful?")
        await _audit(request, user, scope_obj, "upload", key=key, status="ok")
        return JSONResponse({"key": key, "size": meta["size"]}, status_code=201)

    @api.post("/scopes/{scope}/convert")
    async def api_scope_convert(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
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
            await _audit(
                request, user, scope_obj, "convert",
                key=source_key, target_format=target_format, status="done",
            )
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
            await _audit(
                request, user, scope_obj, "convert",
                key=source_key, target_format=target_format,
                status="error", error=str(exc),
            )
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        await _audit(
            request, user, scope_obj, "convert",
            key=source_key, target_format=target_format, status="queued",
            job_id=job.job_id,
        )
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

    # ── /api/admin/* ────────────────────────────────────────────────
    #
    # Every endpoint below is admin-gated via require_admin (composes
    # with current_user). Without DB everything 503s — there's no
    # in-memory fallback for project membership, by design.
    admin = APIRouter(
        prefix="/api/admin",
        dependencies=[Depends(auth_module.require_admin)],
    )

    def _require_pool(request: Request):
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            raise HTTPException(
                status_code=503,
                detail="admin endpoints require a Postgres-backed deployment",
            )
        return pool

    def _validate_uuid(value: str, what: str = "id") -> str:
        import uuid as _uuid
        try:
            return str(_uuid.UUID(value))
        except (ValueError, AttributeError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid {what}") from exc

    @admin.get("/audit")
    async def admin_audit(
        request: Request,
        user_sub: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        action: str | None = None,
        before_id: int | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        pool = _require_pool(request)
        rows = await db_module.list_audit(
            pool,
            user_sub=user_sub,
            scope_kind=scope_kind,
            scope_id=scope_id,
            action=action,
            limit=limit,
            before_id=before_id,
        )
        # Page cursor: smallest id from this batch. Caller passes it back
        # as ``before_id`` to fetch the next older page.
        next_before = rows[-1]["id"] if len(rows) >= max(1, min(limit, 500)) else None
        return JSONResponse({"entries": rows, "next_before_id": next_before})

    @admin.get("/projects")
    async def admin_projects_list(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        return JSONResponse({"projects": await db_module.list_all_projects(pool)})

    @admin.post("/projects")
    async def admin_projects_create(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        body = await request.json()
        slug = (body.get("slug") or "").strip()
        name = (body.get("name") or "").strip()
        if not slug or not name:
            raise HTTPException(status_code=400, detail="slug and name required")
        # Slug shape: lowercase, alnum + hyphens. Keeps URLs / on-disk
        # prefixes predictable; doesn't otherwise constrain the name.
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
            raise HTTPException(
                status_code=400,
                detail="slug must be lowercase alnum/hyphens (max 63)",
            )
        try:
            project = await db_module.create_project(pool, slug, name)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(project, status_code=201)

    @admin.delete("/projects/{project_id}")
    async def admin_projects_archive(
        project_id: str,
        request: Request,
    ) -> Response:
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")
        ok = await db_module.archive_project(pool, pid)
        if not ok:
            raise HTTPException(status_code=404, detail="project not found")
        return Response(status_code=204)

    @admin.get("/projects/{project_id}/members")
    async def admin_project_members_list(
        project_id: str,
        request: Request,
    ) -> JSONResponse:
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")
        if not await db_module.project_exists(pool, pid):
            raise HTTPException(status_code=404, detail="project not found")
        return JSONResponse(
            {"members": await db_module.list_project_members(pool, pid)}
        )

    @admin.post("/projects/{project_id}/members")
    async def admin_project_members_add(
        project_id: str,
        request: Request,
    ) -> JSONResponse:
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")
        body = await request.json()
        sub = (body.get("user_sub") or "").strip()
        role = (body.get("role") or "member").strip() or "member"
        if not sub:
            raise HTTPException(status_code=400, detail="user_sub required")
        if not await db_module.project_exists(pool, pid):
            raise HTTPException(status_code=404, detail="project not found")
        added = await db_module.add_project_member(pool, pid, sub, role)
        return JSONResponse(
            {"user_sub": sub, "role": role, "added": added},
            status_code=201 if added else 200,
        )

    @admin.delete("/projects/{project_id}/members/{user_sub}")
    async def admin_project_members_remove(
        project_id: str,
        user_sub: str,
        request: Request,
    ) -> Response:
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")
        ok = await db_module.remove_project_member(pool, pid, user_sub)
        if not ok:
            raise HTTPException(status_code=404, detail="not a member")
        return Response(status_code=204)

    # ── Admin storage view ──────────────────────────────────────────
    #
    # Enriched per-scope listing for the admin storage tab: every
    # source file with its detected format, size, last_modified, and
    # the derived blobs already cached for it. The DELETE endpoint
    # removes a source plus all of its derived siblings — the admin
    # panel surfaces it as a single "delete" action so the bucket
    # doesn't drift into a state where derived blobs outlive their
    # source.
    #
    # Scoped via the same _scope_from_path dep as the user-facing
    # storage routes — admins still need scope access (member of the
    # project, owner of the user scope, etc.). Shared scope is open to
    # any authed user.

    _SOURCE_FORMAT_NAMES = {
        ".ifc": "IFC",
        ".step": "STEP",
        ".stp": "STEP",
        ".stl": "STL",
        ".obj": "OBJ",
        ".ply": "PLY",
        ".dae": "Collada",
        ".off": "OFF",
        ".gltf": "glTF",
        ".glb": "glTF (binary)",
        ".xml": "Genie XML",
        ".inp": "Abaqus input",
        ".fem": "Sesam FEM",
        ".sat": "ACIS",
        ".acis": "ACIS",
        ".zip": "Bundle (zip)",
        ".sif": "Sesam Result (sif)",
    }

    def _format_label(key: str) -> str:
        ext = pathlib.PurePosixPath(key).suffix.lower()
        return _SOURCE_FORMAT_NAMES.get(ext, ext.lstrip(".").upper() or "—")

    def _derived_source_of(derived_key: str) -> tuple[str, str] | None:
        """Recover (source_key, target_format) from a `_derived/<src>.<fmt>`
        key. Returns None when the key doesn't match the convention."""
        from .converter import TARGET_FORMATS, is_derived_key
        if not is_derived_key(derived_key):
            return None
        stripped = derived_key[len("_derived/"):]
        for tgt in TARGET_FORMATS:
            suffix = "." + tgt
            if stripped.endswith(suffix):
                return stripped[: -len(suffix)], tgt
        return None

    @admin.get("/scopes/{scope}/files")
    async def admin_storage_list(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        from .converter import is_derived_key, supported_targets_for

        files = await storage.list(scope_obj)
        sources: dict[str, dict] = {}
        derived_index: dict[str, list[dict]] = {}

        for f in files:
            if is_derived_key(f.key):
                parsed = _derived_source_of(f.key)
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
                    "format": _format_label(f.key),
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
                    "format": _format_label(src_key),
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

    @admin.delete("/scopes/{scope}/blobs/{key:path}")
    async def admin_storage_delete(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .converter import TARGET_FORMATS, derived_key_for, is_derived_key

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")

        # If the admin pointed at a derived blob, only that blob goes;
        # don't fan out and delete the source under their feet.
        if is_derived_key(clean):
            try:
                await storage.delete(scope_obj, clean)
            except Exception as exc:
                logger.exception("admin: delete failed for %s", clean)
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            await _audit(request, user, scope_obj, "delete", key=clean, status="ok")
            return JSONResponse({"deleted": [clean]})

        # Source delete: also reap every derived blob keyed off this
        # source. Build the candidate set up front so a partial failure
        # mid-loop doesn't leave inconsistent metadata.
        candidates = [clean]
        for tgt in TARGET_FORMATS:
            try:
                candidates.append(derived_key_for(clean, tgt))
            except Exception:
                continue

        deleted: list[str] = []
        errors: list[str] = []
        for k in candidates:
            try:
                await storage.delete(scope_obj, k)
                deleted.append(k)
            except FileNotFoundError:
                # Derived blob just wasn't there — that's fine.
                continue
            except Exception as exc:
                # Some backends raise a generic error for "not found";
                # treat it as benign for derived siblings, but if the
                # source itself can't be deleted, surface the failure.
                msg = str(exc).lower()
                if "not found" in msg or "no such" in msg:
                    continue
                if k == clean:
                    logger.exception("admin: delete failed for source %s", clean)
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                errors.append(f"{k}: {exc}")

        await _audit(
            request, user, scope_obj, "delete",
            key=clean, status="ok",
            error="; ".join(errors) or None,
        )
        return JSONResponse({"deleted": deleted, "errors": errors})

    app.include_router(admin)

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
            _wire_spa_fallback(app, static_dir)

    return app


def _wire_spa_fallback(app: FastAPI, static_dir: pathlib.Path) -> None:
    """Register a catch-all that serves the SPA shell for client-side routes.

    StaticFiles(html=True) only serves index.html for directory roots —
    a deep link like ``/auth/callback`` (which is what OIDC redirects
    to) misses the disk and falls into FastAPI's default 404. We
    instead resolve every non-API request manually:

    1. ``/api/*`` misses → 404 (don't disguise API bugs as SPA pages).
    2. Path matches a file on disk → serve it (assets, favicon, etc.).
    3. Anything else → return ``index.html`` and let the SPA router
       consume the URL.

    Must register *after* every explicit route the API exposes; the
    path-converter pattern matches anything that didn't already match.
    """
    static_root = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # Don't paper over API mistakes by returning the SPA shell.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        if full_path:
            candidate = (static_dir / full_path).resolve()
            # Reject path traversal — the resolved target must live
            # inside static_dir.
            try:
                candidate.relative_to(static_root)
            except ValueError:
                raise HTTPException(status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")


app = create_app()
