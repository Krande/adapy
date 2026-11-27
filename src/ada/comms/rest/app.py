from __future__ import annotations

import asyncio
import pathlib
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

from ada.config import logger

from . import auth as auth_module
from . import background_tasks
from . import db as db_module
from . import local_shutdown
from .config import Settings, load_settings
from .job_grants import GrantService, KvGrantBindings, MemoryGrantBindings
from .job_transport import build_transport
from .plugin_registry import load_local_plugins
from .queue import JobQueue
from .routes.admin_audit_perf import router as admin_audit_perf_router
from .routes.admin_audit_runs import router as admin_audit_runs_router
from .routes.admin_audit_schedules import router as admin_audit_schedules_router
from .routes.admin_corpora import router as admin_corpora_router
from .routes.admin_db import retention_loop as db_retention_loop
from .routes.admin_db import router as admin_db_router
from .routes.admin_plugin_jobs import router as admin_plugin_jobs_router
from .routes.admin_projects import router as admin_projects_router
from .routes.admin_provider_grants import router as admin_provider_grants_router
from .routes.admin_settings import router as admin_settings_router
from .routes.admin_storage import router as admin_storage_router
from .routes.admin_storage_compression import router as admin_storage_compression_router
from .routes.admin_users import router as admin_users_router
from .routes.admin_workers import router as admin_workers_router
from .routes.asset_apply_changes import admin_router as admin_asset_apply_changes_router
from .routes.asset_exports import router as asset_exports_router
from .routes.asset_schedules import admin_router as admin_asset_schedules_router
from .routes.asset_schedules import router as asset_schedules_router
from .routes.asset_sweeps import admin_router as admin_asset_sweeps_router
from .routes.assets import router as assets_router
from .routes.attribute_search import router as attribute_search_router
from .routes.audit_local import router as audit_local_router
from .routes.clash_check import router as clash_check_router
from .routes.clash_contacts import router as clash_contacts_router
from .routes.components import router as components_router
from .routes.convert import router as convert_router
from .routes.deps import (  # noqa: F401 — _merge_spec re-exported for tests/importers of the old name
    CAPABILITY_REQUIREMENTS_SETTING,
    RestContext,
    _merge_spec,
    publish_capability_requirements,
    refresh_worker_registry,
)
from .routes.element_links import router as element_links_router
from .routes.equipment_types import router as equipment_types_router
from .routes.export_selection import router as export_selection_router
from .routes.fea import router as fea_router
from .routes.job_storage import router as job_storage_router
from .routes.local_jobs import apply_launch_limits
from .routes.local_jobs import router as local_jobs_router
from .routes.plugin_jobs import router as plugin_jobs_router
from .routes.plugins import router as plugins_router
from .routes.preferences import router as preferences_router
from .routes.procedural_engines import router as procedural_engines_router
from .routes.procedural_models import router as procedural_models_router
from .routes.projects import router as projects_router
from .routes.public_settings import router as public_settings_router
from .routes.rpc import router as rpc_router
from .routes.runtime_config import router as runtime_config_router
from .routes.saved_groups import router as saved_groups_router
from .routes.simulations import router as simulations_router
from .routes.source_nodes import router as source_nodes_router
from .routes.storage import router as storage_router
from .routes.system_templates import router as system_templates_router
from .routes.viewer_control import router as viewer_control_router
from .routes.viewer_control import ws_router as viewer_control_ws_router
from .routes.worker_bootstrap import router as worker_bootstrap_router
from .storage import Storage

