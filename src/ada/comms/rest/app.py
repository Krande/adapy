from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
)
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
from .converter import (
    LEGACY_CONVERT_EXTS,
    TARGET_FORMATS,
    ConverterRegistry,
    UnsupportedFormat,
    derived_key_for,
    fea_artefact_manifest_key_for,
    fea_artefact_prefix_for,
    fea_meta_key_for,
    is_fea_artefact_source,
    is_fea_result_key,
    is_supported_source,
    supported_targets_for,
)
from .handlers import dispatch
from .queue import JobQueue
from .scope import Scope
from .scope import can_access as scope_can_access
from .storage import Storage
from .storage_ops import (
    delete_blob_cascade,
    derived_source_of,
    move_keys_to_folder,
    rename_key_cascade,
)

# Text-heavy CAD/FEM formats compress 5–10× with gzip; binary mesh
# formats already pack their geometry tightly so we skip them. The
# storage layer transparently decompresses on read; the download
# endpoint forwards Content-Encoding: gzip so browsers handle it on
# the user's machine. ada.from_<format> in the worker sees the original
# bytes via Storage.get_bytes.
_GZIP_UPLOAD_EXTS: frozenset[str] = frozenset(
    {".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis", ".sif"}
    # .sin is already binary (Norsam direct-access) so gzip rarely
    # helps and the slim API container's allowlist gates uploads
    # separately via FEA_ARTEFACT_SOURCE_EXTS — see converter.py.
)

# Hard cap on the regular API-buffered upload path. Above this we make
# the client request a presigned URL and PUT directly at the object
# store, so the API process never sees the bytes. 200 MB is high enough
# for typical IFC/Genie XML/STEP work and low enough that buffering it
# in Python doesn't blow the worker's RAM budget.
_DIRECT_UPLOAD_THRESHOLD_BYTES: int = 200 * 1024 * 1024


async def _parse_move_body(request: Request) -> tuple[list[str], str]:
    """Validate the ``{"keys": [...], "folder": "..."}`` move payload."""
    body = await request.json()
    raw_keys = body.get("keys")
    folder_raw = body.get("folder")

    if not isinstance(raw_keys, list) or not raw_keys:
        raise HTTPException(status_code=400, detail="keys must be a non-empty list")
    if any(not isinstance(k, str) or not k.strip() for k in raw_keys):
        raise HTTPException(status_code=400, detail="every key must be a non-empty string")
    if not isinstance(folder_raw, str) or not folder_raw.strip():
        raise HTTPException(status_code=400, detail="folder required")
    folder = folder_raw.strip().strip("/")
    if not folder:
        raise HTTPException(status_code=400, detail="folder required")
    return raw_keys, folder


async def _parse_rename_body(request: Request) -> tuple[str, str]:
    """Validate the ``{"old_key": str, "new_key": str}`` rename payload."""
    body = await request.json()
    old_raw = body.get("old_key")
    new_raw = body.get("new_key")

    if not isinstance(old_raw, str) or not old_raw.strip():
        raise HTTPException(status_code=400, detail="old_key required")
    if not isinstance(new_raw, str) or not new_raw.strip():
        raise HTTPException(status_code=400, detail="new_key required")
    old_key = old_raw.strip().lstrip("/")
    new_key = new_raw.strip().lstrip("/")
    if not old_key or not new_key:
        raise HTTPException(status_code=400, detail="old_key and new_key required")
    if new_key.endswith("/"):
        raise HTTPException(status_code=400, detail="new_key must not end with /")
    if new_key == old_key:
        raise HTTPException(status_code=400, detail="new_key matches old_key")
    return old_key, new_key


def _content_encoding_for(key: str) -> str | None:
    return "gzip" if pathlib.PurePosixPath(key).suffix.lower() in _GZIP_UPLOAD_EXTS else None


_ADAPY_VERSION: str | None = None


