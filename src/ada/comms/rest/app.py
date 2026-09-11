from __future__ import annotations

import asyncio
import copy
import datetime
import json
import os
import pathlib
import re
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
    fea_artefact_manifest_key_for,
    fea_artefact_prefix_for,
    fea_manifest_stale_reason,
    fea_meta_key_for,
    is_fea_artefact_source,
    is_fea_result_key,
    is_supported_source,
    merge_option_into,
    supported_targets_for,
)
from .handlers import dispatch
from .plugin_registry import discover_local_plugins
from .qualification import CAPABILITY_REQUIREMENTS_KEY
from .queue import JobQueue
from .routes.deps import (  # noqa: F401 — _merge_spec re-exported for tests/importers of the old name
    DIRECT_UPLOAD_THRESHOLD_BYTES,
    RestContext,
    _merge_spec,
    human_bytes,
    live_worker_specs,
    parse_scope,
    pending_upload_detail,
    require_catalog_pool,
    require_pool,
    resolve_project_scope,
    scope_from_header,
    scope_from_path,
)
from .routes.plugin_jobs import enqueue_plugin_job
from .routes.plugin_jobs import router as plugin_jobs_router
from .routes.plugins import router as plugins_router
from .routes.procedural_models import router as procedural_models_router
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

# The API-buffered upload cap lives in routes/deps.py (extracted routers
# share it); the old module name stays bound for the routes still in here.
_DIRECT_UPLOAD_THRESHOLD_BYTES: int = DIRECT_UPLOAD_THRESHOLD_BYTES


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


#: How long a presigned upload URL is valid, and — via pending_uploads — how
#: long an unfinished upload blocks a job against its key before the entry is
#: reaped. One literal rather than two so the two can't drift apart: a mint
#: that outlives the block on it would let a job dispatch against a key the
#: browser is still (validly) PUTting to.
_UPLOAD_URL_TTL_SECONDS = 3600


# ``human_bytes`` / ``pending_upload_detail`` live in routes/deps.py (needed by
# routes/plugin_jobs.py's ``POST /plugins/{id}/jobs``); the old module names
# stay bound here for the routes still in this module that call them.
_human_bytes = human_bytes
_pending_upload_detail = pending_upload_detail