#: How long a job -> worker binding is kept. Longer than any job runs; a binding outliving its job
#: holds nothing (a finished job gets no grant), it only keeps the audit trail's answer.
_GRANT_BINDING_TTL_S = 7 * 86400


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
        load_local_plugins("api")

        # Connect to NATS lazily; a missing URL just disables the queue.
        if queue.enabled:
            try:
                # manage=True: the API owns the JetStream topology
                # (stream + KV bucket) and brings it forward on deploy.
                # Workers connect with manage=False so their credentials
                # need no stream-admin rights.
                await queue.connect(manage=True, name="adapy-viewer-api")
                logger.info("queue connected to %s", settings.queue.url)
                # One long-lived watcher per bucket instead of a scan per
                # question — routing alone used to scan once per enqueued job.
                await queue.start_mirrors()
            except Exception as exc:
                logger.warning("queue connect failed (%s); convert endpoints will return 503", exc)
        # Job grants for remote workers (job_grants.py): the responder joins a queue group on this
        # connection and the bindings get a bucket of their own. Only with a secret AND a queue --
        # a remote worker reaches the API's grants through the bus or not at all.
        app.state.job_grants = None
        if settings.worker_grants.enabled:
            if not queue.enabled:
                logger.warning("job grants: ADA_VIEWER_WORKER_GRANT_SECRET is set but there is no queue; not answering")
            else:
                try:
                    kv = await queue.ensure_bucket(settings.worker_grants.kv_bucket, ttl_s=_GRANT_BINDING_TTL_S)
                    grant_service.bindings = KvGrantBindings(kv)
                    await grant_service.start(queue)
                    app.state.job_grants = grant_service
                except Exception:
                    logger.exception("job grants: could not start the responder; remote workers get no grants")
        # Postgres pool — ``None`` when DATABASE_URL is empty. We don't
        # let DB connect failures abort startup: the API can still
        # serve the shared bucket, and a failed pool surfaces as 503s
        # on the multi-tenant endpoints rather than a crash loop.
        try:
            app.state.db_pool = await db_module.init_pool(settings.database_url)
        except Exception:
            logger.exception("db: pool init failed; running shared-only")
            app.state.db_pool = None

        # A queue-less server runs its jobs itself, through one local queue: its limits come from
        # the settings store, over the environment (routes/local_jobs.py).
        if not queue.enabled:
            await apply_launch_limits(storage, app.state.db_pool)

        # Grants are the only scope -> provider-collection control: once, the per-scope collection
        # matrix and the attribute shares become grants (`provider_grant_migration`). A failure is
        # logged and retried on the next start; the API serves meanwhile.
        if app.state.db_pool is not None:
            from .provider_grant_migration import migrate_legacy_access

            try:
                await migrate_legacy_access(app.state.db_pool)
            except Exception:
                logger.exception("provider grants: could not migrate the legacy access rules; retried on next start")

        # Reconcile the capability requirements from the database into KV. The
        # admin write publishes directly, but best-effort; doing it again here
        # means a restart repairs a publish that failed, rather than leaving
        # workers gating against a document older than the one an admin sees.
        if app.state.db_pool is not None and queue.enabled:
            try:
                await publish_capability_requirements(
                    queue, await db_module.get_setting(app.state.db_pool, CAPABILITY_REQUIREMENTS_SETTING)
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
                background_tasks.scheduler_loop(rest_ctx, app.state.db_pool),
                name="audit-scheduler",
            )
        # Plugin-job scheduler. Same conditions as the audit scheduler above and
        # for the same reason: without a pool there are no schedules to read, and
        # without a queue there is nothing to enqueue onto.
        app.state.plugin_scheduler_task = None
        if app.state.db_pool is not None and queue.enabled:
            app.state.plugin_scheduler_task = asyncio.create_task(
                background_tasks.plugin_schedule_loop(rest_ctx, app.state.db_pool),
                name="plugin-job-scheduler",
            )
        # Issue-bot poller (M5). Only needs the DB pool — the bot
        # talks to an HTTP forge, not NATS, so a queue-less deploy
        # can still publish failure issues. Skipped without a pool.
        app.state.issue_bot_task = None
        if app.state.db_pool is not None:
            app.state.issue_bot_task = asyncio.create_task(
                background_tasks.issue_bot_loop(rest_ctx, app.state.db_pool),
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
                background_tasks.profile_parser_loop(storage, app.state.db_pool),
                name="audit-profile-parser",
            )
        # Database retention (Admin -> Database): once a day, prune each area an admin gave a
        # retention. Idle -- one settings read an hour -- until one is set.
        app.state.db_retention_task = None
        if app.state.db_pool is not None:
            app.state.db_retention_task = asyncio.create_task(
                db_retention_loop(app.state.db_pool),
                name="db-retention",
            )
        # Stale-worker GC. A pod that crashes / scales down can leave its registry entry behind
        # (unregister_worker is best-effort); without pruning these accumulate and pollute the
        # capability matrix. Drop entries unseen for WORKER_PRUNE_AFTER_S (2 days), checked hourly.
        app.state.worker_prune_task = None
        if queue.enabled:
            app.state.worker_prune_task = asyncio.create_task(
                background_tasks.worker_prune_loop(queue),
                name="worker-prune",
            )
        # Worker-registry snapshot refresher. Prime it once now (a single
        # ~1s NATS scan at boot) so the very first /config.js is warm, then
        # keep it fresh in the background — the config endpoints read the
        # cached snapshot instead of hitting NATS on the request path.
        app.state.worker_registry_task = None
        if queue.enabled:
            await refresh_worker_registry(queue, _worker_registry)
            app.state.worker_registry_task = asyncio.create_task(
                background_tasks.worker_registry_refresh_loop(queue, _worker_registry),
                name="worker-registry-refresh",
            )
        # Completed-job KV cleanup. Keeps the shared bucket lean (the KV is a
        # transient progress cache; durable history lives in Postgres/S3) so
        # keys() scans stay cheap and never storm NATS again.
        app.state.job_cleanup_task = None
        if queue.enabled:
            app.state.job_cleanup_task = asyncio.create_task(
                background_tasks.job_cleanup_loop(queue),
                name="job-kv-cleanup",
            )
        # Element links of simulation runs still queued or running follow their runs (assoc_links):
        # a run nobody looks at still ends in its links. With or without a database.
        from .assoc_links import sweeper_loop

        app.state.assoc_sweeper_task = asyncio.create_task(
            sweeper_loop(storage, rest_ctx.jobs, app.state.db_pool),
            name="assoc-sweeper",
        )
        # `ada dev connections`: the library's host process and file watcher (dev_connections.py).
        if app.state.dev_connections is not None:
            app.state.dev_connections.start()
        yield
        if app.state.dev_connections is not None:
            app.state.dev_connections.stop()
        # Cancel scheduler + issue bot first so a tick in flight
        # doesn't try to use a pool / queue that's about to be torn
        # down.
        for attr in (
            "scheduler_task",
            "plugin_scheduler_task",
            "issue_bot_task",
            "profile_parser_task",
            "db_retention_task",
            "worker_prune_task",
            "worker_registry_task",
            "job_cleanup_task",
            "assoc_sweeper_task",
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
        if app.state.job_grants is not None:
            await app.state.job_grants.stop()
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

    @app.middleware("http")
    async def _reset_request_user_middleware(request, call_next):
        token = auth_module.set_request_user(None)
        try:
            return await call_next(request)
        finally:
            auth_module.reset_request_user(token)

    # A connection library served live (`ada dev connections`), when ADA_DEV_CONNECTIONS names one.
    # Built here, so a bad ada_connections.toml fails the start with its own message.
    from .dev_connections import DevConnections
    from .dev_connections import configured_path as _dev_connections_path

    _dev_path = _dev_connections_path()
    app.state.dev_connections = DevConnections(_dev_path) if _dev_path else None
    # Runs handed off to a remote Ada Studio (simulation_relay.py), when the settings name one.
    from .simulation_relay import SimulationRelay

    app.state.simulation_relay = (
        SimulationRelay(settings.simulation_remote) if settings.simulation_remote is not None else None
    )
    # Cached worker-registry snapshot, refreshed off the request path by
    # ``background_tasks.worker_registry_refresh_loop``. ``queue.list_workers()`` is an
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
    # How this deployment runs jobs. Decided ONCE, here, so no request has to
    # ask `queue.enabled` and then write its own answer for "and if not?" —
    # see ada.comms.rest.job_transport.
    jobs = build_transport(
        queue,
        storage,
        _worker_registry,
        pool=lambda: getattr(app.state, "db_pool", None),
    )
    rest_ctx = RestContext(
        settings=settings,
        storage=storage,
        queue=queue,
        jobs=jobs,
        worker_registry=_worker_registry,
        compression_state=compression_state,
    )
    app.state.rest = rest_ctx
    # Built here, started in the lifespan once the queue is up (it needs the bus for its bindings).
    grant_service = GrantService(
        settings.worker_grants,
        storage,
        queue.get,
        MemoryGrantBindings(),
        pool=lambda: getattr(app.state, "db_pool", None),
    )
    app.state.job_grants = None

    @app.get("/healthz")
    async def healthz() -> Response:
        # Public — load balancers + readiness probes hit this.
        return Response(status_code=200)

    # /api/config is *almost* public (the SPA fetches it before it has
    # a token, to learn whether auth is enabled and what the issuer is)
    # — but it never leaks user data, so we serve it unauthenticated.
    # ``POST /api/local/shutdown`` — only on a launcher's local viewer. See
    # local_shutdown.py for why a deployment can never have it.
    local_shutdown.install(app, settings)

    # /api/config and /config.js (routes/runtime_config.py): public, on the app itself.
    app.include_router(runtime_config_router)
    # /api/viewer/ws (routes/viewer_control.py): a viewer tab's websocket. On the app itself, as a
    # websocket handshake carries no bearer header: a ticket from the authenticated
    # POST /api/viewer/ws-ticket is its credential.
    app.include_router(viewer_control_ws_router)
    # GET /api/workers/bootstrap (routes/worker_bootstrap.py): public, non-secret settings a remote
    # worker joins with.
    app.include_router(worker_bootstrap_router)
    # /api/jobs/{job_id}/storage/... (routes/job_storage.py): a remote worker's job-scoped storage.
    # On the app itself, NOT under the user-auth router: its only credential is a job grant, and
    # a user's token is refused there as firmly as a grant is refused everywhere else.
    app.include_router(job_storage_router)

    # Every /api/* below this line requires a verified user. The dep is
    # attached to the router so individual routes don't have to repeat
    # `Depends(current_user)`. When auth is disabled the dep returns the
    # synthetic local-dev user, so dev / desktop paths see no behavior
    # change beyond an extra (free) function call per request.
    api = APIRouter(prefix="/api", dependencies=[Depends(auth_module.current_user)])

    # The FlatBuffer RPC envelope (routes/rpc.py).
    api.include_router(rpc_router)

    api.include_router(projects_router)
    api.include_router(preferences_router)
    api.include_router(local_jobs_router)

    api.include_router(storage_router)

    # In-browser (WASM) conversion audit rows and viewer load/render samples (routes/audit_local.py).
    api.include_router(audit_local_router)

    api.include_router(fea_router)

    # Conversion / utility jobs and the shared job-status poll (routes/convert.py).
    api.include_router(convert_router)
    # The connection-component panel (routes/components.py).
    api.include_router(components_router)
    # The `public.*` settings any user may read (routes/public_settings.py).
    api.include_router(public_settings_router)

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
    # Change checks a provider declares (`asset_schedules`): run now, list runs, read items.
    api.include_router(asset_schedules_router)
    # Attribute search over the index the publish job ingests (routes/attribute_search.py). Fixed
    # `assets/search...` paths, registered before the asset store's routes all the same.
    api.include_router(attribute_search_router)
    # What a collection offers for a new element to point at (routes/reference_data.py). Fixed
    # `assets/reference...` paths, before the asset store's routes for the same reason.
    from .routes.reference_data import router as reference_data_router

    api.include_router(reference_data_router)
    # Search hits exported as IFC into a chosen folder (routes/asset_exports.py), and per-file
    # provenance (`/scopes/{scope}/files/meta`). Fixed paths, no parameterised sibling to collide with.
    api.include_router(asset_exports_router)
    # What a scope may read of the provider store (routes/provider_grants.py): the member-facing
    # `/scopes/{scope}/provider-grants` and the workers' `/provider-grants/datasets`. Fixed paths.
    from .routes.provider_grants import router as provider_grants_router

    api.include_router(provider_grants_router)
    # Which scope-gated plugins are on for the caller in a scope (routes/plugin_access.py). Fixed path.
    from .routes.plugin_access import router as plugin_access_router

    api.include_router(plugin_access_router)

    # Core asset store (routes/assets.py). Every path is
    # ``/scopes/{scope}/assets/...`` with a fixed first segment after
    # ``assets``, so there is no fixed/parameterised sibling to collide with.
    api.include_router(assets_router)

    # Clashes: joint identification + the hand-off to a generator (routes/clash_check.py,
    # Decision 10 / Phase 6). Every path is ``/scopes/{scope}/clash-check...`` or
    # ``/scopes/{scope}/clash-detail``, neither colliding with any fixed/parameterised sibling.
    api.include_router(clash_check_router)
    # Convex-contact clash runs on the viewer GLB: ``/scopes/{scope}/clash-contacts...``.
    api.include_router(clash_contacts_router)
    # Driving the caller's own open viewer (routes/viewer_control.py): fixed ``/viewer/...`` paths.
    api.include_router(viewer_control_router)
    # The scope's saved groups in brief (routes/saved_groups.py): ``/scopes/{scope}/saved-groups``.
    api.include_router(saved_groups_router)

    # Simulation providers and runs (routes/simulations.py): ``/simulation/providers`` and
    # ``/scopes/{scope}/simulations...``, neither colliding with a fixed/parameterised sibling.
    api.include_router(simulations_router)
    # Per-element associations: links under a node, subjects and batches of runs (routes/element_links.py).
    api.include_router(element_links_router)

    # A selection as a STEP/IFC download (routes/export_selection.py). One fixed path,
    # ``/scopes/{scope}/export-selection``, with no parameterised sibling to collide with.
    api.include_router(export_selection_router)

    # `ada dev connections` (routes/dev_connections.py): fixed `/dev/connections...` paths, mounted
    # only while a library is being served.
    if app.state.dev_connections is not None:
        from .routes.dev_connections import router as dev_connections_router

        api.include_router(dev_connections_router)

    # The connection builder on a deployed studio (routes/connections.py): the specs the live
    # workers' code registers, built on their pools. ``/scopes/{scope}/connections...`` has no
    # parameterised sibling to collide with.
    from .routes.connections import router as connections_router

    api.include_router(connections_router)

    # Per-scope catalogs the cellbuilder places by slug, and the procedural-engine registry
    # (routes/equipment_types.py, routes/system_templates.py, routes/procedural_engines.py).
    api.include_router(equipment_types_router)
    api.include_router(system_templates_router)
    api.include_router(procedural_engines_router)

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

    admin.include_router(admin_settings_router)
    admin.include_router(admin_storage_compression_router)
    admin.include_router(admin_db_router)
    admin.include_router(admin_provider_grants_router)
    # Scope-gated plugins turned on per scope or per user (routes/plugin_access.py, Admin > Plugins).
    from .routes.plugin_access import admin_router as admin_plugin_access_router

    admin.include_router(admin_plugin_access_router)
    # The provider store's robot assets as .adabots bundles (routes/admin_robot_assets.py).
    from .routes.admin_robot_assets import router as admin_robot_assets_router

    admin.include_router(admin_robot_assets_router)
    # Material overrides of the reference data (routes/reference_data.py).
    from .routes.reference_data import admin_router as admin_reference_router

    admin.include_router(admin_reference_router)

    admin.include_router(admin_workers_router)

    admin.include_router(admin_plugin_jobs_router)
    admin.include_router(admin_asset_schedules_router)
    admin.include_router(admin_asset_sweeps_router)
    admin.include_router(admin_asset_apply_changes_router)
    # Two providers' tessellations compared: the gallery and its pictures (routes/geometry_compare.py).
    from .routes.geometry_compare import admin_router as admin_geometry_compare_router

    admin.include_router(admin_geometry_compare_router)

    # Audit runs: registered before the parameterised ``/audit/{audit_id}`` routes
    # (routes/admin_audit_perf.py) so the literal ``runs`` segment is not matched
    # against the int route (a 422 if the parameterised route won the match).
    admin.include_router(admin_audit_runs_router)

    admin.include_router(admin_corpora_router)

    admin.include_router(admin_audit_schedules_router)

    admin.include_router(admin_audit_perf_router)

    admin.include_router(admin_projects_router)

    admin.include_router(admin_users_router)

    admin.include_router(admin_storage_router)

    app.include_router(admin)

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
    3. A missing static asset (``assets/``, ``wasm/``, a script, style, wasm or
       source map) → 404. Answering it with the SPA shell was how a tab opened
       before a deploy broke: its lazy chunks are gone after the deploy, the
       shell came back as ``text/html``, and the browser refused it as a module
       ("Failed to load module script").
    4. Anything else → return ``index.html`` and let the SPA router
       consume the URL.

    Caching: ``index.html`` is ``no-cache`` (always revalidated, so a deploy is
    picked up), and the content-hashed files under ``assets/`` are immutable.

    Must register *after* every explicit route the API exposes; the
    path-converter pattern matches anything that didn't already match.
    """
    static_root = static_dir.resolve()
    asset_suffixes = {".js", ".mjs", ".css", ".wasm", ".map"}

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
            headers={"Document-Policy": "js-profiling", "Cache-Control": "no-cache"},
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
                if full_path.startswith("assets/"):
                    # Vite names these by content hash: a name never changes meaning.
                    return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})
                return FileResponse(candidate)
            if full_path.startswith(("assets/", "wasm/")) or pathlib.PurePosixPath(full_path).suffix in asset_suffixes:
                raise HTTPException(status_code=404)
        return _index_response()


app = create_app()
