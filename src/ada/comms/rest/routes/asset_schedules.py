"""Provider-declared schedules and change checks -- the ``asset_schedules`` part of the asset-provider seam.

A provider declares on its plugin spec, next to ``asset_collection_request`` and friends, the
jobs it offers for scheduling::

    asset_schedules=[{
        "id": "changes", "kind": "change-check", "label": "...", "description": "...",
        "options": {"action": "..."},          # fixed job options
        "collection_option": "project",        # the option that receives the collection
        "settings": [{"name": "exclude_users", "choices": "change_users"}],
    }]

Two surfaces read it:

* **Admin → Providers → Scheduled jobs** (``admin_router``): job × scope × collection ×
  frequency × settings, every one a choice -- nothing here accepts free-text options. A row is
  stored in ``plugin_job_schedules`` (the scheduler that already runs those fires it) with the
  provider, collection and entry id beside it, so its options are rebuilt from the LIVE
  declaration at every firing rather than frozen at creation.
* **Sources tab → Check for changes** (``router``): runs the provider's ``change-check`` entry
  now.

Every change check, from either, gets an ``asset_change_runs`` row. Its job answers with the
provider-neutral ``asset_changes`` block (``ada.assets/changes@1``), which this module copies in
once the job is done. Report only: nothing is published as a consequence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import Scope
from .deps import (
    RestContext,
    SystemUser,
    next_fire,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
    scope_from_path,
)
from .plugin_jobs import enqueue_plugin_job, plugin_job_requires_admin

router = APIRouter()
admin_router = APIRouter()

CHANGE_CHECK = "change-check"
KINDS = (CHANGE_CHECK, "job")
#: A setting's ``choices`` value asking core to offer the users earlier change checks reported.
CHOICES_CHANGE_USERS = "change_users"
CHANGES_SCHEMA = "ada.assets/changes@1"
ITEMS_SCHEMA = "ada.assets/change-items@1"

#: The frequencies an admin picks from. UTC, like every schedule here. A preset rather than a
#: cron field: the point of this surface is that nothing on it is free text.
FREQUENCIES: dict[str, dict[str, str]] = {
    "hourly": {"label": "Every hour", "cron": "0 * * * *"},
    "every-4h": {"label": "Every 4 hours", "cron": "0 */4 * * *"},
    "daily": {"label": "Daily at 02:00 UTC", "cron": "0 2 * * *"},
    "weekdays": {"label": "Weekdays at 02:00 UTC", "cron": "0 2 * * 1-5"},
    "weekly": {"label": "Weekly, Monday 02:00 UTC", "cron": "0 2 * * 1"},
}


# --- the declaration ---------------------------------------------------------------------


def schedule_entries(spec: dict) -> list[dict]:
    """The spec's well-formed ``asset_schedules`` entries. A malformed one is dropped with a log
    line rather than failing every listing: one plugin's typo must not hide the others."""
    raw = spec.get("asset_schedules")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        kind = entry.get("kind") or "job"
        options = entry.get("options")
        if not sid or sid in seen or kind not in KINDS or not isinstance(options, dict):
            logger.warning("asset_schedules: ignoring a malformed entry on %s: %r", spec.get("slug"), entry)
            continue
        if kind == CHANGE_CHECK and not entry.get("collection_option"):
            logger.warning("asset_schedules: change-check %r on %s names no collection_option", sid, spec.get("slug"))
            continue
        seen.add(sid)
        out.append(entry)
    return out


def _plugin_id(spec: dict) -> str:
    return str(spec.get("slug") or spec.get("id") or "")


async def provider_specs(ctx: RestContext) -> dict[str, list[dict]]:
    """Live plugin specs that declare schedules, grouped by ``asset_provider_id``."""
    out: dict[str, list[dict]] = {}
    for spec in (await ctx.jobs.advertised_specs("plugin_specs")).values():
        provider = spec.get("asset_provider_id")
        if isinstance(provider, str) and provider and schedule_entries(spec):
            out.setdefault(provider, []).append(spec)
    return out


def find_entry(specs: list[dict], job: str | None = None, *, kind: str | None = None) -> tuple[dict, dict] | None:
    """``(spec, entry)`` for a provider's entry by id, or its entry of ``kind``. The first
    declaring spec wins, as for every other asset-provider key."""
    for spec in specs:
        for entry in schedule_entries(spec):
            if (job is not None and entry["id"] == job) or (kind is not None and entry.get("kind") == kind):
                return spec, entry
    return None


