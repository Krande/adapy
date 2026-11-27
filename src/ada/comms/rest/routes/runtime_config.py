"""The runtime configuration the SPA reads before it has a token:
``GET /api/config`` (JSON) and ``GET /config.js`` (the ``window.*`` globals shim).

Both are public — they carry no user data — so ``create_app`` includes this router
on the app itself, outside the authenticated ``/api`` router. Both read the cached
worker-registry snapshot on :class:`~.deps.RestContext`, never NATS, so page load
never waits on the queue. Extracted from ``create_app``; see ``routes/__init__``.
"""

from __future__ import annotations

import os
import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import local_shutdown
from ..converter import LEGACY_CONVERT_EXTS
from .deps import (
    RestContext,
    convert_enabled,
    rest_context,
    worker_advertised_conversions,
    worker_advertised_exts,
    worker_advertised_utilities,
)

router = APIRouter()

_ADAPY_VERSION: str | None = None


def resolve_adapy_version() -> str:
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
            # ada._version, not ada.__version__: the viewer's stripped ada/__init__ doesn't re-export it.
            from ada._version import __version__ as v

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


@router.get("/api/config")
async def api_config(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    settings, queue = ctx.settings, ctx.queue
    # Image tags. Viewer's tag is baked in at image-build time
    # (deploy/Dockerfile.viewer ARG IMAGE_TAG) and read from env.
    # Worker's tag comes from the shared NATS KV — the worker
    # publishes its tag on startup. Either may be missing in dev /
    # local runs; the SPA hides the row when both are empty.
    viewer_tag = os.environ.get("ADA_IMAGE_TAG", "").strip() or None
    # Cached snapshot — no NATS on the request path (see RestContext.worker_registry).
    worker_tag: str | None = ctx.worker_registry["image_tag"] if queue.enabled else None
    extra_source_exts = await worker_advertised_exts(queue, ctx.worker_registry)
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
    conversion_matrix = await worker_advertised_conversions(queue, ctx.worker_registry)
    # Worker-advertised utilities (Utilities panel in the scene component).
    utilities = await worker_advertised_utilities(queue, ctx.worker_registry)
    return JSONResponse(
        {
            "transport": "rest",
            "apiBase": "/api",
            "convertEnabled": convert_enabled(queue, ctx.jobs, conversion_matrix),
            "localJobs": ctx.jobs.kind == "local",
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
            # Whether this page may stop the local viewer it is served by,
            # and the token that does it. JSON read by fetch, so another
            # origin cannot read it; never in /config.js. See local_shutdown.
            "localShutdown": local_shutdown.config_entry(settings, request),
            # Whether an agent may drive a viewer over /api/viewer (ADA_VIEWER_CONTROL): "off" (the default),
            # "opt-in" (each tab turns it on) or "auto" (every tab takes part unless switched off).
            "viewerControl": settings.viewer_control,
        }
    )


@router.get("/config.js")
async def config_js(request: Request, ctx: RestContext = Depends(rest_context)) -> PlainTextResponse:
    # Tiny JS shim the SPA loads before its main bundle. Sets the
    # window globals that comms/index.ts inspects to pick the
    # transport and bootstrap auth. Generated dynamically so a
    # single image targets multiple deployments. Strings are JSON-
    # encoded so any embedded quotes can't break out of the literal.
    import json as _json

    settings, queue, storage, app = ctx.settings, ctx.queue, ctx.storage, request.app

    viewer_tag = os.environ.get("ADA_IMAGE_TAG", "").strip() or None
    # Cached snapshot — no NATS on the request path (see RestContext.worker_registry).
    worker_tag: str | None = ctx.worker_registry["image_tag"] if queue.enabled else None
    extra_source_exts = await worker_advertised_exts(queue, ctx.worker_registry)
    streaming_only_exts = sorted(e for e in extra_source_exts if e not in LEGACY_CONVERT_EXTS)
    conversion_matrix = await worker_advertised_conversions(queue, ctx.worker_registry)

    adapy_version = resolve_adapy_version()

    a = settings.auth
    body = (
        'window.COMMS_MODE = "rest";\n'
        'window.API_BASE = "/api";\n'
        f"window.CONVERT_ENABLED = {'true' if convert_enabled(queue, ctx.jobs, conversion_matrix) else 'false'};\n"
        # This server runs its jobs itself, through a queue whose limits the user sets
        # (Settings > Local jobs; GET/PUT /api/local/jobs).
        f"window.LOCAL_JOBS = {'true' if ctx.jobs.kind == 'local' else 'false'};\n"
        f"window.AUTH_ENABLED = {'true' if a.enabled else 'false'};\n"
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
    # The capability only, never the token: any page may include this
    # script. Emitted only when true, so a deployment's config.js is as
    # before.
    if local_shutdown.enabled(settings):
        body += "window.ADA_LOCAL_SHUTDOWN = true;\n"
    # `ada dev connections`: the studio opens its connection builder. Emitted only when true.
    # Viewer control (ADA_VIEWER_CONTROL): "opt-in" or "auto", the page's "External controls" switch off or
    # on by default. Emitted only when enabled.
    if settings.viewer_control_enabled:
        body += f"window.ADA_VIEWER_CONTROL = {_json.dumps(settings.viewer_control)};\n"
    if app.state.dev_connections is not None:
        body += "window.ADA_DEV_CONNECTIONS = true;\n"
    # What a local (desktop) viewer lacks, so the page does not ask for it: no database (the
    # admin/audit routes 503) and a local store (no presigned URLs). Emitted only when lacking.
    if getattr(app.state, "db_pool", None) is None:
        body += "window.ADA_NO_DATABASE = true;\n"
    if not storage.supports_presigned_uploads:
        body += "window.ADA_NO_PRESIGNED_URLS = true;\n"
    # Where the in-browser Python engine loads Pyodide from, for a deployment that
    # serves it itself (an offline site, a mirror). Emitted only when set: absent,
    # the page uses a runtime staged next to its wheels, else the public CDN.
    pyodide_index_url = os.environ.get("ADA_VIEWER_PYODIDE_INDEX_URL", "").strip()
    if pyodide_index_url:
        body += f"window.PYODIDE_INDEX_URL = {_json.dumps(pyodide_index_url)};\n"

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
