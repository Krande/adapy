from __future__ import annotations

import asyncio
import json
import os
import pathlib
import tempfile
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
    fea_meta_key_for,
    is_fea_result_key,
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
        # Image tags. Viewer's tag is baked in at image-build time
        # (deploy/Dockerfile.viewer ARG IMAGE_TAG) and read from env.
        # Worker's tag comes from the shared NATS KV — the worker
        # publishes its tag on startup. Either may be missing in dev /
        # local runs; the SPA hides the row when both are empty.
        viewer_tag = os.environ.get("ADA_IMAGE_TAG", "").strip() or None
        worker_tag: str | None = None
        if queue.enabled:
            try:
                worker_tag = await queue.get_meta("worker_image_tag")
            except Exception:
                logger.exception("config: failed to read worker image tag")
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
            "viewerImageTag": viewer_tag,
            "workerImageTag": worker_tag,
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

    async def _resolve_project_scope(pool, scope: Scope) -> Scope:
        """Resolve ``project:<slug>`` to ``project:<uuid>`` against the DB.

        ``_parse_scope`` doesn't know whether the id segment is a UUID or
        a slug — it just hands the raw string through. UUID-shaped ids
        pass through unchanged; non-UUID strings get looked up against
        ``projects.slug`` so callers can use the friendlier form in
        URLs and config files. Without a DB, slug lookup is impossible
        and ``can_access`` will reject regardless, so we leave the scope
        as-is.
        """
        if scope.kind != "project" or scope.id is None or pool is None:
            return scope
        import uuid as _uuid

        try:
            _uuid.UUID(scope.id)
            return scope
        except (ValueError, AttributeError, TypeError):
            pass
        resolved = await db_module.project_id_from_slug(pool, scope.id)
        if resolved is None:
            # Don't leak existence: same status as the membership check
            # below would have produced for a non-member of an unknown
            # project.
            raise HTTPException(status_code=403, detail="forbidden")
        return Scope.project(resolved)

    async def _scope_from_path(
        scope: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> Scope:
        s = _parse_scope(scope, user)
        pool = getattr(request.app.state, "db_pool", None)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")
        return s

    async def _scope_from_header(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> Scope:
        s = _parse_scope(request.headers.get("X-Scope", "shared"), user)
        pool = getattr(request.app.state, "db_pool", None)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
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
        from .converter import is_derived_key, is_supported_source, is_versions_artefact_key

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        if is_derived_key(clean):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_versions_artefact_key(clean) and not is_supported_source(clean):
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

    @api.put("/scopes/{scope}/derived")
    async def api_scope_derived_put(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Upload a derived blob produced by the in-browser pyodide
        converter.

        Why a dedicated endpoint: the regular ``PUT /scopes/{scope}/blobs/{key}``
        rejects writes to ``_derived/`` because that namespace is the
        server worker's domain. The pyodide pipeline produces the same
        kind of derived GLB though, just in the browser, and needs to
        land at the same key the rest of the viewer reads from
        (``_derived/<source>.<target>``). This route takes the
        (source, target) pair, derives the canonical key via the same
        ``derived_key_for`` helper the worker uses, and writes the body
        bytes there.

        Body: raw bytes of the derived blob.
        Query: ``source`` (existing source key in the scope), ``target``
        (default ``glb``).
        """
        from .converter import derived_key_for, is_supported_source

        source = (request.query_params.get("source") or "").strip().lstrip("/")
        target = (request.query_params.get("target") or "glb").strip().lstrip(".").lower()
        if not source:
            raise HTTPException(status_code=400, detail="source query param required")
        if not is_supported_source(source):
            raise HTTPException(status_code=415, detail=f"unsupported source: {source}")
        # Confirm the source exists in this scope before writing the
        # derived — otherwise the SPA could pollute the cache for a
        # source that isn't visible to the user.
        try:
            source_exists = await storage.exists(scope_obj, source)
        except Exception:
            source_exists = False
        if not source_exists:
            raise HTTPException(
                status_code=404,
                detail=f"source not found in scope: {source}",
            )

        # Same direct-upload guardrail as the source PUT path.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                announced = int(cl)
            except ValueError:
                announced = -1
            if announced > _DIRECT_UPLOAD_THRESHOLD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"derived upload exceeds {_DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
                )

        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")

        try:
            derived_key = derived_key_for(source, target)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await storage.put_bytes(scope_obj, derived_key, data)
        except Exception as exc:
            logger.exception("derived upload failed for %s", derived_key)
            await _audit(
                request, user, scope_obj, "convert",
                key=source, target_format=target, status="error", error=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        await _audit(
            request, user, scope_obj, "convert",
            key=source, target_format=target, status="done",
        )
        return JSONResponse({"key": derived_key, "size": len(data)}, status_code=201)

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
        from .converter import is_derived_key, is_supported_source, is_versions_artefact_key

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
        if not is_versions_artefact_key(key) and not is_supported_source(key):
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
        from .converter import is_derived_key, is_supported_source, is_versions_artefact_key

        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if is_derived_key(key):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_versions_artefact_key(key) and not is_supported_source(key):
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
        # Optional FEA result selection: present only for SIF picks. We
        # don't 400 if these arrive on a non-SIF source — the
        # derived_key helper just ignores them.
        raw_step = body.get("step")
        raw_field = body.get("field")
        step: int | None = None
        field: str | None = None
        if raw_step is not None and raw_field is not None:
            try:
                step = int(raw_step)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="step must be an integer") from exc
            field = str(raw_field).strip() or None
            if not field:
                raise HTTPException(status_code=400, detail="field must be non-empty")
        # Optional per-conversion overrides for the global app_settings
        # toggles (use_sat_pcurves / pcurve_drive_edge / skip_shapefix /
        # merge_meshes / profile_conversions). Unknown keys are dropped
        # rather than 400'd so a future-server-old-client mix degrades
        # to "global setting wins".
        _ALLOWED_OPTS = {
            "use_sat_pcurves",
            "pcurve_drive_edge",
            "skip_shapefix",
            "merge_meshes",
            "profile_conversions",
        }
        raw_opts = body.get("conversion_options") or {}
        conversion_options: dict | None = None
        if isinstance(raw_opts, dict) and raw_opts:
            cleaned: dict[str, str | None] = {}
            for k, v in raw_opts.items():
                if k not in _ALLOWED_OPTS:
                    continue
                if v is None:
                    cleaned[k] = None
                elif isinstance(v, bool):
                    cleaned[k] = "true" if v else "false"
                else:
                    cleaned[k] = str(v)
            if cleaned:
                conversion_options = cleaned
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
            derived_key = derived_key_for(source_key, target_format, step=step, field=field)
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
                step=step,
                field=field,
                conversion_options=conversion_options,
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

    @api.get("/scopes/{scope}/result-meta")
    async def api_scope_result_meta(
        key: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Return the (steps, fields) inventory for a FEA result file.

        Cached as ``_derived/<src>.meta.json`` after the first parse —
        SIF parsing on multi-hundred-MB decks takes 30s+ and the picker
        UI is interactive, so we do not want to recompute on every
        modal open.

        404 if the source is missing; 415 if the source isn't a FEA
        result file (the picker shouldn't ask in the first place, but
        guarding here lets the frontend treat the endpoint as the
        source of truth).
        """
        from .converter import compute_fea_meta

        source_key = (key or "").strip().lstrip("/")
        if not source_key:
            raise HTTPException(status_code=400, detail="key required")
        if not is_fea_result_key(source_key):
            raise HTTPException(
                status_code=415,
                detail=f"result-meta only applies to FEA result files; got {source_key!r}",
            )
        if not await storage.exists(scope_obj, source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")

        meta_key = fea_meta_key_for(source_key)
        try:
            cached = await storage.get_bytes(scope_obj, meta_key)
        except FileNotFoundError:
            cached = None
        except Exception:
            # Treat any cache-read hiccup as a miss; recompute is the
            # safer path than handing the user a 500 because of a stale
            # half-written meta blob.
            logger.exception("result-meta: cache read failed for %s", meta_key)
            cached = None
        if cached:
            try:
                return JSONResponse(json.loads(cached.decode("utf-8")))
            except Exception:
                logger.exception("result-meta: cache parse failed for %s; recomputing", meta_key)

        # Cache miss — pull source to a tempfile and parse on a thread.
        src_suffix = pathlib.PurePosixPath(source_key).suffix or ""
        src_fd, src_name = tempfile.mkstemp(suffix=src_suffix)
        os.close(src_fd)
        src_path = pathlib.Path(src_name)
        try:
            try:
                await storage.stream_to_path(scope_obj, source_key, src_path)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            loop = asyncio.get_running_loop()
            try:
                meta = await loop.run_in_executor(None, compute_fea_meta, src_path)
            except UnsupportedFormat as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:
                logger.exception("result-meta: parse failed for %s", source_key)
                raise HTTPException(status_code=500, detail=f"parse failed: {exc}") from exc
        finally:
            try:
                src_path.unlink()
            except OSError:
                pass

        try:
            await storage.put_bytes(
                scope_obj,
                meta_key,
                json.dumps(meta).encode("utf-8"),
            )
        except Exception:
            # Cache write is best-effort — we still return the meta we
            # just computed, just at the cost of recomputing next time.
            logger.exception("result-meta: cache write failed for %s", meta_key)
        return JSONResponse(meta)

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

    @admin.get("/settings/{key}")
    async def admin_get_setting(
        key: str,
        request: Request,
    ) -> JSONResponse:
        """Generic key/value get from app_settings. Returns
        ``{"key": k, "value": v}`` with v=null when unset."""
        pool = _require_pool(request)
        value = await db_module.get_setting(pool, key)
        return JSONResponse({"key": key, "value": value})

    @admin.post("/settings/{key}")
    async def admin_set_setting(
        key: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Upsert a setting. Body: ``{"value": "..."}``. The audit trail
        for who-flipped-what lives on the row's ``updated_by`` column."""
        pool = _require_pool(request)
        body = await request.json()
        if "value" not in body:
            raise HTTPException(status_code=400, detail="value required")
        value = "" if body["value"] is None else str(body["value"])
        await db_module.set_setting(pool, key, value, updated_by=user.sub)
        return JSONResponse({"key": key, "value": value})

    @admin.post("/auth/cli-token")
    async def admin_mint_cli_token(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Mint a 30-day bearer token bound to the current OIDC
        identity. Returned once, never stored server-side. Use it as
        ``Authorization: Bearer <token>`` from CLI / pixi tasks."""
        config = request.app.state.auth_config
        token, exp = auth_module.mint_cli_token(user, config)
        return JSONResponse({"token": token, "expires_at": exp})

    @admin.post("/auth/cli-token/revoke")
    async def admin_revoke_cli_tokens(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Revoke every CLI token previously minted for the current
        user by bumping the per-user cutoff. The OIDC bearer used for
        this request stays valid — only self-issued CLI tokens are
        affected."""
        pool = _require_pool(request)
        revoked_at = await auth_module.revoke_cli_tokens(pool, user)
        return JSONResponse({"revoked_at": revoked_at})

    @admin.get("/audit/{audit_id}")
    async def admin_audit_get(
        audit_id: int,
        request: Request,
    ) -> JSONResponse:
        """Return a single audit row's metadata. The local repro
        tooling reads ``target_format`` + ``key`` from here so it can
        invoke the converter without re-listing the whole audit log."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        return JSONResponse(row)

    @admin.get("/audit/{audit_id}/source")
    async def admin_audit_source(
        audit_id: int,
        request: Request,
    ) -> StreamingResponse:
        """Download the original source blob referenced by an audit
        row. Mirrors the profile-download pattern but resolves
        ``scope_kind/scope_id + key`` instead of ``profile_key`` —
        useful for reproducing a failed conversion locally without
        having to know the storage scope. 404 when the row is missing
        or the blob is gone (e.g. expired ephemeral storage)."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        key = row.get("key")
        if not key:
            raise HTTPException(status_code=404, detail="audit row has no source key")
        scope = (
            Scope.shared()
            if row["scope_kind"] == "shared"
            else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
        )
        try:
            result = await storage.open_stream(scope, key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        filename = key.rsplit("/", 1)[-1]
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        if result.content_encoding:
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(
            result.stream, media_type="application/octet-stream", headers=headers
        )

    @admin.get("/audit/{audit_id}/profile")
    async def admin_audit_profile(
        audit_id: int,
        request: Request,
    ) -> StreamingResponse:
        """Download the cProfile dump attached to an audit row, if
        any. 404 when the row or its profile_key is missing."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        profile_key = row.get("profile_key")
        if not profile_key:
            raise HTTPException(status_code=404, detail="no profile attached to this row")
        scope = (
            Scope.shared()
            if row["scope_kind"] == "shared"
            else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
        )
        try:
            result = await storage.open_stream(scope, profile_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # .prof is binary cProfile output (marshal-formatted). Browsers
        # download it as-is — snakeviz / speedscope load directly.
        filename = profile_key.rsplit("/", 1)[-1]
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
        }
        if result.content_encoding:
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(
            result.stream, media_type="application/octet-stream", headers=headers
        )

    @admin.get("/audit/{audit_id}/metrics-history")
    async def admin_audit_metrics_history(
        audit_id: int,
        request: Request,
    ) -> JSONResponse:
        """Return the per-heartbeat resource samples captured by the
        worker subprocess wrapper. One sample per ~2 s while the
        convert child was alive — RSS, CPU user/sys, IO bytes, all
        time-aligned by ``elapsed_s``. The SPA renders these as a
        time-series chart in the audit details modal so an operator
        sees memory growth + CPU pressure as the run progresses.

        Empty array when the row pre-dates the subprocess wrapper or
        the worker pod was killed before it could append. ``None``
        from the DB collapses to ``[]`` here so the chart renders an
        explicit "no data" state rather than crashing on null."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        samples = row.get("metrics_samples") or []
        return JSONResponse({"audit_id": audit_id, "samples": samples})

    @admin.get("/audit/{audit_id}/profile/stats")
    async def admin_audit_profile_stats(
        audit_id: int,
        request: Request,
        limit: int = 500,
    ) -> JSONResponse:
        """Server-side parse of the .prof for the SPA dashboard. Returns
        a JSON list of per-function rows the table can sort/filter
        without dragging pstats / snakeviz / a marshal parser into the
        browser. ``.prof`` download stays available alongside.

        Each row carries: function name, file:line, ncalls, primitive
        ncalls, total time (excluding sub-calls), per-call total,
        cumulative time (including sub-calls), per-call cumulative.
        """
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        profile_key = row.get("profile_key")
        if not profile_key:
            raise HTTPException(status_code=404, detail="no profile attached to this row")
        scope = (
            Scope.shared()
            if row["scope_kind"] == "shared"
            else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
        )
        # pstats only reads from disk, so stash the bytes in a tempfile.
        try:
            data = await storage.get_bytes(scope, profile_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        import pstats
        import tempfile
        import pathlib as _pl
        tmp = _pl.Path(tempfile.mkstemp(suffix=".prof")[1])
        try:
            tmp.write_bytes(data)
            try:
                stats = pstats.Stats(str(tmp))
            except Exception as exc:
                raise HTTPException(
                    status_code=500, detail=f"failed to parse profile: {exc}"
                ) from exc
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        # stats.stats: dict[(filename, lineno, funcname), (cc, nc, tt, ct, callers)].
        rows = []
        total_tt = 0.0
        for (fn, line, name), (cc, nc, tt, ct, _callers) in stats.stats.items():
            total_tt += tt
            rows.append({
                "func": name,
                "file": fn,
                "line": line,
                "ncalls": nc,
                "primitive_calls": cc,
                "tottime": tt,
                "percall_tot": (tt / nc) if nc else 0.0,
                "cumtime": ct,
                "percall_cum": (ct / cc) if cc else 0.0,
            })
        # Default presentation sort: cumtime desc — same as pstats default.
        rows.sort(key=lambda r: r["cumtime"], reverse=True)
        if limit and len(rows) > limit:
            rows = rows[:limit]
        return JSONResponse({
            "audit_id": audit_id,
            "total_tottime": total_tt,
            "row_count": len(rows),
            "rows": rows,
        })

    @admin.delete("/audit/metrics")
    async def admin_clear_metrics(request: Request) -> JSONResponse:
        """Wipe all metrics + profile blobs. Audit rows themselves
        stay; only the metrics columns are nulled and the .prof blobs
        deleted from storage. Used to reclaim DB / object-store space
        after a profiling session."""
        pool = _require_pool(request)
        result = await db_module.clear_audit_metrics(pool)
        deleted_blobs = 0
        blob_errors: list[str] = []
        for entry in result["profile_keys"]:
            try:
                scope = (
                    Scope.shared()
                    if entry["scope_kind"] == "shared"
                    else Scope(kind=entry["scope_kind"], id=entry["scope_id"])  # type: ignore[arg-type]
                )
                await storage.delete(scope, entry["profile_key"])
                deleted_blobs += 1
            except FileNotFoundError:
                # Already gone — fine.
                deleted_blobs += 1
            except Exception as exc:
                logger.warning(
                    "clear_metrics: failed to delete %s: %s",
                    entry["profile_key"], exc,
                )
                blob_errors.append(f"{entry['profile_key']}: {exc}")
        return JSONResponse({
            "rows_cleared": result["rows_cleared"],
            "profiles_deleted": deleted_blobs,
            "errors": blob_errors,
        })

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
    async def admin_projects_create(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
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
        # Auto-add the creator as owner so the new project shows up in
        # their /api/me.scopes immediately. Without this the project is
        # orphaned until an admin manually adds someone — easy to forget,
        # and it leaves the creator unable to push artefacts to the
        # project they just made.
        await db_module.add_project_member(pool, project["id"], user.sub, role="owner")
        project["member_count"] = 1
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

    @admin.post("/projects/{project_id}/ci-bot")
    async def admin_provision_ci_bot(
        project_id: str,
        request: Request,
    ) -> JSONResponse:
        """Provision (or rotate the token of) a CI bot user for a project.

        One-shot: creates the bot user row if missing, ensures it's a
        project member, revokes any prior tokens, and mints a fresh
        30-day CLI bearer. The bot's ``sub`` is derived from the
        project's slug (``ci:<slug>``) so a project rename is the only
        way to change it.

        The token is returned exactly once. Re-calling rotates: prior
        tokens for this bot are immediately invalidated via the per-user
        revoke cutoff. Always admin-gated.
        """
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")

        row = await pool.fetchrow(
            "SELECT slug FROM projects WHERE id = $1 AND archived_at IS NULL",
            pid,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        slug = row["slug"]
        bot_sub = f"ci:{slug}"
        bot_email = f"ci+{slug}@bot.local"
        bot_display = f"CI Bot: {slug}"

        await db_module.upsert_user(pool, bot_sub, bot_email, bot_display)
        await db_module.add_project_member(pool, pid, bot_sub, role="ci")

        bot_user = User(
            sub=bot_sub,
            email=bot_email,
            display_name=bot_display,
            groups=frozenset(),
            is_admin=False,
        )
        # Rotate: invalidate any tokens minted before now for this bot,
        # then mint a fresh one. The cutoff is iat-based so the token
        # we're about to mint (with a fresh iat) survives.
        await auth_module.revoke_cli_tokens(pool, bot_user)
        config = request.app.state.auth_config
        token, exp = auth_module.mint_cli_token(bot_user, config)
        return JSONResponse(
            {
                "user_sub": bot_sub,
                "token": token,
                "expires_at": exp,
            },
            status_code=201,
        )

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

        viewer_tag = os.environ.get("ADA_IMAGE_TAG", "").strip() or None
        worker_tag: str | None = None
        if queue.enabled:
            try:
                worker_tag = await queue.get_meta("worker_image_tag")
            except Exception:
                logger.exception("config.js: failed to read worker image tag")

        a = settings.auth
        body = (
            'window.COMMS_MODE = "rest";\n'
            'window.API_BASE = "/api";\n'
            f'window.CONVERT_ENABLED = {"true" if queue.enabled else "false"};\n'
            f'window.AUTH_ENABLED = {"true" if a.enabled else "false"};\n'
            f"window.AUTH_ISSUER = {_json.dumps(a.issuer)};\n"
            f"window.AUTH_CLIENT_ID = {_json.dumps(a.client_id)};\n"
            f"window.AUTH_AUDIENCE = {_json.dumps(a.audience)};\n"
            f"window.VIEWER_IMAGE_TAG = {_json.dumps(viewer_tag)};\n"
            f"window.WORKER_IMAGE_TAG = {_json.dumps(worker_tag)};\n"
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
