"""``/api/scopes/{scope}/connections/*``: the connection builder on a deployed studio.

The same three answers ``ada dev connections`` gives the studio (:mod:`.dev_connections`) -- the
specs, a build of one from the builder's inputs, and a detail offered as a simulation subject --
but read from the CODE the live workers run rather than from a library held in-process:

* the specs are what the workers advertise on their heartbeat (``connection_specs``, see
  ``worker/registration.py``): every ``ConnectionSpec`` their installed packages registered, with
  its form schema, its defaults and the capability (worker pool) that builds it. No manifest, no
  bake: a spec is offered while a pool that has it is online, and a change to a spec is offered
  once the workers run the new code.
* a build is a ``component_build`` job in report mode, routed to the spec's capability. The route
  waits for it and answers in the dev route's shape, so the studio's connection builder reads one
  shape from both.

Which scopes offer which specs is the admin's: ``public.connection_scopes`` maps a capability to the
scopes (their wire form, ``Scope.wire()``; ``user`` for every personal scope) that offer the specs
it builds. A capability the setting does not name is offered everywhere; core's built-ins
(capability ``None``) always are.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()

#: The admin's map ``{capability: [scope wire, ...]}``; readable by every signed-in user (``public.``).
SCOPES_SETTING = "public.connection_scopes"
#: The scope a build lands in, as the studio names it: the caller's own, as for ``ada dev connections``.
BUILD_SCOPE = "user:me"
#: How long a build may take before the route gives up waiting (the job keeps running).
BUILD_TIMEOUT_S = 300.0
POLL_INTERVAL_S = 0.3
#: The library the studio is told it is looking at.
LIBRARY = {"name": "Connection specs", "config": "", "root": "", "watch": []}

REPORT_SUFFIX = ".report.json"
ERROR_SUFFIX = ".error.json"


def scope_offers(setting: Any, scope: Scope, capability: str | None) -> bool:
    """Whether ``scope`` offers the specs ``capability`` builds, by the admin's setting."""
    if capability is None or not isinstance(setting, dict):
        return True
    scopes = setting.get(capability)
    if not isinstance(scopes, list):
        return True
    if scope.kind == "user":
        # A personal scope has no wire form another caller could name; ``user`` stands for them all.
        return "user" in scopes
    return scope.wire() in scopes


async def _setting(request: Request) -> Any:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return None
    value = await db_module.get_setting(pool, SCOPES_SETTING)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value


def _entry(spec: dict) -> dict:
    """An advertised spec in the shape the studio's builder reads (``DevConnectionSpec``)."""
    schema = dict(spec.get("roles") or {})
    defaults = spec.get("defaults") or schema.get("defaults") or {}
    schema["defaults"] = defaults
    return {
        "name": spec.get("name") or spec.get("slug"),
        "schema": schema,
        "defaults": defaults,
        "module": spec.get("module"),
        "source": None,
        "in_library": False,
        "capability": spec.get("capability"),
        "unavailable": bool(spec.get("unavailable")),
    }


async def _offered(request: Request, ctx: RestContext, scope: Scope) -> list[dict]:
    setting = await _setting(request)
    advertised = await ctx.jobs.advertised_specs("connection_specs")
    out = [
        _entry(s)
        for s in advertised.values()
        if isinstance(s, dict) and scope_offers(setting, scope, s.get("capability"))
    ]
    return sorted(out, key=lambda e: str(e["name"]))