def _resolve_adapy_version() -> str:
    """adapy version for the viewer's config.js (window.ADAPY_VERSION). Resolved once.

    The viewer image copies adapy *source* (no installed-distribution metadata) and runs a
    stripped ``ada/__init__`` (no ``__version__``), so neither ``importlib.metadata`` nor
    ``ada.__version__`` resolves there. Fall back to the shipped ``pyproject.toml`` — the single
    source of truth. Order: explicit env, a real install's ``ada.__version__``, then pyproject.
    """
    global _ADAPY_VERSION
    if _ADAPY_VERSION is not None:
        return _ADAPY_VERSION

    import re

    version = (os.environ.get("ADAPY_VERSION") or "").strip()
    if not version:
        try:
            import ada as _ada

            v = (getattr(_ada, "__version__", "") or "").strip()
            if v and v != "0.0.0":
                version = v
        except Exception:  # noqa: BLE001 — version is display-only
            pass
    if not version:
        here = pathlib.Path(__file__).resolve()
        for base in (pathlib.Path("/app"), *here.parents):
            pp = base / "pyproject.toml"
            try:
                if pp.is_file():
                    m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', pp.read_text(encoding="utf-8"))
                    if m:
                        version = m.group(1)
                        break
            except Exception:  # noqa: BLE001
                pass

    _ADAPY_VERSION = version
    return version


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
        # Built-in audit scheduler (M4). Skip when the DB pool failed
        # or when the queue is disabled — without either we have no
        # way to fire a sweep, so spinning the loop would just log
        # warnings every 30s. The task is cancelled at shutdown.
        app.state.scheduler_task = None
        if app.state.db_pool is not None and queue.enabled:
            app.state.scheduler_task = asyncio.create_task(
                _scheduler_loop(app.state.db_pool),
                name="audit-scheduler",
            )
        # Issue-bot poller (M5). Only needs the DB pool — the bot
        # talks to an HTTP forge, not NATS, so a queue-less deploy
        # can still publish failure issues. Skipped without a pool.
        app.state.issue_bot_task = None
        if app.state.db_pool is not None:
            app.state.issue_bot_task = asyncio.create_task(
                _issue_bot_loop(app.state.db_pool),
                name="audit-issue-bot",
            )
        # Profile hotspot parser (M7). Pulls each new ``.prof`` blob
        # produced by the conversion worker, extracts the top-K
        # functions by cumtime, and lands them in
        # ``profile_function_stats`` so the perf dashboard's
        # hotspots view can GROUP BY across runs without round-
        # tripping through pstats at query time. Idle if profiling
        # is disabled — there'll just be no rows to claim.
        app.state.profile_parser_task = None
        if app.state.db_pool is not None:
            app.state.profile_parser_task = asyncio.create_task(
                _profile_parser_loop(app.state.db_pool),
                name="audit-profile-parser",
            )
        yield
        # Cancel scheduler + issue bot first so a tick in flight
        # doesn't try to use a pool / queue that's about to be torn
        # down.
        for attr in (
            "scheduler_task",
            "issue_bot_task",
            "profile_parser_task",
        ):
            task = getattr(app.state, attr, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    # CancelledError is the normal shutdown path; any
                    # other exception we want to see in the logs but
                    # not block the rest of teardown.
                    logger.debug("background task %s cancelled", attr)
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

    async def _is_accepted_source(key: str) -> bool:
        """``is_supported_source`` plus a check against the workers'
        advertised extra extensions. Use this on every upload / bake
        endpoint that needs to gate "is this file something we can
        actually process" — the static check alone misses extensions
        contributed by capability workers."""
        if is_supported_source(key):
            return True
        ext = pathlib.PurePosixPath(key).suffix.lower()
        return ext in await _worker_advertised_exts()

    async def _worker_advertised_exts() -> list[str]:
        """Union of source-file extensions advertised by every
        currently-registered worker via its registry entry's
        ``source_exts`` field.

        adapy itself doesn't know what extensions any particular
        worker brings — the worker introspects its own
        stream-reader registry at startup (whatever plug-ins ran
        before ``ada.comms.rest.worker`` connected) and publishes the
        resulting suffix set. ``/api/config`` then merges every
        online worker's list so the upload picker can include them
        without anything outside the plug-in repeating the list.
        Workers that fall off the heartbeat (online=false) still
        contribute briefly; the goal is to keep the picker stable
        across pod restarts, not to gate on liveness.

        Returns a sorted, lowercased list with a leading dot on each
        entry — ready to feed into the existing extension-check call
        sites without further normalisation.
        """
        if not queue.enabled:
            return []
        try:
            workers = await queue.list_workers()
        except Exception:
            logger.exception("config: failed to read worker registry")
            return []
        out: set[str] = set()
        for w in workers:
            for raw in w.get("source_exts") or []:
                if not isinstance(raw, str):
                    continue
                ext = raw.strip().lower()
                if not ext:
                    continue
                if not ext.startswith("."):
                    ext = f".{ext}"
                out.add(ext)
        return sorted(out)

    async def _worker_advertised_conversions() -> list[dict]:
        """Merged conversion matrix across every currently-registered
        worker.

        Each worker publishes its own ``conversions: [{from, to:
        [...]}, ...]`` matrix on its NATS KV record (see
        ``worker.py``). This helper unions the per-worker
        per-source target lists into a single matrix so the SPA can
        render the /convert page's target dropdown without caring
        which worker pool will end up picking up the job.

        Returns a sorted list of ``{"from": ".step", "to": ["glb",
        "ifc", ...]}`` entries — same shape ConverterRegistry.matrix()
        produces, just aggregated across workers. Empty list when
        the queue is disabled (dev / desktop mode) or no worker has
        registered yet.
        """
        if not queue.enabled:
            return []
        try:
            workers = await queue.list_workers()
        except Exception:
            logger.exception("config: failed to read worker registry for matrix")
            return []
        merged: dict[str, set[str]] = {}
        for w in workers:
            for entry in w.get("conversions") or []:
                if not isinstance(entry, dict):
                    continue
                frm = (entry.get("from") or "").strip().lower()
                if not frm:
                    continue
                if not frm.startswith("."):
                    frm = f".{frm}"
                tos = entry.get("to")
                if not isinstance(tos, list):
                    continue
                bucket = merged.setdefault(frm, set())
                for t in tos:
                    if isinstance(t, str) and t.strip():
                        bucket.add(t.strip().lstrip(".").lower())
        return [{"from": frm, "to": sorted(merged[frm])} for frm in sorted(merged)]

    async def _worker_advertised_utilities() -> list[dict]:
        """Merged utility specs across every currently-registered worker.

        Each worker publishes ``utilities: [{name, description, kwargs, inputs,
        affects, returns}, ...]`` on its NATS KV record (see worker.py). We dedupe
        by name (first writer wins) so the SPA's Utilities panel lists each once,
        regardless of how many worker pods advertise it. Empty when the queue is
        disabled or no worker has registered yet.
        """
        if not queue.enabled:
            return []
        try:
            workers = await queue.list_workers()
        except Exception:
            logger.exception("config: failed to read worker registry for utilities")
            return []
        by_name: dict[str, dict] = {}
        for w in workers:
            for spec in w.get("utilities") or []:
                if isinstance(spec, dict) and isinstance(spec.get("name"), str):
                    by_name.setdefault(spec["name"], spec)
        return [by_name[n] for n in sorted(by_name)]

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
        extra_source_exts = await _worker_advertised_exts()
        # Subset of stream-readable extensions that the legacy /convert
        # pipeline does NOT handle. The SPA uses this to pick between
        # /convert (auto-GLB preview) and /fea/manifest (streaming
        # bake) at upload time — feeding /convert one of these would
        # 415. .sif is stream-readable AND legacy-convertable so it
        # falls out of this set and continues to get the eager GLB
        # preview path.
        streaming_only_exts = sorted(e for e in extra_source_exts if e not in LEGACY_CONVERT_EXTS)
        # Merged conversion matrix across live workers. The /convert
        # page reads this to populate the target dropdown per source
        # extension. Empty in dev / desktop mode (queue disabled) or
        # when no worker has registered yet; the SPA falls back to a
        # narrower static set in that case.
        conversion_matrix = await _worker_advertised_conversions()
        # Worker-advertised utilities (Utilities panel in the scene component).
        utilities = await _worker_advertised_utilities()
        return JSONResponse(
            {
                "transport": "rest",
                "apiBase": "/api",
                "convertEnabled": queue.enabled,
                "utilities": utilities,
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
                "extraSourceExts": extra_source_exts,
                "streamingOnlyExts": streaming_only_exts,
                "conversionMatrix": conversion_matrix,
            }
        )

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
            pid = s[len("project:") :].strip()
            if not pid:
                raise HTTPException(status_code=400, detail="missing project id")
            return Scope.project(pid)
        if s.startswith("corpus:"):
            slug = s[len("corpus:") :].strip()
            if not slug:
                raise HTTPException(status_code=400, detail="missing corpus slug")
            # Admin-only gate fires in scope_can_access; here we just
            # parse. Non-admin requests hit a 403 at the access check.
            return Scope.corpus(slug)
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
        request: Request | None,
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
        audit_run_id: str | None = None,
        pool=None,
    ) -> None:
        """Best-effort audit row insert. No-ops without DB; never raises.

        Audit failures must not break user requests — a missing log line
        is preferable to a 500 on a successful upload.

        ``audit_run_id`` links the row to an admin-triggered audit
        sweep so the dispatcher can show per-cell pass/fail in the
        admin panel. NULL on every user-driven action.

        ``request`` is the FastAPI request when called from a route
        handler; the dispatcher / scheduler tick / issue-bot pass
        ``None`` instead and provide ``pool`` directly, since they
        run outside a request lifecycle. Either path is acceptable —
        we pick whichever pool source is available.
        """
        if pool is None and request is not None:
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
                audit_run_id=audit_run_id,
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
        # Pull the worker-advertised extension set so the file lister
        # can flag plug-in formats (e.g. .odb when an abaqus-capability
        # worker is online) instead of silently dropping them. Cheap
        # NATS KV read; failures degrade to the static list.
        try:
            extra_source_exts = frozenset(await _worker_advertised_exts())
        except Exception:
            logger.exception("rpc: failed to read worker-advertised exts")
            extra_source_exts = frozenset()
        try:
            reply = await dispatch(payload, storage, scope, extra_source_exts=extra_source_exts)
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
        return JSONResponse({"projects": [{"id": p.id, "slug": p.slug, "name": p.name, "role": p.role} for p in rows]})

    # ── Scope-shaped storage + conversion routes ─────────────────────

    @api.get("/scopes/{scope}/files")
    async def api_scope_files(
        scope_obj: Scope = Depends(_scope_from_path),
        include_derived: bool = False,
    ) -> JSONResponse:
        from .converter import is_derived_key, supported_targets_for

        files = await storage.list(scope_obj)

        if not include_derived:
            # Default — hide the _derived/ namespace. Those blobs are
            # an internal cache, not user files. Convert + download
            # surfaces them explicitly when needed.
            return JSONResponse(
                {
                    "files": [{"key": f.key, "size": f.size} for f in files if not is_derived_key(f.key)],
                }
            )

        # Convert-page mode — group every derived blob under its
        # source so the page can list pre-existing conversions next
        # to fresh upload rows. Same grouping the admin storage list
        # builds; same helpers reused (``derived_source_of`` parses
        # the full derived-key zoo including the streaming-FEA tree
        # and SIF step/field picks). Orphans (derived without a
        # source in this scope) are dropped here — the admin tab is
        # where you go to clean those up.
        sources: dict[str, dict] = {}
        derived_index: dict[str, list[dict]] = {}
        for f in files:
            if is_derived_key(f.key):
                parsed = derived_source_of(f.key)
                if parsed is None:
                    continue
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
            if entry is not None:
                entry["derived"] = derived_list
        out = sorted(
            sources.values(),
            key=lambda e: e.get("last_modified") or "",
            reverse=True,
        )
        return JSONResponse({"files": out})

    async def _serve_blob_range(request: Request, scope_obj: Scope, key: str, range_header: str) -> Response | None:
        """Serve a single byte range of an identity-stored object as 206.

        Returns ``None`` (caller serves the whole object) when the range
        can't be honoured: a gzip-at-rest object, a malformed/multi-range
        header, or an unsatisfiable window. Only a single ``bytes=a-b``
        range is supported — that's all the FEA per-step fetch needs."""
        spec = range_header.strip()
        if not spec.lower().startswith("bytes=") or "," in spec:
            return None  # only single-range bytes= supported
        rng = spec[len("bytes=") :].strip()
        if "-" not in rng:
            return None
        start_s, end_s = rng.split("-", 1)
        try:
            # Ranges over a gzipped body would hand back compressed bytes;
            # let the caller serve the whole object instead.
            if await storage.is_gzip_stored(scope_obj, key):
                return None
            meta = await storage.head(scope_obj, key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"not found: {key}")
        if meta is None:
            raise HTTPException(status_code=404, detail=f"not found: {key}")
        size = int(meta["size"])
        try:
            if start_s == "":
                # suffix range: bytes=-N → last N bytes
                n = int(end_s)
                start = max(0, size - n)
                end = size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s != "" else size - 1
        except ValueError:
            return None
        if start < 0 or start >= size or end < start:
            # 416 Range Not Satisfiable
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        length = end - start + 1
        try:
            chunk = await storage.get_range(scope_obj, key, start, length)
        except Exception as exc:
            logger.warning("blob range fetch failed for %s [%d,%d]: %s", key, start, end, exc)
            return None
        # Content-Length is left to Starlette (derived from the body) so it
        # can never disagree with the payload — a mismatch makes some
        # reverse proxies emit a broken response ("Failed to fetch").
        return Response(
            content=chunk,
            status_code=206,
            media_type="application/octet-stream",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{size}",
            },
        )

    @api.get("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_get(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> Response:
        from .converter import is_derived_key

        # Range support — lets the FEA viewer pull a single step out of a
        # multi-step field blob instead of downloading the whole stack of
        # steps. Only valid for identity-stored objects; gzip-at-rest blobs
        # (manifest JSON, legacy field blobs) are served whole with
        # Content-Encoding so the browser auto-decompresses.
        #
        # The range can arrive two ways: the standard ``Range`` header, or
        # ``?range_start=&range_end=`` query params. The query-param form is
        # proxy-proof — some ingresses/CDNs (notably on the mobile path)
        # strip the Range *header*, which would silently fall back to a
        # whole-blob download; a query string always survives.
        qp = request.query_params
        range_header = request.headers.get("range")
        if "range_start" in qp:
            rs = (qp.get("range_start") or "").strip()
            re_ = (qp.get("range_end") or "").strip()
            if rs:
                range_header = f"bytes={rs}-{re_}"
        if range_header:
            served = await _serve_blob_range(request, scope_obj, key, range_header)
            if served is not None:
                if not is_derived_key(key):
                    await _audit(request, user, scope_obj, "download", key=key, status="ok")
                return served
            # else: fall through to whole-object stream (gzipped/un-rangeable)

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
        if not is_derived_key(key):
            await _audit(request, user, scope_obj, "download", key=key, status="ok")
        headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        if result.content_encoding:
            # See storage.py: gzipped sources/derived round-trip via
            # Content-Encoding so the browser auto-decompresses.
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)

    @api.put("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_put(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .converter import is_derived_key, is_versions_artefact_key

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        if is_derived_key(clean):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_versions_artefact_key(clean) and not await _is_accepted_source(clean):
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

    # ── User-level file management (personal scope only) ────────────
    # Regular users manage their own files; shared/project scopes stay
    # admin-managed (project scopes mix the CI versions/ tree with
    # regular files and get their own treatment later). CI version
    # blobs and the bake cache are protected even inside the personal
    # scope — deleting a source still cascades its derived blobs via
    # the shared storage_ops helpers.

    def _require_personal(scope_obj: Scope) -> None:
        if scope_obj.kind != "user":
            raise HTTPException(
                status_code=403,
                detail="file management is personal-scope only",
            )

    def _reject_protected_key(key: str) -> None:
        from .converter import is_derived_key, is_versions_artefact_key

        if is_derived_key(key) or is_versions_artefact_key(key):
            raise HTTPException(
                status_code=400,
                detail="versions/ and _derived/ keys are admin-managed",
            )

    @api.delete("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_delete(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        _require_personal(scope_obj)
        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        _reject_protected_key(clean)
        result = await delete_blob_cascade(storage, scope_obj, clean)
        await _audit(
            request,
            user,
            scope_obj,
            "delete",
            key=clean,
            status="ok",
            error="; ".join(result["errors"]) or None,
        )
        return JSONResponse(result)

    @api.post("/scopes/{scope}/keys/move-to-folder")
    async def api_scope_keys_move_to_folder(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        _require_personal(scope_obj)
        keys, folder = await _parse_move_body(request)
        _reject_protected_key(folder + "/")
        for k in keys:
            _reject_protected_key(k.strip().lstrip("/"))
        result = await move_keys_to_folder(storage, scope_obj, keys, folder)
        for entry in result["moved"]:
            await _audit(
                request,
                user,
                scope_obj,
                "move",
                key=entry["old"],
                status="ok",
                error="; ".join(entry["siblings_failed"]) or None,
            )
        return JSONResponse(result)

    @api.post("/scopes/{scope}/keys/rename")
    async def api_scope_keys_rename(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        _require_personal(scope_obj)
        old_key, new_key = await _parse_rename_body(request)
        _reject_protected_key(old_key)
        _reject_protected_key(new_key)
        result = await _rename_with_status(scope_obj, old_key, new_key)
        await _audit(request, user, scope_obj, "rename", key=old_key, status="ok")
        return JSONResponse(result)

    async def _rename_with_status(scope_obj: Scope, old_key: str, new_key: str) -> dict:
        """Run a single cascade rename, mapping helper failures to HTTP errors."""
        live_keys = {f.key for f in await storage.list(scope_obj)}
        result = await rename_key_cascade(storage, scope_obj, old_key, new_key, live_keys)
        if "reason" in result:
            reason = result["reason"]
            if reason == "source not found":
                raise HTTPException(status_code=404, detail=reason)
            if reason.startswith("target already exists"):
                raise HTTPException(status_code=409, detail=reason)
            raise HTTPException(status_code=400, detail=reason)
        return result

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
        from .converter import derived_key_for

        source = (request.query_params.get("source") or "").strip().lstrip("/")
        target = (request.query_params.get("target") or "glb").strip().lstrip(".").lower()
        # When the caller drives its own audit lifecycle via the
        # ``audit/local`` endpoints (the WASM pipeline, which records a
        # metrics-rich two-phase row), suppress the auto-audit here so a
        # single conversion doesn't produce two audit_log rows.
        managed_audit = (request.query_params.get("managed_audit") or "").strip().lower() in ("1", "true", "yes")
        if not source:
            raise HTTPException(status_code=400, detail="source query param required")
        if not await _is_accepted_source(source):
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
            if not managed_audit:
                await _audit(
                    request,
                    user,
                    scope_obj,
                    "convert",
                    key=source,
                    target_format=target,
                    status="error",
                    error=str(exc),
                )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if not managed_audit:
            await _audit(
                request,
                user,
                scope_obj,
                "convert",
                key=source,
                target_format=target,
                status="done",
            )
        return JSONResponse({"key": derived_key, "size": len(data)}, status_code=201)

    # ── Browser-driven (WASM) conversion audit ────────────────────────
    #
    # The in-browser pyodide engine runs conversions with no NATS job, so
    # it can't ride the worker's queued→running→done audit lifecycle.
    # These two routes give it parity: open a 'running' row (returns a
    # ``wasm-<uuid>`` job_id), then patch it terminal with metrics. The
    # ``wasm-`` job_id prefix + ``wasm:`` image tag let the audit panel
    # tell in-browser rows from worker rows; the ``wasm-`` guard on the
    # update stops a browser from mutating a worker's row.
    _WASM_JOB_PREFIX = "wasm-"

    @api.post("/scopes/{scope}/audit/local")
    async def api_scope_audit_local_create(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Open an audit row for an in-browser (WASM) conversion.

        Body (JSON): ``{key, target_format, audit_run_id?, image_tag?}``.
        ``audit_run_id`` attaches the row to an admin audit-run sweep
        (section F) and is admin-only. Returns ``{job_id}``.
        """
        import uuid

        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            raise HTTPException(status_code=503, detail="audit requires a database")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        key = (str(body.get("key") or "")).strip().lstrip("/") or None
        target_format = (str(body.get("target_format") or "")).strip().lstrip(".").lower() or None
        image_tag = body.get("image_tag")
        worker_image_tag = (
            image_tag if (isinstance(image_tag, str) and image_tag.startswith("wasm:")) else "wasm:unknown"
        )
        audit_run_id = body.get("audit_run_id")
        if audit_run_id is not None:
            audit_run_id = str(audit_run_id).strip() or None
        if audit_run_id is not None:
            if not getattr(user, "is_admin", False):
                raise HTTPException(status_code=403, detail="audit_run_id requires admin")
            # audit_run_id is a UUID column; a malformed value would raise
            # a DataError deep in asyncpg. Treat any lookup failure as
            # "no such run" so a bad id is a clean 404, not a 500.
            try:
                run = await db_module.get_audit_run(pool, audit_run_id)
            except Exception:
                run = None
            if run is None:
                raise HTTPException(status_code=404, detail="audit run not found")

        job_id = _WASM_JOB_PREFIX + uuid.uuid4().hex
        try:
            await db_module.insert_audit(
                pool,
                user_sub=user.sub,
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                action="convert",
                key=key,
                target_format=target_format,
                status="running",
                job_id=job_id,
                audit_run_id=audit_run_id,
                worker_image_tag=worker_image_tag,
            )
        except Exception as exc:
            logger.exception("audit/local create failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"job_id": job_id}, status_code=201)

    @api.post("/scopes/{scope}/audit/local/{job_id}")
    async def api_scope_audit_local_update(
        job_id: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Patch a WASM conversion's audit row to its terminal outcome.

        Body (JSON): ``{status, error?, traceback?, duration_ms?,
        read_bytes?, write_bytes?, peak_rss_kb?, metrics_samples?}``.
        ``status`` ∈ {done, ok, error, skipped, cancelled}. Guarded so a
        browser can only patch its own ``wasm-`` rows.
        """
        # Reject a malformed id before touching infra so the guard is
        # independent of DB availability.
        if not job_id.startswith(_WASM_JOB_PREFIX):
            raise HTTPException(status_code=400, detail="job_id must be a wasm- local id")
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            raise HTTPException(status_code=503, detail="audit requires a database")
        owner = await db_module.get_audit_owner_by_job(pool, job_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="audit row not found")
        # Ownership: the row's creator, or an admin (audit-run sweeps).
        if owner["user_sub"] != user.sub and not getattr(user, "is_admin", False):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        status = (str(body.get("status") or "")).strip().lower()
        _allowed = {"done", "ok", "error", "skipped", "cancelled"}
        if status not in _allowed:
            raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_allowed)}")

        def _int_or_none(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        try:
            await db_module.update_audit_by_job(
                pool,
                job_id=job_id,
                status=status,
                error=(str(body["error"]) if body.get("error") is not None else None),
                duration_ms=_int_or_none(body.get("duration_ms")),
                traceback=(str(body["traceback"]) if body.get("traceback") is not None else None),
                peak_rss_kb=_int_or_none(body.get("peak_rss_kb")),
                read_bytes=_int_or_none(body.get("read_bytes")),
                write_bytes=_int_or_none(body.get("write_bytes")),
            )
        except Exception as exc:
            logger.exception("audit/local update failed for %s", job_id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # Optional heartbeat samples — best-effort, must never fail the call.
        samples = body.get("metrics_samples")
        if isinstance(samples, list):
            for s in samples:
                if not isinstance(s, dict):
                    continue
                try:
                    await db_module.append_metrics_sample_by_job(pool, job_id=job_id, sample=s)
                except Exception:
                    logger.exception("audit/local: metrics sample append failed for %s", job_id)
        return JSONResponse({"ok": True})

    @api.post("/scopes/{scope}/fea/artefacts")
    async def api_scope_fea_artefacts_upload(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Upload a browser-baked FEA artefact tree (section D).

        The pyodide FEM stack runs ``bake_fea_artefacts_from_source`` in
        the browser and zips the output dir; the body is that raw zip.
        Each entry (``fea.manifest.json``, ``fea.mesh.glb``,
        ``fea.<field>.bin``, ...) is written under the canonical
        ``_derived/<source>.fea/`` prefix with the *same* gzip policy the
        worker uses (``storage.put_bytes`` compresses ``.json``/``.bin``;
        the mesh GLB is stored as-is), so the existing streaming-FEA
        reader consumes it unchanged.

        Query: ``source`` (existing source key in the scope).
        """
        import io
        import posixpath
        import zipfile

        source = (request.query_params.get("source") or "").strip().lstrip("/")
        if not source:
            raise HTTPException(status_code=400, detail="source query param required")
        # Gate on the FEA-artefact source set (.rmed/.sif/...), the same
        # predicate the GET /fea/manifest worker route uses — not the
        # general convert-source check, since these sources have no
        # convert-registry target and the browser path runs worker-free.
        if not is_fea_artefact_source(source):
            raise HTTPException(status_code=415, detail=f"not a FEA artefact source: {source}")
        try:
            source_exists = await storage.exists(scope_obj, source)
        except Exception:
            source_exists = False
        if not source_exists:
            raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")

        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                announced = int(cl)
            except ValueError:
                announced = -1
            if announced > _DIRECT_UPLOAD_THRESHOLD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"fea artefact upload exceeds {_DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
                )

        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail=f"body is not a valid zip: {exc}") from exc

        # Each entry must be a bare ``fea.*`` filename — no subdirs, no
        # path traversal — so a crafted zip can't escape the per-source
        # prefix and write arbitrary keys.
        entries = [n for n in zf.namelist() if not n.endswith("/")]
        for n in entries:
            base = posixpath.basename(n)
            if base != n or not base or base.startswith(".") or not base.startswith("fea."):
                raise HTTPException(status_code=400, detail=f"illegal artefact entry: {n!r}")
        names = {posixpath.basename(n) for n in entries}
        if "fea.manifest.json" not in names:
            raise HTTPException(status_code=400, detail="zip missing fea.manifest.json")

        prefix = fea_artefact_prefix_for(source)
        written = 0
        try:
            for n in entries:
                base = posixpath.basename(n)
                payload = zf.read(n)
                # Mirror the worker's compression policy exactly: gzip only
                # the manifest JSON; store .bin blobs (and the mesh GLB)
                # identity so the viewer can HTTP-Range a single field step.
                content_encoding = "gzip" if base.lower().endswith(".json") else None
                await storage.put_bytes(
                    scope_obj,
                    prefix + base,
                    payload,
                    content_encoding=content_encoding,
                )
                written += 1
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("fea artefact upload failed for %s", source)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return JSONResponse(
            {"manifest_key": fea_artefact_manifest_key_for(source), "count": written},
            status_code=201,
        )

    @api.post("/scopes/{scope}/fea/artefact")
    async def api_scope_fea_artefact_upload_one(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Upload a *single* browser-baked FEA artefact file (section D).

        The per-file counterpart of ``POST /fea/artefacts`` (zip): the
        in-browser bake ships each ``fea.*`` file as it lands instead of
        accumulating the whole tree and zipping it, so neither the browser
        (output tree + zip) nor this endpoint (whole zip in memory, capped
        by the direct-upload threshold) has to hold the entire artefact set
        at once. Same prefix, same per-extension gzip policy as the zip
        route, so the streaming-FEA reader consumes the result unchanged.

        Query: ``source`` (existing source key) + ``name`` (the bare
        ``fea.*`` filename). Body: the raw file bytes.
        """
        import posixpath

        source = (request.query_params.get("source") or "").strip().lstrip("/")
        if not source:
            raise HTTPException(status_code=400, detail="source query param required")
        if not is_fea_artefact_source(source):
            raise HTTPException(status_code=415, detail=f"not a FEA artefact source: {source}")

        name = (request.query_params.get("name") or "").strip()
        base = posixpath.basename(name)
        # Same guard as the zip route: a bare ``fea.*`` filename only — no
        # subdirs, no traversal, so a request can't escape the per-source
        # prefix and write an arbitrary key.
        if base != name or not base or base.startswith(".") or not base.startswith("fea."):
            raise HTTPException(status_code=400, detail=f"illegal artefact name: {name!r}")

        try:
            source_exists = await storage.exists(scope_obj, source)
        except Exception:
            source_exists = False
        if not source_exists:
            raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")

        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                announced = int(cl)
            except ValueError:
                announced = -1
            if announced > _DIRECT_UPLOAD_THRESHOLD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"fea artefact file exceeds {_DIRECT_UPLOAD_THRESHOLD_BYTES} bytes",
                )

        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")

        prefix = fea_artefact_prefix_for(source)
        # gzip only the manifest JSON; .bin blobs stay identity so the
        # viewer can HTTP-Range a single field step (see the blobs route).
        content_encoding = "gzip" if base.lower().endswith(".json") else None
        try:
            await storage.put_bytes(scope_obj, prefix + base, data, content_encoding=content_encoding)
        except Exception as exc:
            logger.exception("fea artefact file upload failed for %s/%s", source, base)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return JSONResponse({"key": prefix + base, "name": base}, status_code=201)

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
        from .converter import is_derived_key, is_versions_artefact_key

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
        if not is_versions_artefact_key(key) and not await _is_accepted_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        try:
            url = await storage.presigned_put_url(scope_obj, key, expires_in_seconds=3600)
        except Exception as exc:
            logger.exception("presign failed for %s", key)
            raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
        # Hint that the client should gzip + send Content-Encoding=gzip
        # when this key's extension is in the compressible list. The
        # encoding header is *not* signed into the presigned URL —
        # SigV4 treats unsigned request headers as opaque metadata, so
        # the browser can attach Content-Encoding without breaking the
        # signature. The object store records the header on the object,
        # the read path's get_bytes/stream_to_path sniffs the gzip
        # magic anyway, and a browser without CompressionStream falls
        # back to raw PUT (the sweep job picks it up later).
        return JSONResponse(
            {
                "url": url,
                "key": key,
                "method": "PUT",
                "expires_in_seconds": 3600,
                "content_encoding": _content_encoding_for(key),
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
        from .converter import is_derived_key, is_versions_artefact_key

        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if is_derived_key(key):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        if not is_versions_artefact_key(key) and not await _is_accepted_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        meta = await storage.head(scope_obj, key)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"object not found at {key}; was the PUT successful?")
        await _audit(request, user, scope_obj, "upload", key=key, status="ok")
        return JSONResponse({"key": key, "size": meta["size"]}, status_code=201)

    @api.post("/scopes/{scope}/download-url")
    async def api_scope_download_url(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Mint a presigned GET URL for direct download from the object
        store. Mirrors /upload-url — same auth surface, same fallback
        semantics for local-backed deployments.

        Streaming via GET /blobs/{key} still works for clients that
        prefer the API-tunneled path; this endpoint exists so the CLI
        and other automated consumers can avoid pinning a worker
        thread for the entire transfer of large artefacts.
        """
        from .converter import is_derived_key

        if not storage.supports_presigned_uploads:
            raise HTTPException(
                status_code=503,
                detail="presigned downloads require an S3-compatible backend",
            )
        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        meta = await storage.head(scope_obj, key)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"object not found at {key}")
        ttl = 15 * 60
        try:
            url = await storage.presigned_get_url(scope_obj, key, expires_in_seconds=ttl)
        except Exception as exc:
            logger.exception("presign GET failed for %s", key)
            raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
        # Audit the URL minting, not the eventual GET — the object
        # store does the transfer outside our request path, so this is
        # the last hook we have on the event.
        if not is_derived_key(key):
            await _audit(request, user, scope_obj, "download", key=key, status="presigned")
        return JSONResponse(
            {
                "url": url,
                "key": key,
                "method": "GET",
                "expires_in_seconds": ttl,
                "size": meta["size"],
            }
        )

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
        # Optional per-conversion overrides. Allowlist is the union of:
        #   1. Names declared at any ``@converter(options=[...])``
        #      site — registry-driven path that flows the value to
        #      the handler as a kwarg.
        #   2. Legacy env-var-driven names whose consuming code in
        #      ada/occ/geom/surfaces.py still reads ``os.environ``.
        #      These stay until the surfaces.py path learns to take
        #      these as function parameters; migrating them retires
        #      the legacy half-union below.
        # Unknown keys are dropped rather than 400'd so a future-server-
        # old-client mix degrades to "global setting wins".
        _LEGACY_ENV_OPTS = {
            "use_sat_pcurves",
            "skip_shapefix",
            "profile_conversions",
        }
        _ALLOWED_OPTS = ConverterRegistry.all_options() | _LEGACY_ENV_OPTS
        raw_opts = body.get("conversion_options") or {}
        conversion_options: dict | None = None
        if isinstance(raw_opts, dict) and raw_opts:
            # Preserve native types (bool stays bool, int stays int)
            # so the worker's kwarg path forwards them as-is to the
            # handler. The legacy env-var path in worker.py already
            # ``str()``s on its way to the env-var write, so storing
            # natives doesn't regress that rail.
            cleaned: dict[str, object] = {}
            for k, v in raw_opts.items():
                if k not in _ALLOWED_OPTS:
                    continue
                if v is None:
                    cleaned[k] = None
                else:
                    cleaned[k] = v
            if cleaned:
                conversion_options = cleaned
        if not source_key:
            raise HTTPException(status_code=400, detail="source_key required")
        if not await _is_accepted_source(source_key):
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
                request,
                user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="done",
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
                request,
                user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        await _audit(
            request,
            user,
            scope_obj,
            "convert",
            key=source_key,
            target_format=target_format,
            status="queued",
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
        return JSONResponse({"source_key": source_key, "targets": supported_targets_for(source_key)})

    @api.post("/scopes/{scope}/utility")
    async def api_scope_utility(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Run a worker @utility against a loaded scene GLB.

        Body: ``{"source_key": ..., "utility_name": ..., "kwargs": {...}}``. The
        utility runs as a NATS job (target_format ``utility``) and writes a
        viewer-ops JSON blob at the returned ``derived_key``; the SPA polls the
        job, fetches that blob, and applies the ops to the live scene. Always
        recomputed (``force_rebuild``) since the result depends on the kwargs,
        which aren't encoded in the derived key.
        """
        from .utility import viewops_key_for

        body = await request.json()
        source_key = (body.get("source_key") or "").strip()
        utility_name = (body.get("utility_name") or "").strip()
        raw_kwargs = body.get("kwargs") or {}
        if not source_key:
            raise HTTPException(status_code=400, detail="source_key required")
        if not utility_name:
            raise HTTPException(status_code=400, detail="utility_name required")
        if not isinstance(raw_kwargs, dict):
            raise HTTPException(status_code=400, detail="kwargs must be an object")
        # Gate against the live worker-advertised utility set so we don't enqueue
        # a job no worker can serve.
        advertised = {u.get("name") for u in await _worker_advertised_utilities()}
        if advertised and utility_name not in advertised:
            raise HTTPException(
                status_code=404,
                detail=f"unknown utility {utility_name!r}; available: {sorted(n for n in advertised if n)}",
            )
        if not await storage.exists(scope_obj, source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="utilities disabled (no NATS configured)")

        derived_key = viewops_key_for(source_key, utility_name)
        try:
            job = await queue.enqueue(
                source_key,
                "utility",
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                conversion_options={"utility_name": utility_name, "kwargs": raw_kwargs},
                derived_key=derived_key,
                force_rebuild=True,
            )
        except Exception as exc:
            logger.exception("utility enqueue failed")
            await _audit(
                request,
                user,
                scope_obj,
                "utility",
                key=source_key,
                target_format="utility",
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        await _audit(
            request,
            user,
            scope_obj,
            "utility",
            key=source_key,
            target_format="utility",
            status="queued",
            job_id=job.job_id,
        )
        payload = asdict(job)
        payload["utility_name"] = utility_name
        return JSONResponse(payload, status_code=202)

    @api.post("/scopes/{scope}/my-jobs/{job_id}/cancel")
    async def api_scope_cancel_my_job(
        request: Request,
        job_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Mark a queued/running conversion as cancelled.

        Caller must own the job (audit_log.user_sub matches). Side
        effects:
          * audit_log row flipped to ``status='cancelled'`` (only if
            it was still queued or running — terminal rows are left
            alone);
          * the KV bucket entry is updated best-effort so any active
            poll loop sees the new status on its next tick.

        Limitation: the worker process isn't notified, so a bake that
        was actively mid-run will continue to completion. The
        resulting derived blob lands on storage as orphaned data; a
        future iteration could add a worker-side cancel-flag poll to
        bail mid-stage. For the toast UX this is enough — the user
        sees the row disappear and is unblocked.
        """
        pool = _require_pool(request)
        cancelled = await db_module.cancel_audit_by_job(
            pool,
            job_id=job_id,
            user_sub=user.sub,
        )
        if not cancelled:
            return JSONResponse(
                {"job_id": job_id, "cancelled": False, "reason": "not owned, missing, or already terminal"},
                status_code=404,
            )
        # Best-effort: nudge the KV bucket so /api/convert/{job_id}
        # polls immediately reflect the new status. Worker writes will
        # subsequently overwrite this back to 'running' on its next
        # progress tick — that's expected; the audit_log row is the
        # source of truth for the user-visible state.
        queue = getattr(request.app.state, "queue", None)
        if queue is not None:
            try:
                await queue.update(
                    job_id,
                    status="cancelled",
                    error="cancelled by user",
                )
            except Exception:
                # Queue update is decorative; the audit row is what
                # the toast restore reads.
                pass
        return JSONResponse({"job_id": job_id, "cancelled": True})

    @api.get("/scopes/{scope}/my-jobs")
    async def api_scope_my_jobs(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
        limit: int = 200,
    ) -> JSONResponse:
        """Conversions the calling user kicked off in this scope that
        are still in flight (``queued`` or ``running``).

        Frontend hits this on app load to repopulate the
        bottom-right ConversionProgress toast for jobs that survived
        a page reload. Scoped to (current user, current scope) so a
        user can't see anyone else's jobs and so cross-scope jobs
        don't clutter the toast when the active scope is unrelated.

        ``error`` rows are intentionally excluded — they're terminal
        and the toast's error-row UX expects manual dismissal, not a
        silent restore. Errors that happened while the user was away
        can be discovered via the (admin) audit log or a future
        per-user history view.
        """
        pool = _require_pool(request)
        rows = await db_module.list_audit(
            pool,
            user_sub=user.sub,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            statuses=["queued", "running"],
            # Cap at 500 (same as the underlying list_audit clamp)
            # so a typo'd limit can't pin the DB. 200 default is
            # plenty of headroom over the typical interactive
            # /convert flow (handfuls of jobs); audit-dispatched
            # cells are filtered out below so the upper bound is
            # almost never hit in practice.
            limit=min(max(int(limit), 1), 500),
            # Audit-dispatched cells are tagged with the admin's
            # user_sub but they belong to a batch sweep, not an
            # interactive /convert click. Filtering them out here
            # keeps the bottom-right toast a reflection of "things
            # I just clicked Convert on" — the Audit Runs admin tab
            # is the right surface for batch progress.
            exclude_audit_dispatched=True,
        )
        return JSONResponse({"jobs": rows})

    @api.get("/scopes/{scope}/result-meta")
    async def api_scope_result_meta(
        request: Request,
        key: str,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Return the (steps, fields) inventory for a FEA result file.

        Cache hit: 200 with the parsed JSON.
        Cache miss: 202 with ``{"job_id": ..., "status": "queued"}``;
        frontend polls ``/api/convert/{job_id}`` until done, then
        re-fetches this endpoint to get the body.

        SIF parsing on multi-hundred-MB decks takes 30 s+ and the
        slim API container doesn't carry ada.fem at all — both
        reasons push the work into the worker queue. Same shape as
        the streaming-viewer manifest endpoint.

        404 if the source is missing; 415 if the source isn't a FEA
        result file.
        """

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
            # Treat any cache-read hiccup as a miss; rebuild is the
            # safer path than handing the user a 500 because of a stale
            # half-written meta blob.
            logger.exception("result-meta: cache read failed for %s", meta_key)
            cached = None
        if cached:
            try:
                return JSONResponse(json.loads(cached.decode("utf-8")))
            except Exception:
                logger.exception("result-meta: cache parse failed for %s; rebuilding", meta_key)

        # Cache miss — enqueue a worker job and return 202. Frontend
        # polls /convert/{job_id} until done, then re-fetches this
        # endpoint.
        if not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="result-meta disabled (no NATS configured)",
            )
        try:
            job = await queue.enqueue(
                source_key,
                "fea_meta",
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                derived_key=meta_key,
            )
        except Exception as exc:
            logger.exception("result-meta: enqueue failed for %s", source_key)
            await _audit(
                request,
                user,
                scope_obj,
                "fea_meta",
                key=source_key,
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        await _audit(
            request,
            user,
            scope_obj,
            "fea_meta",
            key=source_key,
            status="queued",
            job_id=job.job_id,
        )
        return JSONResponse(
            {
                "job_id": job.job_id,
                "source_key": source_key,
                "meta_key": meta_key,
                "status": job.status,
                "progress": job.progress,
                "stage": job.stage,
            },
            status_code=202,
        )

    @api.get("/scopes/{scope}/fea/manifest")
    async def api_scope_fea_manifest(
        request: Request,
        key: str,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Return the streaming-viewer manifest for a FEA source.

        Cache hit: 200 with the parsed manifest JSON.
        Cache miss: 202 with ``{"job_id": ..., "status": "queued"}``;
        frontend polls ``/api/convert/{job_id}`` until done, then
        re-fetches this endpoint.

        The bake itself runs in the worker container (which has the
        full ada.fem stack); the API container is intentionally slim
        and can't import ada.fem at all.
        """

        source_key = (key or "").strip().lstrip("/")
        if not source_key:
            raise HTTPException(status_code=400, detail="key required")
        if not is_fea_artefact_source(source_key):
            # adapy ships built-in stream readers for .rmed and .sif;
            # capability workers register additional ones at startup
            # (e.g. abaqus → .odb / .sqlite) and publish the set into
            # the worker registry. Honour those here so a worker plug-in
            # doesn't have to also patch the API gate. The worker-side
            # bake still re-validates via its own ``make_stream_reader``
            # registry, so an extension the API accepted but no worker
            # actually handles surfaces as a clear bake error rather
            # than getting silently dropped.
            ext = pathlib.PurePosixPath(source_key).suffix.lower()
            if ext not in await _worker_advertised_exts():
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"streaming FEA viewer only supports .rmed / .sif / .sin "
                        f"or worker-advertised stream readers; got {source_key!r}"
                    ),
                )
        if not await storage.exists(scope_obj, source_key):
            raise HTTPException(status_code=404, detail=f"source not found: {source_key}")

        manifest_key = fea_artefact_manifest_key_for(source_key)
        try:
            cached = await storage.get_bytes(scope_obj, manifest_key)
        except FileNotFoundError:
            cached = None
        except Exception:
            logger.exception("fea-manifest: cache read failed for %s", manifest_key)
            cached = None
        if cached:
            try:
                return JSONResponse(json.loads(cached.decode("utf-8")))
            except Exception:
                logger.exception("fea-manifest: cache parse failed for %s; rebuilding", manifest_key)

        # Cache miss — enqueue a worker bake and return 202. Frontend
        # polls /convert/{job_id} via the existing route and re-fetches
        # this endpoint when the job hits status=done.
        if not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="bake disabled (no NATS configured)",
            )
        try:
            job = await queue.enqueue(
                source_key,
                "fea_artefacts",
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                # derived_key is the manifest path so the worker's
                # "already cached?" short-circuit lines up with this
                # endpoint's cache check.
                derived_key=manifest_key,
            )
        except Exception as exc:
            logger.exception("fea-manifest: enqueue failed for %s", source_key)
            await _audit(
                request,
                user,
                scope_obj,
                "fea_bake",
                key=source_key,
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        await _audit(
            request,
            user,
            scope_obj,
            "fea_bake",
            key=source_key,
            status="queued",
            job_id=job.job_id,
        )
        return JSONResponse(
            {
                "job_id": job.job_id,
                "source_key": source_key,
                "manifest_key": manifest_key,
                "status": job.status,
                "progress": job.progress,
                "stage": job.stage,
            },
            status_code=202,
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
        if not await scope_can_access(user, job_scope, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")
        return JSONResponse(asdict(job))

    # ── /api/components/* ────────────────────────────────────────────
    #
    # Connection-component panel:
    #   * /api/components/profiles?category=...   — section dropdown data
    #   * /api/components/specs?scope=...&branch= — preview gallery from
    #                                               the latest ada-build
    #                                               manifest on a branch
    #   * /api/components/build (POST)            — on-demand build job
    #                                               for user-tweaked
    #                                               inputs
    # The build-status poll reuses /api/convert/{job_id} since component
    # jobs flow through the same NATS queue + KV.
    from .components_manifest import (
        _scope_url_segment,
        expose_manifest,
        resolve_latest_manifest,
    )

    # ada.sections.profile_lookup is imported lazily — the viewer image
    # ships a minimal /app/src/ada/ layout (only ada.config + ada.comms)
    # and a top-level import here would crash uvicorn on startup. Keep
    # the import inside the handler so the rest of the API still serves
    # if profile_lookup or its dep chain isn't present.
    @api.get("/components/profiles")
    async def api_components_profiles(category: str | None = None) -> JSONResponse:
        try:
            from ada.sections.profile_lookup import (
                list_categories as _list_section_categories,
            )
            from ada.sections.profile_lookup import (
                load_profiles_by_category as _load_profiles_by_category,
            )
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"section catalogue unavailable: {exc}",
            )
        if category is None:
            return JSONResponse({"categories": _list_section_categories()})
        profiles = _load_profiles_by_category(category)
        return JSONResponse({"category": category, "profiles": profiles})

    @api.get("/components/specs")
    async def api_components_specs(
        request: Request,
        branch: str | None = None,
        scope: str | None = None,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """List published component specs.

        Auto-discovery: with no ``scope`` query param, scans every
        scope the caller can access (personal, shared, all
        memberships) and aggregates whichever happen to have a
        manifest published. Each returned spec entry carries the
        ``scope`` it was found in so the frontend can resolve preview
        URLs and route builds correctly.

        Likewise without a ``branch`` query param, ``versions/`` is
        scanned wholesale and the newest manifest anywhere in each
        scope wins — the bake project may not have ever published to
        ``main`` and the viewer shouldn't have to guess the right
        branch name. Pass ``?branch=...`` when you need to pin to a
        specific bake branch (tests, direct API consumers).

        Explicit override: ``?scope=...`` restricts the lookup to one
        scope — useful for tests and direct API consumers that don't
        want the full sweep.

        Name collisions across scopes: first-found wins. Order is
        personal → shared → projects (matches /api/me ordering).
        """
        pool = getattr(request.app.state, "db_pool", None)
        if scope is not None:
            scope_obj = _parse_scope(scope, user)
            scope_obj = await _resolve_project_scope(pool, scope_obj)
            if not await scope_can_access(user, scope_obj, pool):
                raise HTTPException(status_code=403, detail="forbidden")
            candidate_scopes: list[Scope] = [scope_obj]
        else:
            candidate_scopes = [Scope.user(user.sub), Scope.shared()]
            if pool is not None:
                for p in await db_module.list_user_projects(pool, user.sub):
                    candidate_scopes.append(Scope.project(p.id))

        sources: list[dict] = []
        all_specs: dict[str, dict] = {}
        for cand in candidate_scopes:
            try:
                resolved = await resolve_latest_manifest(storage, cand, branch)
            except Exception as exc:
                logger.debug("components/specs: skipping scope %s (%s)", cand, exc)
                continue
            if resolved is None:
                continue
            scope_str = _scope_url_segment(cand)
            sources.append({"scope": scope_str, "branch": resolved.branch, "commit": resolved.commit})
            exposed = expose_manifest(resolved, cand)
            for name, entry in exposed["specs"].items():
                if name in all_specs:
                    continue  # first scope to publish this name wins
                spec_entry = dict(entry)
                spec_entry["scope"] = scope_str
                spec_entry["branch"] = resolved.branch
                all_specs[name] = spec_entry

        return JSONResponse({"branch": branch, "sources": sources, "specs": all_specs})

    @api.post("/components/build")
    async def api_components_build(
        request: Request,
        scope: str = "shared",
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Enqueue an on-demand component build for user-tweaked inputs.

        Body: ``{"spec_name": str, "inputs": dict, "name": str | None,
        "extra_handler_kwargs": dict | None}``. Returns ``{"job_id":
        str}``; poll status via the existing ``GET /api/convert/{job_id}``.
        Result GLB lands at the job's derived_key and is fetchable via
        ``GET /api/scopes/{scope}/blobs/{derived_key}``.
        """
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="component build disabled (no NATS configured)")
        body = await request.json()
        spec_name = body.get("spec_name")
        if not isinstance(spec_name, str) or not spec_name:
            raise HTTPException(status_code=400, detail="spec_name (str) is required")
        inputs = body.get("inputs")
        if not isinstance(inputs, dict):
            raise HTTPException(status_code=400, detail="inputs (dict) is required")
        component_name = body.get("name")
        extra_kwargs = body.get("extra_handler_kwargs") or {}
        if not isinstance(extra_kwargs, dict):
            raise HTTPException(status_code=400, detail="extra_handler_kwargs must be a dict")

        scope_obj = _parse_scope(scope, user)
        scope_obj = await _resolve_project_scope(getattr(request.app.state, "db_pool", None), scope_obj)
        if not await scope_can_access(user, scope_obj, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")

        # No source file for component_build — use a synthetic source_key
        # that captures spec_name + inputs hash so cache hits work for
        # identical configurations (frontend submitting the same form
        # twice should not double-build). derived_key is the produced
        # GLB blob.
        import hashlib as _hashlib

        inputs_hash = _hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        source_key = f"_synthetic/component_build/{spec_name}/{inputs_hash}"
        derived_key = f"_derived/component_builds/{spec_name}/{inputs_hash}.glb"

        # Capability resolution: caller-supplied wins (frontend forwards
        # the manifest's ``capability`` from the spec entry). Otherwise
        # re-resolve the manifest in this scope and use its top-level
        # capability so the right worker pool picks the job up. Falls
        # back to the default pool when the manifest doesn't declare one
        # (the spec must then be registered on the base worker — built-in
        # adapy specs).
        target_capability = body.get("capability")
        if not isinstance(target_capability, str) or not target_capability.strip():
            target_capability = None
            resolved = await resolve_latest_manifest(storage, scope_obj, branch=None)
            if resolved is not None:
                manifest_cap = resolved.body.get("capability")
                if isinstance(manifest_cap, str) and manifest_cap.strip():
                    target_capability = manifest_cap.strip().lower()

        job = await queue.enqueue(
            source_key,
            target_format="component_build",
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            conversion_options={
                "spec_name": spec_name,
                "inputs": inputs,
                "name": component_name,
                "extra_handler_kwargs": extra_kwargs,
            },
            derived_key=derived_key,
            target_capability=target_capability,
        )
        return JSONResponse({"job_id": job.job_id, "derived_key": derived_key})

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

    # Per-scope compression-sweep state lives in NATS KV (queue.set/
    # get_compress_sweep_state) so a new session can observe an
    # in-flight sweep started elsewhere. We keep a small in-process
    # cache too so per-file state updates inside the BackgroundTask
    # don't have to re-read from KV between mutations.
    compression_state: dict[str, dict] = {}

    async def _save_compression_state(scope_label: str) -> None:
        state = compression_state.get(scope_label)
        if state is None:
            return
        try:
            await queue.set_compress_sweep_state(scope_label, state)
        except Exception:
            logger.exception("compression sweep: KV write failed (non-fatal)")

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

    async def _compression_sweep(scope_obj: Scope, scope_label: str) -> None:
        import gzip as _gzip
        import shutil as _shutil
        import tempfile as _tempfile

        from .converter import is_derived_key as _is_derived_key

        state = compression_state[scope_label]
        try:
            entries = await storage.list(scope_obj)
        except Exception as exc:
            state["error"] = f"list failed: {exc}"
            state["completed_at"] = time.time()
            state["last_update"] = time.time()
            await _save_compression_state(scope_label)
            return
        candidates = [e for e in entries if _content_encoding_for(e.key) == "gzip" and not _is_derived_key(e.key)]
        state["total"] = len(candidates)
        state["last_update"] = time.time()
        await _save_compression_state(scope_label)
        for entry in candidates:
            if state.get("cancelled"):
                break
            state["current_key"] = entry.key
            state["last_update"] = time.time()
            await _save_compression_state(scope_label)
            try:
                # Stream the object to disk so the viewer pod never has
                # to hold the whole payload in RAM — a 900 MB SIF with
                # the default 1 GiB memory limit OOM-kills the process
                # if we try the load-into-bytes path.
                with _tempfile.TemporaryDirectory() as tmpdir:
                    raw_path = pathlib.Path(tmpdir) / "raw"
                    gz_path = pathlib.Path(tmpdir) / "gz"
                    await storage.stream_to_path_raw(
                        scope_obj,
                        entry.key,
                        raw_path,
                    )
                    with open(raw_path, "rb") as fh:
                        magic = fh.read(2)
                    if magic == b"\x1f\x8b":
                        state["already_gzipped"] += 1
                        continue
                    with open(raw_path, "rb") as fin, _gzip.open(gz_path, "wb", compresslevel=6) as fout:
                        _shutil.copyfileobj(fin, fout, length=1 << 20)
                    # The gzipped result is typically ~5–10× smaller
                    # than the raw payload — safely fits in memory for
                    # the put_bytes call. If we ever hit a case where
                    # even the compressed size exceeds the pod's RAM
                    # limit, switch to a streaming put.
                    gzipped = gz_path.read_bytes()
                await storage.put_bytes(
                    scope_obj,
                    entry.key,
                    gzipped,
                    content_encoding="gzip",
                    pre_compressed=True,
                )
                state["compressed"] += 1
                state["bytes_before"] += entry.size or 0
                state["bytes_after"] += len(gzipped)
            except Exception as exc:
                logger.exception("compress sweep failed on %s/%s", scope_label, entry.key)
                state["errors"].append({"key": entry.key, "error": str(exc)})
            finally:
                state["processed"] += 1
                state["last_update"] = time.time()
                await _save_compression_state(scope_label)
        state["completed_at"] = time.time()
        state["current_key"] = None
        state["last_update"] = time.time()
        await _save_compression_state(scope_label)

    @admin.post("/storage/{scope}/compress-uncompressed")
    async def admin_compress_uncompressed(
        scope: str,
        background_tasks: BackgroundTasks,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Sweep the scope for objects whose extension is in the
        gzip-compressible list but whose stored bytes aren't gzipped,
        and rewrite each as ``Content-Encoding: gzip``.

        Runs in a background task so the request returns immediately;
        progress is reported via the companion
        ``GET /storage/compression-status`` endpoint. Re-triggering
        while a sweep is running for the same scope returns 409.
        """
        scope_label = scope
        current = await queue.get_compress_sweep_state(scope_label)
        if current and current.get("completed_at") is None:
            # Treat as orphaned if last_update is older than 90 s — the
            # most likely cause is a viewer pod restart that lost the
            # BackgroundTask. Override the stale state with a fresh
            # one rather than 409-blocking forever.
            last_update = current.get("last_update") or current.get("started_at") or 0
            if time.time() - last_update < 90:
                raise HTTPException(
                    status_code=409,
                    detail=f"sweep already running for {scope_label}",
                )

        compression_state[scope_label] = {
            "started_at": time.time(),
            "completed_at": None,
            "last_update": time.time(),
            "total": 0,
            "processed": 0,
            "compressed": 0,
            "already_gzipped": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "errors": [],
            "error": None,
            "cancelled": False,
            "current_key": None,
        }
        await _save_compression_state(scope_label)
        background_tasks.add_task(_compression_sweep, scope_obj, scope_label)
        return JSONResponse(
            {"scope": scope_label, "status": "started"},
            status_code=202,
        )

    @admin.get("/storage/compression-status")
    async def admin_compression_status() -> JSONResponse:
        """Snapshot of every recorded compression sweep keyed by scope.
        State lives in NATS KV so a new session sees in-flight sweeps
        that were started elsewhere; an entry with ``completed_at: null``
        and ``last_update`` older than 90 s indicates the viewer pod
        restarted mid-sweep (the work was lost — re-trigger to resume)."""
        try:
            scopes = await queue.list_compress_sweep_states()
        except Exception:
            logger.exception("compression status: KV read failed")
            scopes = {}
        # Layer in any in-process state that hasn't been flushed to KV
        # yet (e.g. between mutations within the BackgroundTask).
        for label, state in compression_state.items():
            scopes[label] = state
        # Tag each entry with an ``orphaned`` flag for the frontend's
        # toast logic — saves the client recomputing the staleness.
        now = time.time()
        for state in scopes.values():
            if state.get("completed_at") is None:
                last = state.get("last_update") or state.get("started_at") or 0
                state["orphaned"] = (now - last) > 90
            else:
                state["orphaned"] = False
        return JSONResponse({"scopes": scopes})

    @admin.get("/workers")
    async def admin_list_workers() -> JSONResponse:
        """Snapshot of every worker pod that recently checked in.

        Each running worker re-PUTs its registry entry every 15 s; the
        admin panel marks rows older than 60 s as offline (kept in the
        list briefly so a flapping pod is visible while it restarts).
        The list itself is just the KV scan — no DB hit, safe to poll
        at the panel's refresh cadence.
        """
        if not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="worker registry requires a NATS-backed queue",
            )
        try:
            workers = await queue.list_workers()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"could not read worker registry: {exc}",
            ) from exc
        now = time.time()
        # Annotate each row with a derived ``online`` boolean so the
        # frontend doesn't have to recompute the staleness threshold.
        # Same window the routing path uses (queue._capability_for_ext) so
        # "shown online in the UI" and "eligible for auto-routing" agree.
        stale_after_s = queue.WORKER_STALE_AFTER_S
        for w in workers:
            hb = w.get("last_heartbeat")
            try:
                w["online"] = isinstance(hb, (int, float)) and (now - hb) <= stale_after_s
            except TypeError:
                w["online"] = False
        # Newest registration first; offline rows sink to the bottom so
        # the live fleet sits at the top of the table.
        workers.sort(
            key=lambda w: (not w.get("online"), -float(w.get("last_heartbeat") or 0)),
        )
        return JSONResponse({"workers": workers, "now": now, "stale_after_s": stale_after_s})

    # ── Audit runs (M1 admin audit panel) ─────────────────────────────
    #
    # POST  /admin/audit/runs           — kick off a sweep
    # GET   /admin/audit/runs           — recent runs (paginated)
    # GET   /admin/audit/runs/{id}      — one run + per-cell grid
    #
    # Registered BEFORE ``/audit/{audit_id}`` below so the literal
    # ``runs`` segment doesn't get matched against the parameterized
    # int route (FastAPI tries routes in registration order; a path
    # segment ``"runs"`` would fail the ``audit_id: int`` validation
    # with a 422 if the parameterized route won the match).

    # Synthetic User stand-in used by the scheduler tick + cron-fired
    # runs. ``_parse_scope`` only reads ``.sub`` (and only on
    # ``user:me``, which a scheduled run wouldn't sensibly use), but
    # we still give it a recognisable identifier so audit rows say
    # ``created_by=system`` rather than ``None``.
    class _SystemUser:
        sub = "system"
        is_admin = True

    def _validate_cron(cron_expr: str) -> str:
        """Parse-and-normalise a 5-field cron expression. Returns the
        cleaned form on success; raises HTTPException(400) on a
        malformed input so the REST handler can surface a useful
        message instead of a 500."""
        from croniter import CroniterBadCronError, croniter  # type: ignore

        cleaned = cron_expr.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="cron_expr is required")
        try:
            croniter(cleaned)
        except (CroniterBadCronError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"invalid cron expression: {exc}",
            ) from exc
        return cleaned

    def _next_fire(cron_expr: str, *, after=None):
        """Compute the next firing instant from ``after`` (defaults to
        now). Returns a timezone-aware UTC datetime — Postgres
        ``TIMESTAMPTZ`` round-trips it without conversion surprises."""
        from datetime import datetime, timezone

        from croniter import croniter  # type: ignore

        base = after or datetime.now(timezone.utc)
        return croniter(cron_expr, base).get_next(datetime)

    async def _scheduler_loop(pool) -> None:
        """Background task: tick every 30s, claim due schedules, fire
        the same dispatch code path as ``POST /admin/audit/runs``.

        The loop is defensive — exceptions inside one tick are logged
        but don't kill the loop, so a transient DB blip or a
        malformed schedule row never silently disables the whole
        scheduler. Only ``asyncio.CancelledError`` exits cleanly (the
        shutdown path).

        Cross-replica safety: ``claim_due_audit_schedule`` is the only
        operation that mutates a schedule's ``last_fired_at`` /
        ``next_fire_at``, and it does so under
        ``FOR UPDATE SKIP LOCKED`` so two replicas ticking the same
        row produce at most one fire.
        """
        from datetime import datetime, timezone

        # One tick is "claim and dispatch until no more rows are due,
        # then sleep". The inner while-loop ensures a backlog (e.g.
        # after a deploy with several pending schedules) drains in
        # one tick instead of one-per-30s.
        TICK_INTERVAL_S = 30.0
        logger.info("audit scheduler: starting (tick every %ss)", TICK_INTERVAL_S)
        try:
            while True:
                try:
                    now = datetime.now(timezone.utc)
                    while True:
                        try:
                            # Compute provisional next_fire_at from
                            # the CURRENT time, not from the row's
                            # cron_expr — we don't know cron_expr
                            # until we've claimed the row. We claim
                            # with a placeholder, then immediately
                            # follow up to set the correct
                            # next_fire_at based on the row's expr.
                            placeholder_next = now  # overwritten below
                            row = await db_module.claim_due_audit_schedule(
                                pool,
                                now=now,
                                next_fire_at=placeholder_next,
                            )
                        except Exception:
                            logger.exception("audit scheduler: claim failed")
                            break
                        if row is None:
                            break
                        # Update next_fire_at to the real value
                        # computed from this row's cron expression.
                        # If parsing fails (shouldn't — we validated
                        # on insert/update) leave the schedule alone
                        # and disable it via a skip reason.
                        try:
                            real_next = _next_fire(row["cron_expr"], after=now)
                            await db_module.update_audit_schedule(
                                pool,
                                row["id"],
                                next_fire_at=real_next,
                                next_fire_at_set=True,
                            )
                        except Exception as exc:
                            logger.exception(
                                "audit scheduler: cron parse failed for %s",
                                row["id"],
                            )
                            await db_module.set_audit_schedule_skip_reason(
                                pool,
                                row["id"],
                                f"cron parse failed: {exc}",
                            )
                            continue

                        await _scheduler_fire(pool, row)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("audit scheduler: tick failed")
                await asyncio.sleep(TICK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("audit scheduler: stopped")
            raise

    async def _scheduler_fire(pool, schedule_row: dict) -> None:
        """One claimed-row's dispatch. Resolves scope, runs the
        concurrent-fire guard, creates the audit_run, and kicks off
        ``_audit_dispatch`` in a fresh task (mirroring the
        BackgroundTask path used by the manual route)."""
        sched_id = schedule_row["id"]
        scope_str = schedule_row["scope"]
        worker_pool = schedule_row["worker_pool"]
        try:
            s = _parse_scope(scope_str, _SystemUser())
            s = await _resolve_project_scope(pool, s)
        except HTTPException as exc:
            await db_module.set_audit_schedule_skip_reason(
                pool,
                sched_id,
                f"scope resolution failed ({exc.status_code}): {exc.detail}",
            )
            return
        except Exception as exc:
            logger.exception("audit scheduler: scope resolution crashed")
            await db_module.set_audit_schedule_skip_reason(
                pool,
                sched_id,
                f"scope resolution crashed: {exc}",
            )
            return

        # Concurrent-fire guard: a previous run with the same
        # (scope, worker_pool) is still in-flight. Skipping is the
        # safe choice — overlapping audits would double the worker
        # pool load and confuse the per-cell grid.
        try:
            in_flight = await db_module.audit_run_exists_for_key(
                pool,
                scope_str,
                worker_pool,
            )
        except Exception:
            logger.exception("audit scheduler: concurrent-fire check failed")
            return
        if in_flight:
            await db_module.set_audit_schedule_skip_reason(
                pool,
                sched_id,
                "skipped: previous audit run still in-flight",
            )
            return

        try:
            run = await db_module.create_audit_run(
                pool,
                scope=scope_str,
                worker_pool=worker_pool,
                trigger="cron",
                note=f"scheduled: {schedule_row['name']}",
                created_by="system",
            )
        except Exception as exc:
            logger.exception("audit scheduler: create_audit_run failed")
            await db_module.set_audit_schedule_skip_reason(
                pool,
                sched_id,
                f"create_audit_run failed: {exc}",
            )
            return

        # Mirror the manual path: dispatch runs as a fire-and-forget
        # task so the scheduler tick stays responsive. The task takes
        # over emitting audit_log rows + bumping run counters.
        asyncio.create_task(
            _audit_dispatch(run["id"], s, worker_pool, "system", pool),
            name=f"audit-dispatch-{run['id']}",
        )

    # ── Issue-bot configuration + poller (M5) ─────────────────────

    # Settings keys for the audit-failure → issue-tracker bridge.
    # Tokens are NEVER stored in app_settings; the deployment puts
    # the token in an env var (typically populated from a k8s Secret)
    # and ``token_env_name`` here records which env var to read.
    _ISSUE_KIND_KEY = "audit.issue_target.kind"
    _ISSUE_REPO_KEY = "audit.issue_target.repo"
    _ISSUE_BASE_URL_KEY = "audit.issue_target.base_url"
    _ISSUE_TOKEN_ENV_KEY = "audit.issue_target.token_env_name"

    async def _load_issue_target_config(pool) -> dict | None:
        """Read the configured issue target from app_settings + the
        token from the named env var. Returns ``None`` when the
        target is disabled / unconfigured / missing the token; the
        caller treats that as ``issue_bot_status='skipped'``.
        """
        kind = await db_module.get_setting(pool, _ISSUE_KIND_KEY)
        if not kind or kind.strip().lower() in ("", "disabled", "off"):
            return None
        repo = await db_module.get_setting(pool, _ISSUE_REPO_KEY)
        if not repo:
            return None
        token_env = await db_module.get_setting(pool, _ISSUE_TOKEN_ENV_KEY)
        if not token_env:
            return None
        token = os.environ.get(token_env.strip())
        if not token:
            logger.warning(
                "issue-bot: token env var %r is not set; skipping sync",
                token_env,
            )
            return None
        base_url = await db_module.get_setting(pool, _ISSUE_BASE_URL_KEY)
        return {
            "kind": kind.strip().lower(),
            "repo": repo.strip(),
            "base_url": (base_url or "").strip() or None,
            "token": token,
            "token_env": token_env.strip(),
        }

    async def _run_issue_bot_for(pool, run: dict) -> None:
        """Sync one finished audit run against the configured forge.

        Stamps the run's ``issue_bot_status`` to a terminal value
        ('done' / 'skipped' / 'failed'). Catches and records every
        exception so a single bad run can't kill the poller.
        """
        from . import audit_issue, issue_client

        run_id = run["id"]
        cfg = await _load_issue_target_config(pool)
        if cfg is None:
            await db_module.mark_audit_run_issue_bot(
                pool,
                run_id,
                status="skipped",
                error="issue target disabled or token env var unset",
            )
            return

        try:
            failed = await db_module.list_failed_audit_run_jobs(pool, run_id)
        except Exception as exc:
            logger.exception("issue-bot: list_failed_audit_run_jobs failed")
            await db_module.mark_audit_run_issue_bot(
                pool,
                run_id,
                status="failed",
                error=f"db read failed: {exc}",
            )
            return

        # No failures → nothing to publish, but we still rebuild the
        # dashboard so a clean run flips the dashboard back to "no
        # open regressions".
        try:
            client = issue_client.build_client(
                cfg["kind"],
                repo=cfg["repo"],
                token=cfg["token"],
                base_url=cfg["base_url"],
            )
        except Exception as exc:
            await db_module.mark_audit_run_issue_bot(
                pool,
                run_id,
                status="failed",
                error=f"client init failed: {exc}",
            )
            return

        summary: dict
        if failed:
            try:
                summary = await audit_issue.sync_run_issues(
                    client,
                    run=run,
                    failed_jobs=failed,
                )
            except Exception as exc:
                logger.exception("issue-bot: sync_run_issues failed")
                await db_module.mark_audit_run_issue_bot(
                    pool,
                    run_id,
                    status="failed",
                    error=f"sync failed: {exc}",
                )
                return
        else:
            summary = {"opened": 0, "commented": 0, "errors": [], "unique_failures": 0}

        try:
            dash = await audit_issue.rebuild_dashboard_issue(client, last_run=run)
        except Exception as exc:
            logger.exception("issue-bot: rebuild_dashboard_issue failed")
            dash = {"updated": False, "error": str(exc)}

        if summary["errors"] or not dash.get("updated", False):
            note_parts: list[str] = []
            if summary["errors"]:
                note_parts.append(f"{len(summary['errors'])} per-issue errors: " + "; ".join(summary["errors"][:3]))
            if not dash.get("updated", False) and dash.get("error"):
                note_parts.append(f"dashboard: {dash['error']}")
            await db_module.mark_audit_run_issue_bot(
                pool,
                run_id,
                status="failed",
                error=" | ".join(note_parts) or "unknown",
            )
            return

        note = f"opened={summary['opened']} commented={summary['commented']} " f"unique={summary['unique_failures']}"
        await db_module.mark_audit_run_issue_bot(
            pool,
            run_id,
            status="done" if failed else "skipped",
            error=None if failed else "no failures to report",
        )
        logger.info("issue-bot: synced run %s — %s", run_id, note)

    # ── Profile hotspot parser (M7 perf dashboard) ────────────────

    _PROFILE_TOP_K = 50

    async def _parse_one_profile(pool, claimed: dict) -> None:
        """Download one .prof blob, extract top-K functions by
        cumtime, and write rows into ``profile_function_stats``.
        Failures get stamped on the audit_log row's
        ``profile_stats_error`` so the operator can debug, but the
        loop continues — one bad blob mustn't stop the queue."""
        import pathlib as _pl
        import pstats

        audit_id = int(claimed["id"])
        try:
            scope = (
                Scope.shared()
                if claimed["scope_kind"] == "shared"
                else Scope(kind=claimed["scope_kind"], id=claimed["scope_id"])  # type: ignore[arg-type]
            )
            data = await storage.get_bytes(scope, claimed["profile_key"])
        except Exception as exc:
            logger.warning(
                "profile parser: storage read failed for audit %s: %s",
                audit_id,
                exc,
            )
            await db_module.mark_profile_stats_failed(
                pool,
                audit_id,
                f"storage read failed: {exc}",
            )
            return

        # pstats only reads from disk — stash bytes in a tempfile
        # rather than threading a BytesIO through it.
        tmp_path = _pl.Path(tempfile.mkstemp(suffix=".prof")[1])
        try:
            tmp_path.write_bytes(data)
            try:
                stats = pstats.Stats(str(tmp_path))
            except Exception as exc:
                logger.warning(
                    "profile parser: pstats failed for audit %s: %s",
                    audit_id,
                    exc,
                )
                await db_module.mark_profile_stats_failed(
                    pool,
                    audit_id,
                    f"pstats parse failed: {exc}",
                )
                return
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        rows: list[dict] = []
        for (fn, line, name), (cc, nc, tt, ct, _callers) in stats.stats.items():
            rows.append(
                {
                    "func": name or "",
                    "file": fn or "",
                    "line": int(line) if line is not None else 0,
                    "ncalls": int(nc),
                    "primitive_calls": int(cc),
                    "tottime": float(tt),
                    "cumtime": float(ct),
                }
            )
        rows.sort(key=lambda r: r["cumtime"], reverse=True)
        rows = rows[:_PROFILE_TOP_K]

        try:
            await db_module.insert_profile_function_stats(
                pool,
                audit_id,
                rows,
            )
        except Exception as exc:
            logger.exception("profile parser: insert failed for audit %s", audit_id)
            await db_module.mark_profile_stats_failed(
                pool,
                audit_id,
                f"insert failed: {exc}",
            )

    async def _profile_parser_loop(pool) -> None:
        """Background task: pull unprocessed audit_log rows with a
        ``profile_key``, parse the .prof, persist top-K function
        stats. Idle when profiling is disabled (no rows match).

        Batch-size limit per tick keeps the parser from monopolising
        the event loop after a big audit run lands hundreds of new
        .prof blobs at once.
        """
        TICK_INTERVAL_S = 30.0
        BATCH_PER_TICK = 5
        logger.info(
            "profile parser: starting (tick every %ss, batch %d)",
            TICK_INTERVAL_S,
            BATCH_PER_TICK,
        )
        try:
            while True:
                try:
                    for _ in range(BATCH_PER_TICK):
                        claimed = await db_module.claim_unprocessed_profile_row(pool)
                        if claimed is None:
                            break
                        await _parse_one_profile(pool, claimed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("profile parser: tick failed")
                await asyncio.sleep(TICK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("profile parser: stopped")
            raise

    async def _run_issue_bot_for_conversion(pool, row: dict) -> None:
        """Sync ONE user-driven failed conversion against the forge.

        Reuses :func:`sync_run_issues` with a 1-job list and a
        synthetic 'run' wrapper labelled "user conversion" so the
        comment / issue body wording reflects the trigger. Skips
        the dashboard rebuild — that's the responsibility of the
        audit-run bot pass; rebuilding on every single user
        failure would hammer the forge needlessly.
        """
        from . import audit_issue, issue_client

        audit_id = int(row["id"])
        cfg = await _load_issue_target_config(pool)
        if cfg is None:
            await db_module.mark_audit_log_issue_bot(
                pool,
                audit_id,
                status="skipped",
                error="issue target disabled or token env var unset",
            )
            return

        try:
            client = issue_client.build_client(
                cfg["kind"],
                repo=cfg["repo"],
                token=cfg["token"],
                base_url=cfg["base_url"],
            )
        except Exception as exc:
            await db_module.mark_audit_log_issue_bot(
                pool,
                audit_id,
                status="failed",
                error=f"client init failed: {exc}",
            )
            return

        run_wrapper = {
            "id": f"audit-row-{audit_id}",
            "started_at": row.get("ts"),
        }
        try:
            summary = await audit_issue.sync_run_issues(
                client,
                run=run_wrapper,
                failed_jobs=[row],
                source_label="user conversion",
            )
        except Exception as exc:
            logger.exception(
                "issue-bot: sync_run_issues failed for audit row %s",
                audit_id,
            )
            await db_module.mark_audit_log_issue_bot(
                pool,
                audit_id,
                status="failed",
                error=f"sync failed: {exc}",
            )
            return

        if summary["errors"]:
            await db_module.mark_audit_log_issue_bot(
                pool,
                audit_id,
                status="failed",
                error="; ".join(summary["errors"][:3]),
            )
            return

        await db_module.mark_audit_log_issue_bot(
            pool,
            audit_id,
            status="done",
            error=None,
        )
        logger.info(
            "issue-bot: synced user conversion %s — opened=%d commented=%d",
            audit_id,
            summary["opened"],
            summary["commented"],
        )

    async def _dispatch_auto_validation(pool, parent: dict) -> None:
        """Append a validation (cross-format parity) pass to a finished
        ``auto_validate`` conversion run — *into the same run*, not a new one.
        The run's total grows by the parity cell count and it reopens to
        ``running`` until those cells land (then it re-finishes). The claim
        already stamped the parent so this runs once; failures are logged but
        never break the poller tick."""
        try:
            s = _parse_scope(parent["scope"], _SystemUser())
            s = await _resolve_project_scope(pool, s)
        except Exception:
            logger.exception("auto-validate: scope resolution failed for run %s", parent["id"])
            return
        # Awaited (not fire-and-forget) so the run is reopened with its parity
        # cells before the issue-bot drain in the same tick can claim it.
        try:
            await _audit_dispatch(
                parent["id"],
                s,
                parent["worker_pool"],
                "system",
                pool,
                False,
                validate_only=True,
                extend=True,
            )
            logger.info("auto-validate: appended validation cells to run %s", parent["id"])
        except Exception:
            logger.exception("auto-validate: dispatch failed for run %s", parent["id"])

    async def _issue_bot_loop(pool) -> None:
        """Background task: drain (1) finished audit runs + (2)
        failed user-driven conversions per tick. Defensive —
        exceptions in one tick don't kill the loop.

        User-conversion failures are processed individually but
        rate-limited per tick (``USER_BATCH_PER_TICK``) so a burst
        of failures doesn't flood the forge API. Audit-run sweeps
        batch all of a run's failures into one sync, so they're
        not rate-limited the same way.
        """
        TICK_INTERVAL_S = 30.0
        USER_BATCH_PER_TICK = 10
        logger.info(
            "issue-bot poller: starting (tick every %ss, user batch %d)",
            TICK_INTERVAL_S,
            USER_BATCH_PER_TICK,
        )
        try:
            while True:
                try:
                    # Auto-validate first: a finished auto_validate run gets its
                    # parity cells appended (reopening it to 'running'), so the
                    # issue-bot below only claims a run once it's *truly* done —
                    # conversions and validation together. Claimed once (the
                    # claim stamps auto_validate_dispatched_at).
                    while True:
                        parent = await db_module.claim_audit_run_for_auto_validate(pool)
                        if parent is None:
                            break
                        await _dispatch_auto_validation(pool, parent)
                    # Drain finished audit runs (each represents many
                    # failures batched into one sync, more valuable
                    # to keep current).
                    while True:
                        run = await db_module.claim_audit_run_for_issue_bot(pool)
                        if run is None:
                            break
                        await _run_issue_bot_for(pool, run)
                    # Then user-conversion failures, capped per tick.
                    for _ in range(USER_BATCH_PER_TICK):
                        conv = await db_module.claim_failed_conversion_for_issue_bot(pool)
                        if conv is None:
                            break
                        await _run_issue_bot_for_conversion(pool, conv)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("issue-bot poller: tick failed")
                await asyncio.sleep(TICK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("issue-bot poller: stopped")
            raise

    async def _audit_run_list_cells(
        scope_obj: Scope,
        validate_only: bool,
    ) -> list[tuple[str, str]]:
        """Enumerate the (source_key, target_format) cells for an audit
        run over ``scope_obj`` — the scope's non-derived, supported
        source files crossed with the converter matrix. ``validate_only``
        emits only per-source ``parity`` cells. Shared by the NATS
        dispatcher, the WASM dispatcher, and the cells endpoint so the
        three never disagree on what a run covers. May raise on a scope
        listing failure (caller decides how to surface it)."""
        from .converter import ConverterRegistry, is_derived_key, is_supported_source

        files = await storage.list(scope_obj)
        cells: list[tuple[str, str]] = []
        for f in files:
            if is_derived_key(f.key):
                continue
            if not is_supported_source(f.key):
                continue
            ext = pathlib.PurePosixPath(f.key).suffix.lower()
            targets = ConverterRegistry.targets_for(ext)
            if validate_only:
                # A validation run does cross-format visual-parity only (no conversion
                # grid), and only when the source can produce a structure-preserving
                # format to compare against. Parity is a validation concern — full
                # conversion runs do not emit parity cells.
                if any(t in ("ifc", "xml", "step") for t in targets):
                    cells.append((f.key, "parity"))
            else:
                for target_format in targets:
                    cells.append((f.key, target_format))
        return cells

    async def _audit_dispatch(
        run_id: str,
        scope_obj: Scope,
        worker_pool: str | None,
        user_sub: str,
        pool,
        force_rebuild: bool = False,
        validate_only: bool = False,
        extend: bool = False,
    ) -> None:
        """Enumerate the scope's files × the converter matrix and
        enqueue one regular convert job per cell. Cached cells
        (derived blob already present) are audited as ``done``
        immediately. Runs in a BackgroundTask so the request returns
        202 immediately; the operator polls the run row for progress.

        ``force_rebuild`` skips the cached-blob short-circuit so
        every cell is re-converted from source. Used for perf
        measurement runs where a 4-hour audit re-run mustn't
        short-circuit 80% of cells against prior outputs.

        ``extend`` *appends* the enumerated cells to an already-finished run
        (growing its total + reopening it) instead of setting the total from
        scratch — the auto-validation pass uses this to fold its parity cells
        into the conversion run rather than spawning a separate run. With
        ``extend`` an empty cell set is a no-op (the finished run is left as
        is) rather than a zero-total finish.

        Errors during enumeration / enqueue surface as a ``failed``
        audit row on the cell that tripped them — the run still
        finishes when the rest of the jobs complete.
        """
        from .converter import derived_key_for

        synthetic_user = type("AdminAuditUser", (), {"sub": user_sub})()
        # Collect viable cells before enqueueing so the total is
        # exact — set_audit_run_total flips the row to 'finished'
        # if it gets zero, so a typo'd scope shows up immediately in
        # the UI rather than as a perpetually-running ghost.
        try:
            cells = await _audit_run_list_cells(scope_obj, validate_only)
        except Exception:
            logger.exception("audit run %s: scope listing failed", run_id)
            if not extend:
                await db_module.set_audit_run_total(pool, run_id, 0)
            return

        if extend:
            if not cells:
                return  # nothing to append; leave the finished run untouched
            await db_module.extend_audit_run_total(pool, run_id, len(cells))
        else:
            await db_module.set_audit_run_total(pool, run_id, len(cells))
            if not cells:
                return

        for source_key, target_format in cells:
            # Parity cells produce no derived blob, so there is nothing to cache
            # against — always enqueue, and audit under action="validate".
            if target_format == "parity":
                try:
                    job = await queue.enqueue(
                        source_key,
                        "parity",
                        scope_kind=scope_obj.kind,
                        scope_id=scope_obj.id,
                        target_capability=worker_pool,
                        force_rebuild=force_rebuild,
                        # Parity produces no derived blob — pass an explicit derived_key so
                        # enqueue doesn't route through derived_key_for(), which rejects the
                        # "parity" pseudo-format (not in TARGET_FORMATS). Same pattern the
                        # fea_artefacts flow uses for its manifest key.
                        derived_key=f"_derived/{source_key}.parity",
                    )
                except Exception as exc:
                    logger.exception("audit run %s: parity enqueue failed for %s", run_id, source_key)
                    await _audit(
                        None,
                        synthetic_user,
                        scope_obj,
                        "validate",
                        key=source_key,
                        target_format="parity",
                        status="error",
                        error=str(exc),
                        audit_run_id=run_id,
                        pool=pool,
                    )
                    continue
                await _audit(
                    None,
                    synthetic_user,
                    scope_obj,
                    "validate",
                    key=source_key,
                    target_format="parity",
                    status="queued",
                    job_id=job.job_id,
                    audit_run_id=run_id,
                    pool=pool,
                )
                continue

            try:
                derived_key = derived_key_for(source_key, target_format)
            except Exception as exc:
                # Should never trigger — targets_for already filtered
                # to viable targets — but record the failure so the
                # grid surfaces it instead of silently shrinking the
                # cell count.
                await _audit(
                    None,
                    synthetic_user,
                    scope_obj,
                    "convert",
                    key=source_key,
                    target_format=target_format,
                    status="error",
                    error=str(exc),
                    audit_run_id=run_id,
                    pool=pool,
                )
                continue

            if force_rebuild:
                cached = False
            else:
                try:
                    cached = await storage.exists(scope_obj, derived_key)
                except Exception:
                    logger.exception(
                        "audit run %s: storage.exists failed for %s",
                        run_id,
                        derived_key,
                    )
                    cached = False

            if cached:
                # Cached cell — count as ``done`` without enqueueing.
                # The audit row carries the run id; insert_audit bumps
                # the run's ok counter inline (see db.insert_audit).
                await _audit(
                    None,
                    synthetic_user,
                    scope_obj,
                    "convert",
                    key=source_key,
                    target_format=target_format,
                    status="done",
                    audit_run_id=run_id,
                    pool=pool,
                )
                continue

            try:
                job = await queue.enqueue(
                    source_key,
                    target_format,
                    scope_kind=scope_obj.kind,
                    scope_id=scope_obj.id,
                    target_capability=worker_pool,
                    force_rebuild=force_rebuild,
                )
            except Exception as exc:
                logger.exception(
                    "audit run %s: enqueue failed for %s -> %s",
                    run_id,
                    source_key,
                    target_format,
                )
                await _audit(
                    None,
                    synthetic_user,
                    scope_obj,
                    "convert",
                    key=source_key,
                    target_format=target_format,
                    status="error",
                    error=str(exc),
                    audit_run_id=run_id,
                    pool=pool,
                )
                continue

            await _audit(
                None,
                synthetic_user,
                scope_obj,
                "convert",
                key=source_key,
                target_format=target_format,
                status="queued",
                job_id=job.job_id,
                audit_run_id=run_id,
                pool=pool,
            )

    # Synthetic worker_pool value that routes an audit run to the
    # in-browser WASM engine instead of a NATS worker pool.
    _WASM_POOL = "wasm"

    async def _audit_dispatch_wasm(
        run_id: str,
        scope_obj: Scope,
        pool,
        validate_only: bool = False,
    ) -> None:
        """WASM audit run: enumerate cells + set the run total, but do
        NOT enqueue anything. The browser fetches the cell matrix via
        ``GET /admin/audit/runs/{id}/cells`` and runs each cell in
        pyodide, writing its audit row through the ``audit/local``
        endpoints (which carry the ``audit_run_id`` and bump the run
        counters). Mirrors the zero-cell short-circuit so a typo'd scope
        finishes immediately rather than hanging as a ghost run."""
        try:
            cells = await _audit_run_list_cells(scope_obj, validate_only)
        except Exception:
            logger.exception("wasm audit run %s: scope listing failed", run_id)
            await db_module.set_audit_run_total(pool, run_id, 0)
            return
        await db_module.set_audit_run_total(pool, run_id, len(cells))

    @admin.post("/audit/runs")
    async def admin_audit_run_create(
        request: Request,
        background_tasks: BackgroundTasks,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Kick off a regression sweep across one scope.

        Body: ``{"scope": "shared" | "user:me" | "project:<id>",
                 "worker_pool": "audit" | "wasm" | null,
                 "note": "...",
                 "force_rebuild": false }``.

        ``worker_pool="wasm"`` runs the sweep in the browser (no NATS):
        the run is created and its cell total computed, but nothing is
        enqueued — the SPA drives the cells via the WASM engine. Every
        other pool routes to a NATS worker as before.

        ``force_rebuild`` skips the cached-cell short-circuit so
        every cell actually re-converts. Default false — daily
        regression sweeps want the fast cached path.

        Returns 202 with the new run id; client polls
        ``GET /admin/audit/runs/{id}`` for progress.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        scope_str = (body.get("scope") or "shared").strip()
        worker_pool = body.get("worker_pool") or None
        is_wasm = isinstance(worker_pool, str) and worker_pool.strip().lower() == _WASM_POOL
        # The browser engine needs no NATS; only worker-pool runs do.
        if not is_wasm and not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="conversion disabled (no NATS configured)",
            )
        note = body.get("note") or None
        force_rebuild = bool(body.get("force_rebuild") or False)
        # validate_only: a validation-phase run — enqueue only the per-source
        # cross-format parity cells, skipping the conversion grid. The parity job
        # re-derives from source, so it needs no prior conversion outputs.
        validate_only = bool(body.get("validate_only") or False)
        # auto_validate: once this conversion run finishes, the finished-run
        # poller fires a follow-up validate_only run for the same scope. Only
        # meaningful for a worker-pool conversion run (a validation run / a
        # browser run has nothing to chain).
        auto_validate = bool(body.get("auto_validate") or False) and not validate_only and not is_wasm

        s = _parse_scope(scope_str, user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")

        run = await db_module.create_audit_run(
            pool,
            scope=scope_str,
            worker_pool=(_WASM_POOL if is_wasm else worker_pool),
            trigger="manual",
            note=note,
            created_by=user.sub,
            force_rebuild=force_rebuild,
            auto_validate=auto_validate,
        )
        if is_wasm:
            # Parity cells are a worker-only concern (no browser parity
            # engine), so a WASM run is always the full conversion grid —
            # validate_only is ignored here and in the cells endpoint so
            # the run total and the browser's cell list always agree.
            background_tasks.add_task(
                _audit_dispatch_wasm,
                run["id"],
                s,
                pool,
            )
        else:
            background_tasks.add_task(
                _audit_dispatch,
                run["id"],
                s,
                worker_pool,
                user.sub,
                pool,
                force_rebuild,
                validate_only,
            )
        return JSONResponse(run, status_code=202)

    @admin.get("/audit/active")
    async def admin_audit_active(request: Request) -> JSONResponse:
        """Lightweight summary of running audit sweeps. Powers the
        ambient bottom-right badge that links into the Audit Runs
        admin tab; the badge polls this on a 15s cadence, so the
        query needs to stay cheap (one indexed aggregate on the
        ``audit_runs_running_idx`` partial index)."""
        pool = _require_pool(request)
        return JSONResponse(await db_module.active_audit_summary(pool))

    @admin.get("/audit/runs")
    async def admin_audit_runs_list(
        request: Request,
        limit: int = 50,
        before_started_at: str | None = None,
    ) -> JSONResponse:
        pool = _require_pool(request)
        runs = await db_module.list_audit_runs(
            pool,
            limit=limit,
            before_started_at=before_started_at,
        )
        next_before = runs[-1]["started_at"] if len(runs) >= max(1, min(limit, 200)) else None
        return JSONResponse({"runs": runs, "next_before_started_at": next_before})

    @admin.get("/audit/runs/{run_id}")
    async def admin_audit_run_get(
        run_id: str,
        request: Request,
    ) -> JSONResponse:
        pool = _require_pool(request)
        run = await db_module.get_audit_run(pool, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")
        jobs = await db_module.list_audit_run_jobs(pool, run_id)
        return JSONResponse({"run": run, "jobs": jobs})

    @admin.get("/audit/runs/{run_id}/parity")
    async def admin_audit_run_parity(
        run_id: str,
        request: Request,
    ) -> JSONResponse:
        pool = _require_pool(request)
        rows = await db_module.list_audit_run_parity(pool, run_id)
        return JSONResponse({"run_id": run_id, "parity": rows})

    @admin.post("/audit/runs/{run_id}/cancel")
    async def admin_audit_run_cancel(
        run_id: str,
        request: Request,
    ) -> JSONResponse:
        """Abort a running audit. Flips ``status='aborted'`` and
        cancels every queued / running child cell. No-op (404) if
        the run is already terminal — re-cancelling a finished or
        already-aborted run isn't useful.

        Late worker completions arriving after the abort still bump
        counters (so the per-cell grid keeps growing), but the run
        won't auto-flip back to ``finished``."""
        pool = _require_pool(request)
        run = await db_module.abort_audit_run(pool, run_id)
        if run is None:
            raise HTTPException(
                status_code=404,
                detail="audit run not found or not in running state",
            )
        return JSONResponse(run)

    @admin.post("/audit/runs/{run_id}/re-dispatch")
    async def admin_audit_run_re_dispatch(
        run_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Re-run a prior audit against the same scope / pool / settings.

        Creates a fresh run that mirrors the prior one's ``scope``,
        ``worker_pool``, ``force_rebuild`` and ``auto_validate`` (linked via
        ``parent_run_id``), then dispatches it the same way the original was
        (NATS workers, or the browser WASM engine for a ``wasm`` pool). The
        cell set is re-enumerated from the scope at dispatch time, so a
        re-dispatch reflects the scope's current files — not a frozen copy."""
        pool = _require_pool(request)
        prior = await db_module.get_audit_run(pool, run_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="audit run not found")

        scope_str = prior["scope"]
        worker_pool = prior["worker_pool"]
        is_wasm = isinstance(worker_pool, str) and worker_pool.strip().lower() == _WASM_POOL
        if not is_wasm and not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")

        s = _parse_scope(scope_str, user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")

        run = await db_module.create_audit_run(
            pool,
            scope=scope_str,
            worker_pool=worker_pool,
            trigger="re-dispatch",
            note=f"re-run of {run_id[:8]}",
            created_by=user.sub,
            force_rebuild=prior["force_rebuild"],
            auto_validate=prior["auto_validate"],
            parent_run_id=run_id,
        )
        if is_wasm:
            background_tasks.add_task(_audit_dispatch_wasm, run["id"], s, pool)
        else:
            background_tasks.add_task(
                _audit_dispatch,
                run["id"],
                s,
                worker_pool,
                user.sub,
                pool,
                prior["force_rebuild"],
                False,
            )
        return JSONResponse(run, status_code=202)

    @admin.post("/audit/runs/{run_id}/validate")
    async def admin_audit_run_validate(
        run_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Append a validation (cross-format parity) pass to a finished run —
        the manual counterpart to the auto-validate toggle. Grows the run's
        total + reopens it, then enqueues the parity cells under the same run
        id. 409 if the run isn't finished or has already been validated (the
        pass runs at most once per run; re-run the audit for a fresh one)."""
        pool = _require_pool(request)
        run = await db_module.get_audit_run(pool, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")

        s = _parse_scope(run["scope"], user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")
        if not queue.enabled and run["worker_pool"] != _WASM_POOL:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")

        claimed = await db_module.claim_run_for_validation(pool, run_id)
        if claimed is None:
            raise HTTPException(
                status_code=409,
                detail="run is not finished, or its validation pass has already been dispatched",
            )
        background_tasks.add_task(
            _audit_dispatch,
            run_id,
            s,
            run["worker_pool"],
            user.sub,
            pool,
            False,  # force_rebuild
            True,  # validate_only
            True,  # extend — append into the existing run
        )
        return JSONResponse(claimed, status_code=202)

    @admin.delete("/audit/runs/{run_id}")
    async def admin_audit_run_delete(
        run_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Delete an audit run and its audit_log rows (parity rows cascade).
        Refuses a still-running run — cancel it first — so an in-flight sweep
        can't be deleted out from under its workers."""
        pool = _require_pool(request)
        run = await db_module.get_audit_run(pool, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")
        if run["status"] == "running":
            raise HTTPException(status_code=409, detail="cancel the run before deleting it")
        deleted = await db_module.delete_audit_run(pool, run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="audit run not found")
        return JSONResponse({"deleted": run_id})

    @admin.get("/audit/cell-history")
    async def admin_audit_cell_history(
        request: Request,
        key: str,
        target: str,
        limit: int = 50,
    ) -> JSONResponse:
        """Historic results for one ``(source key, target_format)`` cell across
        every run — newest first. Drives the grid's right-click 'show history'
        table so an operator can see how one conversion has trended."""
        pool = _require_pool(request)
        rows = await db_module.audit_log_history_for_cell(pool, key, target, limit=limit)
        return JSONResponse({"key": key, "target_format": target, "history": rows})

    @admin.get("/audit/runs/{run_id}/cells")
    async def admin_audit_run_cells(
        run_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Cell matrix for an audit run — drives the browser (WASM)
        sweep executor (section F).

        Returns ``{run_id, scope, cells: [{source_key, target_format,
        done}]}`` where ``done`` flags cells that already have a terminal
        audit row for this run, so a reload resumes (skips finished cells)
        instead of re-running them. Cells come from the same enumeration
        the dispatcher used, so the list matches the run's total.
        """
        pool = _require_pool(request)
        # get_audit_run takes a UUID column; a malformed id would raise
        # deep in asyncpg — treat any lookup miss as 404.
        try:
            run = await db_module.get_audit_run(pool, run_id)
        except Exception:
            run = None
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")

        scope_str = run["scope"]
        s = _parse_scope(scope_str, user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")

        try:
            cells = await _audit_run_list_cells(s, validate_only=False)
        except Exception as exc:
            logger.exception("audit run %s: cell enumeration failed", run_id)
            raise HTTPException(status_code=503, detail=f"scope listing failed: {exc}") from exc

        jobs = await db_module.list_audit_run_jobs(pool, run_id)
        _terminal = {"done", "ok", "error", "skipped", "cancelled"}
        done_set = {(j["key"], j["target_format"]) for j in jobs if j["status"] in _terminal}
        out = [{"source_key": k, "target_format": t, "done": (k, t) in done_set} for (k, t) in cells]
        return JSONResponse({"run_id": run_id, "scope": scope_str, "cells": out})

    # ── Corpora (M3 admin audit panel) ────────────────────────────────
    #
    # GET    /admin/corpora               list live corpora
    # POST   /admin/corpora               create a corpus
    # DELETE /admin/corpora/{slug}        archive (soft-delete)
    #
    # Per-corpus file management reuses the existing
    # ``/api/scopes/{scope}/files`` family — corpus is just another
    # ScopeKind, so listing / uploading / downloading bytes flows
    # through the same code paths as user / project scopes (now gated
    # by ``is_admin`` via scope_can_access).

    _SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

    @admin.get("/corpora")
    async def admin_corpora_list(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        rows = await db_module.list_corpora(pool)
        return JSONResponse({"corpora": rows})

    @admin.post("/corpora")
    async def admin_corpora_create(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Create a new corpus.

        Body: ``{"slug": "cad-baseline", "name": "...",
                 "description": "..." }``.

        ``slug`` is lowercase ASCII with hyphen separators — used in
        URLs (``corpus:cad-baseline``) and storage prefixes
        (``corpus/cad-baseline/``). Duplicate-against-live returns 409
        via the partial unique index on ``corpora.slug``.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        slug = (body.get("slug") or "").strip().lower()
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip() or None
        if not slug or not _SLUG_RE.match(slug):
            raise HTTPException(
                status_code=400,
                detail=("slug must be lowercase ASCII with hyphen separators " "(e.g. 'cad-baseline')"),
            )
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        try:
            row = await db_module.create_corpus(
                pool,
                slug=slug,
                name=name,
                description=description,
                created_by=user.sub,
            )
        except Exception as exc:
            # asyncpg surfaces unique-violation via ``UniqueViolationError``;
            # treat that specifically as 409 instead of a generic 500.
            if exc.__class__.__name__ == "UniqueViolationError":
                raise HTTPException(
                    status_code=409,
                    detail=f"corpus slug {slug!r} already in use",
                ) from exc
            raise
        return JSONResponse(row, status_code=201)

    @admin.delete("/corpora/{slug}")
    async def admin_corpora_archive(slug: str, request: Request) -> JSONResponse:
        """Soft-delete a corpus by slug. Storage bytes are NOT wiped —
        the operator handles that out-of-band if disk pressure
        matters. The slug becomes available for reuse immediately
        because the uniqueness index is partial-on-live."""
        pool = _require_pool(request)
        ok = await db_module.archive_corpus(pool, slug)
        if not ok:
            raise HTTPException(status_code=404, detail=f"corpus {slug!r} not found")
        return JSONResponse({"slug": slug, "archived": True})

    # ── Audit schedules (M4 admin audit panel) ────────────────────────
    #
    # GET    /admin/audit/schedules            list live schedules
    # POST   /admin/audit/schedules            create a schedule
    # PATCH  /admin/audit/schedules/{id}       partial update
    # DELETE /admin/audit/schedules/{id}       soft-archive
    # POST   /admin/audit/schedules/{id}/fire  fire-now (bypasses cron)
    #
    # The actual firing happens via the scheduler background task
    # (see ``_scheduler_loop`` above). These endpoints just CRUD the
    # rows + offer a manual override for "fire this schedule right
    # now" which the admin UI binds to a button.

    @admin.get("/audit/schedules")
    async def admin_audit_schedules_list(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        rows = await db_module.list_audit_schedules(pool)
        return JSONResponse({"schedules": rows})

    @admin.post("/audit/schedules")
    async def admin_audit_schedules_create(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Create a new schedule.

        Body: ``{"name": "...", "cron_expr": "0 2 * * *",
                 "scope": "corpus:cad-baseline",
                 "worker_pool": "audit" | null,
                 "enabled": true}``.

        ``cron_expr`` is validated via croniter; the next fire instant
        is computed and stored so the scheduler tick can pick it up
        on its very next pass without re-parsing.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        name = (body.get("name") or "").strip()
        cron_expr = _validate_cron(body.get("cron_expr") or "")
        scope_str = (body.get("scope") or "").strip()
        worker_pool = body.get("worker_pool") or None
        enabled = bool(body.get("enabled", True))
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if not scope_str:
            raise HTTPException(status_code=400, detail="scope required")
        # Validate scope-string parses (raises 400 with detail). Don't
        # resolve project slugs to ids yet — slug→id resolution
        # happens at fire time so renaming a project doesn't strand
        # a schedule.
        _ = _parse_scope(scope_str, user)
        next_fire = _next_fire(cron_expr)
        try:
            row = await db_module.create_audit_schedule(
                pool,
                name=name,
                cron_expr=cron_expr,
                scope=scope_str,
                worker_pool=worker_pool,
                next_fire_at=next_fire,
                enabled=enabled,
                created_by=user.sub,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "UniqueViolationError":
                raise HTTPException(
                    status_code=409,
                    detail=f"schedule name {name!r} already in use",
                ) from exc
            raise
        return JSONResponse(row, status_code=201)

    @admin.patch("/audit/schedules/{schedule_id}")
    async def admin_audit_schedules_update(
        schedule_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Partial update. Recognised fields: ``name``, ``cron_expr``,
        ``scope``, ``worker_pool``, ``enabled``. Editing ``cron_expr``
        recomputes ``next_fire_at`` from the current instant so a
        retimed schedule fires from the new pattern immediately
        instead of waiting for the old slot."""
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        kwargs: dict = {}
        if "name" in body:
            val = (body.get("name") or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            kwargs["name"] = val
        new_cron: str | None = None
        if "cron_expr" in body:
            new_cron = _validate_cron(body.get("cron_expr") or "")
            kwargs["cron_expr"] = new_cron
        if "scope" in body:
            val = (body.get("scope") or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail="scope cannot be empty")
            _ = _parse_scope(val, user)
            kwargs["scope"] = val
        if "worker_pool" in body:
            kwargs["worker_pool"] = body.get("worker_pool") or None
            kwargs["worker_pool_set"] = True
        if "enabled" in body:
            kwargs["enabled"] = bool(body["enabled"])
        if new_cron is not None:
            kwargs["next_fire_at"] = _next_fire(new_cron)
            kwargs["next_fire_at_set"] = True
        try:
            row = await db_module.update_audit_schedule(pool, schedule_id, **kwargs)
        except Exception as exc:
            if exc.__class__.__name__ == "UniqueViolationError":
                raise HTTPException(
                    status_code=409,
                    detail="schedule name already in use",
                ) from exc
            raise
        if row is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        return JSONResponse(row)

    @admin.delete("/audit/schedules/{schedule_id}")
    async def admin_audit_schedules_archive(
        schedule_id: str,
        request: Request,
    ) -> JSONResponse:
        pool = _require_pool(request)
        ok = await db_module.archive_audit_schedule(pool, schedule_id)
        if not ok:
            raise HTTPException(status_code=404, detail="schedule not found")
        return JSONResponse({"id": schedule_id, "archived": True})

    @admin.post("/audit/schedules/{schedule_id}/fire")
    async def admin_audit_schedules_fire_now(
        schedule_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Manual "fire now" — dispatch the schedule's scope without
        waiting for the next cron slot. Honours the concurrent-fire
        guard so a "fire now" while another run is still in flight
        returns 409 instead of stacking workloads.

        Does NOT advance ``next_fire_at`` — the next scheduled slot
        still fires as planned. Useful for testing a freshly-created
        schedule or for backfilling after fixing a broken corpus.
        """
        if not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="conversion disabled (no NATS configured)",
            )
        pool = _require_pool(request)
        row = await db_module.get_audit_schedule(pool, schedule_id)
        if row is None or row["archived_at"] is not None:
            raise HTTPException(status_code=404, detail="schedule not found")
        scope_str = row["scope"]
        worker_pool = row["worker_pool"]
        s = _parse_scope(scope_str, user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")
        if await db_module.audit_run_exists_for_key(pool, scope_str, worker_pool):
            raise HTTPException(
                status_code=409,
                detail="another audit run with this (scope, pool) is still running",
            )
        run = await db_module.create_audit_run(
            pool,
            scope=scope_str,
            worker_pool=worker_pool,
            trigger="manual",  # operator-initiated even though it's a schedule
            note=f"fire-now: {row['name']}",
            created_by=user.sub,
        )
        background_tasks.add_task(
            _audit_dispatch,
            run["id"],
            s,
            worker_pool,
            user.sub,
            pool,
        )
        return JSONResponse(run, status_code=202)

    # ── Issue target configuration (M5) ───────────────────────────
    #
    # Tokens are deployed via env vars (typically populated from a
    # k8s Secret). The DB stores only the env var name, never the
    # raw token. ``GET`` reports whether the configured env var is
    # currently set on this API process so the admin sees "token
    # configured" vs "token env var missing".

    _ISSUE_TARGET_KINDS: frozenset[str] = frozenset({"disabled", "github", "forgejo"})

    @admin.get("/audit/issue-target")
    async def admin_issue_target_get(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        kind = await db_module.get_setting(pool, _ISSUE_KIND_KEY) or "disabled"
        repo = await db_module.get_setting(pool, _ISSUE_REPO_KEY) or ""
        base_url = await db_module.get_setting(pool, _ISSUE_BASE_URL_KEY) or ""
        token_env = await db_module.get_setting(pool, _ISSUE_TOKEN_ENV_KEY) or ""
        # ``token_present`` is the truthy-state of the env var on the
        # currently-serving replica. Replicas with different env
        # would disagree here — that's fine, the UI label is "as
        # seen by this API process".
        token_present = bool(token_env and os.environ.get(token_env))
        return JSONResponse(
            {
                "kind": kind,
                "repo": repo,
                "base_url": base_url,
                "token_env_name": token_env,
                "token_present": token_present,
            }
        )

    @admin.put("/audit/issue-target")
    async def admin_issue_target_set(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Overwrite the four issue-target settings atomically.

        Body: ``{"kind": "github"|"forgejo"|"disabled", "repo": "owner/name",
                 "base_url": "...", "token_env_name": "..."}``.

        We never accept a raw ``token`` field here — credentials live
        in env vars (sourced from k8s Secrets); the operator changes
        the actual token by rotating the Secret + re-rolling the
        deployment, not via this endpoint.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        kind = (body.get("kind") or "disabled").strip().lower()
        if kind not in _ISSUE_TARGET_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"kind must be one of {sorted(_ISSUE_TARGET_KINDS)}",
            )
        repo = (body.get("repo") or "").strip()
        base_url = (body.get("base_url") or "").strip()
        token_env = (body.get("token_env_name") or "").strip()
        if kind != "disabled":
            if not repo or "/" not in repo:
                raise HTTPException(
                    status_code=400,
                    detail="repo must be 'owner/name' when kind is not disabled",
                )
            if kind == "forgejo" and not base_url:
                raise HTTPException(
                    status_code=400,
                    detail=("base_url required for forgejo " "(e.g. https://git.example.com/api/v1)"),
                )
            if not token_env:
                raise HTTPException(
                    status_code=400,
                    detail="token_env_name required when kind is not disabled",
                )
        await db_module.set_setting(pool, _ISSUE_KIND_KEY, kind, updated_by=user.sub)
        await db_module.set_setting(pool, _ISSUE_REPO_KEY, repo, updated_by=user.sub)
        await db_module.set_setting(pool, _ISSUE_BASE_URL_KEY, base_url, updated_by=user.sub)
        await db_module.set_setting(pool, _ISSUE_TOKEN_ENV_KEY, token_env, updated_by=user.sub)
        token_present = bool(token_env and os.environ.get(token_env))
        return JSONResponse(
            {
                "kind": kind,
                "repo": repo,
                "base_url": base_url,
                "token_env_name": token_env,
                "token_present": token_present,
            }
        )

    @admin.post("/audit/runs/{run_id}/sync-issues")
    async def admin_audit_run_sync_issues(
        run_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        """Manually retry the issue-bot for one run. Clears the
        run's ``issue_bot_status`` so the next poller tick picks it
        up — also kicks off an immediate sync as a BackgroundTask so
        the user doesn't have to wait the full 30 s for the poller."""
        pool = _require_pool(request)
        run = await db_module.get_audit_run(pool, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="audit run not found")
        if run["status"] != "finished":
            raise HTTPException(
                status_code=400,
                detail="run is not finished; sync only meaningful on finished runs",
            )
        ok = await db_module.reset_audit_run_issue_bot(pool, run_id)
        if not ok:
            raise HTTPException(status_code=409, detail="reset failed (race?)")

        # Kick the bot immediately for snappier feedback. The poller
        # would catch it on its next tick anyway, but the user just
        # clicked a button and waiting 30s is unfriendly.
        async def _kick() -> None:
            claimed = await db_module.claim_audit_run_for_issue_bot(pool)
            if claimed is not None:
                await _run_issue_bot_for(pool, claimed)

        background_tasks.add_task(_kick)
        return JSONResponse({"id": run_id, "status": "queued"}, status_code=202)

    @admin.post("/audit/{audit_id}/sync-issue")
    async def admin_audit_log_sync_issue(
        audit_id: int,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> JSONResponse:
        """Manually retry the issue-bot for ONE failed conversion
        (M5b). Mirror of the per-run sync endpoint; resets the
        row's issue_bot_status and kicks an immediate sync as a
        background task so the operator gets quick feedback."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        if row.get("status") not in ("error", "failed"):
            raise HTTPException(
                status_code=400,
                detail="row is not in a failed state; sync only meaningful on failures",
            )
        ok = await db_module.reset_audit_log_issue_bot(pool, audit_id)
        if not ok:
            raise HTTPException(status_code=409, detail="reset failed (race?)")

        async def _kick() -> None:
            claimed = await db_module.claim_failed_conversion_for_issue_bot(pool)
            if claimed is not None:
                await _run_issue_bot_for_conversion(pool, claimed)

        background_tasks.add_task(_kick)
        return JSONResponse({"id": audit_id, "status": "queued"}, status_code=202)

    # ── Cross-conversion perf dashboard (M6) ──────────────────────
    #
    # GET /admin/audit/perf?since=30&trigger=all
    #   Aggregates audit_log convert rows over the last N days,
    #   returns per-cell metrics + streaming-candidate verdict.
    #
    # GET /admin/audit/perf/thresholds
    # PUT /admin/audit/perf/thresholds
    #   Read / update the streaming-classifier thresholds. Defaults
    #   ship in audit_perf.DEFAULT_THRESHOLDS; admin overrides land
    #   in app_settings under audit.perf.thresholds.<key>.

    _PERF_TRIGGERS: frozenset[str] = frozenset({"all", "audit", "user"})

    async def _load_perf_thresholds(pool) -> dict:
        """Read the admin-overridable thresholds from app_settings,
        layered on top of the ``audit_perf.DEFAULT_THRESHOLDS``. Keys
        live under ``audit.perf.thresholds.<short_name>``; values are
        stored as JSON-encoded floats so a typo'd string can't sneak
        through to the classifier."""
        from . import audit_perf

        overrides: dict[str, float] = {}
        for key in audit_perf.DEFAULT_THRESHOLDS:
            raw = await db_module.get_setting(
                pool,
                f"audit.perf.thresholds.{key}",
            )
            if raw is None:
                continue
            try:
                overrides[key] = float(raw)
            except (TypeError, ValueError):
                continue
        return audit_perf.merged_thresholds(overrides)

    @admin.get("/audit/perf")
    async def admin_audit_perf(
        request: Request,
        since: int = 30,
        trigger: str = "all",
        audit_run_id: str | None = None,
        worker_image_tag: str | None = None,
    ) -> JSONResponse:
        """Cross-conversion perf snapshot. ``since`` is days back from
        now; ``trigger`` is one of ``all`` / ``audit`` / ``user``.

        ``audit_run_id`` locks the snapshot to one sweep; pair with
        ``worker_image_tag`` to lock it to one worker build (so an
        upgrade between the same-named runs doesn't smear results).

        Response shape:

        ``{"cells": [...with streaming verdict],
           "thresholds": {...effective},
           "since_days": N,
           "trigger": "...",
           "audit_run_id": ... | None,
           "worker_image_tag": ... | None,
           "generated_at": "ISO-8601"}``

        Every cell in ``cells`` carries a ``streaming`` field
        (``{"is_candidate": bool, "signals": [...]}``) so the UI can
        render the badge without an extra round trip.
        """
        from datetime import datetime, timezone

        from . import audit_perf

        pool = _require_pool(request)
        trig = (trigger or "all").strip().lower()
        if trig not in _PERF_TRIGGERS:
            raise HTTPException(
                status_code=400,
                detail=f"trigger must be one of {sorted(_PERF_TRIGGERS)}",
            )
        run_id = (audit_run_id or "").strip() or None
        worker_tag = (worker_image_tag or "").strip() or None
        cells = await db_module.aggregate_conversion_metrics(
            pool,
            since_days=since,
            trigger=None if trig == "all" else trig,
            audit_run_id=run_id,
            worker_image_tag=worker_tag,
        )
        thresholds = await _load_perf_thresholds(pool)
        annotated = audit_perf.annotate(cells, thresholds=thresholds)
        return JSONResponse(
            {
                "cells": annotated,
                "thresholds": thresholds,
                "signal_reasons": audit_perf.SIGNAL_REASONS,
                "since_days": max(1, min(365, since)),
                "trigger": trig,
                "audit_run_id": run_id,
                "worker_image_tag": worker_tag,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @admin.get("/audit/perf/workers")
    async def admin_audit_perf_workers(
        request: Request,
        since: int = 90,
    ) -> JSONResponse:
        """Distinct ``worker_image_tag`` values seen in the perf
        window, with the row count + most recent timestamp for each.
        Drives the PerformanceTab "Worker SHA" picker so the user
        only sees tags that have real data behind them. Sorted by
        ``last_seen`` desc — the freshest build first.
        """
        pool = _require_pool(request)
        days = max(1, min(365, since))
        rows = await pool.fetch(
            """
            SELECT worker_image_tag AS tag,
                   COUNT(*)        AS samples,
                   MAX(ts)         AS last_seen
            FROM audit_log
            WHERE action = 'convert'
              AND worker_image_tag IS NOT NULL
              AND ts > NOW() - ($1 * INTERVAL '1 day')
            GROUP BY worker_image_tag
            ORDER BY last_seen DESC
            """,
            days,
        )
        workers = [
            {
                "tag": r["tag"],
                "samples": int(r["samples"] or 0),
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            }
            for r in rows
        ]
        return JSONResponse({"workers": workers, "since_days": days})

    @admin.get("/audit/perf/thresholds")
    async def admin_perf_thresholds_get(request: Request) -> JSONResponse:
        """Effective streaming-classifier thresholds (defaults +
        admin overrides). Returned alongside the per-key defaults so
        the editor can show "reset to default" deltas."""
        from . import audit_perf

        pool = _require_pool(request)
        return JSONResponse(
            {
                "thresholds": await _load_perf_thresholds(pool),
                "defaults": audit_perf.DEFAULT_THRESHOLDS,
            }
        )

    @admin.put("/audit/perf/thresholds")
    async def admin_perf_thresholds_set(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Overwrite thresholds. Body: ``{"<key>": <float>, ...}``.

        Unknown keys are rejected with 400 so a typo doesn't quietly
        disable a signal. Pass ``null`` for a key to clear an
        override (the default takes over). All writes happen against
        the same ``app_settings`` table the rest of the admin
        settings use.
        """
        from . import audit_perf

        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        unknown = sorted(set(body.keys()) - set(audit_perf.DEFAULT_THRESHOLDS))
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown threshold keys: {unknown}",
            )
        for key, raw in body.items():
            setting_key = f"audit.perf.thresholds.{key}"
            if raw is None:
                # Clear → write the empty string; get_setting + float()
                # treat that as "no override" because the float()
                # coercion fails. Cleanest path without adding a
                # dedicated delete helper.
                await db_module.set_setting(
                    pool,
                    setting_key,
                    "",
                    updated_by=user.sub,
                )
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"{key}: must be a number ({exc})",
                ) from exc
            await db_module.set_setting(
                pool,
                setting_key,
                str(val),
                updated_by=user.sub,
            )
        return JSONResponse(
            {
                "thresholds": await _load_perf_thresholds(pool),
                "defaults": audit_perf.DEFAULT_THRESHOLDS,
            }
        )

    @admin.get("/audit/perf/hotspots")
    async def admin_audit_perf_hotspots(
        request: Request,
        source_ext: str | None = None,
        target_format: str | None = None,
        since: int = 30,
        limit: int = 25,
    ) -> JSONResponse:
        """Function-level hot paths inside one cell, aggregated across
        every cProfile-tagged conversion in the window.

        ``source_ext`` and ``target_format`` narrow the join to one
        (source × target) cell; omit either to aggregate across all
        cells (useful for "what's slow overall" exploratory views).
        Returns the top N functions by SUMmed cumulative time —
        same shape pstats uses, just rolled up.

        Data only exists once ``profile_conversions=true`` is set on
        the app settings (global) or per-job, AND the background
        profile-parser loop has caught up with the new .prof blobs.
        ``profiles_in_window=0`` flags the "profiling disabled or
        nothing parsed yet" empty state cleanly.
        """
        pool = _require_pool(request)
        out = await db_module.aggregate_profile_hotspots(
            pool,
            source_ext=source_ext,
            target_format=target_format,
            since_days=since,
            limit=limit,
        )
        return JSONResponse(
            {
                "source_ext": source_ext,
                "target_format": target_format,
                **out,
            }
        )

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
        return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)

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
        return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)

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
        import pathlib as _pl
        import pstats

        tmp = _pl.Path(tempfile.mkstemp(suffix=".prof")[1])
        try:
            tmp.write_bytes(data)
            try:
                stats = pstats.Stats(str(tmp))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"failed to parse profile: {exc}") from exc
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
            rows.append(
                {
                    "func": name,
                    "file": fn,
                    "line": line,
                    "ncalls": nc,
                    "primitive_calls": cc,
                    "tottime": tt,
                    "percall_tot": (tt / nc) if nc else 0.0,
                    "cumtime": ct,
                    "percall_cum": (ct / cc) if cc else 0.0,
                }
            )
        # Default presentation sort: cumtime desc — same as pstats default.
        rows.sort(key=lambda r: r["cumtime"], reverse=True)
        if limit and len(rows) > limit:
            rows = rows[:limit]
        return JSONResponse(
            {
                "audit_id": audit_id,
                "total_tottime": total_tt,
                "row_count": len(rows),
                "rows": rows,
            }
        )

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
                    entry["profile_key"],
                    exc,
                )
                blob_errors.append(f"{entry['profile_key']}: {exc}")
        return JSONResponse(
            {
                "rows_cleared": result["rows_cleared"],
                "profiles_deleted": deleted_blobs,
                "errors": blob_errors,
            }
        )

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
        return JSONResponse({"members": await db_module.list_project_members(pool, pid)})

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
        ".sin": "Sesam Result (sin, Norsam binary)",
    }

    def _format_label(key: str) -> str:
        ext = pathlib.PurePosixPath(key).suffix.lower()
        return _SOURCE_FORMAT_NAMES.get(ext, ext.lstrip(".").upper() or "—")

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
                parsed = derived_source_of(f.key)
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
        result = await delete_blob_cascade(storage, scope_obj, key)
        await _audit(
            request,
            user,
            scope_obj,
            "delete",
            key=key.lstrip("/"),
            status="ok",
            error="; ".join(result["errors"]) or None,
        )
        return JSONResponse(result)

    @admin.post("/scopes/{scope}/keys/move-to-folder")
    async def admin_keys_move_to_folder(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Batch-move source keys to a destination folder prefix.

        Body: ``{"keys": [...], "folder": "..."}``. Each source key
        is renamed to ``<folder>/<basename(src_key)>`` within the
        same scope, with derived siblings cascading (see
        storage_ops.move_keys_to_folder). Per-key failures don't
        abort the batch — the caller gets ``{moved, failed}``.
        """

        keys, folder = await _parse_move_body(request)
        result = await move_keys_to_folder(storage, scope_obj, keys, folder)
        for entry in result["moved"]:
            await _audit(
                request,
                user,
                scope_obj,
                "move",
                key=entry["old"],
                status="ok",
                error="; ".join(entry["siblings_failed"]) or None,
            )
        return JSONResponse(result)

    @admin.post("/scopes/{scope}/keys/rename")
    async def admin_keys_rename(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Rename a single source key (derived siblings cascade)."""
        old_key, new_key = await _parse_rename_body(request)
        result = await _rename_with_status(scope_obj, old_key, new_key)
        await _audit(request, user, scope_obj, "rename", key=old_key, status="ok")
        return JSONResponse(result)

    @admin.post("/scopes/{scope}/keys/copy-from")
    async def admin_keys_copy_from(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),  # destination scope (e.g. a corpus)
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Server-side copy source keys from another scope into this one.

        Body: ``{"src_scope": "user:me", "keys": [...]}``. Each key is copied
        (Garage / S3 CopyObject — no download/reupload) from ``src_scope`` to the
        same key in the path scope. The caller must be able to read ``src_scope``.
        Per-key reporting: ``{copied, failed}`` — a collision (target exists),
        missing source, or backend error doesn't abort the batch.
        """
        from .converter import is_derived_key

        pool = getattr(request.app.state, "db_pool", None)
        body = await request.json()
        src_raw = body.get("src_scope")
        raw_keys = body.get("keys")
        if not isinstance(src_raw, str) or not src_raw.strip():
            raise HTTPException(status_code=400, detail="src_scope required")
        if not isinstance(raw_keys, list) or not raw_keys:
            raise HTTPException(status_code=400, detail="keys must be a non-empty list")
        if any(not isinstance(k, str) or not k.strip() for k in raw_keys):
            raise HTTPException(status_code=400, detail="every key must be a non-empty string")

        src_scope = await _resolve_project_scope(pool, _parse_scope(src_raw.strip(), user))
        if not await scope_can_access(user, src_scope, pool):
            raise HTTPException(status_code=403, detail="forbidden: source scope")
        if src_scope.prefix() == scope_obj.prefix():
            raise HTTPException(status_code=400, detail="source and destination scope are the same")

        # Dedup while preserving order.
        seen: set[str] = set()
        keys: list[str] = []
        for raw in raw_keys:
            cleaned = raw.strip().lstrip("/")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                keys.append(cleaned)

        # Snapshot destination keys so we can skip collisions without a HEAD per file.
        dst_keys = {f.key for f in await storage.list(scope_obj)}

        copied: list[dict] = []
        failed: list[dict] = []
        for key in keys:
            if is_derived_key(key):
                failed.append({"key": key, "reason": "cannot copy derived blobs"})
                continue
            if key in dst_keys:
                failed.append({"key": key, "reason": "target already exists"})
                continue
            try:
                # overwrite=True for the same S3 reason as rename above (the safe
                # default raises ``copy-if-not-exists not supported``); the
                # application-layer dst_keys pre-check is the real collision guard.
                await storage.copy(src_scope, key, scope_obj, key, overwrite=True)
            except Exception as exc:
                logger.exception("admin: copy failed for %s (%s -> %s)", key, src_raw, scope_obj.prefix())
                failed.append({"key": key, "reason": str(exc)})
                continue
            dst_keys.add(key)
            copied.append({"key": key})
            await _audit(request, user, scope_obj, "copy", key=key, status="ok")

        return JSONResponse({"copied": copied, "failed": failed})

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
        extra_source_exts = await _worker_advertised_exts()
        streaming_only_exts = sorted(e for e in extra_source_exts if e not in LEGACY_CONVERT_EXTS)
        conversion_matrix = await _worker_advertised_conversions()

        adapy_version = _resolve_adapy_version()

        a = settings.auth
        body = (
            'window.COMMS_MODE = "rest";\n'
            'window.API_BASE = "/api";\n'
            f'window.CONVERT_ENABLED = {"true" if queue.enabled else "false"};\n'
            f'window.AUTH_ENABLED = {"true" if a.enabled else "false"};\n'
            f"window.AUTH_ISSUER = {_json.dumps(a.issuer)};\n"
            f"window.AUTH_CLIENT_ID = {_json.dumps(a.client_id)};\n"
            f"window.AUTH_AUDIENCE = {_json.dumps(a.audience)};\n"
            f"window.AUTH_SCOPE = {_json.dumps(a.scope)};\n"
            f"window.VIEWER_IMAGE_TAG = {_json.dumps(viewer_tag)};\n"
            f"window.WORKER_IMAGE_TAG = {_json.dumps(worker_tag)};\n"
            f"window.ADAPY_VERSION = {_json.dumps(adapy_version)};\n"
            f"window.EXTRA_SOURCE_EXTS = {_json.dumps(extra_source_exts)};\n"
            f"window.STREAMING_ONLY_EXTS = {_json.dumps(streaming_only_exts)};\n"
            f"window.CONVERSION_MATRIX = {_json.dumps(conversion_matrix)};\n"
        )
        # config.js is the SPA's source of truth for runtime config
        # (worker registry → extraSourceExts / streamingOnlyExts, image
        # tags, auth) — content changes between requests as workers come
        # and go. Without explicit no-store, Safari iOS / Chrome cache
        # it heuristically and the SPA keeps reading stale window.* on
        # every reload. Observed symptom: an .odb upload routes to
        # /convert (415) instead of /fea/manifest because a cached
        # config.js predates the worker registering its plug-in.
        return PlainTextResponse(
            body,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

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