def collections_of(spec: dict) -> list[str]:
    field = spec.get("asset_collections_field")
    values = spec.get(field) if isinstance(field, str) else None
    return [str(v) for v in values] if isinstance(values, list) else []


def _declared_option(spec: dict, name: str) -> dict:
    for decl in spec.get("job_options") or ():
        if isinstance(decl, dict) and decl.get("name") == name:
            return decl
    return {}


def entry_settings(spec: dict, entry: dict) -> list[dict]:
    """The settings an admin may choose for ``entry``, each merged with the spec's ``job_options``
    declaration. ``source`` says where the choices come from: ``enum`` (declared), ``spec``
    (``enum_from``: a spec list) or ``change_users`` (earlier runs)."""
    out = []
    for raw in entry.get("settings") or ():
        item = {"name": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else None
        if not item or not item.get("name"):
            continue
        decl = _declared_option(spec, str(item["name"]))
        setting = {
            "name": str(item["name"]),
            "type": decl.get("type") or "enum",
            "title": item.get("title") or decl.get("title") or str(item["name"]),
            "description": item.get("description") or decl.get("description"),
            "default": decl.get("default"),
        }
        if item.get("choices") == CHOICES_CHANGE_USERS:
            setting["source"] = CHOICES_CHANGE_USERS
        elif isinstance(decl.get("enum"), list):
            setting["source"] = "enum"
            labels = decl.get("labels") if isinstance(decl.get("labels"), dict) else {}
            setting["choices"] = [{"value": v, "label": str(labels.get(str(v), v))} for v in decl["enum"]]
        elif isinstance(decl.get("enum_from"), str):
            setting["source"] = "spec"
            setting["choices"] = [{"value": v, "label": str(v)} for v in spec.get(decl["enum_from"]) or () if v]
        else:
            # No choices anywhere: not offered. This surface never falls back to free text.
            logger.info(
                "asset_schedules: setting %r on %s has no choices; not offered", setting["name"], spec.get("slug")
            )
            continue
        out.append(setting)
    return out


async def setting_choices(pool, setting: dict, *, scope: str, provider: str, collection: str) -> list[dict]:
    if setting.get("source") == CHOICES_CHANGE_USERS:
        users = await db_module.asset_change_users(pool, scope=scope, provider=provider, collection=collection)
        return [{"value": u, "label": u} for u in users]
    return list(setting.get("choices") or ())


async def build_options(
    pool,
    spec: dict,
    entry: dict,
    *,
    scope: str,
    provider: str,
    collection: str | None,
    settings: dict | None,
    validate: bool = True,
) -> dict:
    """The job options for one entry: its fixed options, the collection, and the chosen settings.
    Every chosen value must be one of its setting's choices -- checked here, server side, so a
    hand-made request cannot smuggle free text past the dropdowns."""
    options = dict(entry["options"])
    collection_option = entry.get("collection_option")
    if collection_option:
        if not collection:
            raise HTTPException(status_code=400, detail=f"{entry['id']!r} needs a collection")
        if validate:
            offered = collections_of(spec)
            if offered and collection not in offered:
                raise HTTPException(
                    status_code=400,
                    detail=f"{collection!r} is not a collection {provider!r} offers ({', '.join(offered[:20])})",
                )
        options[str(collection_option)] = collection
    declared = {s["name"]: s for s in entry_settings(spec, entry)}
    for name, value in (settings or {}).items():
        setting = declared.get(name)
        if setting is None:
            if validate:
                raise HTTPException(status_code=400, detail=f"{name!r} is not a setting of {entry['id']!r}")
            continue
        if value is None or value == [] or value == "":
            continue
        if validate:
            allowed = {
                str(c["value"])
                for c in await setting_choices(
                    pool, setting, scope=scope, provider=provider, collection=collection or ""
                )
            }
            values = value if isinstance(value, list) else [value]
            bad = [v for v in values if str(v) not in allowed]
            if bad:
                raise HTTPException(status_code=400, detail=f"{name}: {bad!r} not among its choices")
        options[name] = value
    return options


def settings_of(entry: dict, options: dict) -> dict:
    """The chosen settings, read back out of stored job options."""
    names = [s if isinstance(s, str) else (s or {}).get("name") for s in entry.get("settings") or ()]
    return {n: options[n] for n in names if n and n in options}


def job_keys(plugin_id: str, options: dict) -> tuple[str, str]:
    """``(derived_key, derived_prefix)`` exactly as ``enqueue_plugin_job`` would derive the key --
    computed here so the run knows where its summary lands and the provider gets a prefix to
    write its item list under."""
    opts_hash = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    prefix = f"_derived/plugin_jobs/{plugin_id}/{opts_hash}"
    return f"{prefix}.json", prefix


async def start_change_check(
    ctx: RestContext,
    pool,
    *,
    scope_obj: Scope,
    scope: str,
    provider: str,
    collection: str,
    spec: dict,
    entry: dict,
    options: dict,
    user,
    via: str,
    schedule_id: str | None = None,
    request: Request | None = None,
) -> dict:
    """Enqueue one change check and record its run. ``options`` are final (built and stamped)."""
    plugin_id = _plugin_id(spec)
    derived_key, derived_prefix = job_keys(plugin_id, options)
    job_id = await enqueue_plugin_job(
        ctx,
        plugin_id=plugin_id,
        options=options,
        scope_obj=scope_obj,
        user=user,
        pool=pool,
        request=request,
        derived_key=derived_key,
        derived_prefix=derived_prefix,
        plugin_spec=spec,
    )
    return await db_module.insert_asset_change_run(
        pool,
        scope=scope,
        provider=provider,
        collection=collection,
        plugin_id=plugin_id,
        job_id=job_id,
        derived_key=derived_key,
        requested_by=getattr(user, "sub", None),
        requested_via=via,
        schedule_id=schedule_id,
    )


def _block_of(summary: Any) -> dict | None:
    block = summary.get("asset_changes") if isinstance(summary, dict) else None
    return block if isinstance(block, dict) else None


async def finish_pending_runs(ctx: RestContext, pool, scope_obj: Scope, runs: list[dict]) -> list[dict]:
    """Copy the result of every finished job into its run. Best-effort per run: one unreadable
    summary must not fail the listing."""
    out = []
    for run in runs:
        if run["status"] != "queued" or not run.get("job_id"):
            out.append(run)
            continue
        try:
            snapshot = await ctx.jobs.status(run["job_id"])
            state = (snapshot.status if snapshot is not None else "").lower()
            finished = None
            if state == "done":
                try:
                    summary = json.loads(await ctx.storage.get_bytes(scope_obj, run["derived_key"]))
                except Exception as exc:  # noqa: BLE001 - reported on the run
                    finished = await db_module.finish_asset_change_run(
                        pool, run["id"], status="error", error=f"the job's summary could not be read: {exc}"
                    )
                else:
                    block = _block_of(summary)
                    if block is None:
                        finished = await db_module.finish_asset_change_run(
                            pool,
                            run["id"],
                            status="error",
                            error="the job answered without an asset_changes block -- the provider does not "
                            "implement change checks in this version",
                        )
                    else:
                        finished = await db_module.finish_asset_change_run(pool, run["id"], status="done", block=block)
            elif state in ("error", "cancelled"):
                detail = (snapshot.payload or {}).get("error") if snapshot is not None else None
                finished = await db_module.finish_asset_change_run(
                    pool, run["id"], status="error", error=str(detail or f"the job was {state}")
                )
            out.append(finished or run)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("asset change runs: could not finish run %s", run.get("id"))
            out.append(run)
    return out


async def scope_key(pool, scope: str, user=None) -> str:
    """The canonical key runs are stored under: the scope's storage prefix, as the change feed
    keys its rows. A schedule keeps the scope as the admin chose it (``project:<slug>`` survives a
    rename); the Sources tab asks by id -- both resolve to the same key."""
    resolved = await resolve_project_scope(pool, parse_scope(scope, user or SystemUser()))
    return resolved.prefix()


def _public_run(run: dict) -> dict:
    return {k: v for k, v in run.items() if k not in ("derived_key", "items_key", "items")}


async def _specs_or_404(ctx: RestContext, provider: str) -> list[dict]:
    specs = (await provider_specs(ctx)).get(provider)
    if not specs:
        raise HTTPException(
            status_code=404,
            detail=f"no online worker advertises schedules for provider {provider!r}",
        )
    return specs


async def _saved_settings(pool, *, key: str, provider: str, collection: str, entry: dict) -> dict:
    """A manual check uses the settings of this scope's scheduled check for the same collection,
    if there is one -- so excluding a batch account applies to both."""
    for row in await db_module.list_plugin_job_schedules(pool, asset_provider=provider):
        if row["collection"] != collection or row["schedule_job"] != entry["id"]:
            continue
        try:
            if await scope_key(pool, row["scope"]) == key:
                return settings_of(entry, row["options"])
        except HTTPException:
            continue
    return {}


# --- Sources tab: check now, list runs, read a run's items ---------------------------------


@router.post("/scopes/{scope}/asset-changes/check")
async def api_asset_changes_check(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Run a provider's change check for one collection now. Body: ``{provider, collection}``."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    collection = str(body.get("collection") or "").strip()
    if not provider or not collection:
        raise HTTPException(status_code=400, detail="provider and collection are required")
    found = find_entry(await _specs_or_404(ctx, provider), kind=CHANGE_CHECK)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no change check")
    spec, entry = found
    if not user.is_admin and await plugin_job_requires_admin(_plugin_id(spec), pool, spec):
        raise HTTPException(status_code=403, detail=f"{provider!r} change checks are restricted to admins")
    key = scope_obj.prefix()
    settings = await _saved_settings(pool, key=key, provider=provider, collection=collection, entry=entry)
    options = await build_options(
        pool, spec, entry, scope=key, provider=provider, collection=collection, settings=settings, validate=False
    )
    # Varies the options hash, like `refresh` on the read path: a check must never answer
    # from an earlier check's cached summary.
    options["requested_at"] = datetime.now(timezone.utc).isoformat()
    run = await start_change_check(
        ctx,
        pool,
        scope_obj=scope_obj,
        scope=key,
        provider=provider,
        collection=collection,
        spec=spec,
        entry=entry,
        options=options,
        user=user,
        via="user",
        request=request,
    )
    return JSONResponse(_public_run(run), status_code=202)


@router.get("/scopes/{scope}/asset-changes/runs")
async def api_asset_changes_runs(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    provider: str | None = None,
    collection: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    """This scope's change-check runs, newest first, finishing any whose job has completed."""
    pool = require_pool(request)
    runs = await db_module.list_asset_change_runs(
        pool,
        scope=scope_obj.prefix(),
        provider=provider or None,
        collection=collection or None,
        limit=max(1, min(int(limit), 200)),
    )
    runs = await finish_pending_runs(ctx, pool, scope_obj, runs)
    return JSONResponse({"runs": [_public_run(r) for r in runs]})


@router.get("/scopes/{scope}/asset-changes/runs/{run_id}/items")
async def api_asset_changes_items(
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run's changed nodes (``ada.assets/change-items@1``)."""
    pool = require_pool(request)
    run = await db_module.get_asset_change_run(pool, run_id)
    if run is None or run["scope"] != scope_obj.prefix():
        raise HTTPException(status_code=404, detail="run not found")
    [run] = await finish_pending_runs(ctx, pool, scope_obj, [run])
    items = run.get("items")
    if items is None and run.get("items_key"):
        try:
            doc = json.loads(await ctx.storage.get_bytes(scope_obj, run["items_key"]))
        except Exception as exc:  # noqa: BLE001 - the run outlived its item file
            raise HTTPException(status_code=410, detail=f"this run's item list is no longer stored: {exc}") from exc
        items = doc.get("items") if isinstance(doc, dict) else doc
    return JSONResponse(
        {"run": _public_run(run), "schema": ITEMS_SCHEMA, "items": items if isinstance(items, list) else []}
    )


# --- Admin → Providers → Scheduled jobs -------------------------------------------------------


@admin_router.get("/asset-schedules")
async def admin_asset_schedules(request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Everything the Providers tab needs: each provider's declared jobs (with their settings and
    the collections it offers), the frequencies, the schedules made from them with their last
    change-check run, and any plain plugin-job schedule left from before this existed."""
    pool = require_pool(request)
    specs = await provider_specs(ctx)
    providers = []
    for provider, group in sorted(specs.items()):
        spec = group[0]
        providers.append(
            {
                "provider": provider,
                "plugin_id": _plugin_id(spec),
                "collections": collections_of(spec),
                "jobs": [
                    {
                        "id": e["id"],
                        "kind": e.get("kind") or "job",
                        "label": e.get("label") or e["id"],
                        "description": e.get("description"),
                        "settings": entry_settings(spec, e),
                    }
                    for e in schedule_entries(spec)
                ],
            }
        )
    rows = await db_module.list_plugin_job_schedules(pool)
    asset_rows = [r for r in rows if r.get("asset_provider")]
    latest = await db_module.latest_asset_change_runs_for_schedules(pool, [r["id"] for r in asset_rows])
    by_cron = {v["cron"]: k for k, v in FREQUENCIES.items()}
    schedules = []
    for row in asset_rows:
        run = latest.get(row["id"])
        if run is not None and run["status"] == "queued":
            # Finished here too, not only by the Sources tab's listing: an admin watching this
            # table after "Run now" must see the result without opening the scope.
            try:
                scope_obj = await resolve_project_scope(pool, parse_scope(row["scope"], SystemUser()))
                [run] = await finish_pending_runs(ctx, pool, scope_obj, [run])
            except HTTPException:
                pass
        found = find_entry(specs.get(row["asset_provider"], []), row["schedule_job"])
        schedules.append(
            {
                **{k: row[k] for k in ("id", "scope", "collection", "enabled", "last_fired_at", "next_fire_at")},
                "provider": row["asset_provider"],
                "job": row["schedule_job"],
                "kind": row["schedule_kind"],
                "frequency": by_cron.get(row["cron_expr"]),
                "settings": settings_of(found[1], row["options"]) if found else {},
                "declared": found is not None,
                "last_skipped_reason": row["last_skipped_reason"],
                "last_job_id": row["last_job_id"],
                "last_run": _public_run(run) if run else None,
            }
        )
    legacy = [
        {k: r[k] for k in ("id", "name", "cron_expr", "scope", "plugin_id", "options", "enabled", "last_fired_at")}
        for r in rows
        if not r.get("asset_provider")
    ]
    return JSONResponse(
        {
            "providers": providers,
            "frequencies": [{"id": k, **v} for k, v in FREQUENCIES.items()],
            "schedules": schedules,
            "legacy": legacy,
        }
    )


@admin_router.get("/asset-schedules/choices")
async def admin_asset_schedule_choices(
    request: Request,
    provider: str,
    job: str,
    scope: str,
    collection: str = "",
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Each setting's choices for one job, scope and collection (``change_users`` depends on both)."""
    pool = require_pool(request)
    found = find_entry(await _specs_or_404(ctx, provider), job)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no job {job!r}")
    spec, entry = found
    key = await scope_key(pool, scope, user)
    return JSONResponse(
        {
            "settings": {
                s["name"]: await setting_choices(pool, s, scope=key, provider=provider, collection=collection)
                for s in entry_settings(spec, entry)
            }
        }
    )


def _frequency_cron(frequency: Any) -> str:
    preset = FREQUENCIES.get(str(frequency or ""))
    if preset is None:
        raise HTTPException(status_code=400, detail=f"frequency must be one of {', '.join(FREQUENCIES)}")
    return preset["cron"]


@admin_router.post("/asset-schedules")
async def admin_asset_schedules_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: ``{provider, job, scope, collection, frequency, settings, enabled}`` -- all choices."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    job = str(body.get("job") or "").strip()
    scope = str(body.get("scope") or "").strip()
    collection = str(body.get("collection") or "").strip() or None
    if not provider or not job or not scope:
        raise HTTPException(status_code=400, detail="provider, job and scope are required")
    _ = parse_scope(scope, user)
    found = find_entry(await _specs_or_404(ctx, provider), job)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} declares no job {job!r}")
    spec, entry = found
    cron = _frequency_cron(body.get("frequency"))
    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    options = await build_options(
        pool,
        spec,
        entry,
        scope=await scope_key(pool, scope, user),
        provider=provider,
        collection=collection,
        settings=settings,
    )
    name = f"{provider}: {entry.get('label') or job} -- {scope}{' / ' + collection if collection else ''}"
    try:
        row = await db_module.create_plugin_job_schedule(
            pool,
            name=name,
            cron_expr=cron,
            scope=scope,
            plugin_id=_plugin_id(spec),
            options=options,
            enabled=bool(body.get("enabled", True)),
            next_fire_at=next_fire(cron),
            created_by=user.sub,
            asset_provider=provider,
            collection=collection,
            schedule_job=job,
            schedule_kind=entry.get("kind") or "job",
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(
                status_code=409, detail="that job is already scheduled for this scope and collection"
            ) from exc
        raise
    return JSONResponse(row, status_code=201)


@admin_router.patch("/asset-schedules/{schedule_id}")
async def admin_asset_schedules_update(
    schedule_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: any of ``{frequency, settings, enabled}``."""
    pool = require_pool(request)
    row = await db_module.get_plugin_job_schedule(pool, schedule_id)
    if row is None or not row.get("asset_provider"):
        raise HTTPException(status_code=404, detail="provider schedule not found")
    body = await request.json() if await request.body() else {}
    fields: dict = {}
    if "frequency" in body:
        fields["cron_expr"] = _frequency_cron(body["frequency"])
        fields["next_fire_at"] = next_fire(fields["cron_expr"])
    if "settings" in body:
        found = find_entry(await _specs_or_404(ctx, row["asset_provider"]), row["schedule_job"])
        if found is None:
            raise HTTPException(status_code=409, detail="the provider no longer declares this job")
        spec, entry = found
        fields["options"] = await build_options(
            pool,
            spec,
            entry,
            scope=await scope_key(pool, row["scope"]),
            provider=row["asset_provider"],
            collection=row["collection"],
            settings=body["settings"] if isinstance(body["settings"], dict) else {},
        )
    if "enabled" in body:
        fields["enabled"] = bool(body["enabled"])
        if fields["enabled"]:
            fields.setdefault("next_fire_at", next_fire(fields.get("cron_expr") or row["cron_expr"]))
    updated = await db_module.update_plugin_job_schedule(pool, schedule_id, **fields)
    return JSONResponse(updated)


@admin_router.delete("/asset-schedules/{schedule_id}")
async def admin_asset_schedules_delete(schedule_id: str, request: Request) -> JSONResponse:
    pool = require_pool(request)
    if not await db_module.archive_plugin_job_schedule(pool, schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found, or already removed")
    return JSONResponse({"archived": schedule_id})


# --- the scheduler's hook --------------------------------------------------------------------


async def asset_schedule_dispatch(ctx: RestContext, pool, schedule_row: dict, options: dict) -> tuple[dict, dict, dict]:
    """For a provider schedule: ``(options, enqueue_kwargs, spec)`` rebuilt from the LIVE
    declaration plus the stored settings, so a provider that changes its fixed options is followed
    without re-saving every schedule. Raises ``LookupError`` with a skip reason when the provider
    is offline or no longer declares the job."""
    provider = schedule_row["asset_provider"]
    specs = (await provider_specs(ctx)).get(provider)
    if not specs:
        raise LookupError(f"no online worker advertises provider {provider!r}; slot skipped")
    found = find_entry(specs, schedule_row["schedule_job"])
    if found is None:
        raise LookupError(f"provider {provider!r} no longer declares job {schedule_row['schedule_job']!r}")
    spec, entry = found
    rebuilt = await build_options(
        pool,
        spec,
        entry,
        scope=schedule_row["scope"],
        provider=provider,
        collection=schedule_row["collection"],
        settings=settings_of(entry, schedule_row["options"] or {}),
        validate=False,
    )
    for key, value in options.items():
        if key not in schedule_row["options"]:
            rebuilt[key] = value  # the scheduler's own stamp (scheduled_at)
    kwargs: dict = {}
    if (entry.get("kind") or "job") == CHANGE_CHECK:
        kwargs["derived_key"], kwargs["derived_prefix"] = job_keys(_plugin_id(spec), rebuilt)
    return rebuilt, kwargs, spec


async def record_scheduled_run(
    pool, schedule_row: dict, *, scope_obj: Scope, job_id: str, derived_key: str | None
) -> None:
    """The run row for a change check the scheduler just fired. Best-effort: the job is already
    queued, and failing to record it must not turn a fired slot into a skipped one."""
    try:
        await db_module.insert_asset_change_run(
            pool,
            scope=scope_obj.prefix(),
            provider=schedule_row["asset_provider"],
            collection=schedule_row["collection"] or "",
            plugin_id=schedule_row["plugin_id"],
            job_id=job_id,
            derived_key=derived_key,
            requested_by="system",
            requested_via="schedule",
            schedule_id=schedule_row["id"],
        )
    except Exception:
        logger.exception("asset schedules: could not record the run of %s", schedule_row.get("id"))
