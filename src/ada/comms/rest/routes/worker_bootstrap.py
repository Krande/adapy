"""``GET /api/workers/bootstrap``: what a remote worker needs to know to join, given only the URL.

Public, and non-secret by construction: the NATS URL a worker connects to, how it asks for job
grants (subject prefix, the private inbox rule, the grant lifetime, upload part size and limit),
the oldest ada-py the deployment accepts, and a per-capability block of the deployment's own
settings, served as configured and never interpreted here. A worker's setup reads this from the
host URL instead of being handed a file of settings; the credential it still needs (its NATS key)
is the one thing it creates itself.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ada.config import logger

router = APIRouter()

#: The app_settings key a deployment may keep the capability block in instead of the environment.
BOOTSTRAP_EXTRA_SETTING = "worker_bootstrap_extra"
BOOTSTRAP_SCHEMA = "ada.viewer/worker-bootstrap@1"


def _version() -> str | None:
    try:
        from importlib.metadata import version

        return version("ada-py")
    except Exception:  # noqa: BLE001 - not installed as a distribution (a source checkout)
        return None


async def _capabilities(request: Request, configured: dict) -> dict:
    """The capability block: the environment's when set, else the app_settings key's."""
    if configured:
        return configured
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return {}
    from .. import db as db_module

    try:
        raw = await db_module.get_setting(pool, BOOTSTRAP_EXTRA_SETTING)
        value = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - a broken setting serves nothing, not a 500
        logger.warning("worker bootstrap: app setting %s is not a JSON object; not served", BOOTSTRAP_EXTRA_SETTING)
        return {}
    return value if isinstance(value, dict) else {}


def bootstrap_document(settings, capabilities: dict) -> dict:
    cfg = settings.worker_grants
    return {
        "schema": BOOTSTRAP_SCHEMA,
        "api_version": _version(),
        "nats": {"url": cfg.nats_public_url or None},
        "grants": {
            "enabled": cfg.enabled,
            "subject_prefix": cfg.subject_prefix,
            "subject": f"{cfg.subject_prefix}.<capability>.<worker name>",
            "inbox_prefix": "_INBOX_<worker name>",
            "ttl_s": cfg.ttl_s,
            "refresh_after_s": int(cfg.ttl_s * 2 / 3),
            "probe_job_id": "probe",
            "storage_path": "/api/jobs/<job id>/storage",
            "small_object_max": cfg.small_object_max,
            "multipart": {"part_size": cfg.part_size, "max_size": cfg.max_size},
        },
        "worker": {"min_ada_py": cfg.min_ada_py or None},
        "capabilities": capabilities,
    }


@router.get("/api/workers/bootstrap")
async def worker_bootstrap(request: Request) -> JSONResponse:
    settings = request.app.state.rest.settings
    capabilities = await _capabilities(request, settings.worker_grants.bootstrap_extra)
    return JSONResponse(bootstrap_document(settings, capabilities), headers={"Cache-Control": "no-cache"})
