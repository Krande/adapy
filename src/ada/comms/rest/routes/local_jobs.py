"""The local job queue's limits: ``GET /api/local/jobs`` and ``PUT /api/local/jobs/settings``.

Only on a queue-less server (``LocalJobTransport``): there every job runs in the server's own
environment, through :mod:`..local_queue`, and how many run at once and how much CPU they may take
is the user's call. Behind a queue the workers' own limits decide, and these routes 404.

WHERE THE LIMITS LIVE. The server's settings store: ``app_settings`` when there is a database,
else one hidden file in the shared scope (``_prefs/_server/local_jobs.json``, which the blob
routes never serve) -- the same fallback the preferences use, so a local viewer keeps its limits
across restarts. A value not set there falls back to the environment
(``ADA_LOCAL_JOB_PROCESSES``, ``ADA_LOCAL_JOB_CPU_BUDGET``), which is also what
``ada serve api`` / ``ada desktop`` set from their ``--max-jobs`` / ``--cpu-budget`` flags. A
limit given at launch wins for that launch and is saved (:func:`apply_launch_limits`), so the
setting shown is always the one in force.

A change applies to the next job that starts; running jobs keep what they were given.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from .. import local_queue
from ..auth import User
from ..converters.keys import PREFERENCES_PREFIX
from ..scope import Scope
from .deps import RestContext, rest_context

router = APIRouter()

SETTING_MAX_PARALLEL = "local_jobs.max_parallel"
SETTING_CPU_BUDGET = "local_jobs.cpu_budget"
#: The no-database store: hidden (``_prefs/``), and not a user's (theirs are 16-hex hashes).
SETTINGS_FILE_KEY = f"{PREFERENCES_PREFIX}_server/local_jobs.json"


async def read_stored(storage: Any, pool: Any) -> dict[str, Any]:
    """The stored raw values (``max_parallel``, ``cpu_budget``), absent where unset."""
    out: dict[str, Any] = {}
    if pool is not None:
        for field, key in (("max_parallel", SETTING_MAX_PARALLEL), ("cpu_budget", SETTING_CPU_BUDGET)):
            value = await db_module.get_setting(pool, key)
            if value not in (None, ""):
                out[field] = value
        return out
    try:
        doc = json.loads(await storage.get_bytes(Scope.shared(), SETTINGS_FILE_KEY))
    except Exception:
        return out
    if isinstance(doc, dict):
        out.update({k: doc[k] for k in ("max_parallel", "cpu_budget") if doc.get(k) not in (None, "")})
    return out


async def write_stored(storage: Any, pool: Any, values: dict[str, Any], *, updated_by: str | None) -> None:
    if pool is not None:
        for field, key in (("max_parallel", SETTING_MAX_PARALLEL), ("cpu_budget", SETTING_CPU_BUDGET)):
            value = values.get(field)
            await db_module.set_setting(pool, key, "" if value is None else str(value), updated_by=updated_by)
        return
    doc = {k: values.get(k) for k in ("max_parallel", "cpu_budget")}
    await storage.put_bytes(Scope.shared(), SETTINGS_FILE_KEY, json.dumps(doc).encode("utf-8"))


def _limits(values: dict[str, Any]) -> local_queue.LocalJobLimits:
    """Stored values over the environment's, field by field."""
    env = local_queue.env_limits()
    mp = values.get("max_parallel", env.max_parallel_raw)
    cb = values.get("cpu_budget", env.cpu_budget_raw)
    return local_queue.limits_from(mp, cb)


async def load_limits(storage: Any, pool: Any) -> None:
    """At start-up: put the stored limits (over the environment's) in force."""
    try:
        values = await read_stored(storage, pool)
        local_queue.queue.configure(_limits(values))
    except ValueError:
        logger.warning("local jobs: the stored limits are not valid; using the environment's", exc_info=True)
        local_queue.queue.configure(local_queue.env_limits())
    except Exception:
        logger.exception("local jobs: could not read the stored limits; using the environment's")
        local_queue.queue.configure(local_queue.env_limits())


async def apply_launch_limits(storage: Any, pool: Any) -> None:
    """At start-up: limits given at launch win, and are saved; otherwise the stored ones apply."""
    if not local_queue.env_overrides_present():
        await load_limits(storage, pool)
        return
    env = local_queue.env_limits()
    try:
        stored = await read_stored(storage, pool)
    except Exception:
        stored = {}
    if env.max_parallel_raw is not None:
        stored["max_parallel"] = env.max_parallel_raw
    if env.cpu_budget_raw is not None:
        stored["cpu_budget"] = env.cpu_budget_raw
    try:
        await write_stored(storage, pool, stored, updated_by="launch")
    except Exception:
        logger.exception("local jobs: could not save the launch limits (they apply to this run)")
    local_queue.queue.configure(_limits(stored))


def _require_local(ctx: RestContext) -> None:
    if ctx.jobs.kind != "local":
        raise HTTPException(status_code=404, detail="this server has a job queue: its workers' limits apply")


def _view() -> dict[str, Any]:
    limits = local_queue.queue.limits
    return {
        **limits.to_json(),
        "host_cores": local_queue.host_cores(),
        "default_max_parallel": local_queue.default_max_parallel(),
        **local_queue.queue.snapshot(),
    }


@router.get("/local/jobs")
async def api_local_jobs(ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """The limits in force, the machine's cores, and what is running and queued now."""
    _require_local(ctx)
    return JSONResponse(_view())


@router.put("/local/jobs/settings")
async def api_local_jobs_settings(
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.require_admin),
) -> JSONResponse:
    """Set ``max_parallel`` (1..256) and/or ``cpu_budget`` (cores ``4``, a fraction ``0.5`` or a
    percentage ``"50%"``); ``null`` returns a field to the environment's default. Applies to the
    next job that starts."""
    _require_local(ctx)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    pool = getattr(request.app.state, "db_pool", None)
    values = await read_stored(ctx.storage, pool)
    for field in ("max_parallel", "cpu_budget"):
        if field in body:
            if body[field] is None or body[field] == "":
                values.pop(field, None)
            else:
                values[field] = body[field]
    try:
        limits = _limits(values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored = {
        "max_parallel": limits.max_parallel_raw if "max_parallel" in values else None,
        "cpu_budget": limits.cpu_budget_raw if "cpu_budget" in values else None,
    }
    await write_stored(ctx.storage, pool, stored, updated_by=user.sub)
    local_queue.queue.configure(limits)
    return JSONResponse(_view())
