from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from ada.config import logger
from ada.core.file_system import new_temp_path

from . import auth as auth_module
from . import db as db_module
from . import failure_capture, local_jobs, pending_uploads
from .auth import User
from .config import Settings, load_settings
from .converter import (
    LEGACY_CONVERT_EXTS,
    TARGET_FORMATS,
    ConverterRegistry,
    UnsupportedFormat,
    derived_key_for,
    merge_option_into,
    supported_targets_for,
)
from .handlers import dispatch
from .plugin_registry import discover_local_plugins
from .queue import JobQueue
from .routes.admin_audit_perf import router as admin_audit_perf_router
from .routes.admin_audit_perf import run_issue_bot_for, run_issue_bot_for_conversion
from .routes.admin_audit_runs import (
    WASM_POOL,
    audit_cells_for_files,
    audit_dispatch,
    audit_dispatch_wasm,
    audit_run_list_cells,
)
from .routes.admin_audit_runs import router as admin_audit_runs_router
from .routes.admin_audit_schedules import router as admin_audit_schedules_router
from .routes.admin_corpora import router as admin_corpora_router
from .routes.admin_plugin_jobs import plugin_schedule_fire
from .routes.admin_plugin_jobs import router as admin_plugin_jobs_router
from .routes.admin_projects import router as admin_projects_router
from .routes.admin_settings import router as admin_settings_router
from .routes.admin_storage_compression import router as admin_storage_compression_router
from .routes.admin_workers import router as admin_workers_router
from .routes.deps import (  # noqa: F401 — _merge_spec re-exported for tests/importers of the old name
    CAPABILITY_REQUIREMENTS_SETTING,
    DIRECT_UPLOAD_THRESHOLD_BYTES,
    GZIP_UPLOAD_EXTS,
    RestContext,
    SystemUser,
    _merge_spec,
    content_encoding_for,
    format_label,
    human_bytes,
    is_accepted_source,
    live_worker_specs,
    next_fire,
    parse_move_body,
    parse_rename_body,
    parse_scope,
    pending_upload_detail,
    publish_capability_requirements,
    require_catalog_pool,
    require_pool,
    resolve_project_scope,
    scope_from_header,
    scope_from_path,
    validate_cron,
    worker_advertised_exts,
)
from .routes.fea import router as fea_router
from .routes.plugin_jobs import enqueue_plugin_job
from .routes.plugin_jobs import router as plugin_jobs_router
from .routes.plugins import router as plugins_router
from .routes.procedural_models import router as procedural_models_router
from .routes.projects import router as projects_router
from .routes.source_nodes import router as source_nodes_router
from .routes.storage import rename_with_status
from .routes.storage import router as storage_router
from .scope import Scope
from .scope import can_access as scope_can_access
from .storage import Storage
from .storage_ops import delete_blob_cascade, derived_source_of, move_keys_to_folder

# Text-heavy CAD/FEM formats compress 5–10× with gzip; binary mesh
# formats already pack their geometry tightly so we skip them. The
# The gzip-upload extension set, the move/rename body parsers, the
# content-encoding-for-key helper, the direct-upload cap and the
# presigned-URL TTL all live in routes/deps.py now (routes/storage.py
# shares them); the old module names stay bound below for the routes
# still in this closure (the admin storage-compression sweep and the
# admin key move/rename routes).
_GZIP_UPLOAD_EXTS = GZIP_UPLOAD_EXTS
_DIRECT_UPLOAD_THRESHOLD_BYTES: int = DIRECT_UPLOAD_THRESHOLD_BYTES
_parse_move_body = parse_move_body
_parse_rename_body = parse_rename_body
_content_encoding_for = content_encoding_for