#: The `app_settings` key an admin edits. Mirrored into the KV meta keyspace
#: under `CAPABILITY_REQUIREMENTS_KEY` so workers without a database can read it.
CAPABILITY_REQUIREMENTS_SETTING = "capability_requirements"

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
    # Explicit per-app services for extracted routers (routes/*.py) — what
    # they may reach instead of this closure. See routes/__init__.py.
    rest_ctx = RestContext(settings=settings, storage=storage, queue=queue)
    app.state.rest = rest_ctx

    @app.get("/healthz")
    async def healthz() -> Response:
        # Public — load balancers + readiness probes hit this.
        return Response(status_code=200)

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
        """Mirror the requirement document into the NATS KV meta keyspace.

        Workers read it from there, not from Postgres — deliberately. The worker
        this gate exists for is the one least likely to have a database
        connection: an off-cluster machine has no reason to be given one, and
        making qualification depend on Postgres would leave exactly that worker
        ungated. KV is already how it learns everything else about the
        deployment.

        Best-effort. Failing to publish must not fail the admin's write: the
        setting is stored either way, and the next successful publish (or a
        restart) reconciles. Workers that cannot read it fail OPEN, so the
        blast radius of this not landing is "the gate is not yet enforced",
        never "the fleet stopped".
        """
        if not queue.enabled:
            return
        try:
            await queue.set_meta(CAPABILITY_REQUIREMENTS_KEY, value or "")
        except Exception:
            logger.exception("could not publish capability requirements to the job queue")

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
        workers = _worker_registry["workers"]
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

        # Corpus scopes are admin-only (scope_can_access gates them). Advertise
        # them here so an admin can browse + visualise corpus files straight from
        # the main storage panel — the same list/convert flow every other scope
        # uses. Non-admins never see them; the backend rejects the scope anyway.
        if user.is_admin and pool is not None:
            try:
                for c in await db_module.list_corpora(pool):
                    scopes.append({"kind": "corpus", "id": c["slug"], "name": c["name"]})
            except Exception:
                logger.exception("api_me: listing corpora failed")

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
        from .converter import (
            HIDDEN_PREFIXES,
            is_derived_key,
            is_hidden_key,
            supported_targets_for,
        )

        if not include_derived:
            # Default — hide internal namespaces (_derived/ convert cache + _overlays/
            # auto-disposed utility overlays). Those blobs aren't user files. Convert +
            # download surfaces the derived ones explicitly when needed.
            #
            # Skip them in the STORAGE layer rather than filtering here: an audited scope holds
            # far more derived blobs than files (the corpus: ~28k against 141), so filtering after
            # a full listing made this endpoint O(audit history) — ~1.6 s to return 9 KB, against
            # 30 ms for an unaudited scope with the same file count. is_hidden_key still runs; it
            # is now cheap agreement rather than the mechanism.
            files = await storage.list(scope_obj, skip_prefixes=HIDDEN_PREFIXES)
            rows = {f.key: {"key": f.key, "size": f.size} for f in files if not is_hidden_key(f.key)}
            # A key with a pending upload gets the "uploading" fields whether or
            # not it already appears above — present-but-pending means the PUT
            # landed and /upload-complete simply hasn't run yet; absent-but-
            # pending means it either has not landed or the store does not hide
            # a partial object from a concurrent listing. Either way the caller
            # should not treat it as ready.
            for key, pending in pending_uploads.list_for_scope(scope_obj).items():
                fields = pending.as_dict()
                if key in rows:
                    # Real bytes are already listed with a real size — a
                    # missing size_hint must not clobber it back to 0.
                    fields.pop("size", None)
                    rows[key].update(fields)
                else:
                    rows[key] = {"key": key, **fields}
            return JSONResponse({"files": list(rows.values())})

        files = await storage.list(scope_obj)

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
        # Same merge as the plain listing above, in this richer shape. A key
        # already in ``sources`` (the object landed; only /upload-complete
        # hasn't run) keeps its real size/format and gains the upload fields;
        # one that isn't there yet is added as a synthetic row so the convert
        # page shows "uploading" instead of the file simply not existing.
        for key, pending in pending_uploads.list_for_scope(scope_obj).items():
            fields = pending.as_dict()
            entry = sources.get(key)
            if entry is None:
                sources[key] = {
                    "key": key,
                    "last_modified": None,
                    "format": _format_label(key),
                    "available_targets": supported_targets_for(key),
                    "derived": [],
                    **fields,
                }
            else:
                # Real bytes are already listed with a real size — a missing
                # size_hint must not clobber it back to 0.
                fields.pop("size", None)
                entry.update(fields)
        out = sorted(
            sources.values(),
            key=lambda e: e.get("last_modified") or "",
            reverse=True,
        )
        return JSONResponse({"files": out})

    @api.get("/scopes/{scope}/overlays")
    async def api_scope_overlays(scope_obj: Scope = Depends(_scope_from_path)) -> JSONResponse:
        # Saved utility overlays (_overlays/<model-stem>.<utility>.glb) so the utils menu can
        # offer previously-generated merge/diff overlays for the loaded model. The client
        # filters by model stem — an overlay generated on MyModel only shows when
        # MyModel is loaded. Excluded from the normal file list (is_hidden_key).
        files = await storage.list(scope_obj)
        overlays = [
            {"key": f.key, "size": f.size, "last_modified": f.last_modified}
            for f in files
            if f.key.lstrip("/").startswith("_overlays/")
        ]
        overlays.sort(key=lambda o: o.get("last_modified") or "", reverse=True)
        return JSONResponse({"overlays": overlays})

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

    # ── Source-node change tracking ──────────────────────────────────
    #
    # "Has the external source moved since I exported this?" -- see
    # migrations/028_source_nodes.sql. Rows are written by the plugin that
    # drives the source: through the worker's ``source_nodes`` facade when that
    # worker has a pool, and through the POST below when it does not (a worker
    # outside the cluster reaches this API and nothing else).
    # A deployment with no Postgres answers 503 rather than an empty list,
    # because "nothing has changed" and "nobody is recording changes" must not
    # look the same to a consumer deciding whether to trust an asset.

    def _source_nodes_pool(request: Request):
        pool = getattr(request.app.state, "db_pool", None)
        if pool is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "source-node change tracking needs a database; this deployment runs without "
                    "DATABASE_URL. An asset's freshness cannot be answered here."
                ),
            )
        return pool

    def _source_node_json(n) -> dict:
        return {
            "node_ref": n.node_ref,
            "parent_ref": n.parent_ref,
            "name": n.name,
            "last_changed_at": n.last_changed_at.isoformat(),
            "last_changed_by": n.last_changed_by,
            "observed_at": n.observed_at.isoformat(),
        }

    @api.get("/scopes/{scope}/source-nodes")
    async def api_source_nodes(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        source: str | None = None,
        since: str | None = None,
        refs: str | None = None,
        limit: int = 1000,
    ) -> JSONResponse:
        """Change state for one scope's external-source nodes.

        Three shapes, one route, because they answer one question at different
        widths:

        * no ``source`` -- which sources are recorded here at all, with counts.
          What a consumer reads before it knows any refs.
        * ``refs=a,b,c`` -- those nodes, in one round trip. The freshness check:
          a caller holding published assets asks about every one at once rather
          than N times over a slow link.
        * ``since=<iso8601>`` -- what has moved since a cursor the caller keeps.
          The polling shape.
        """
        pool = _source_nodes_pool(request)
        # prefix(), not str(). `Scope` is a dataclass, so str() is its REPR --
        # "Scope(kind='shared', id=None)" -- which is not an identifier: it
        # changes shape if a field is ever added or reordered, and every row
        # keyed by the old spelling is orphaned without anything reporting it.
        # prefix() is the canonical scope key the storage layer already uses.
        scope_str = scope_obj.prefix()

        if not source:
            return JSONResponse(
                {
                    "scope": scope_str,
                    "sources": [
                        {
                            "source": row["source"],
                            "nodes": row["nodes"],
                            "last_changed_at": row["last_changed_at"].isoformat() if row["last_changed_at"] else None,
                            "observed_at": row["observed_at"].isoformat() if row["observed_at"] else None,
                        }
                        for row in await db_module.list_source_node_sources(pool, scope=scope_str)
                    ],
                }
            )

        limit = max(1, min(int(limit or 1000), 10000))

        if refs:
            wanted = [r.strip() for r in refs.split(",") if r.strip()]
            if len(wanted) > limit:
                raise HTTPException(status_code=400, detail=f"at most {limit} refs per request, got {len(wanted)}")
            found = await db_module.get_source_nodes(pool, scope=scope_str, source=source, node_refs=wanted)
            by_ref = {n.node_ref: n for n in found}
            return JSONResponse(
                {
                    "scope": scope_str,
                    "source": source,
                    "nodes": [_source_node_json(by_ref[r]) for r in wanted if r in by_ref],
                    # Named rather than merely absent: a ref nobody has recorded
                    # is not a ref that has not changed, and a caller must be
                    # able to tell those apart before trusting an asset.
                    "unknown": [r for r in wanted if r not in by_ref],
                }
            )

        parsed_since = None
        if since:
            try:
                parsed_since = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"since is not an ISO-8601 timestamp: {since!r}")

        nodes = await db_module.list_source_nodes_changed_since(
            pool, scope=scope_str, source=source, since=parsed_since, limit=limit
        )
        return JSONResponse(
            {
                "scope": scope_str,
                "source": source,
                "since": parsed_since.isoformat() if parsed_since else None,
                "nodes": [_source_node_json(n) for n in nodes],
                "truncated": len(nodes) >= limit,
            }
        )

    _SOURCE_NODE_WRITE_LIMIT = 10000

    def _parse_source_node(raw, index: int) -> dict:
        """One posted node -> the dict ``record_source_nodes`` takes.

        Strict about ``last_changed_at`` on purpose. That column only ever
        moves FORWARD (``GREATEST`` in the upsert), so a timestamp written
        wrong is not a transient wrong answer -- it is permanent, and no later
        correct observation can pull it back. A naive timestamp is the way that
        happens in practice: a writer in CET posting local wall-clock is read as
        UTC, lands up to two hours in the FUTURE, and every consumer then
        believes its export is stale forever. Cheap to reject, unfixable to
        accept, so an offset is required rather than assumed.
        """
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"nodes[{index}] must be an object")
        node_ref = (str(raw.get("node_ref") or "")).strip()
        if not node_ref:
            raise HTTPException(status_code=400, detail=f"nodes[{index}] is missing node_ref")
        changed = raw.get("last_changed_at")
        if isinstance(changed, str):
            try:
                changed = datetime.datetime.fromisoformat(changed.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"nodes[{index}].last_changed_at is not an ISO-8601 timestamp: {changed!r}",
                )
        if not isinstance(changed, datetime.datetime):
            raise HTTPException(
                status_code=400,
                detail=f"nodes[{index}] is missing last_changed_at (an ISO-8601 timestamp)",
            )
        if changed.tzinfo is None or changed.tzinfo.utcoffset(changed) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"nodes[{index}].last_changed_at has no UTC offset. This column only moves "
                    "forward, so a mis-read timestamp is permanent -- send an offset "
                    "(…Z or …+02:00) rather than local wall-clock."
                ),
            )

        def _opt(field: str):
            val = raw.get(field)
            if val is None:
                return None
            val = str(val).strip()
            return val or None

        return {
            "node_ref": node_ref,
            "parent_ref": _opt("parent_ref"),
            "name": _opt("name"),
            "last_changed_at": changed,
            "last_changed_by": _opt("last_changed_by"),
        }

    @api.post("/scopes/{scope}/source-nodes")
    async def api_source_nodes_record(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Record observed source nodes. Body: ``{source, nodes: [...]}``.

        WHY A ROUTE EXISTS AT ALL. Until now the only way to write this table
        was the worker's ``source_nodes`` facade, which is a database pool --
        so recording required the writer to hold Postgres credentials. That is
        fine for a worker inside the cluster and wrong for one outside it. A
        worker that joins the job queue from another network is a supported
        deployment, and such a worker deliberately runs without
        ``DATABASE_URL``: one fewer credential, and no route from outside to
        the database. Change tracking was therefore silently inert on exactly
        the workers most likely to be driving an external source -- the plugin
        warned, recorded nothing, and its caller re-scanned from cold forever.
        A writer that can already reach this API over HTTPS should not need a
        second, far more powerful credential to say "this node moved".

        Authorisation is the scope's own: ``_scope_from_path`` has already
        rejected a caller who is not a member of a project scope, which is the
        same gate that decides who may write that scope's blobs.

        Nodes are capped per request; the writer chunks. A roll-up stamps every
        node ABOVE a changed leaf (the writer's job -- see
        ``record_source_nodes``), so the natural batch is thousands of rows and
        an uncapped body is a proxy-sized surprise rather than a feature.
        """
        pool = _source_nodes_pool(request)
        scope_str = scope_obj.prefix()  # prefix(), not str() -- see the GET above.

        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        source = (str(body.get("source") or "")).strip()
        if not source:
            raise HTTPException(status_code=400, detail="source is required")
        raw_nodes = body.get("nodes")
        if not isinstance(raw_nodes, list):
            raise HTTPException(status_code=400, detail="nodes must be a list")
        if len(raw_nodes) > _SOURCE_NODE_WRITE_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"at most {_SOURCE_NODE_WRITE_LIMIT} nodes per request, got {len(raw_nodes)}. "
                    "Post in chunks; the upsert is idempotent."
                ),
            )
        nodes = [_parse_source_node(n, i) for i, n in enumerate(raw_nodes)]

        # An empty list is accepted rather than 400: a sweep that found nothing
        # changed is a normal outcome, and making the writer special-case it
        # invites the writer to skip the call and lose the "I ran" signal.
        written = await db_module.record_source_nodes(pool, scope=scope_str, source=source, nodes=nodes)
        logger.info(
            "source-nodes: %s recorded %d node(s) for source=%s scope=%s",
            getattr(user, "sub", "?"),
            written,
            source,
            scope_str,
        )
        return JSONResponse({"scope": scope_str, "source": source, "recorded": written})

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
        headers: dict[str, str] = {}
        if result.content_encoding:
            # See storage.py: gzipped sources/derived round-trip via
            # Content-Encoding so the browser auto-decompresses. A gzip-at-rest
            # object CANNOT be byte-ranged (a range would hand back compressed
            # bytes — see _serve_blob_range), so advertise no-range honestly:
            # otherwise a client/CDN trusts ``Accept-Ranges: bytes`` and a Range
            # GET against the presigned S3 object returns corrupt partial gzip.
            headers["Content-Encoding"] = result.content_encoding
            headers["Accept-Ranges"] = "none"
        else:
            headers["Accept-Ranges"] = "bytes"
        return StreamingResponse(result.stream, media_type="application/octet-stream", headers=headers)

    @api.put("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_put(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        from .converter import (
            is_derived_key,
            is_published_asset_key,
            is_versions_artefact_key,
        )

        clean = key.lstrip("/")
        if not clean:
            raise HTTPException(status_code=400, detail="empty key")
        if is_derived_key(clean):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
        # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
        # to them. Two predicates rather than one because they part company on deletability — see
        # is_published_asset_key. The is_derived_key guard above applies to both.
        if (
            not is_versions_artefact_key(clean)
            and not is_published_asset_key(clean)
            and not await _is_accepted_source(clean)
        ):
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
    # regular files), with one carve-out: a project member may DELETE a
    # published dataset blob under ``assets/`` in that project, because
    # such a publish targets the project scope and would otherwise be
    # irreversible for everyone but an admin. See _member_may_delete.
    # CI version blobs and the bake cache are protected even inside the
    # personal scope — deleting a source still cascades its derived
    # blobs via the shared storage_ops helpers.

    def _require_personal(scope_obj: Scope) -> None:
        if scope_obj.kind != "user":
            raise HTTPException(
                status_code=403,
                detail="file management is personal-scope only",
            )

    def _reject_protected_key(key: str) -> None:
        # Note the absence of is_published_asset_key here, and keep it absent.
        # ``assets/`` blobs are exempt from the upload extension whitelist the same way
        # ``versions/`` blobs are, but they are user-published rather than CI-pushed, so
        # whoever wrote one has to be able to remove it. Adding the prefix here would make
        # every published dataset a one-way door: writable once, never deletable, not even
        # by its publisher in their own personal scope.
        from .converter import is_derived_key, is_versions_artefact_key

        if is_derived_key(key) or is_versions_artefact_key(key):
            raise HTTPException(
                status_code=400,
                detail="versions/ and _derived/ keys are admin-managed",
            )

    def _member_may_delete(scope_obj: Scope, key: str) -> bool:
        """The one carve-out from "non-personal scopes are admin-managed".

        A published dataset is written into the scope it describes, which is
        usually a shared project scope rather than the publisher's personal one.
        Without this, a project-scoped publish is irreversible for everyone
        except an admin, which is the exact failure mode ``assets/`` exists to
        avoid. Membership is not re-checked here: ``_scope_from_path`` has
        already run ``scope.can_access``, which for a project scope is a
        ``project_members`` lookup, so reaching this code at all means the
        caller is a member of this project.

        Delete only — deliberately NOT extended to rename / move-to-folder.
        Those take a *destination* key, so allowing them would let a member move
        bytes out of ``assets/`` and land arbitrary content at an unvalidated
        key in a shared scope, bypassing the upload extension gate that only
        exempts the prefix itself. They also rewrite the key, and the key is the
        published dataset's identity and provenance; the correct fix for a wrong
        key is to delete and publish again, which this allows.
        """
        from .converter import is_published_asset_key

        return scope_obj.kind == "project" and is_published_asset_key(key)

    @api.delete("/scopes/{scope}/blobs/{key:path}")
    async def api_scope_blob_delete(
        key: str,
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        clean = key.lstrip("/")
        if not _member_may_delete(scope_obj, clean):
            _require_personal(scope_obj)
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
        from .converter import (
            is_derived_key,
            is_published_asset_key,
            is_versions_artefact_key,
        )

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
        # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
        # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
        # to them. Two predicates rather than one because they part company on deletability — see
        # is_published_asset_key. The is_derived_key guard above applies to both.
        if not is_versions_artefact_key(key) and not is_published_asset_key(key) and not await _is_accepted_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        try:
            url = await storage.presigned_put_url(scope_obj, key, expires_in_seconds=_UPLOAD_URL_TTL_SECONDS)
        except Exception as exc:
            logger.exception("presign failed for %s", key)
            raise HTTPException(status_code=500, detail=f"presign failed: {exc}") from exc
        # Size is a client-supplied hint, not verified against anything — it only
        # ever reaches a progress bar (as "0 / size_hint" until a heartbeat says
        # otherwise) or a 409 body, never a decision. See pending_uploads.
        raw_size = body.get("size")
        size_hint = raw_size if isinstance(raw_size, int) and raw_size >= 0 else None
        pending_uploads.mark_pending(
            scope_obj, key, user_id=user.sub, size_hint=size_hint, ttl_seconds=_UPLOAD_URL_TTL_SECONDS
        )
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
                "expires_in_seconds": _UPLOAD_URL_TTL_SECONDS,
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
        from .converter import (
            is_derived_key,
            is_published_asset_key,
            is_versions_artefact_key,
        )

        body = await request.json()
        key = (body.get("key") or "").strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if is_derived_key(key):
            raise HTTPException(status_code=403, detail="cannot write to _derived/")
        # ``versions/`` (CI-pushed build outputs) and ``assets/`` (published datasets) are stored
        # blobs, not conversion inputs, so the accepted-source extension whitelist does not apply
        # to them. Two predicates rather than one because they part company on deletability — see
        # is_published_asset_key. The is_derived_key guard above applies to both.
        if not is_versions_artefact_key(key) and not is_published_asset_key(key) and not await _is_accepted_source(key):
            raise HTTPException(status_code=415, detail=f"unsupported file type: {key}")
        meta = await storage.head(scope_obj, key)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"object not found at {key}; was the PUT successful?")
        # Only now — head() confirms the object is there, which is the closest
        # this process gets to "the PUT actually finished". Left pending on a
        # 404 above: an upload that failed or is still mid-flight must keep
        # blocking jobs against this key, not clear the gate on the strength of
        # a failed finalise call.
        pending_uploads.mark_complete(scope_obj, key)
        await _audit(request, user, scope_obj, "upload", key=key, status="ok")
        return JSONResponse({"key": key, "size": meta["size"]}, status_code=201)

    @api.post("/scopes/{scope}/upload-progress")
    async def api_scope_upload_progress(
        request: Request,
        scope_obj: Scope = Depends(_scope_from_path),
        user: User = Depends(auth_module.current_user),
    ) -> Response:
        """Heartbeat from the browser's own upload-progress events.

        Body: ``{key, loaded, total}``. The API cannot observe a direct
        browser→object-store PUT itself — that is the whole point of a
        presigned URL — so the only source of real progress is the XHR
        ``upload`` progress event already wired in ``putToPresignedUrl``. This
        endpoint exists so that progress reaches somewhere OTHER than the tab
        doing the upload: a second tab, a second viewer, or the same tab after
        a reload, all read it back via ``GET /scopes/{scope}/files``.

        Best-effort like ``_audit``: a call for a key with no pending upload
        (already completed, expired, or never registered — e.g. the browser
        raced its own ``/upload-complete``) is not an error, just nothing to
        update. 204 either way; the body only needs to distinguish "keep
        heartbeating" from "stop", which the caller gets from whether a later
        ``GET /files`` still shows this key as uploading.
        """
        try:
            body = await request.json()
        except Exception:
            body = {}
        key = (str(body.get("key") or "")).strip().lstrip("/")
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        try:
            loaded = int(body.get("loaded"))
            total = int(body.get("total"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="loaded and total must be integers") from None
        pending_uploads.heartbeat(scope_obj, key, loaded=loaded, total=total)
        return Response(status_code=204)

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
        pending = pending_uploads.get(scope_obj, source_key)
        if pending is not None:
            raise HTTPException(status_code=409, detail=_pending_upload_detail(source_key, pending))
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
        force_rebake = False
        if cached:
            manifest: dict | None = None
            try:
                manifest = json.loads(cached.decode("utf-8"))
            except Exception:
                logger.exception("fea-manifest: cache parse failed for %s; rebuilding", manifest_key)
            stale: str | None = None
            if manifest is not None:
                # Freshness, not just existence: a bake made before the
                # current bake output (bake_version) or before the source's
                # last re-upload (a deck is routinely re-solved in place under
                # the same name) must be rebuilt, not served forever.
                try:
                    src_head = await storage.head(scope_obj, source_key)
                    man_head = await storage.head(scope_obj, manifest_key)
                except Exception:
                    logger.exception("fea-manifest: head failed for %s", source_key)
                    src_head = man_head = None
                stale = fea_manifest_stale_reason(manifest, src_head, man_head)
                if stale is None:
                    return JSONResponse(manifest)
            # A cached entry exists but is stale or unusable. The worker's
            # already-cached short-circuit keys on the manifest's existence,
            # so a plain enqueue would no-op straight back here; force the
            # rebake through it.
            force_rebake = True
            if not queue.enabled and manifest is not None:
                # No worker to rebake with. A stale manifest still describes
                # real (older) results; serving it beats a 503 — log so the
                # operator sees why the deck lacks the newer bake output.
                logger.warning(
                    "fea-manifest: serving stale bake for %s (%s) — bake queue disabled",
                    source_key,
                    stale,
                )
                return JSONResponse(manifest)
            logger.info(
                "fea-manifest: cached bake for %s is stale (%s) — re-baking",
                source_key,
                stale or "unparsable manifest",
            )

        # Cache miss (or stale hit) — enqueue a worker bake and return 202.
        # Frontend polls /convert/{job_id} via the existing route and
        # re-fetches this endpoint when the job hits status=done.
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
                # A stale/unusable cached manifest EXISTS, so that
                # short-circuit must be bypassed for the rebake to happen.
                force_rebuild=force_rebake,
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
        ``{"key": k, "value": v}`` with v=null when unset. Admin-only; keys in
        the ``public.`` namespace are additionally readable by any authenticated
        user via ``GET /api/settings/{key}``."""
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
        if key == CAPABILITY_REQUIREMENTS_SETTING:
            await _publish_capability_requirements(value)
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

    @admin.post("/workers/prune")
    async def admin_prune_workers() -> JSONResponse:
        """Manually drop every currently-OFFLINE worker registry entry (heartbeat older than the
        staleness window). A live pod re-registers within a heartbeat tick, so this only clears dead
        registrations left by crashed / scaled-down pods — which otherwise linger and pollute the
        capability matrix. The hourly background task also prunes, but only at the conservative 2-day
        horizon; this button is the immediate manual cleanup."""
        if not queue.enabled:
            raise HTTPException(
                status_code=503,
                detail="worker registry requires a NATS-backed queue",
            )
        try:
            pruned = await queue.prune_stale_workers(max_age_s=queue.WORKER_STALE_AFTER_S)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"could not prune worker registry: {exc}",
            ) from exc
        return JSONResponse({"pruned": pruned})

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

    # ── Plugin-job schedules (cron for plugin jobs) ───────────────

    #: Options key the tick stamps with each firing's timestamp.
    #:
    #: ALWAYS, AND THERE IS NO WAY TO TURN IT OFF. Core hashes a plugin job's
    #: options into its source key so identical requests cache-hit, which is
    #: right for a user pressing a button twice and catastrophic for a schedule:
    #: byte-identical options every hour means the second firing and every one
    #: after returns the FIRST run's summary. Hourly green ticks, the worker never
    #: touched, and a caller trusting data that stopped moving.
    #:
    #: Read by nobody. It exists to change the hash, exactly as `refresh` does on
    #: the plugin-job read path -- and it is stamped by the TICK rather than
    #: offered as a schedule field, because a scheduled run that legitimately
    #: answers "same as last time" cannot be told apart from one that never
    #: happened.
    SCHEDULE_FIRE_TOKEN = "scheduled_at"

    def _plugin_schedule_options(schedule_row: dict, fired_at) -> dict:
        """The options to dispatch: the schedule's own, plus the fire token."""
        options = dict(schedule_row.get("options") or {})
        options[SCHEDULE_FIRE_TOKEN] = fired_at.isoformat()
        return options

    @admin.get("/plugin-jobs/schedules")
    async def admin_plugin_job_schedules_list(request: Request) -> JSONResponse:
        pool = _require_pool(request)
        include_archived = request.query_params.get("include_archived") in ("1", "true", "yes")
        rows = await db_module.list_plugin_job_schedules(pool, include_archived=include_archived)
        return JSONResponse({"schedules": rows})

    @admin.post("/plugin-jobs/schedules")
    async def admin_plugin_job_schedules_create(
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Create a schedule.

        Body: ``{"name", "cron_expr", "scope", "plugin_id", "options",
        "capability", "enabled"}``.

        ``plugin_id`` is NOT checked against the live plugin registry. A schedule
        may legitimately be created before the worker that serves it exists, or
        survive a pool being down for a day; refusing here would make the admin
        panel depend on a worker being up to configure anything. An id nothing
        serves shows up as a queued job that nobody picks up, which is already
        visible in /my-jobs.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        name = (body.get("name") or "").strip()
        cron_expr = _validate_cron(body.get("cron_expr") or "")
        scope_str = (body.get("scope") or "").strip()
        plugin_id = (body.get("plugin_id") or "").strip()
        options = body.get("options") or {}
        capability = (body.get("capability") or "").strip() or None
        enabled = bool(body.get("enabled", True))

        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if not scope_str:
            raise HTTPException(status_code=400, detail="scope required")
        if not plugin_id:
            raise HTTPException(status_code=400, detail="plugin_id required")
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")
        if SCHEDULE_FIRE_TOKEN in options:
            # Refused rather than silently overwritten: a caller who set it
            # believes it means something, and the tick is about to replace it.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"options.{SCHEDULE_FIRE_TOKEN} is reserved — the scheduler stamps it on every "
                    "firing so that two firings never share an options hash and cache-hit"
                ),
            )
        # Parses only. Slug-to-id resolution happens at fire time so renaming a
        # project does not strand a schedule.
        _ = _parse_scope(scope_str, user)

        try:
            row = await db_module.create_plugin_job_schedule(
                pool,
                name=name,
                cron_expr=cron_expr,
                scope=scope_str,
                plugin_id=plugin_id,
                options=options,
                capability=capability,
                enabled=enabled,
                next_fire_at=_next_fire(cron_expr),
                created_by=user.sub,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "UniqueViolationError":
                raise HTTPException(status_code=409, detail=f"schedule name {name!r} already in use") from exc
            raise
        return JSONResponse(row, status_code=201)

    @admin.patch("/plugin-jobs/schedules/{schedule_id}")
    async def admin_plugin_job_schedules_update(
        schedule_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Partial update. Only the fields present in the body move."""
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        fields: dict = {}

        if "name" in body:
            name = (body.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="name cannot be empty")
            fields["name"] = name
        if "cron_expr" in body:
            fields["cron_expr"] = _validate_cron(body.get("cron_expr") or "")
            # A changed expression makes the stored next_fire_at meaningless, so
            # it is recomputed here rather than left to drift until the next fire.
            fields["next_fire_at"] = _next_fire(fields["cron_expr"])
        if "scope" in body:
            scope_str = (body.get("scope") or "").strip()
            if not scope_str:
                raise HTTPException(status_code=400, detail="scope cannot be empty")
            _ = _parse_scope(scope_str, user)
            fields["scope"] = scope_str
        if "plugin_id" in body:
            plugin_id = (body.get("plugin_id") or "").strip()
            if not plugin_id:
                raise HTTPException(status_code=400, detail="plugin_id cannot be empty")
            fields["plugin_id"] = plugin_id
        if "options" in body:
            options = body.get("options") or {}
            if not isinstance(options, dict):
                raise HTTPException(status_code=400, detail="options must be an object")
            if SCHEDULE_FIRE_TOKEN in options:
                raise HTTPException(
                    status_code=400,
                    detail=f"options.{SCHEDULE_FIRE_TOKEN} is reserved — the scheduler stamps it",
                )
            fields["options"] = options
        if "capability" in body:
            fields["capability"] = (body.get("capability") or "").strip() or None
        if "enabled" in body:
            fields["enabled"] = bool(body.get("enabled"))
            # Re-enabling a schedule whose next_fire_at is long past would fire
            # immediately and then again on its real slot. Recomputed from now.
            if fields["enabled"]:
                current = await db_module.get_plugin_job_schedule(pool, schedule_id)
                if current is None:
                    raise HTTPException(status_code=404, detail="schedule not found")
                fields.setdefault("next_fire_at", _next_fire(current["cron_expr"]))

        row = await db_module.update_plugin_job_schedule(pool, schedule_id, **fields)
        if row is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        return JSONResponse(row)

    @admin.delete("/plugin-jobs/schedules/{schedule_id}")
    async def admin_plugin_job_schedules_archive(schedule_id: str, request: Request) -> JSONResponse:
        pool = _require_pool(request)
        if not await db_module.archive_plugin_job_schedule(pool, schedule_id):
            raise HTTPException(status_code=404, detail="schedule not found, or already archived")
        return JSONResponse({"archived": schedule_id})

    @admin.post("/plugin-jobs/schedules/{schedule_id}/run")
    async def admin_plugin_job_schedules_run_now(schedule_id: str, request: Request) -> JSONResponse:
        """Fire a schedule immediately, without waiting for its slot.

        The reason this exists is that a schedule is otherwise unverifiable: an
        admin who has just created one has no way to learn whether its options,
        scope and plugin actually produce a job short of waiting for the cron to
        come round. It does not disturb the timetable -- ``next_fire_at`` is left
        alone, so the scheduled slot still happens.
        """
        pool = _require_pool(request)
        row = await db_module.get_plugin_job_schedule(pool, schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="schedule not found")
        from datetime import datetime, timezone

        outcome = await _plugin_schedule_fire(pool, row, fired_at=datetime.now(timezone.utc))
        if outcome.get("skipped"):
            # 409: the request was valid, the state said no. The reason is the
            # useful half.
            raise HTTPException(status_code=409, detail=outcome["skipped"])
        return JSONResponse(outcome)

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
        """Enqueue one schedule's job. Returns what happened.

        ``{"job_id": ..., "schedule": ...}`` on a firing, or
        ``{"skipped": "<reason>"}`` when state said no. Every skip is also written
        to the row, because a schedule that silently does nothing is the failure
        this whole feature exists to remove.
        """
        sched_id = schedule_row["id"]
        plugin_id = schedule_row["plugin_id"]

        try:
            scope_obj = _parse_scope(schedule_row["scope"], _SystemUser())
            scope_obj = await _resolve_project_scope(pool, scope_obj)
        except HTTPException as exc:
            reason = f"scope {schedule_row['scope']!r} did not resolve ({exc.status_code}): {exc.detail}"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}
        except Exception as exc:
            logger.exception("plugin-job scheduler: scope resolution crashed for %s", sched_id)
            reason = f"scope resolution crashed: {exc}"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}

        # Concurrent-fire guard. A plugin job can hold a single licensed
        # workstation for minutes, so an overlapping firing does not just double
        # the load -- the two contend for one resource and the loser fails in a
        # way that reads as the plugin's fault. The missed slot is NOT backfired:
        # the next slot is the next chance, which is the same choice the audit
        # scheduler makes and for the same reason.
        try:
            candidates = await db_module.plugin_job_in_flight_jobs(
                pool,
                scope_kind=scope_obj.kind,
                scope_id=scope_obj.id,
                plugin_id=plugin_id,
            )
            # THE AUDIT ROW IS NOT THE ANSWER ON ITS OWN. Its terminal status is
            # written by the worker, so a worker with no database pool never writes
            # one -- it announces that at startup -- and in such a deployment every
            # plugin job stays `queued` in the log forever. Trusting the row alone
            # would let a schedule fire exactly ONCE and then block itself for good,
            # which is a worse failure than the double-firing the guard prevents.
            #
            # So each candidate is checked against the queue, which the worker DOES
            # update. A missing entry is decisive too: it means the job is gone from
            # the queue entirely, so nothing is going to run it whatever the row says.
            in_flight = None
            for candidate in candidates:
                entry = await queue.get(candidate) if queue.enabled else None
                if entry is None:
                    continue
                if str(getattr(entry, "status", "") or "").lower() in ("queued", "running"):
                    in_flight = candidate
                    break
        except Exception:
            logger.exception("plugin-job scheduler: concurrent-fire check failed for %s", sched_id)
            # Not firing is the safe half of an unknown: a duplicate run on a
            # single-seat resource is worse than a missed slot.
            reason = "could not check whether a previous job is still running; slot skipped"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}
        if in_flight is not None:
            reason = f"previous {plugin_id} job {in_flight} still queued or running"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}

        # ROUTING IS RESOLVED HERE, AND A SLOT THAT CANNOT ROUTE IS SKIPPED.
        #
        # A plugin job's pool comes from the plugin's LIVE spec, which exists only
        # while a worker advertising it is online. Enqueuing anyway is not a
        # smaller failure than skipping: with no spec there is no capability, and
        # a job with no capability goes to the default pool, which no specialised
        # worker subscribes to. Nothing ever pulls it -- so it is not retried, and
        # it never reaches the delivery-attempt cap that would mark it failed. It
        # sits at "queued" for good, with nothing in any worker's log to explain
        # it, which is the single worst outcome available here.
        #
        # A missed slot is recoverable and says so; the next slot is the next
        # chance, and `last_skipped_reason` names what was wrong. This matters most
        # during a worker restart, which is exactly when an unattended schedule is
        # most likely to fire into an empty fleet.
        #
        # An explicitly configured capability is trusted and fires regardless: the
        # admin named the pool, and a pool may be legitimately empty for a while.
        capability = (schedule_row.get("capability") or "").strip() or None
        plugin_spec = None
        if capability is None:
            for _spec in (await _live_worker_specs("plugin_specs")).values():
                if _spec.get("slug") == plugin_id or _spec.get("id") == plugin_id:
                    plugin_spec = _spec
                    break
            if plugin_spec is None:
                reason = (
                    f"no online worker advertises {plugin_id!r}, so the job could not be routed to a "
                    f"pool; slot skipped rather than queued where nothing would ever pull it"
                )
                await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
                return {"skipped": reason}

        options = _plugin_schedule_options(schedule_row, fired_at)
        try:
            job_id = await _enqueue_plugin_job(
                plugin_id=plugin_id,
                options=options,
                scope_obj=scope_obj,
                capability=capability,
                user=_SystemUser(),
                pool=pool,
                plugin_spec=plugin_spec,
            )
        except HTTPException as exc:
            reason = f"enqueue refused ({exc.status_code}): {exc.detail}"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}
        except Exception as exc:
            logger.exception("plugin-job scheduler: enqueue crashed for %s", sched_id)
            reason = f"enqueue crashed: {exc}"
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}

        # CLEARED ON SUCCESS, not only on claim. The tick's claim clears it, but
        # "Run now" calls this function directly and bypasses the claim -- so a
        # schedule that skipped once and then fired successfully kept displaying the
        # old skip note indefinitely, which reads as the current state and sent
        # someone looking for a queued job that had finished long before.
        #
        # Written here rather than at each call site because every successful
        # firing, however it was triggered, makes the previous skip history.
        await db_module.update_plugin_job_schedule(pool, sched_id, last_job_id=job_id, last_skipped_reason=None)
        logger.info(
            "plugin-job scheduler: fired %s (%s) -> job %s",
            schedule_row["name"],
            plugin_id,
            job_id,
        )
        return {"job_id": job_id, "schedule": schedule_row["name"], "plugin_id": plugin_id}

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

    def _audit_cells_for_files(
        files,
        validate_only: bool,
    ) -> list[tuple[str, str]]:
        """Pure cell enumeration over an already-fetched file listing —
        lets the dispatcher derive both the conversion grid and the
        parity-cell reservation from ONE listing, so the two counts
        can't disagree on what files existed at dispatch time."""
        from .converter import ConverterRegistry, is_hidden_key, is_supported_source

        cells: list[tuple[str, str]] = []
        for f in files:
            # Skip ALL internal namespaces, not just _derived/: _overlays/ (utility previews) and
            # _reconvert/ (gallery throwaway re-conversions) are not corpus sources and must never
            # become audit cells. is_hidden_key covers all three; is_derived_key did not match
            # _reconvert/ (deliberately not a derived product), so it leaked into runs.
            if is_hidden_key(f.key):
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
        files = await storage.list(scope_obj)
        return _audit_cells_for_files(files, validate_only)

    async def _audit_dispatch(
        run_id: str,
        scope_obj: Scope,
        worker_pool: str | None,
        user_sub: str,
        pool,
        force_rebuild: bool = False,
        validate_only: bool = False,
        extend: bool = False,
        reserve_validation: bool = False,
        consume_reserve: bool = False,
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

        ``reserve_validation`` (auto-validate runs) counts the parity cells
        into the run's total upfront — as ``validate_total`` — so the total
        is complete from the very start instead of growing when the
        validation pass begins. The parity cells themselves are enqueued
        later by the auto-validate poller, which fires once the conversion
        cells alone have landed.

        ``extend`` *appends* the enumerated cells to an existing run instead
        of setting the total from scratch — used by the validation pass.
        With ``consume_reserve`` (a run dispatched with
        ``reserve_validation``) the reserved count is swapped for the actual
        parity cell count, so the total only moves by scope drift between
        the two enumerations. Without it (manual Validate on a finished run,
        or pre-reservation rows) the total grows by the parity cell count
        and the run reopens; an empty cell set is then a no-op.

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
            files = await storage.list(scope_obj)
        except Exception:
            logger.exception("audit run %s: scope listing failed", run_id)
            if extend:
                if consume_reserve:
                    # Release the reservation so the run can finish instead of
                    # hanging forever on cells that will never be enqueued.
                    await db_module.consume_audit_run_validation_reserve(pool, run_id, 0)
                return
            await db_module.set_audit_run_total(pool, run_id, 0)
            return
        cells = _audit_cells_for_files(files, validate_only)

        if extend:
            if consume_reserve:
                # Swap the upfront reservation for the actual parity cell
                # count (finishes the run right here when that count is 0).
                await db_module.consume_audit_run_validation_reserve(pool, run_id, len(cells))
                if not cells:
                    return
            else:
                if not cells:
                    return  # nothing to append; leave the finished run untouched
                await db_module.extend_audit_run_total(pool, run_id, len(cells))
        else:
            # Reserve the auto-validation parity cells in the total now — the
            # counter-bump finish check compares against the full total, so
            # the run stays 'running' through the gap between the last
            # conversion cell and the poller dispatching the parity cells.
            reserved = len(_audit_cells_for_files(files, True)) if reserve_validation else 0
            await db_module.set_audit_run_total(pool, run_id, len(cells) + reserved, validate_total=reserved)
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
                # Count the auto-validation parity cells into the run total
                # from the start (dispatched later by the poller).
                reserve_validation=auto_validate,
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
        # Deep-clean the cancelled cells' still-queued JetStream messages so the
        # worker never pulls a doomed conversion (wasted download/convert/hang).
        purge_ids = run.pop("cancelled_job_ids", []) or []
        if purge_ids and queue is not None:
            try:
                await queue.purge_jobs(purge_ids)
            except Exception:
                logger.exception("audit cancel: queue purge failed for run %s", run_id)
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
                reserve_validation=prior["auto_validate"],
            )
        return JSONResponse(run, status_code=202)

    @admin.post("/audit/runs/{run_id}/rerun-cell")
    async def admin_audit_run_rerun_cell(
        run_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Re-run one cell of an existing run in place (right-click → Rerun).

        Enqueues a single force-rebuild conversion for ``{key, target}`` against
        the run's own scope/pool, re-points the cell's audit row at the new job
        and reopens the run (``db.reset_audit_cell_for_rerun``). The worker's
        normal completion path updates the row and re-finishes the run, so the
        counters, the sum-of-cells runtime and the grid cell all reflect the
        fresh result — no full re-dispatch of the other 900+ cells."""
        from .converter import derived_key_for

        body = await request.json()
        key = (body or {}).get("key")
        target = (body or {}).get("target")
        if not key or not target:
            raise HTTPException(status_code=400, detail="key and target are required")
        if target == "parity":
            raise HTTPException(
                status_code=400,
                detail="parity cells have no derived product — use Re-validate on the run instead",
            )

        pool = _require_pool(request)
        prior = await db_module.get_audit_run(pool, run_id)
        if prior is None:
            raise HTTPException(status_code=404, detail="audit run not found")
        worker_pool = prior["worker_pool"]
        if isinstance(worker_pool, str) and worker_pool.strip().lower() == _WASM_POOL:
            raise HTTPException(status_code=400, detail="cannot re-run a single cell of a wasm run from the server")
        if not queue.enabled:
            raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")

        s = _parse_scope(prior["scope"], user)
        s = await _resolve_project_scope(pool, s)
        if not await scope_can_access(user, s, pool):
            raise HTTPException(status_code=403, detail="forbidden")

        try:
            derived_key_for(key, target)  # validate the target is convertible for this source
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"not a convertible cell: {exc}") from exc

        try:
            job = await queue.enqueue(
                key,
                target,
                scope_kind=s.kind,
                scope_id=s.id,
                target_capability=worker_pool,
                force_rebuild=True,
            )
        except Exception as exc:
            logger.exception("rerun-cell enqueue failed for %s -> %s", key, target)
            raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

        found = await db_module.reset_audit_cell_for_rerun(pool, run_id, key, target, job.job_id)
        if not found:
            raise HTTPException(status_code=404, detail="cell not found in this run")
        run = await db_module.get_audit_run(pool, run_id)
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
        deleted, queued_job_ids = await db_module.delete_audit_run(pool, run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="audit run not found")
        # The rows are gone, so the worker's cancel check can't catch these — purge
        # their still-queued JetStream messages so they aren't pulled + processed.
        if queued_job_ids and queue is not None:
            try:
                await queue.purge_jobs(queued_job_ids)
            except Exception:
                logger.exception("audit delete: queue purge failed for run %s", run_id)
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

    @admin.patch("/corpora/{slug}")
    async def admin_corpora_update(slug: str, request: Request) -> JSONResponse:
        """Update a corpus's display name / description.

        Body: ``{"name": "...", "description": "..."}`` — name required
        non-empty, empty description clears it. The slug itself is
        immutable: it's baked into the storage prefix
        (``corpus/<slug>/``) and scope URLs, so renaming it would
        orphan the bucket bytes.
        """
        pool = _require_pool(request)
        body = await request.json() if await request.body() else {}
        name = (body.get("name") or "").strip()
        description = (body.get("description") or "").strip() or None
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        row = await db_module.update_corpus(pool, slug, name=name, description=description)
        if row is None:
            raise HTTPException(status_code=404, detail=f"corpus {slug!r} not found")
        return JSONResponse(row)

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

    @admin.get("/audit/frontend-loads")
    async def admin_audit_frontend_loads(
        request: Request,
        since: int = 30,
    ) -> JSONResponse:
        """Per-file browser model-load perf snapshot (``action = 'view'``).

        One cell per GLB loaded, with p50/p95 of every load phase and a
        ``dominant_bound`` label (io / network / cpu / gpu) so a slow
        load is immediately attributable to a bottleneck class. ``since``
        is days back from now. Drives the admin "Frontend Loads" tab.
        """
        from datetime import datetime, timezone

        pool = _require_pool(request)
        cells = await db_module.aggregate_view_load_metrics(pool, since_days=since)
        return JSONResponse(
            {
                "cells": cells,
                "since_days": max(1, min(365, since)),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @admin.get("/audit/frontend-loads/hotspots")
    async def admin_audit_frontend_loads_hotspots(
        request: Request,
        key: str | None = None,
        since: int = 30,
        limit: int = 100,
        kind: str = "view",
    ) -> JSONResponse:
        """Function-level hotspots across browser ``view`` loads or
        ``render`` windows (``kind``) — summed JS Self-Profiling self-time
        per TS/WASM frame. Optionally scoped to one ``key`` (GLB file).
        Empty ``functions`` with ``loads_in_window=0`` means no profiled
        rows (self-profiling unsupported/disabled or
        ``Document-Policy: js-profiling`` not served)."""
        pool = _require_pool(request)
        key_arg = (key or "").strip() or None
        action = "render" if (kind or "").strip().lower() == "render" else "view"
        out = await db_module.aggregate_view_load_hotspots(
            pool, action=action, key=key_arg, since_days=since, limit=limit
        )
        return JSONResponse({**out, "key": key_arg, "kind": action, "since_days": max(1, min(365, since))})

    @admin.get("/audit/render")
    async def admin_audit_render(
        request: Request,
        since: int = 30,
    ) -> JSONResponse:
        """Per-file steady-state render-performance snapshot
        (``action = 'render'``). One cell per GLB with median/worst FPS,
        CPU vs GPU frame time, draw calls + triangles rendered, and a
        ``dominant_bound`` (cpu / gpu) label."""
        from datetime import datetime, timezone

        pool = _require_pool(request)
        cells = await db_module.aggregate_render_metrics(pool, since_days=since)
        return JSONResponse(
            {
                "cells": cells,
                "since_days": max(1, min(365, since)),
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

    def _audit_time_bounds(since: str | None, until: str | None):
        """Parse the shared time window, or 400 with the offending value.

        Relative forms ("6h") resolve against the SERVER clock — see
        db.parse_audit_time_bound for why the browser must not do it. Both the
        log and the summary take the same two parameters so a window set on one
        means the same thing on the other.
        """
        try:
            return (
                db_module.parse_audit_time_bound(since),
                db_module.parse_audit_time_bound(until),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # REGISTERED BEFORE ``/audit/{audit_id}`` ON PURPOSE. Starlette matches in
    # declaration order, so a static segment that could also parse as a path
    # parameter has to come first — otherwise this lands on the row-detail
    # route, which tries to read "summary" as an int and 422s. Moving it below
    # is a silent breakage: the URL still exists, it just answers wrong.
    @admin.get("/audit/summary")
    async def admin_audit_summary(
        request: Request,
        user_sub: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        key: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> JSONResponse:
        """Counts behind the Audit tab's Overview, under the log's own filter.

        Takes the same query parameters as ``GET /admin/audit`` and normalises
        them identically, so one filter drives both surfaces. ``status`` is
        accepted-and-ignored by omission: the summary exists to show how a
        population splits across states, and the status tiles are the control
        that sets that filter — honouring it would zero the other tiles the
        moment you clicked one. See ``db.summarize_audit``.

        Counting is done in the database rather than over a page of rows: the
        log is keyset-paginated at 100, so summing what the client happens to
        be holding would report "13 failed" for a sweep with hundreds.
        """
        pool = _require_pool(request)
        key_like = (key or "").strip() or None
        target_format = (target or "").strip().lstrip(".").lower() or None
        since_ts, until_ts = _audit_time_bounds(since, until)
        summary = await db_module.summarize_audit(
            pool,
            user_sub=user_sub,
            scope_kind=scope_kind,
            scope_id=scope_id,
            action=action,
            target_format=target_format,
            key_like=key_like,
            since=since_ts,
            until=until_ts,
        )
        return JSONResponse(summary)

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
            # The original is gone — the exact case failure capture exists for.
            # Serve the preserved copy so a row stays reproducible after the
            # user deletes (or replaces) the file that broke.
            failure_key = row.get("failure_key")
            if not failure_key:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            try:
                result = await storage.open_stream(failure_capture.failure_scope(), failure_key)
            except FileNotFoundError:
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

    @admin.get("/audit/{audit_id}/log")
    async def admin_audit_log_file(
        audit_id: int,
        request: Request,
    ) -> StreamingResponse:
        """Download the captured stdout/stderr log for a conversion (every conversion now ships
        one). 404 when the row or its log_key is missing — i.e. a conversion that predates the
        log-capture, not a silent gap."""
        pool = _require_pool(request)
        row = await db_module.get_audit_by_id(pool, audit_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"audit row {audit_id} not found")
        log_key = row.get("log_key")
        if not log_key:
            raise HTTPException(status_code=404, detail="no log attached to this row")
        scope = (
            Scope.shared()
            if row["scope_kind"] == "shared"
            else Scope(kind=row["scope_kind"], id=row["scope_id"])  # type: ignore[arg-type]
        )
        try:
            result = await storage.open_stream(scope, log_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        headers = {"Content-Disposition": f'attachment; filename="{log_key.rsplit("/", 1)[-1]}"'}
        if result.content_encoding:
            headers["Content-Encoding"] = result.content_encoding
        return StreamingResponse(result.stream, media_type="text/plain; charset=utf-8", headers=headers)

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

    @admin.get("/audit/{audit_id}/client-metrics")
    async def admin_audit_client_metrics(
        audit_id: int,
        request: Request,
    ) -> JSONResponse:
        """Return the ``client_metrics`` payload for one browser
        view/render audit row — the per-phase IO/network/CPU/GPU split,
        payload + device context, and (when profiling was on) the
        per-function self-time frames. Backs the audit-log detail view's
        Client tab so a single load/render event can be inspected.
        ``null`` when the row isn't a browser-instrumented one."""
        pool = _require_pool(request)
        cm = await db_module.get_audit_client_metrics(pool, audit_id)
        return JSONResponse({"audit_id": audit_id, "client_metrics": cm})

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

        tmp = new_temp_path(suffix=".prof")
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
                # Report only the failed key to the client; the exception detail is logged
                # above (avoid leaking backend/stack-trace text in the response).
                blob_errors.append(entry["profile_key"])
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
        target: str | None = None,
        status: str | None = None,
        key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        before_id: int | None = None,
        limit: int = 100,
    ) -> JSONResponse:
        pool = _require_pool(request)
        # ``key`` is a case-insensitive substring filter on the source filepath/
        # filename so the audit log can be narrowed to one file or folder.
        key_like = (key or "").strip() or None
        # ``target`` filters by the conversion's target format (glb / ifc / step / …).
        target_format = (target or "").strip().lstrip(".").lower() or None
        # ``status`` filters by job state (queued / running / done / error).
        status_norm = (status or "").strip().lower() or None
        since_ts, until_ts = _audit_time_bounds(since, until)
        rows = await db_module.list_audit(
            pool,
            user_sub=user_sub,
            scope_kind=scope_kind,
            scope_id=scope_id,
            action=action,
            target_format=target_format,
            statuses=[status_norm] if status_norm else None,
            key_like=key_like,
            since=since_ts,
            until=until_ts,
            limit=limit,
            before_id=before_id,
        )
        # Page cursor: smallest id from this batch. Caller passes it back
        # as ``before_id`` to fetch the next older page.
        next_before = rows[-1]["id"] if len(rows) >= max(1, min(limit, 500)) else None
        return JSONResponse({"entries": rows, "next_before_id": next_before})

    @admin.get("/worker-packages/{image_tag:path}")
    async def admin_worker_packages(image_tag: str, request: Request) -> JSONResponse:
        """The captured package manifest ("pixi list") for a worker image tag —
        linked from a convert audit row via its worker_image_tag."""
        pool = _require_pool(request)
        manifest = await db_module.get_worker_packages(pool, image_tag)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"no package manifest for worker {image_tag!r}")
        return JSONResponse(manifest)

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

    # A bot name is one path-safe token. The colon is excluded because it is
    # the SEPARATOR: a name containing one could spell another bot's subject
    # (`ci:<slug>:a:b`) and quietly take over its tokens and its revocation.
    _CI_BOT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

    def _ci_bot_identity(slug: str, name: str | None) -> tuple[str, str, str]:
        """``(sub, email, display)`` for a project's CI bot.

        Unnamed is ``ci:<slug>`` -- unchanged, so every token already issued
        under that subject keeps working and keeps being rotated by the same
        call as before. A name appends one more segment.
        """
        if not name:
            return f"ci:{slug}", f"ci+{slug}@bot.local", f"CI Bot: {slug}"
        return f"ci:{slug}:{name}", f"ci+{slug}.{name}@bot.local", f"CI Bot: {slug} / {name}"

    async def _ci_bot_request(request: Request, project_id: str) -> tuple[object, str, str, str, str]:
        """Shared prologue: validate, resolve the project, build the identity."""
        pool = _require_pool(request)
        pid = _validate_uuid(project_id, "project_id")
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        name = (str(body.get("name") or "")).strip().lower() or None
        if name is not None and not _CI_BOT_NAME_RE.match(name):
            raise HTTPException(
                status_code=400,
                detail=(
                    "name must be 1-64 characters of a-z, 0-9, dot, dash or underscore, "
                    "starting alphanumeric. A colon is not allowed: it separates the "
                    "project from the bot, so a name containing one could spell another "
                    "bot's identity."
                ),
            )
        row = await pool.fetchrow(
            "SELECT slug FROM projects WHERE id = $1 AND archived_at IS NULL",
            pid,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="project not found")
        sub, email, display = _ci_bot_identity(row["slug"], name)
        return pool, pid, sub, email, display

    @admin.post("/projects/{project_id}/ci-bot")
    async def admin_provision_ci_bot(
        project_id: str,
        request: Request,
    ) -> JSONResponse:
        """Provision (or rotate the token of) a CI bot user for a project.

        One-shot: creates the bot user row if missing, ensures it's a
        project member, revokes any prior tokens, and mints a fresh
        30-day CLI bearer. The token is returned exactly once, and
        re-calling ROTATES -- prior tokens for that bot stop validating
        immediately via the per-user revoke cutoff. Always admin-gated.

        MORE THAN ONE BOT PER PROJECT. Body: ``{"name": "..."}``, optional.
        Without it the subject is ``ci:<slug>``, exactly as before. With it,
        ``ci:<slug>:<name>``.

        The reason is that one identity per project forces every consumer to
        share one credential, and the revoke cutoff is stored per SUBJECT --
        so rotating for one consumer silently breaks the others, and every
        audit row reads ``ci:<slug>`` no matter which of them acted. Give the
        build uploader and a data-recording worker a name each and both
        problems go away: separate rotation, separate revocation, and an audit
        trail that says which one did the thing.

        Nothing about the token or the revocation model changes to allow it. A
        distinct subject simply HAS its own cutoff, which is why this is a new
        segment on the subject rather than a token id and a revocation list.
        """
        pool, pid, bot_sub, bot_email, bot_display = await _ci_bot_request(request, project_id)

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

    @admin.post("/projects/{project_id}/ci-bot/revoke")
    async def admin_revoke_ci_bot(
        project_id: str,
        request: Request,
    ) -> JSONResponse:
        """Kill a CI bot's tokens WITHOUT minting a replacement.

        Until now the only way to invalidate a bot's token was to mint another
        one, which is the wrong move for a leaked credential or a decommissioned
        consumer: it hands you a fresh secret you did not want and leaves the
        bot able to act. Revoking on its own is the thing an operator reaches
        for when something has gone wrong, and it did not exist.

        The bot stays a project member. Removing it is a separate, deliberate
        act (``DELETE /projects/{id}/members/{sub}``) -- and keeping the
        membership means its audit history still resolves to a named principal
        rather than a bare subject nobody can identify later.
        """
        pool, _pid, bot_sub, bot_email, bot_display = await _ci_bot_request(request, project_id)
        bot_user = User(
            sub=bot_sub,
            email=bot_email,
            display_name=bot_display,
            groups=frozenset(),
            is_admin=False,
        )
        revoked_at = await auth_module.revoke_cli_tokens(pool, bot_user)
        logger.info("admin: revoked CI bot tokens for %s", bot_sub)
        return JSONResponse({"user_sub": bot_sub, "revoked_at": revoked_at})

    @admin.post("/jobs/{job_id}/cancel")
    async def admin_cancel_job(
        job_id: str,
        request: Request,
        user: User = Depends(auth_module.current_user),
    ) -> JSONResponse:
        """Cancel and clear any job, whoever started it. Admin only.

        THE JOB THIS EXISTS FOR is the one nothing will ever finish: queued
        against a capability no live worker serves, or left behind by a pool
        that was renamed or retired. Nothing pulls it, so it never reaches a
        terminal status, so ``purge_completed_jobs`` -- which sweeps terminal
        entries only -- never touches it. The entry stays in the KV bucket
        forever, is replayed by every registry scan, and reads in the admin
        panel as work still pending.

        The user-facing ``my-jobs/{job_id}/cancel`` could not clear these:
        its SQL filters on ``audit_log.user_sub``, so only the person who
        started a job can stop it, and an operator cleaning up after a retired
        pool is by definition not that person.

        TWO INDEPENDENT EFFECTS, BOTH REPORTED, because a stuck job can be
        stuck in either half alone:

        * ``cancelled`` -- the audit row moved from queued/running to
          cancelled. False if the row is missing or already terminal.
        * ``purged`` -- the KV entry was dropped. False if there was none.

        404 only when NEITHER did anything, i.e. there was no such job in
        either place. A row that is already terminal with a leftover KV entry
        is a real thing to clean up, and reporting that as "not found" would
        send an operator looking for a job that is right in front of them.

        Note the message may still be in the JetStream stream: this marks
        state, it does not reach into the stream. A worker that later pulls it
        checks the audit row before starting and drops it (see
        ``_should_skip_cancelled`` in worker.py), so a cancelled job stays
        cancelled -- but the message itself ages out on the stream's own
        limits rather than disappearing here.
        """
        local = local_jobs.registry.get(job_id)
        local_cancelled = local_jobs.registry.cancel(job_id) if local is not None else False

        pool = getattr(request.app.state, "db_pool", None)
        cancelled = False
        if pool is not None:
            cancelled = await db_module.admin_cancel_audit_by_job(
                pool,
                job_id=job_id,
                reason=f"cancelled by administrator {user.sub}",
            )

        purged = False
        queue_obj = getattr(request.app.state, "queue", None)
        if queue_obj is not None:
            try:
                purged = await queue_obj.purge_job(job_id)
            except Exception:
                logger.exception("admin: purging KV entry for job %s failed", job_id)

        if not (cancelled or purged or local_cancelled):
            raise HTTPException(status_code=404, detail="no such job in the audit log or the queue")

        logger.info(
            "admin: %s cancelled job %s (audit=%s kv=%s local=%s)",
            user.sub,
            job_id,
            cancelled,
            purged,
            local_cancelled,
        )
        return JSONResponse({"job_id": job_id, "cancelled": cancelled or local_cancelled, "purged": purged})

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
        ".gnx": "Genie workspace",
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