def _generation(specs: list[dict]) -> int:
    """Changes whenever what is offered does: a new spec, a spec's new schema or defaults."""
    digest = hashlib.sha256(json.dumps(specs, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


@router.get("/scopes/{scope}/connections")
async def api_connections(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The specs this scope offers, in the dev library's state shape (``DevConnectionsState``)."""
    specs = await _offered(request, ctx, scope_obj)
    return JSONResponse(
        {
            "library": LIBRARY,
            "status": "ready",
            "generation": _generation(specs),
            "specs": specs,
            "error": None,
            "changed": [],
            "updated_at": time.time(),
        }
    )


async def _spec_for(request: Request, ctx: RestContext, scope: Scope, body: dict) -> tuple[str, dict, dict]:
    spec_name = body.get("spec_name")
    if not isinstance(spec_name, str) or not spec_name:
        raise HTTPException(status_code=400, detail="spec_name (str) is required")
    inputs = body.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise HTTPException(status_code=400, detail="inputs must be an object")
    entry = next((s for s in await _offered(request, ctx, scope) if s["name"] == spec_name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no online worker offers {spec_name!r} in this scope")
    if entry["unavailable"]:
        raise HTTPException(status_code=503, detail=f"the pool that builds {spec_name!r} is not taking jobs")
    return spec_name, inputs, entry


async def _run(ctx: RestContext, user: User, spec_name: str, inputs: dict, entry: dict, mode: str) -> tuple[Scope, str]:
    """Submit the build and wait for it. Returns the build's scope and derived key; raises the
    HTTPException the dev route would answer with when it fails."""
    scope = Scope.user(user.sub)
    digest = hashlib.sha256(json.dumps([spec_name, inputs, mode], sort_keys=True).encode("utf-8")).hexdigest()[:16]
    ext = ".glb" if mode == "report" else ".weld-assembly.json"
    derived_key = f"_connections/{spec_name}/{digest}{ext}"
    job = await ctx.jobs.submit(
        JobRequest(
            source_key=f"_synthetic/component_build/{spec_name}/{digest}",
            target_format="component_build",
            scope=scope,
            feature="component_build",
            conversion_options={
                "spec_name": spec_name,
                "inputs": inputs,
                "name": spec_name.replace(".", "_"),
                "mode": mode,
            },
            derived_key=derived_key,
            target_capability=entry.get("capability"),
            # A build is cheap and its inputs are its key: the same inputs are the same joint, but
            # the code behind the spec may have changed since, so never answer from an old one.
            force_rebuild=True,
        )
    )
    deadline = time.monotonic() + BUILD_TIMEOUT_S
    while True:
        snap = await ctx.jobs.status(job.job_id)
        status = snap.status if snap else None
        if status in ("done", "error"):
            break
        if time.monotonic() > deadline:
            raise HTTPException(status_code=504, detail=f"the build of {spec_name!r} did not finish in time")
        await asyncio.sleep(POLL_INTERVAL_S)
    if status == "error":
        failure = await _read_json(ctx, scope, derived_key + ERROR_SUFFIX) or {}
        error = failure.get("error") or (snap.payload.get("error") if snap else None) or "the build failed"
        if mode == "subject" and "the detail has no welds" in error:
            raise HTTPException(status_code=409, detail="the detail has no welds")
        raise _BuildFailed(error, failure.get("traceback"), bool(failure.get("declined")))
    return scope, derived_key


class _BuildFailed(Exception):
    def __init__(self, error: str, traceback: str | None, declined: bool):
        super().__init__(error)
        self.error, self.traceback, self.declined = error, traceback, declined


async def _read_json(ctx: RestContext, scope: Scope, key: str) -> dict | None:
    try:
        raw = await ctx.storage.get_bytes(scope, key)
    except Exception:  # noqa: BLE001 - absent is an answer here
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None


def _failed(spec_name: str, e: _BuildFailed, generation: int) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "spec_name": spec_name,
            "error": e.error,
            "traceback": e.traceback,
            # The builder raised NotApplicable: it refused these members (not a crash).
            "declined": e.declined,
            "generation": generation,
        },
    )


@router.post("/scopes/{scope}/connections/build")
async def api_connections_build(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Build a spec from the builder's inputs on the worker pool that has it.

    ``{spec_name, inputs}`` → the dev build's answer: ``{scope, key, inputs, log, counts,
    properties, clash_run, clash_note, generation, build_ms}``; 422 ``{error, traceback, declined}``
    when the builder raises; 404 when no online worker offers the spec in this scope."""
    body = await request.json()
    spec_name, inputs, entry = await _spec_for(request, ctx, scope_obj, body)
    generation = _generation(await _offered(request, ctx, scope_obj))
    t0 = time.perf_counter()
    try:
        scope, key = await _run(ctx, user, spec_name, inputs, entry, "report")
    except _BuildFailed as e:
        return _failed(spec_name, e, generation)
    report = await _read_json(ctx, scope, key + REPORT_SUFFIX) or {}
    return JSONResponse(
        {
            "spec_name": spec_name,
            "scope": BUILD_SCOPE,
            "key": key,
            "inputs": report.get("inputs"),
            "log": report.get("log") or [],
            "counts": report.get("counts") or {},
            "properties": report.get("properties") or {"members": [], "welds": []},
            "clash_run": report.get("clash_run"),
            "clash_note": report.get("clash_note"),
            "generation": generation,
            "build_ms": round((time.perf_counter() - t0) * 1000),
        }
    )


@router.post("/scopes/{scope}/connections/subject")
async def api_connections_subject(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Offer the detail as a simulation subject: a weld assembly, built by the spec's pool and
    stored in the caller's scope. ``{spec_name, inputs}`` → ``{scope, key, kind, welds, label}``;
    409 when the detail has no welds; 422 (as a build) when the builder raises."""
    from ada.simulation.subjects import WELD_ASSEMBLY_KIND

    body = await request.json()
    spec_name, inputs, entry = await _spec_for(request, ctx, scope_obj, body)
    generation = _generation(await _offered(request, ctx, scope_obj))
    try:
        scope, key = await _run(ctx, user, spec_name, inputs, entry, "subject")
    except _BuildFailed as e:
        return _failed(spec_name, e, generation)
    report = await _read_json(ctx, scope, key + REPORT_SUFFIX) or {}
    welds = int(report.get("welds") or 0)
    logger.debug("connections: %s offered as a subject (%d welds)", spec_name, welds)
    return JSONResponse(
        {
            "scope": BUILD_SCOPE,
            "key": key,
            "kind": WELD_ASSEMBLY_KIND,
            "welds": welds,
            "label": f"{spec_name} ({welds} weld{'s' if welds != 1 else ''})",
            "generation": generation,
        }
    )