# ``human_bytes`` / ``pending_upload_detail`` live in routes/deps.py (needed by
# routes/plugin_jobs.py's ``POST /plugins/{id}/jobs``); the old module names
# stay bound here for the routes still in this module that call them.
_human_bytes = human_bytes
_pending_upload_detail = pending_upload_detail

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
        # Plugin backends, the same two ways the worker finds them:
        # ADA_WORKER_PRELOAD (explicit) and the ``ada.plugins`` entry-point group.
        #
        # The API never needed this while every plugin job went to a worker: it
        # only had to ROUTE by capability, which it reads off worker heartbeats.
        # It needs it now because a viewer with no queue runs those jobs itself
        # (see `local_jobs`), and it can only run a plugin it has imported.
        #
        # Both are per-module isolated HERE, which is the opposite of the worker,
        # and deliberately so. A worker exists BECAUSE of its preload, so a failed
        # import there means the pool would sit on the queue answering nothing —
        # better to die loudly. The API exists to serve the viewer; plugin jobs are
        # one endpoint out of dozens. Letting an ImportError out of the lifespan
        # would take the whole viewer down — a crashloop, no storage browser, no
        # scene, no way to even see the error — because one optional plugin backend
        # could not find one of its dependencies. So: log it with the module name
        # and carry on. The failure then degrades to what it actually is, "that
        # capability is missing", and shows up as a 501 from the plugin-job
        # endpoint rather than as an API that will not start.
        preload = os.environ.get("ADA_WORKER_PRELOAD", "").strip()
        if preload:
            import importlib as _importlib

            for mod_name in (m.strip() for m in preload.split(",") if m.strip()):
                logger.info("api: preloading %s", mod_name)
                try:
                    _importlib.import_module(mod_name)
                except Exception:
                    logger.exception("api: preloading %s failed (non-fatal); its plugin jobs will 501", mod_name)
        discover_local_plugins("api")

        # Connect to NATS lazily; a missing URL just disables the queue.
        if queue.enabled:
            try:
                # manage=True: the API owns the JetStream topology
                # (stream + KV bucket) and brings it forward on deploy.
                # Workers connect with manage=False so their credentials
                # need no stream-admin rights.
                await queue.connect(manage=True, name="adapy-viewer-api")
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

        # Reconcile the capability requirements from the database into KV. The
        # admin write publishes directly, but best-effort; doing it again here
        # means a restart repairs a publish that failed, rather than leaving
        # workers gating against a document older than the one an admin sees.
        if app.state.db_pool is not None and queue.enabled:
            try:
                await _publish_capability_requirements(
                    await db_module.get_setting(app.state.db_pool, CAPABILITY_REQUIREMENTS_SETTING)
                )
            except Exception:
                logger.exception("could not reconcile capability requirements at startup")

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
        # Plugin-job scheduler. Same conditions as the audit scheduler above and
        # for the same reason: without a pool there are no schedules to read, and
        # without a queue there is nothing to enqueue onto.
        app.state.plugin_scheduler_task = None
        if app.state.db_pool is not None and queue.enabled:
            app.state.plugin_scheduler_task = asyncio.create_task(
                _plugin_schedule_loop(app.state.db_pool),
                name="plugin-job-scheduler",
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
        # Stale-worker GC. A pod that crashes / scales down can leave its registry entry behind
        # (unregister_worker is best-effort); without pruning these accumulate and pollute the
        # capability matrix. Drop entries unseen for WORKER_PRUNE_AFTER_S (2 days), checked hourly.
        app.state.worker_prune_task = None
        if queue.enabled:
            app.state.worker_prune_task = asyncio.create_task(
                _worker_prune_loop(queue),
                name="worker-prune",
            )
        # Worker-registry snapshot refresher. Prime it once now (a single
        # ~1s NATS scan at boot) so the very first /config.js is warm, then
        # keep it fresh in the background — the config endpoints read the
        # cached snapshot instead of hitting NATS on the request path.
        app.state.worker_registry_task = None
        if queue.enabled:
            await _refresh_worker_registry()
            app.state.worker_registry_task = asyncio.create_task(
                _worker_registry_refresh_loop(),
                name="worker-registry-refresh",
            )
        # Completed-job KV cleanup. Keeps the shared bucket lean (the KV is a
        # transient progress cache; durable history lives in Postgres/S3) so
        # keys() scans stay cheap and never storm NATS again.
        app.state.job_cleanup_task = None
        if queue.enabled:
            app.state.job_cleanup_task = asyncio.create_task(
                _job_cleanup_loop(queue),
                name="job-kv-cleanup",
            )
        yield
        # Cancel scheduler + issue bot first so a tick in flight
        # doesn't try to use a pool / queue that's about to be torn
        # down.
        for attr in (
            "scheduler_task",
            "plugin_scheduler_task",
            "issue_bot_task",
            "profile_parser_task",
            "worker_prune_task",
            "worker_registry_task",
            "job_cleanup_task",
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
    # Cached worker-registry snapshot, refreshed off the request path by
    # ``_worker_registry_refresh_loop``. ``queue.list_workers()`` is an
    # N+1 over NATS KV (list keys, then one round-trip per worker key);
    # the SPA fetches ``/config.js`` (and later ``/api/config``) on its
    # critical startup path, and each of those endpoints derived exts /
    # conversions / utilities by calling ``list_workers()`` 2-3 times.
    # That put 2.5-4s of blocking NATS latency on every page load. The
    # helpers below now read this snapshot synchronously so page load
    # never waits on NATS; the background task keeps it fresh (~3s). The
    # snapshot changes on worker-heartbeat timescales, so a few seconds
    # of staleness is immaterial to the picker / capability matrix.
    # Empty until the first refresh tick (same observable state as a
    # queue-disabled deploy — the SPA falls back to its static set).
    _worker_registry: dict = {"workers": [], "image_tag": None, "ts": 0.0}
    # Per-scope compression-sweep state — see routes/admin_storage.py.
    compression_state: dict = {}
    # Explicit per-app services for extracted routers (routes/*.py) — what
    # they may reach instead of this closure. See routes/__init__.py.
    rest_ctx = RestContext(
        settings=settings,
        storage=storage,
        queue=queue,
        worker_registry=_worker_registry,
        compression_state=compression_state,
    )
    app.state.rest = rest_ctx

    @app.get("/healthz")
    async def healthz() -> Response:
        # Public — load balancers + readiness probes hit this.
        return Response(status_code=200)

    async def _refresh_worker_registry() -> None:
        """Snapshot the worker registry + worker image tag into
        ``_worker_registry``. Defensive: a failed NATS call is logged and
        leaves the previous snapshot in place rather than blanking it."""
        if not queue.enabled:
            return
        try:
            workers = await queue.list_workers()
        except Exception:
            logger.exception("worker registry refresh: list_workers failed")
            return
        image_tag = _worker_registry.get("image_tag")
        try:
            image_tag = await queue.get_meta("worker_image_tag")
        except Exception:
            logger.exception("worker registry refresh: get_meta failed")
        _worker_registry["workers"] = workers
        _worker_registry["image_tag"] = image_tag
        _worker_registry["ts"] = time.time()

    async def _publish_capability_requirements(value: str | None) -> None:
        # routes/deps.py's publish_capability_requirements, bound to this
        # app's queue. routes/admin_settings.py calls the deps function
        # directly via RestContext; kept here under the old name for the
        # lifespan startup publish above.
        await publish_capability_requirements(queue, value)

    async def _is_accepted_source(key: str) -> bool:
        # routes/deps.py's is_accepted_source, bound to this app's queue +
        # worker-registry snapshot. routes/storage.py calls the deps
        # function directly via RestContext; kept here under the old name
        # for the other call sites still inside this closure (convert).
        return await is_accepted_source(queue, _worker_registry, key)

    async def _worker_advertised_exts() -> list[str]:
        # routes/deps.py's worker_advertised_exts, bound to this app's queue
        # + worker-registry snapshot. routes/fea.py calls the deps function
        # directly via RestContext; kept here under the old name for the
        # other call sites still inside this closure.
        return await worker_advertised_exts(queue, _worker_registry)

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
        workers = _worker_registry["workers"]
        merged: dict[str, set[str]] = {}
        # from → target → option-name → option-dict. Per-job knob schemas are unioned across workers
        # by merge_option_into: enum VALUES, each enum_by list, and the per-value label/runtime maps
        # all union, so an engine only one pool can run still appears — fully described — in the list.
        # (Pair with capability routing so the job lands on a pool that actually advertises that
        # engine — see queue source-ext routing.)
        merged_opts: dict[str, dict[str, dict[str, dict]]] = {}
        now = time.time()
        for w in workers:
            # Only LIVE pools define the capability matrix — a dead/stale registration (a pod that
            # crashed or scaled down before unregistering) must not keep advertising engines it no
            # longer runs. Mirrors the routing staleness window; prune_stale_workers GCs them for good.
            hb = w.get("last_heartbeat")
            if not (isinstance(hb, (int, float)) and (now - hb) <= JobQueue.WORKER_STALE_AFTER_S):
                continue
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
                opts_by_target = entry.get("options") or {}
                bucket = merged.setdefault(frm, set())
                for t in tos:
                    if not (isinstance(t, str) and t.strip()):
                        continue
                    target = t.strip().lstrip(".").lower()
                    bucket.add(target)
                    for opt in opts_by_target.get(t) or opts_by_target.get(target) or []:
                        if not isinstance(opt, dict) or not opt.get("name"):
                            continue
                        slot = merged_opts.setdefault(frm, {}).setdefault(target, {})
                        cur = slot.get(opt["name"])
                        if cur is None:
                            slot[opt["name"]] = copy.deepcopy(opt)
                        else:
                            merge_option_into(cur, opt)
        return [
            {
                "from": frm,
                "to": sorted(merged[frm]),
                "options": {tgt: list(by_name.values()) for tgt, by_name in merged_opts.get(frm, {}).items()},
            }
            for frm in sorted(merged)
        ]

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
        workers = _worker_registry["workers"]
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
        # Cached snapshot — no NATS on the request path (see _worker_registry).
        worker_tag: str | None = _worker_registry["image_tag"] if queue.enabled else None
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
                # The adapy git ref this image was built from. Neither
                # identifier above can carry it: viewerImageTag is the
                # ASSEMBLING repo's commit, and the version is stamped from
                # adapy's last release tag -- so a branch cut from a release
                # with no version bump is indistinguishable from the release,
                # and which one is deployed lives only in the inputs of
                # whichever CI run built it. Empty on a build not told.
                "adapyBuildRef": os.environ.get("ADA_ADAPY_REF", "").strip() or None,
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
    # The scope wire format + resolvers live in routes/deps.py (module-level,
    # so extracted routers can ``Depends`` on them); the closure keeps the old
    # names for the routes still defined in here.
    _parse_scope = parse_scope
    _resolve_project_scope = resolve_project_scope
    _scope_from_path = scope_from_path
    _scope_from_header = scope_from_header
    # Best-effort audit row insert (routes/deps.py ``audit_event`` bound to this
    # app's storage); the closure's routes keep calling it as ``_audit``.
    _audit = rest_ctx.audit

    async def _enqueue_plugin_job(**kwargs):
        # routes/plugin_jobs.py's enqueue_plugin_job, bound to this app's
        # RestContext -- shared with the plugin-job scheduler tick further
        # down in this closure (admin/plugin-jobs schedules aren't extracted
        # yet), which is why it is still called as ``_enqueue_plugin_job``.
        return await enqueue_plugin_job(rest_ctx, **kwargs)

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
        # Hand dispatch a lazy provider for the worker-advertised extension
        # set (used only by the file lister, to flag plug-in formats like
        # .odb when an abaqus-capability worker is online). Passing it lazily
        # keeps the worker-registry read off every non-LIST_FILE command —
        # server-info, view-file, etc. no longer pay it — so worker-listing
        # never gates file-finding. The read itself is a cached in-memory
        # lookup (see _worker_registry) that degrades to the static list.
        try:
            reply = await dispatch(payload, storage, scope, exts_provider=_worker_advertised_exts)
        except Exception as exc:
            logger.exception("rpc dispatch failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if reply is None:
            return Response(status_code=204)
        return Response(content=reply, media_type="application/octet-stream")

    api.include_router(projects_router)

    api.include_router(storage_router)

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

        # In-browser conversions patch their row here rather than through _audit
        # or the worker's _audit_done, so failure capture needs its own call: this
        # is the whole WASM pipeline, and without it every browser-side failure
        # would still lose its source the moment the user deletes the file.
        failure_key = None
        if failure_capture.is_failure(status):
            failure_key = await failure_capture.capture_for_job(storage, pool, db_module, job_id)
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
                failure_key=failure_key,
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

    @api.post("/scopes/{scope}/audit/view")
    async def api_scope_audit_view(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Record one browser model-load (``action = 'view'``) or a
        steady-state render window (``action = 'render'`` — selected by
        ``client_metrics.kind == 'render'``).

        Posted by the viewer's opt-in load/render instrumentation
        (admin-only toggle in the Performance options) once a GLB has
        finished loading into the scene, or per render-sample window.
        Body (JSON):

          ``{key, status, duration_ms?, read_bytes?, write_bytes?,
             peak_rss_kb?, client_metrics?}``

        ``client_metrics`` is the per-phase IO/network/CPU/GPU breakdown
        (see migration 017). Best-effort: a metrics post must never break
        the user's session, so a DB hiccup is logged and swallowed with a
        200. Not admin-gated — any user with scope access may record their
        own loads (the row is owned by ``user.sub``); the collection is
        gated client-side so it only fires for admins.
        """
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            # No DB on this deployment — quietly accept so the client
            # doesn't error-toast on a metrics post.
            return JSONResponse({"ok": False, "reason": "no-db"})
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        key = (str(body.get("key") or "")).strip().lstrip("/") or None
        status = (str(body.get("status") or "ok")).strip().lower()
        if status not in {"ok", "done", "error", "failed"}:
            status = "ok"

        def _int_or_none(v):
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        cm = body.get("client_metrics")
        if not isinstance(cm, dict):
            cm = None
        else:
            # Defensive cap: keep the JSONB small and bounded regardless
            # of what a client sends. Scalars pass through; the one allowed
            # nested value is ``profile_frames`` (the JS Self-Profiling top-N
            # self-time table) — bounded to a sane length with scalar-only
            # fields. Any other nested/oversized value is dropped.
            cleaned: dict = {}
            for k, v in list(cm.items())[:64]:
                if not isinstance(k, str):
                    continue
                if isinstance(v, (int, float, bool)) or v is None:
                    cleaned[k[:64]] = v
                elif isinstance(v, str):
                    cleaned[k[:64]] = v[:512]
                elif k == "profile_frames" and isinstance(v, list):
                    frames: list = []
                    for f in v[:80]:
                        if not isinstance(f, dict):
                            continue
                        fn = f.get("fn")
                        if not isinstance(fn, str):
                            continue
                        frame: dict = {"fn": fn[:200]}
                        for fk in ("self_ms", "total_ms"):
                            fv = f.get(fk)
                            if isinstance(fv, (int, float)) and not isinstance(fv, bool):
                                frame[fk] = fv
                        frames.append(frame)
                    if frames:
                        cleaned["profile_frames"] = frames
            cm = cleaned or None

        # Steady-state render-window rows post to the same endpoint but
        # carry ``client_metrics.kind == "render"`` so they land under the
        # 'render' action (the load-time rows use 'view').
        action = "render" if (cm and cm.get("kind") == "render") else "view"

        # A load/render failure is reproducible only from the exact blob that
        # failed: re-deriving it can yield different bytes, so unlike a
        # conversion this captures the derived artifact itself.
        failure_key = None
        if failure_capture.is_failure(status):
            failure_key = await failure_capture.capture(
                storage, pool, db_module, scope=scope_obj, key=key, action=action
            )

        try:
            await db_module.insert_audit(
                pool,
                user_sub=user.sub,
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                action=action,
                key=key,
                target_format=None,
                status=status,
                error=(str(body["error"])[:2000] if body.get("error") is not None else None),
                # Client-side load failures (e.g. a malformed GLB buffer) carry no
                # Python traceback; stash the browser error's JS stack here so the
                # audit Error panel has something actionable to show.
                traceback=(str(body["traceback"])[:8000] if body.get("traceback") is not None else None),
                duration_ms=_int_or_none(body.get("duration_ms")),
                read_bytes=_int_or_none(body.get("read_bytes")),
                write_bytes=_int_or_none(body.get("write_bytes")),
                peak_rss_kb=_int_or_none(body.get("peak_rss_kb")),
                client_metrics=cm,
                failure_key=failure_key,
            )
        except Exception:
            logger.exception("audit/view record failed")
            return JSONResponse({"ok": False, "reason": "insert-failed"})
        return JSONResponse({"ok": True}, status_code=201)

    api.include_router(fea_router)

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
        pending = pending_uploads.get(scope_obj, source_key)
        if pending is not None:
            raise HTTPException(status_code=409, detail=_pending_upload_detail(source_key, pending))
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

        # A re-conversion (gallery "Re-convert") always re-runs and writes to the SEPARATE
        # ``_reconvert/`` namespace, so it never overwrites the ``_derived/`` audit product in a
        # corpus scope. Regular converts keep the cached ``_derived/`` short-circuit below.
        reconvert = bool(body.get("reconvert"))
        try:
            if reconvert:
                from .converter import reconvert_key_for

                derived_key = reconvert_key_for(source_key, target_format)
            else:
                derived_key = derived_key_for(source_key, target_format, step=step, field=field)
        except UnsupportedFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if not reconvert and await storage.exists(scope_obj, derived_key):
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
                derived_key=derived_key if reconvert else None,
                force_rebuild=reconvert,
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
        pending = pending_uploads.get(scope_obj, source_key)
        if pending is not None:
            raise HTTPException(status_code=409, detail=_pending_upload_detail(source_key, pending))
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

    @api.get("/convert/{job_id}")
    async def api_convert_status(
        job_id: str,
        user: User = Depends(auth_module.current_user),
        request: Request = ...,
    ) -> JSONResponse:
        # Job status is identified by a globally unique job_id, so the
        # URL doesn't carry a scope. We still enforce that the caller
        # could access the job's recorded scope before returning it.
        #
        # In-process plugin jobs first: they exist whether or not a queue does,
        # and their ids are namespaced so they can never collide with a queued
        # job's. Checking them before the `queue.enabled` gate is what lets one
        # polling loop in the frontend serve both paths.
        local = local_jobs.registry.get(job_id)
        if local is not None:
            local_scope = Scope(kind=local.scope_kind, id=local.scope_id)
            if not await scope_can_access(user, local_scope, getattr(request.app.state, "db_pool", None)):
                raise HTTPException(status_code=403, detail="forbidden")
            return JSONResponse(local.as_json())
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

    async def _live_worker_specs(field: str, fallback_field: str | None = None) -> dict[str, dict]:
        # Closure-bound shorthand for routes still defined in here; see
        # ``routes.deps.live_worker_specs`` for the semantics.
        return await live_worker_specs(queue, field, fallback_field)

    # Settings whose key begins with this prefix are readable by ANY
    # authenticated user; every other key in app_settings stays admin-only.
    # Writes are always admin-only (POST /api/admin/settings/{key}) — this is a
    # read window, not a public key/value store.
    #
    # It exists because a plugin often has to publish one small piece of
    # deployment configuration that its UI needs for EVERY user, not just
    # admins: which feature is enabled where, which external catalog a project
    # is bound to. Without this each such plugin would need its own core
    # endpoint, and core would end up naming plugins. The prefix is the whole
    # contract: an admin opting a key into `public.` is opting into it being
    # world-readable within the deployment.
    PUBLIC_SETTING_PREFIX = "public."

    @api.get("/settings/{key}")
    async def api_get_public_setting(key: str, request: Request) -> JSONResponse:
        """Read a setting from the publicly-readable namespace. Returns
        ``{"key": k, "value": v}`` with v=null when unset, exactly like the admin
        getter. 403 for any key outside the namespace, so this can never be used
        to read an admin-only setting."""
        if not key.startswith(PUBLIC_SETTING_PREFIX):
            raise HTTPException(
                status_code=403,
                detail=f"only {PUBLIC_SETTING_PREFIX}* settings are readable here",
            )
        pool = _require_pool(request)
        value = await db_module.get_setting(pool, key)
        return JSONResponse({"key": key, "value": value})

    # Plugin + catalog listing routes live in routes/plugins.py (the pattern
    # for splitting this closure — see routes/__init__.py). Included HERE, not
    # after the closure, because route order matters: the fixed-segment
    # ``procedural-models/<catalog>`` paths must register before the
    # ``procedural-models/{model_id}`` routes (routes/procedural_models.py)
    # that follow.
    api.include_router(plugins_router)

    api.include_router(procedural_models_router)

    # Plugin job enqueue + generic job status/cancel routes
    # (routes/plugin_jobs.py). No fixed/parameterised path collision with any
    # neighbour, so registration order relative to them doesn't matter.
    api.include_router(plugin_jobs_router)

    # External-source change tracking (routes/source_nodes.py). Same note:
    # ``/scopes/{scope}/source-nodes`` has no fixed/parameterised sibling to
    # collide with.
    api.include_router(source_nodes_router)

    # ── Equipment-type & system-template catalogs (per-scope) ────────
    #
    # Admin-authored, reusable definitions the cellbuilder places by slug.
    # Equipment types carry a bbox/mass/IFC-class/port list and an optional
    # linked CAD asset (under the hidden _equipment/ prefix) from which the
    # bbox + a preview GLB are inferred by the equipment_bbox worker job.

    _CATALOG_CAD_EXTS = (".step", ".stp", ".ifc", ".glb", ".gltf", ".stl", ".obj", ".sat", ".xml")

    _require_catalog_pool = require_catalog_pool

    async def _get_equipment_in_scope(pool, type_id: str, scope_obj: Scope) -> dict:
        try:
            row = await db_module.get_equipment_type(pool, type_id)
        except Exception:
            row = None
        if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
            raise HTTPException(status_code=404, detail="equipment type not found")
        return row

    async def _get_system_in_scope(pool, template_id: str, scope_obj: Scope) -> dict:
        try:
            row = await db_module.get_system_template(pool, template_id)
        except Exception:
            row = None
        if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
            raise HTTPException(status_code=404, detail="system template not found")
        return row

    async def _get_engine_in_scope(pool, engine_id: str, scope_obj: Scope) -> dict:
        try:
            row = await db_module.get_procedural_engine(pool, engine_id)
        except Exception:
            row = None
        if row is None or row["scope_kind"] != scope_obj.kind or (row["scope_id"] or None) != (scope_obj.id or None):
            raise HTTPException(status_code=404, detail="procedural engine not found")
        return row

    @api.get("/scopes/{scope}/equipment-types")
    async def api_equipment_types_list(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        from .catalog import equipment_preview_glb_key

        pool = _require_catalog_pool(request)
        types = await db_module.list_equipment_types(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
        for t in types:
            key = equipment_preview_glb_key(t["id"])
            t["preview_glb_key"] = key if await storage.exists(scope_obj, key) else None
        return JSONResponse({"equipment_types": types})

    @api.post("/scopes/{scope}/equipment-types", status_code=201)
    async def api_equipment_types_create(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .catalog import slugify

        pool = _require_catalog_pool(request)
        body = await request.json()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug from name/slug")
        desc = body.get("description")
        row = await db_module.create_equipment_type(
            pool,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            created_by=user.sub,
        )
        if row is None:
            raise HTTPException(status_code=409, detail=f"an equipment type with slug {slug!r} already exists")
        return JSONResponse(row, status_code=201)

    @api.get("/scopes/{scope}/equipment-types/{type_id}")
    async def api_equipment_types_get(
        request: Request,
        type_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        from .catalog import equipment_preview_glb_key

        pool = _require_catalog_pool(request)
        row = await _get_equipment_in_scope(pool, type_id, scope_obj)
        key = equipment_preview_glb_key(type_id)
        out = {
            k: row[k]
            for k in ("id", "slug", "name", "description", "doc", "cad_key", "revision", "created_by", "updated_at")
        }
        out["preview_glb_key"] = key if await storage.exists(scope_obj, key) else None
        return JSONResponse(out)

    @api.put("/scopes/{scope}/equipment-types/{type_id}")
    async def api_equipment_types_update(
        request: Request,
        type_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        import asyncpg

        from .catalog import slugify, validate_equipment_doc

        pool = _require_catalog_pool(request)
        row = await _get_equipment_in_scope(pool, type_id, scope_obj)
        body = await request.json()
        name = body.get("name")
        doc = body.get("doc")
        base_revision = body.get("base_revision")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        if not isinstance(doc, dict):
            raise HTTPException(status_code=400, detail="doc (object) is required")
        if not isinstance(base_revision, int):
            raise HTTPException(status_code=400, detail="base_revision (int) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug")
        desc = body.get("description")
        try:
            normalized = validate_equipment_doc(doc)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid equipment doc: {e}")
        try:
            new_rev = await db_module.update_equipment_type(
                pool,
                type_id,
                slug=slug,
                name=name.strip(),
                description=desc if isinstance(desc, str) else None,
                doc=normalized,
                base_revision=base_revision,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail=f"an equipment type with slug {slug!r} already exists")
        if new_rev is None:
            current = await db_module.get_equipment_type(pool, type_id)
            raise HTTPException(
                status_code=409,
                detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
            )
        return JSONResponse({"id": row["id"], "revision": new_rev})

    @api.delete("/scopes/{scope}/equipment-types/{type_id}")
    async def api_equipment_types_delete(
        request: Request,
        type_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        pool = _require_catalog_pool(request)
        await _get_equipment_in_scope(pool, type_id, scope_obj)
        ok = await db_module.archive_equipment_type(pool, type_id)
        if not ok:
            raise HTTPException(status_code=404, detail="equipment type not found")
        return JSONResponse({"status": "archived"})

    @api.post("/scopes/{scope}/equipment-types/{type_id}/cad", status_code=201)
    async def api_equipment_types_cad_upload(
        request: Request,
        type_id: str,
        filename: str = "",
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Attach a CAD/GLB asset to the type by direct body upload. The
        ``?filename=`` query param supplies the extension."""
        import os

        from .catalog import equipment_cad_key

        pool = _require_catalog_pool(request)
        await _get_equipment_in_scope(pool, type_id, scope_obj)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _CATALOG_CAD_EXTS:
            raise HTTPException(status_code=415, detail=f"unsupported CAD type {ext!r}; one of {_CATALOG_CAD_EXTS}")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=400, detail="empty body")
        key = equipment_cad_key(type_id, ext)
        await storage.put_bytes(scope_obj, key, data)
        await db_module.set_equipment_type_cad(pool, type_id, key)
        return JSONResponse({"cad_key": key}, status_code=201)

    @api.post("/scopes/{scope}/equipment-types/{type_id}/cad-from-scope", status_code=201)
    async def api_equipment_types_cad_from_scope(
        request: Request,
        type_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Attach a CAD asset by copying an existing scope file into the type."""
        import os

        from .catalog import equipment_cad_key

        pool = _require_catalog_pool(request)
        await _get_equipment_in_scope(pool, type_id, scope_obj)
        body = await request.json()
        source = (body.get("source_key") or "").strip().lstrip("/")
        if not source:
            raise HTTPException(status_code=400, detail="source_key (str) is required")
        ext = os.path.splitext(source)[1].lower()
        if ext not in _CATALOG_CAD_EXTS:
            raise HTTPException(status_code=415, detail=f"unsupported CAD type {ext!r}")
        try:
            data = await storage.get_bytes(scope_obj, source)
        except Exception:
            raise HTTPException(status_code=404, detail=f"source not found in scope: {source}")
        key = equipment_cad_key(type_id, ext)
        await storage.put_bytes(scope_obj, key, data)
        await db_module.set_equipment_type_cad(pool, type_id, key)
        return JSONResponse({"cad_key": key}, status_code=201)

    @api.post("/scopes/{scope}/equipment-types/{type_id}/infer-bbox")
    async def api_equipment_types_infer_bbox(
        request: Request,
        type_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Enqueue a worker job to read the linked CAD asset, infer the bbox
        into the doc and render a preview GLB for the sidecar viewer."""
        from .catalog import equipment_preview_glb_key

        pool = _require_catalog_pool(request)
        row = await _get_equipment_in_scope(pool, type_id, scope_obj)
        if not row.get("cad_key"):
            raise HTTPException(status_code=400, detail="equipment type has no linked CAD asset to infer from")
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="bbox inference disabled (no NATS configured)")
        # The type's Z-up assumption (default True = verbatim) travels to the
        # worker so inference measures/previews in the frame the user selected.
        cad_z_up = bool((row.get("doc") or {}).get("cad_z_up", True))
        derived_key = equipment_preview_glb_key(type_id)
        job = await queue.enqueue(
            f"_synthetic/equipment/{type_id}/bbox",
            target_format="equipment_bbox",
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            conversion_options={"type_id": type_id, "cad_key": row["cad_key"], "cad_z_up": cad_z_up},
            derived_key=derived_key,
        )
        return JSONResponse({"job_id": job.job_id, "derived_key": derived_key})

    # ── System templates ─────────────────────────────────────────────

    @api.get("/scopes/{scope}/system-templates")
    async def api_system_templates_list(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        pool = _require_catalog_pool(request)
        templates = await db_module.list_system_templates(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
        return JSONResponse({"system_templates": templates})

    @api.post("/scopes/{scope}/system-templates", status_code=201)
    async def api_system_templates_create(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .catalog import slugify

        pool = _require_catalog_pool(request)
        body = await request.json()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug from name/slug")
        desc = body.get("description")
        row = await db_module.create_system_template(
            pool,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            created_by=user.sub,
        )
        if row is None:
            raise HTTPException(status_code=409, detail=f"a system template with slug {slug!r} already exists")
        return JSONResponse(row, status_code=201)

    @api.get("/scopes/{scope}/system-templates/{template_id}")
    async def api_system_templates_get(
        request: Request,
        template_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        pool = _require_catalog_pool(request)
        row = await _get_system_in_scope(pool, template_id, scope_obj)
        return JSONResponse(
            {k: row[k] for k in ("id", "slug", "name", "description", "doc", "revision", "created_by", "updated_at")}
        )

    @api.put("/scopes/{scope}/system-templates/{template_id}")
    async def api_system_templates_update(
        request: Request,
        template_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        import asyncpg

        from .catalog import slugify, validate_system_doc

        pool = _require_catalog_pool(request)
        row = await _get_system_in_scope(pool, template_id, scope_obj)
        body = await request.json()
        name = body.get("name")
        doc = body.get("doc")
        base_revision = body.get("base_revision")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        if not isinstance(doc, dict):
            raise HTTPException(status_code=400, detail="doc (object) is required")
        if not isinstance(base_revision, int):
            raise HTTPException(status_code=400, detail="base_revision (int) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug")
        desc = body.get("description")
        try:
            normalized = validate_system_doc(doc)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid system doc: {e}")
        try:
            new_rev = await db_module.update_system_template(
                pool,
                template_id,
                slug=slug,
                name=name.strip(),
                description=desc if isinstance(desc, str) else None,
                doc=normalized,
                base_revision=base_revision,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail=f"a system template with slug {slug!r} already exists")
        if new_rev is None:
            current = await db_module.get_system_template(pool, template_id)
            raise HTTPException(
                status_code=409,
                detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
            )
        return JSONResponse({"id": row["id"], "revision": new_rev})

    @api.delete("/scopes/{scope}/system-templates/{template_id}")
    async def api_system_templates_delete(
        request: Request,
        template_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        pool = _require_catalog_pool(request)
        await _get_system_in_scope(pool, template_id, scope_obj)
        ok = await db_module.archive_system_template(pool, template_id)
        if not ok:
            raise HTTPException(status_code=404, detail="system template not found")
        return JSONResponse({"status": "archived"})

    # ── /api/scopes/{scope}/procedural-engines ──────────────────────
    #
    # Registry of pluggable procedural-modelling engines. The built-in
    # ``adapy-default`` engine (in-repo compile_procedural_doc, runnable in the
    # browser via the adapy wheel) is always present — unioned in below with an
    # ``origin`` tag — so a scope with no DB rows still offers one engine.
    _BUILTIN_ENGINE = {
        "id": "builtin:adapy-default",
        "slug": "adapy-default",
        "name": "adapy default",
        "description": "Built-in adapy procedural engine (runs server-side and in-browser via WASM).",
        "revision": 0,
        "origin": "builtin",
        # Built-ins compile a single model-level blueprint; cell grouping is a
        # capability engine feature, advertised via the worker heartbeat.
        "supports_grouping": False,
        "doc": {"kind": "builtin", "entrypoint": "ada.topo_model.wasm_compile:compile_doc"},
    }
    # A second built-in: the diagnostic ``echo`` engine (renders the document's
    # cells as raw boxes). It exercises the full engine-selection path — resolve a
    # ``module:callable`` entrypoint and dispatch to it — on both the server and
    # WASM compile, without needing an external wheel. Kept ada-free here (slim API).
    _ECHO_ENGINE = {
        "id": "builtin:echo",
        "slug": "echo",
        "name": "echo (raw cells)",
        "description": "Diagnostic engine: renders the document's cells as raw boxes (no structure).",
        "revision": 0,
        "origin": "builtin",
        "supports_grouping": False,
        "doc": {"kind": "builtin", "entrypoint": "ada.topo_model.echo_engine:compile_doc"},
    }
    _BUILTIN_ENGINES = [_BUILTIN_ENGINE, _ECHO_ENGINE]
    _BUILTIN_ENGINE_IDS = {e["id"] for e in _BUILTIN_ENGINES}
    _BUILTIN_ENGINE_SLUGS = {e["slug"] for e in _BUILTIN_ENGINES}
    _BUILTIN_ENGINE_BY_ID = {e["id"]: e for e in _BUILTIN_ENGINES}

    @api.get("/scopes/{scope}/procedural-engines")
    async def api_procedural_engines_list(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        pool = _require_catalog_pool(request)
        engines = await db_module.list_procedural_engines(pool, scope_kind=scope_obj.kind, scope_id=scope_obj.id)
        for e in engines:
            e["origin"] = "db"
        # Built-ins first, then the scope's registered engines.
        summaries = [
            {k: e[k] for k in ("id", "slug", "name", "description", "revision", "origin", "supports_grouping")}
            for e in _BUILTIN_ENGINES
        ]
        # Fold each engine's advertised capability flags (from live, non-stale
        # workers) onto its summary by slug — this is how a DB-registered capability
        # engine reports ``supports_grouping=True`` while its worker is
        # up. A slug with no live spec defaults to non-grouping. Built-ins carry
        # their static flag above and are overridden only if a worker re-announces.
        engine_caps = await _live_worker_specs("procedural_engine_specs")
        for summary in (*summaries, *engines):
            spec = engine_caps.get(summary.get("slug"))
            summary["supports_grouping"] = bool(
                spec.get("supports_grouping") if spec is not None else summary.get("supports_grouping", False)
            )

        # Engines a live worker advertises in full. A worker can only honestly
        # announce an engine whose code it has, so this is the authoritative
        # answer to "what can actually run right now" -- and it removes the
        # manual step of creating a row per scope for an engine that is already
        # installed and already reachable.
        #
        # Only specs carrying a name AND an entrypoint qualify (see
        # ada.comms.engine_specs.is_offerable): older workers advertise capability flags
        # alone, and surfacing one of those would offer an engine the viewer
        # cannot dispatch to.
        #
        # Built-ins and DB rows win on slug collision. A row is an explicit
        # admin decision -- possibly pinning a different entrypoint or revision --
        # and must not be silently overridden by whatever a pod happens to run.
        from ada.comms.engine_specs import is_offerable

        known = {*_BUILTIN_ENGINE_SLUGS, *(e.get("slug") for e in engines)}
        advertised = [
            {
                "id": f"worker:{spec['slug']}",
                "slug": spec["slug"],
                "name": spec["name"],
                "description": spec.get("description", ""),
                "revision": 0,
                "origin": "worker",
                "supports_grouping": bool(spec.get("supports_grouping", False)),
            }
            for slug, spec in sorted(engine_caps.items())
            if slug not in known and is_offerable(spec)
        ]
        return JSONResponse({"procedural_engines": [*summaries, *engines, *advertised]})

    @api.post("/scopes/{scope}/procedural-engines", status_code=201)
    async def api_procedural_engines_create(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .catalog import slugify

        pool = _require_catalog_pool(request)
        body = await request.json()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug from name/slug")
        if slug in _BUILTIN_ENGINE_SLUGS:
            raise HTTPException(status_code=409, detail=f"{slug!r} is a reserved built-in engine slug")
        desc = body.get("description")
        row = await db_module.create_procedural_engine(
            pool,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            slug=slug,
            name=name.strip(),
            description=desc if isinstance(desc, str) else None,
            created_by=user.sub,
        )
        if row is None:
            raise HTTPException(status_code=409, detail=f"a procedural engine with slug {slug!r} already exists")
        return JSONResponse(row, status_code=201)

    @api.get("/scopes/{scope}/procedural-engines/{engine_id}")
    async def api_procedural_engines_get(
        request: Request,
        engine_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        if engine_id in _BUILTIN_ENGINE_IDS:
            return JSONResponse(_BUILTIN_ENGINE_BY_ID[engine_id])
        pool = _require_catalog_pool(request)
        row = await _get_engine_in_scope(pool, engine_id, scope_obj)
        return JSONResponse(
            {k: row[k] for k in ("id", "slug", "name", "description", "doc", "revision", "created_by", "updated_at")}
        )

    @api.get("/scopes/{scope}/procedural-engines/{engine_id}/resolve")
    async def api_procedural_engines_resolve(
        request: Request,
        engine_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        """Resolve an engine to a BROWSER-runnable descriptor for the in-browser
        (Pyodide) compile: a built-in engine returns its slug + entrypoint; a
        ``kind:wheel`` engine returns its ``module:callable`` entrypoint, the
        ``pyodide_deps`` to micropip-install, and a presigned ``wheel_url`` (when
        the wheel has been built — ``ready``). A ``kind:server`` engine is not
        browser-runnable (``ready: false``)."""

        def _builtin(b: dict) -> JSONResponse:
            return JSONResponse(
                {"kind": "builtin", "slug": b["slug"], "entrypoint": b["doc"]["entrypoint"], "ready": True}
            )

        if engine_id in _BUILTIN_ENGINE_IDS:
            return _builtin(_BUILTIN_ENGINE_BY_ID[engine_id])
        pool = _require_catalog_pool(request)
        row = await _get_engine_in_scope(pool, engine_id, scope_obj)
        doc = row.get("doc") or {}
        kind = doc.get("kind", "builtin")
        if kind == "builtin":
            return JSONResponse(
                {"kind": "builtin", "slug": row["slug"], "entrypoint": doc.get("entrypoint"), "ready": True}
            )
        if kind == "wheel":
            wheel_key = doc.get("wheel_key")
            wheel_url = None
            ready = False
            if wheel_key and storage.supports_presigned_uploads:
                meta = await storage.head(scope_obj, wheel_key)
                if meta is not None:
                    wheel_url = await storage.presigned_get_url(scope_obj, wheel_key, expires_in_seconds=15 * 60)
                    ready = True
            return JSONResponse(
                {
                    "kind": "wheel",
                    "entrypoint": doc.get("entrypoint"),
                    "pyodide_deps": doc.get("pyodide_deps") or [],
                    "wheel_url": wheel_url,
                    "ready": ready,
                }
            )
        # server (or unknown): native-only, not runnable in the browser.
        return JSONResponse({"kind": kind, "entrypoint": doc.get("entrypoint"), "ready": False})

    @api.put("/scopes/{scope}/procedural-engines/{engine_id}")
    async def api_procedural_engines_update(
        request: Request,
        engine_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        import asyncpg

        from .catalog import slugify, validate_engine_doc

        if engine_id in _BUILTIN_ENGINE_IDS:
            raise HTTPException(status_code=403, detail="the built-in engine is read-only")
        pool = _require_catalog_pool(request)
        row = await _get_engine_in_scope(pool, engine_id, scope_obj)
        body = await request.json()
        name = body.get("name")
        doc = body.get("doc")
        base_revision = body.get("base_revision")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(status_code=400, detail="name (str) is required")
        if not isinstance(doc, dict):
            raise HTTPException(status_code=400, detail="doc (object) is required")
        if not isinstance(base_revision, int):
            raise HTTPException(status_code=400, detail="base_revision (int) is required")
        slug = slugify(body.get("slug") or name)
        if not slug:
            raise HTTPException(status_code=400, detail="could not derive a slug")
        if slug in _BUILTIN_ENGINE_SLUGS:
            raise HTTPException(status_code=409, detail=f"{slug!r} is a reserved built-in engine slug")
        desc = body.get("description")
        try:
            normalized = validate_engine_doc(doc)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"invalid engine doc: {e}")
        try:
            new_rev = await db_module.update_procedural_engine(
                pool,
                engine_id,
                slug=slug,
                name=name.strip(),
                description=desc if isinstance(desc, str) else None,
                doc=normalized,
                base_revision=base_revision,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail=f"a procedural engine with slug {slug!r} already exists")
        if new_rev is None:
            current = await db_module.get_procedural_engine(pool, engine_id)
            raise HTTPException(
                status_code=409,
                detail={"message": "revision conflict", "current_revision": current["revision"] if current else None},
            )
        # A kind:wheel engine needs its wheel (re)built from the repo whenever the
        # manifest changes; enqueue the build worker (it clones + builds + stores
        # the wheel under _engines/ and records wheel_key). force so a manifest
        # edit at an unchanged spot still rebuilds.
        if normalized.get("kind") == "wheel" and queue.enabled:
            from .procedural import engine_wheel_dir

            await queue.enqueue(
                f"_synthetic/engine-build/{engine_id}/r{new_rev}",
                target_format="procedural_engine_build",
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                conversion_options={"engine_id": engine_id},
                derived_key=engine_wheel_dir(engine_id),
                force_rebuild=True,
            )
        return JSONResponse({"id": row["id"], "revision": new_rev})

    @api.delete("/scopes/{scope}/procedural-engines/{engine_id}")
    async def api_procedural_engines_delete(
        request: Request,
        engine_id: str,
        scope_obj: Scope = Depends(_scope_from_path),
    ) -> JSONResponse:
        if engine_id in _BUILTIN_ENGINE_IDS:
            raise HTTPException(status_code=403, detail="the built-in engine cannot be deleted")
        pool = _require_catalog_pool(request)
        await _get_engine_in_scope(pool, engine_id, scope_obj)
        ok = await db_module.archive_procedural_engine(pool, engine_id)
        if not ok:
            raise HTTPException(status_code=404, detail="procedural engine not found")
        return JSONResponse({"status": "archived"})

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

    # The generic pool gate lives in routes/deps.py (routes/plugin_jobs.py
    # shares it); the old module name stays bound for the routes still here.
    _require_pool = require_pool

    admin.include_router(admin_settings_router)
    admin.include_router(admin_storage_compression_router)

    admin.include_router(admin_workers_router)

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

    # routes/deps.py's SystemUser / validate_cron / next_fire, bound under
    # the old names for the routes still in this closure (the audit +
    # plugin-job scheduler ticks and the auto-validate poller).
    _SystemUser = SystemUser
    _validate_cron = validate_cron
    _next_fire = next_fire

    async def _worker_registry_refresh_loop() -> None:
        """Background task: refresh the cached worker-registry snapshot
        (:data:`_worker_registry`) every ``REFRESH_INTERVAL_S`` so the
        config endpoints never call ``list_workers()`` (an N+1 over NATS
        KV) on the request path. Defensive — a failed tick keeps the last
        good snapshot; only ``asyncio.CancelledError`` exits (shutdown)."""
        # 15s (was 3s): the refresh does a KV keys() scan, and worker
        # capabilities/tags only change on a worker's ~15s heartbeat, so a
        # tighter interval bought nothing but scan traffic. Cheap now that the
        # KV is kept lean (see _job_cleanup_loop), but no reason to over-poll.
        REFRESH_INTERVAL_S = 15.0
        logger.info("worker registry refresh: starting (every %ss)", REFRESH_INTERVAL_S)
        try:
            while True:
                await asyncio.sleep(REFRESH_INTERVAL_S)
                await _refresh_worker_registry()
        except asyncio.CancelledError:
            logger.info("worker registry refresh: stopped")
            raise

    async def _job_cleanup_loop(q) -> None:
        """Background task: periodically drop completed job entries from the KV
        so the shared bucket stays small. The KV is a transient in-flight
        progress cache only — the durable record lives in Postgres (audit) and
        S3 (blobs/profiles). Without this, terminal job entries piled up
        (observed: 47k), so every worker-registry keys() scan replayed the whole
        bucket and stormed NATS. Grace keeps a completed job readable long
        enough for a polling client to see its final state. Defensive per tick;
        only CancelledError exits."""
        CLEANUP_INTERVAL_S = 300.0
        GRACE_S = 900.0  # keep terminal jobs ~15min so the frontend's final poll still resolves
        logger.info("job KV cleanup: starting (every %ss, grace %ss)", CLEANUP_INTERVAL_S, GRACE_S)
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL_S)
                try:
                    await q.purge_completed_jobs(grace_s=GRACE_S)
                except Exception:
                    logger.exception("job KV cleanup: tick failed")
        except asyncio.CancelledError:
            logger.info("job KV cleanup: stopped")
            raise

    async def _worker_prune_loop(q) -> None:
        """Background task: hourly, hard-prune worker registry entries unseen for
        ``WORKER_PRUNE_AFTER_S`` (2 days). Defensive — one failed tick is logged, not fatal; only
        ``asyncio.CancelledError`` exits cleanly (shutdown)."""
        PRUNE_INTERVAL_S = 3600.0
        logger.info("worker prune: starting (every %ss, horizon %ss)", PRUNE_INTERVAL_S, q.WORKER_PRUNE_AFTER_S)
        try:
            while True:
                await asyncio.sleep(PRUNE_INTERVAL_S)
                try:
                    n = await q.prune_stale_workers()
                    if n:
                        logger.info("worker prune: removed %d stale registration(s)", n)
                except Exception:
                    logger.exception("worker prune: tick failed")
        except asyncio.CancelledError:
            logger.info("worker prune: stopped")
            raise

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

    admin.include_router(admin_plugin_jobs_router)

    async def _plugin_schedule_loop(pool) -> None:
        """Tick every 30 s, claim due schedules, enqueue them as plugin jobs.

        A deliberate mirror of ``_scheduler_loop`` above: same interval, same
        drain-the-backlog inner loop, same "one tick's exception must not kill the
        loop" posture. The two are separate because their payloads are, not
        because they disagree about how ticking works.
        """
        from datetime import datetime, timezone

        TICK_INTERVAL_S = 30.0
        logger.info("plugin-job scheduler: starting (tick every %ss)", TICK_INTERVAL_S)
        try:
            while True:
                try:
                    now = datetime.now(timezone.utc)
                    while True:
                        try:
                            # Claimed with a placeholder next_fire_at and
                            # corrected below, once this row's cron_expr is known.
                            row = await db_module.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now)
                        except Exception:
                            logger.exception("plugin-job scheduler: claim failed")
                            break
                        if row is None:
                            break
                        try:
                            await db_module.update_plugin_job_schedule(
                                pool,
                                row["id"],
                                next_fire_at=_next_fire(row["cron_expr"], after=now),
                            )
                        except Exception as exc:
                            # next_fire_at is now `now`, so this row would be
                            # claimed again on the next tick and spin. Disable it
                            # and say why rather than let it hammer the queue.
                            logger.exception("plugin-job scheduler: could not advance %s", row["id"])
                            await db_module.update_plugin_job_schedule(pool, row["id"], enabled=False)
                            await db_module.set_plugin_job_schedule_skip_reason(
                                pool,
                                row["id"],
                                f"disabled: could not compute the next firing from {row['cron_expr']!r}: {exc}",
                            )
                            continue

                        await _plugin_schedule_fire(pool, row, fired_at=now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("plugin-job scheduler: tick failed")
                await asyncio.sleep(TICK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("plugin-job scheduler: stopped")
            raise

    async def _plugin_schedule_fire(pool, schedule_row: dict, *, fired_at) -> dict:
        # routes/admin_plugin_jobs.py's plugin_schedule_fire, bound to this
        # app's RestContext. The run-now route calls it directly; kept here
        # under the old name for the tick loop above.
        return await plugin_schedule_fire(rest_ctx, pool, schedule_row, fired_at=fired_at)

    # ── Issue-bot poller (M5) ──────────────────────────────────────
    #
    # The issue-target settings + load_issue_target_config live in
    # routes/admin_audit_perf.py now, with the routes; the poller below
    # only needs the two sync functions, kept under their old names.

    async def _run_issue_bot_for(pool, run: dict) -> None:
        return await run_issue_bot_for(pool, run)

    # ── Profile hotspot parser (M7 perf dashboard) ────────────────

    _PROFILE_TOP_K = 50

    async def _parse_one_profile(pool, claimed: dict) -> None:
        """Download one .prof blob, extract top-K functions by
        cumtime, and write rows into ``profile_function_stats``.
        Failures get stamped on the audit_log row's
        ``profile_stats_error`` so the operator can debug, but the
        loop continues — one bad blob mustn't stop the queue."""
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
        tmp_path = new_temp_path(suffix=".prof")
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
        # routes/admin_audit_perf.py's run_issue_bot_for_conversion; kept
        # here under the old name for the issue-bot poller below.
        return await run_issue_bot_for_conversion(pool, row)

    async def _dispatch_auto_validation(pool, parent: dict) -> None:
        """Dispatch the validation (cross-format parity) pass of an
        ``auto_validate`` conversion run — *into the same run*, not a new one.
        Runs dispatched with an upfront reservation (``validate_total > 0``)
        already count these cells in their total, so dispatch consumes the
        reservation; pre-reservation runs get their total extended (and
        reopen from ``finished``) as before. The claim already stamped the
        parent so this runs once; failures are logged but never break the
        poller tick."""
        try:
            s = _parse_scope(parent["scope"], _SystemUser())
            s = await _resolve_project_scope(pool, s)
        except Exception:
            logger.exception("auto-validate: scope resolution failed for run %s", parent["id"])
            if (parent.get("validate_total") or 0) > 0:
                # Release the reservation — no parity cells will ever be
                # enqueued, so the run must not hang 'running' on them.
                await db_module.consume_audit_run_validation_reserve(pool, parent["id"], 0)
            return
        # Awaited (not fire-and-forget) so the run holds its parity cells
        # before the issue-bot drain in the same tick can claim it.
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
                consume_reserve=(parent.get("validate_total") or 0) > 0,
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
                    # Auto-validate first: an auto_validate run whose conversion
                    # cells have landed gets its parity cells dispatched (the
                    # run stays 'running' on its upfront-reserved total; legacy
                    # finished runs reopen), so the issue-bot below only claims
                    # a run once it's *truly* done — conversions and validation
                    # together. Claimed once (the claim stamps
                    # auto_validate_dispatched_at).
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

    # routes/admin_audit_runs.py's WASM_POOL / audit_cells_for_files /
    # audit_run_list_cells / audit_dispatch / audit_dispatch_wasm, bound
    # under the old names for the callers still in this closure (the audit
    # scheduler tick, the auto-validate poller, and the admin audit-schedules
    # fire-now route below).
    _WASM_POOL = WASM_POOL
    _audit_cells_for_files = audit_cells_for_files

    async def _audit_run_list_cells(scope_obj, validate_only):
        return await audit_run_list_cells(rest_ctx, scope_obj, validate_only)

    async def _audit_dispatch(*args, **kwargs):
        return await audit_dispatch(rest_ctx, *args, **kwargs)

    async def _audit_dispatch_wasm(*args, **kwargs):
        return await audit_dispatch_wasm(rest_ctx, *args, **kwargs)

    admin.include_router(admin_audit_runs_router)

    admin.include_router(admin_corpora_router)

    admin.include_router(admin_audit_schedules_router)

    admin.include_router(admin_audit_perf_router)

    admin.include_router(admin_projects_router)

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

    # routes/deps.py's format_label + SOURCE_FORMAT_NAMES, bound under the
    # old name for the admin storage list below (the user-facing files
    # route calls the deps function directly — see routes/storage.py).
    _format_label = format_label

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
            if f.key.lstrip("/").startswith("_overlays/"):
                continue  # auto-disposed utility overlays (merge-preview/diff) — not user files
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
        # routes/storage.py's rename_with_status, shared with the (already
        # extracted) user-facing rename route — see that module's docstring.
        result = await rename_with_status(storage, scope_obj, old_key, new_key)
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
        Per-key reporting: ``{copied, skipped, failed}`` — a key that already
        exists in the destination is reported under ``skipped`` (a no-op, not an
        error, so recursive folder copies tolerate partial overlap); a missing
        source, derived-key reject, or backend error lands in ``failed``. Nothing
        aborts the batch.
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
        skipped: list[dict] = []
        failed: list[dict] = []
        for key in keys:
            if is_derived_key(key):
                failed.append({"key": key, "reason": "cannot copy derived blobs"})
                continue
            if key in dst_keys:
                skipped.append({"key": key, "reason": "already in corpus"})
                continue
            try:
                # overwrite=True for the same S3 reason as rename above (the safe
                # default raises ``copy-if-not-exists not supported``); the
                # application-layer dst_keys pre-check is the real collision guard.
                await storage.copy(src_scope, key, scope_obj, key, overwrite=True)
            except Exception:
                # Full detail is logged; return a generic reason so backend/stack-trace
                # text isn't exposed in the response (CodeQL py/stack-trace-exposure).
                logger.exception("admin: copy failed for %s (%s -> %s)", key, src_raw, scope_obj.prefix())
                failed.append({"key": key, "reason": "copy failed"})
                continue
            dst_keys.add(key)
            copied.append({"key": key})
            await _audit(request, user, scope_obj, "copy", key=key, status="ok")

        return JSONResponse({"copied": copied, "skipped": skipped, "failed": failed})

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
        # Cached snapshot — no NATS on the request path (see _worker_registry).
        worker_tag: str | None = _worker_registry["image_tag"] if queue.enabled else None
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
            # See the Build: line in the Options panel. Emitted even when
            # empty, so a viewer that simply was not told is distinguishable
            # from one running an older image that could not have been.
            f"window.ADAPY_BUILD_REF = {_json.dumps(os.environ.get('ADA_ADAPY_REF', '').strip() or None)};\n"
            f"window.ADAPY_VERSION = {_json.dumps(adapy_version)};\n"
            f"window.EXTRA_SOURCE_EXTS = {_json.dumps(extra_source_exts)};\n"
            f"window.STREAMING_ONLY_EXTS = {_json.dumps(streaming_only_exts)};\n"
            f"window.CONVERSION_MATRIX = {_json.dumps(conversion_matrix)};\n"
        )

        # Runtime default UI shell. Emitted ONLY when configured: the frontend
        # treats an absent global as "no runtime opinion" and keeps whatever
        # default was baked into the bundle at build time, so an image whose
        # deployment sets nothing behaves exactly as it did before. An empty
        # string would instead be a value, and a value has to mean something.
        if settings.ui_default:
            body += f"window.ADA_UI_DEFAULT = {_json.dumps(settings.ui_default)};\n"

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

    def _index_response() -> FileResponse:
        # Grant the JS Self-Profiling API on the SPA document. This is an
        # inert *permission* — it costs nothing and starts no profiling on
        # its own; the viewer only constructs a Profiler when an admin
        # turns on "Profile calls during load" (Performance options). The
        # policy can't be toggled at runtime (it's fixed for the document
        # at load), but it doesn't need to be: with the toggle off, no
        # profiling happens. Chromium-only; other browsers ignore it.
        # Must be on the HTML *document* response, not the assets.
        return FileResponse(
            static_dir / "index.html",
            headers={"Document-Policy": "js-profiling"},
        )

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
                # A direct hit on index.html is still the document — keep
                # the profiling permission on it too.
                if candidate.name == "index.html":
                    return _index_response()
                return FileResponse(candidate)
        return _index_response()


app = create_app()
