"""Provider sweeps -- the ``sweep`` kind of ``asset_schedules``: every collection, one at a time.

A provider declares, beside its change check::

    {"id": "project-sweep", "kind": "sweep", "label": "...",
     "options": {"action": "..."},           # fixed job options
     "collection_option": "project",         # the option that receives each collection
     "settings": [{"name": "sweep_target"}]} # dropdowns, as for any entry

Firing it -- from its schedule, or once from Admin -> Providers -- runs, for EVERY collection the
provider offers, the same two jobs the Sources tab runs for one (``assets/collectionRequest.ts``):
the provider's own job, which stages what it fetched and answers ``asset_staging_id``, and core's
``asset_publish`` of that staging. The browser drives those two for a click; nothing drives them
for a schedule at 02:00, so here the API does.

ONE COLLECTION AT A TIME, UNLESS THE PROVIDER SAYS OTHERWISE. A provider served by one licensed
seat holds one collection open at a time; queuing them all at once would only move the waiting
from here into the queue, where nobody can see which one is slow, and make every job's "fetch"
time include every job before it. Sequential, each item's timings are its own. A provider with no
such seat declares ``"parallel": n`` on its sweep entry, and the sweep then runs up to n
collections at once -- capped by the live workers serving it, since each runs one job at a time
(``sweep_width``). Each lane is still one collection at a time, so timings stay per collection.

A JOB MAY STAGE A COLLECTION IN PARTS: ``asset_staging_ids`` instead of one ``asset_staging_id``,
each published in turn (a provider that exports a collection a piece at a time, to keep each file
small). And an entry may declare ``"fetch_timeout_s"`` when one collection's job runs longer than
:data:`FETCH_TIMEOUT_S`.

EVERY PHASE IS TIMED, AND NOTHING IS AGGREGATED. An item records the provider's phases as its
summary reports them (``timings`` -- e.g. login / export / stage), core's derive and write as the
publish summary reports them, and the wall-clock seen from here: how long the fetch job waited
for a worker (``queued_ms``), how long it then ran (``fetch_ms``), the publish (``publish_ms``)
and the whole item (``total_ms``). A sweep is a measurement before it is anything else.

IN-PROCESS, WITH A HEARTBEAT. The driver is a task in the API process that started it. It stamps
the run's heartbeat on every poll, and a listing reports a running sweep with an old heartbeat as
``interrupted`` (see ``db/asset_sweeps.py``): a restart loses the DRIVER, and says so, rather
than leaving it "running" for ever. ``POST /asset-sweeps/{id}/resume`` gives the same run a new
driver: what finished is kept, what was in flight runs again, the rest runs as it would have
(:func:`resume_sweep`). Its jobs are not lost either -- a fetch that finishes still stages, and
``GET /assets/staging`` lists it. The polling, heartbeat, cancel and skip machinery is shared
with the search-result export (``run_driver.py``).

SKIP CURRENT. Cancel stops the whole sweep; ``POST /asset-sweeps/{id}/skip`` stops only the
collection in flight -- its job is cancelled, its row says who skipped it, and the sweep carries on
with the next collection. A skipped collection is not a failure of the sweep.

RUN OR NOT, PER COLLECTION. A collection the sweep has not reached can be ticked off (``excluded``)
or back on (``pending``) while it runs -- ``PATCH /asset-sweeps/{id}/items/{position}``; the driver
reads each item's state just before starting it. And a provider has a DEFAULT list
(``/asset-sweeps/defaults``): every new sweep of it, scheduled or one-off, starts with the
collections unticked there already ``excluded``. Some collections are too big for a routine sweep,
and remembering to untick them every time is exactly what a default is for. Neither is a failure.

NO SCOPE. A sweep keeps provider data current, and provider data lives once, in the provider store
(``Scope.providers()``, ``routes/provider_store.py``). Every sweep -- an admin's, a scheduled one, a
request made from a scope -- enqueues, stages and publishes there, reads each collection's provider
options from ``providers/assets/_options/<collection>.json``, and an interrupted one resumes there
even when it was started before the store under ``shared`` or a project. An admin's and a scheduled
sweep are recorded under ``providers``; one requested from a scope is recorded under that scope,
which is how the scope polls it.

APPLY CHANGES RIDES ON THIS. An ``apply-changes`` entry (``routes/asset_apply_changes.py``) is
started, scheduled, resumed, skipped and cancelled exactly as a sweep is, and recorded as one; only
the per-collection work differs (``apply_one``: ask for the changes since core's cursor, publish the
patches, then advance the cursor). It may name the collections it runs (a schedule's one, or a
one-off start's ``collection``), and the provider's default sweep list does not apply to it.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..run_driver import (
    ItemSkipped,
    RunCancelled,
    json_of,
    ms,
    spawn,
    user_label,
    wait_for_job,
)
from ..scope import Scope
from .asset_schedules import (
    APPLY_CHANGES,
    SWEEP,
    build_options,
    collections_of,
    find_entry,
    job_keys,
    provider_specs,
    settings_of,
)
from .assets import staged_by_role, submit_asset_publish
from .deps import (
    RestContext,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
)
from .plugin_jobs import enqueue_plugin_job
from .provider_store import PROVIDER_STORE, PROVIDER_STORE_WIRE, job_scope

admin_router = APIRouter()

POLL_INTERVAL_S = 3.0
#: As the Sources tab's request: a whole hierarchy from a live design system is tens of minutes.
FETCH_TIMEOUT_S = 90 * 60
PUBLISH_TIMEOUT_S = 15 * 60


class SweepRefused(Exception):
    """A sweep that could not start: state said no (the provider is offline, already sweeping)."""


#: The job name of a run that requests chosen collections through the provider's own
#: ``asset_collection_request`` (the Sources tab's "Request <collection>"), driven as a sweep.
COLLECTION_REQUEST = "collection-request"


def collection_request_entry(spec: dict) -> dict | None:
    """``asset_collection_request`` as a sweep entry: the same fixed options and collection option
    the Sources tab's request sends, so a run of it asks each collection exactly what a click would.
    None when the spec declares no collection request."""
    decl = spec.get("asset_collection_request")
    if not isinstance(decl, dict):
        return None
    option = decl.get("collection_option")
    if not (isinstance(option, str) and option.strip()):
        return None
    return {
        "id": COLLECTION_REQUEST,
        "kind": SWEEP,
        "label": str(decl.get("label") or "Request collection"),
        "options": dict(decl.get("options") or {}) if isinstance(decl.get("options"), dict) else {},
        "collection_option": option.strip(),
    }


async def advertised_provider_specs(ctx: RestContext, provider: str) -> list[dict]:
    """Every live spec of ``provider`` -- not only those declaring schedules (``provider_specs``):
    a collection request is declared beside them, and a provider may declare it without any."""
    out = []
    for spec in (await ctx.jobs.advertised_specs("plugin_specs")).values():
        if isinstance(spec, dict) and spec.get("asset_provider_id") == provider:
            out.append(spec)
    return out


async def sweep_entry(ctx: RestContext, provider: str, job: str) -> tuple[list[dict], tuple[dict, dict] | None]:
    """``(specs, (spec, entry) | None)`` for ``job`` of ``provider``: a declared sweep or
    apply-changes entry (both driven here), or :data:`COLLECTION_REQUEST` -- the first spec
    declaring a collection request."""
    if job == COLLECTION_REQUEST:
        specs = await advertised_provider_specs(ctx, provider)
        for spec in specs:
            entry = collection_request_entry(spec)
            if entry is not None:
                return specs, (spec, entry)
        return specs, None
    specs = (await provider_specs(ctx)).get(provider) or []
    found = find_entry(specs, job)
    return specs, found if found is not None and found[1].get("kind") in (SWEEP, APPLY_CHANGES) else None


def offered_names(offered: list[str], wanted: list[str]) -> tuple[list[str], list[str]]:
    """``(names, unknown)``: each of ``wanted`` in the provider's own spelling (core indexes
    collections lower-case, a provider may offer ``ALPHA``), in ``wanted`` order, and those it does not
    offer. With nothing offered (a provider that lists none), the names go through as they are."""
    if not offered:
        return list(dict.fromkeys(wanted)), []
    by_lower = {c.lower(): c for c in offered}
    names, unknown = [], []
    for c in wanted:
        name = by_lower.get(str(c).lower())
        if name is None:
            unknown.append(str(c))
        elif name not in names:
            names.append(name)
    return names, unknown


_ms = ms
_json_of = json_of
_Cancelled = RunCancelled


def _all_collections(specs: list[dict]) -> list[str]:
    """Every collection any of the provider's workers offers, in first-seen order. Not the first
    spec's alone: one worker may serve a subset (one pool per collection), and a sweep of
    "every collection" that silently skipped another worker's would be worse than none."""
    seen: dict[str, None] = {}
    for spec in specs:
        for c in collections_of(spec):
            seen.setdefault(c, None)
    return list(seen)


async def _provider_options(ctx: RestContext, scope_obj: Scope | None, provider: str, collection: str) -> dict:
    """The collection's saved provider options (``assets/_options/<collection>.json``, as
    ``assets/providerOptions.ts`` writes it), sent with every request for it -- a sweep asks the
    same question the Sources tab would. None saved is the ordinary case.

    Read from the PROVIDER STORE (``providers/assets/_options/<collection>.json``): one
    configuration per (provider, collection), edited by admins, because the job runs there. Until
    the store holds a document for the collection, the copy a scope saved before the store
    (``scope_obj``, when it is not the store) still answers -- so a request from a scope that
    configured its collection keeps asking what it asked."""
    key = f"assets/_options/{collection.lower()}.json"
    places = [PROVIDER_STORE]
    if scope_obj is not None and scope_obj != PROVIDER_STORE:
        places.append(scope_obj)
    for place in places:
        try:
            doc = _json_of(await ctx.storage.get_bytes(place, key))
        except Exception:  # noqa: BLE001 - absent, or unreadable: nothing to add from here
            continue
        values = ((doc or {}).get("providers") or {}).get(provider) if isinstance(doc, dict) else None
        return dict(values) if isinstance(values, dict) else {}
    return {}


async def start_sweep(
    ctx: RestContext,
    pool,
    *,
    scope: str,
    scope_obj: Scope,
    provider: str,
    job: str,
    settings: dict,
    user,
    via: str,
    schedule_id: str | None = None,
    collections: list[str] | None = None,
) -> dict:
    """Record a sweep and start driving it. Returns the run. Raises :class:`SweepRefused`.

    ``collections``: only these (matched to the provider's spelling, refused when it does not offer
    one), in that order -- and then the provider's default exclusions do not apply: somebody named
    exactly what to run. ``job`` may be :data:`COLLECTION_REQUEST`.

    THE SWEEP RUNS IN THE PROVIDER STORE, whatever ``scope_obj`` says: its jobs enqueue, stage and
    publish there (``job_scope`` -- every spec reaching here declares ``asset_provider_id``).
    ``scope`` is only what the run is RECORDED under: ``providers`` for an admin's or a scheduled
    sweep, the requesting scope's wire for a request made from one (that scope polls its run by
    it)."""
    specs, found = await sweep_entry(ctx, provider, job)
    if not specs:
        raise SweepRefused(f"no online worker advertises provider {provider!r}")
    if found is None:
        what = "no collection request" if job == COLLECTION_REQUEST else f"no sweep {job!r}"
        raise SweepRefused(f"provider {provider!r} declares {what}")
    spec, entry = found
    scope_obj = job_scope(spec, scope_obj)
    busy = await db_module.running_asset_sweep(pool, provider=provider)
    if busy is not None:
        raise SweepRefused(f"a {provider} sweep is already running (started {busy['created_at']})")
    chosen = collections is not None
    if chosen:
        collections, unknown = offered_names(_all_collections(specs), collections)
        if unknown:
            raise SweepRefused(f"provider {provider!r} does not offer {', '.join(unknown)}")
    else:
        collections = _all_collections(specs)
    if not collections:
        raise SweepRefused(f"provider {provider!r} offers no collections to sweep")
    plugin_id = str(spec.get("slug") or spec.get("id") or "")
    run = await db_module.insert_asset_sweep_run(
        pool,
        scope=scope,
        provider=provider,
        plugin_id=plugin_id,
        job=job,
        settings=settings,
        collections=collections,
        requested_by=getattr(user, "sub", None),
        requested_via=via,
        schedule_id=schedule_id,
        # The default list is a SWEEP's (collections too big to export whole routinely); applying
        # changes to one is small by definition, and leaving it out would let its cursor go stale.
        excluded=(
            frozenset()
            if chosen or entry.get("kind") == APPLY_CHANGES
            else frozenset(await default_excluded(pool, provider))
        ),
    )
    spawn(
        _drive(ctx, pool, run, scope_obj=scope_obj, spec=spec, entry=entry, collections=collections, user=user),
        name=f"asset-sweep-{run['id']}",
    )
    logger.info("asset sweep %s: %s %s over %d collection(s)", run["id"], provider, job, len(collections))
    return run


#: Upper bound on a sweep's parallel lanes, whatever a provider declares.
MAX_SWEEP_WIDTH = 16
#: Upper bound on an entry's own fetch timeout.
MAX_FETCH_TIMEOUT_S = 24 * 3600


def fetch_timeout(entry: dict) -> float:
    """How long one collection's fetch job may run: the entry's ``fetch_timeout_s`` when it
    declares one (bounded), else :data:`FETCH_TIMEOUT_S`."""
    try:
        declared = float(entry.get("fetch_timeout_s") or 0)
    except (TypeError, ValueError):
        declared = 0
    return min(declared, MAX_FETCH_TIMEOUT_S) if declared > 0 else FETCH_TIMEOUT_S


def staging_ids_of(summary: dict) -> list[str]:
    """What a fetch job staged: ``asset_staging_ids`` (several parts), else the one
    ``asset_staging_id``. Blank and repeated ids are dropped."""
    raw = summary.get("asset_staging_ids")
    ids = raw if isinstance(raw, list) else [summary.get("asset_staging_id")]
    out: list[str] = []
    for value in ids:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out


async def sweep_width(ctx: RestContext, spec: dict, entry: dict) -> int:
    """How many collections a sweep runs at once: the entry's declared ``parallel`` (default 1 --
    a provider served by one licensed seat must not declare more), capped by how many live
    workers serve the provider's capability. Each worker runs one job at a time, so lanes beyond
    the workers would only queue; with none counted (no registry), one lane."""
    try:
        declared = max(1, min(int(entry.get("parallel") or 1), MAX_SWEEP_WIDTH))
    except (TypeError, ValueError):
        declared = 1
    if declared == 1:
        return 1
    cap = str(spec.get("worker_capability") or "").strip().lower()
    try:
        rows = await ctx.jobs.capabilities()
    except Exception:  # noqa: BLE001 - unknown fleet: be conservative
        return 1
    workers = sum(1 for r in rows if cap and cap in {str(c).lower() for c in (r.get("capabilities") or ())})
    return max(1, min(declared, workers))


async def _wait(
    ctx: RestContext, pool, run_id: str, job_id: str, timeout_s: float, position: int | None = None
) -> tuple[int, int]:
    """Poll ``job_id`` to done. Returns ``(queued_ms, ran_ms)`` -- how long it waited for a worker
    and how long it then ran, to the poll's resolution. Heartbeats the run on every poll; raises
    :class:`_Cancelled` (after cancelling the job) when the run was cancelled, ``ItemSkipped`` when
    the item at ``position`` was asked to be skipped, and RuntimeError when the job failed or
    outlived ``timeout_s`` (``run_driver.wait_for_job``)."""
    return await wait_for_job(
        ctx,
        job_id,
        timeout_s=timeout_s,
        beat=lambda: db_module.heartbeat_asset_sweep_run(pool, run_id),
        position=position,
        # Read here, at call time: a test sets this module's interval to 0.
        poll_s=POLL_INTERVAL_S,
        label=f"asset sweep {run_id}",
    )


async def _drive(
    ctx: RestContext,
    pool,
    run: dict,
    *,
    scope_obj: Scope,
    spec: dict,
    entry: dict,
    collections: list[str],
    user,
) -> None:
    run_id, provider = run["id"], run["provider"]
    failed = 0
    width = await sweep_width(ctx, spec, entry)
    queue = iter(enumerate(collections))
    # One collection's work: a sweep fetches and publishes it whole; an apply-changes run asks for
    # what changed since core's cursor and applies the patches (routes/asset_apply_changes.py). The
    # lanes, heartbeat, cancel, skip and resume around it are the same.
    one = _sweep_one
    if entry.get("kind") == APPLY_CHANGES:
        from .asset_apply_changes import apply_one

        one = apply_one

    async def lane() -> None:
        # Lanes share one iterator, so collections still START in order; with one lane this is
        # exactly the sequential sweep.
        nonlocal failed
        for position, collection in queue:
            if await db_module.heartbeat_asset_sweep_run(pool, run_id) != "running":
                raise _Cancelled
            # Read now, not at start: it may have been ticked off (or back on) since.
            if await db_module.asset_sweep_item_status(pool, run_id, position) != "pending":
                continue
            if not await one(
                ctx, pool, run, position, collection, scope_obj=scope_obj, spec=spec, entry=entry, user=user
            ):
                failed += 1

    try:
        if width > 1:
            logger.info("asset sweep %s: %d collections in parallel", run_id, width)
        outcomes = await asyncio.gather(*(lane() for _ in range(width)), return_exceptions=True)
        # A cancel seen by one lane is seen by the others on their next poll; a crash in one is
        # the sweep's crash, after the other lanes finished what they had in flight.
        for outcome in outcomes:
            if isinstance(outcome, _Cancelled):
                raise outcome
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
    except _Cancelled:
        await db_module.skip_pending_asset_sweep_items(pool, run_id, "the sweep was cancelled")
        logger.info("asset sweep %s: cancelled", run_id)
        return
    except Exception as exc:  # noqa: BLE001 - the driver must always leave the run finished
        logger.exception("asset sweep %s: driver crashed", run_id)
        await db_module.skip_pending_asset_sweep_items(pool, run_id, "the sweep stopped early")
        await db_module.finish_asset_sweep_run(pool, run_id, status="error", error=f"the sweep crashed: {exc}")
        return
    error = f"{failed} of {len(collections)} collection(s) failed" if failed else None
    await db_module.finish_asset_sweep_run(pool, run_id, status="error" if failed else "done", error=error)
    logger.info("asset sweep %s: %s finished (%s)", run_id, provider, error or "all ok")


async def _sweep_one(
    ctx: RestContext,
    pool,
    run: dict,
    position: int,
    collection: str,
    *,
    scope_obj: Scope,
    spec: dict,
    entry: dict,
    user,
) -> bool:
    """Fetch and publish one collection. Returns False when it failed; raises only _Cancelled."""
    run_id, provider, plugin_id = run["id"], run["provider"], run["plugin_id"]
    t_item = time.monotonic()
    timings: dict = {}

    async def item(**fields) -> None:
        await db_module.update_asset_sweep_item(pool, run_id, position, **fields)

    async def finish(status: str, **fields) -> None:
        timings["total_ms"] = _ms(time.monotonic() - t_item)
        await item(status=status, timings=timings, finished_at=datetime.now(timezone.utc), **fields)

    await item(status="fetching", started_at=datetime.now(timezone.utc))
    try:
        options = await build_options(
            pool,
            spec,
            entry,
            scope=scope_obj.prefix(),
            provider=provider,
            collection=collection,
            settings=run["settings"],
            validate=False,
        )
        # The collection's provider options UNDER the entry's: they add to a request and never
        # change which job it is or which collection. `requested_at` makes it a new job, not a
        # cache hit on the last sweep's.
        options = {
            **await _provider_options(ctx, scope_obj, provider, collection),
            **options,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        # The summary's key, so it can be read back -- but NO derived_prefix, exactly as the
        # Sources tab's request sends none: a provider stages under `assets/_staging/` on its own,
        # and a prefix outside it is refused (by the E3D provider after the whole export ran).
        derived_key, _ = job_keys(plugin_id, options)
        fetch_job = await enqueue_plugin_job(
            ctx,
            plugin_id=plugin_id,
            options=options,
            scope_obj=scope_obj,
            user=user,
            pool=pool,
            derived_key=derived_key,
            plugin_spec=spec,
        )
        await item(fetch_job_id=fetch_job)
        timings["queued_ms"], timings["fetch_ms"] = await _wait(
            ctx, pool, run_id, fetch_job, fetch_timeout(entry), position=position
        )
        summary = _json_of(await ctx.storage.get_bytes(scope_obj, derived_key))
        if not isinstance(summary, dict):
            raise RuntimeError("the provider's job answered without a summary")
        if isinstance(summary.get("timings"), dict):
            timings["provider"] = summary["timings"]
        if isinstance(summary.get("login"), dict):
            timings["login"] = summary["login"]

        if summary.get("asset_unchanged") is True:
            await finish("unchanged", revision=summary.get("revision"), message=summary.get("message"))
            return True
        if summary.get("asset_nothing") is True:
            await finish("nothing", message=summary.get("message"))
            return True
        staging_ids = staging_ids_of(summary)
        if not staging_ids:
            raise RuntimeError(
                "the provider's job finished without an asset_staging_id, so there is nothing to publish"
            )

        # Several staged parts (a provider that stages a collection in pieces) are published one
        # after another; one that fails is counted and the rest still publish.
        await item(status="publishing")
        t_pub = time.monotonic()
        revision = None
        publish_failed: list[str] = []
        for staging_id in staging_ids:
            try:
                staged = await staged_by_role(ctx, scope_obj, staging_id)
                publish_job, publish_key = await submit_asset_publish(
                    ctx, scope_obj, provider=provider, staged=staged, user=user
                )
                await item(publish_job_id=publish_job)
                await _wait(ctx, pool, run_id, publish_job, PUBLISH_TIMEOUT_S, position=position)
            except (_Cancelled, ItemSkipped):
                raise
            except Exception as exc:  # noqa: BLE001 - one part's failure is counted, not fatal
                if len(staging_ids) == 1:
                    raise
                publish_failed.append(f"{staging_id}: {exc}")
                continue
            outcome = _json_of(await ctx.storage.get_bytes(scope_obj, publish_key))
            outcome = outcome if isinstance(outcome, dict) else {}
            if isinstance(outcome.get("timings"), dict) and len(staging_ids) == 1:
                timings["publish"] = outcome["timings"]
            revision = outcome.get("revision") or revision
        timings["publish_ms"] = _ms(time.monotonic() - t_pub)
        if len(staging_ids) > 1:
            timings["published_parts"] = len(staging_ids) - len(publish_failed)
        if publish_failed:
            await finish(
                "error",
                revision=revision,
                error=f"{len(publish_failed)} of {len(staging_ids)} staged part(s) failed to publish: "
                + "; ".join(publish_failed[:3]),
            )
            return False
        await finish("done", revision=revision)
        return True
    except _Cancelled:
        await finish("skipped", message="the sweep was cancelled")
        raise
    except ItemSkipped as exc:
        # Skipped on request: this collection stops here (its timings so far are kept) and the
        # sweep goes on. Not a failure -- whoever skipped it knows why.
        await db_module.clear_asset_sweep_skip(pool, run_id, position)
        await finish("skipped", message=str(exc))
        logger.info("asset sweep %s: %s %s", run_id, collection, exc)
        return True
    except HTTPException as exc:
        await finish("error", error=f"{exc.status_code}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - one collection's failure is its row's, not the sweep's
        logger.warning("asset sweep %s: %s failed: %s", run_id, collection, exc)
        await finish("error", error=str(exc))
    return False


#: ``app_settings`` key of a provider's default sweep list: ``{"excluded": [collection, ...]}``.
DEFAULTS_SETTING = "asset_sweeps.defaults.{provider}"


async def default_excluded(pool, provider: str) -> list[str]:
    """The collections a provider's new sweeps start with ticked off. Unreadable means none: a
    sweep that runs one collection too many is better than one that silently runs none."""
    try:
        raw = await db_module.get_setting(pool, DEFAULTS_SETTING.format(provider=provider))
        doc = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("asset sweeps: the default list of %s is unreadable; using none", provider, exc_info=True)
        return []
    excluded = doc.get("excluded") if isinstance(doc, dict) else None
    return [str(c) for c in excluded] if isinstance(excluded, list) else []


async def _scope_of(pool, scope: str, user) -> Scope:
    return await resolve_project_scope(pool, parse_scope(scope, user))


# --- Admin -> Providers -----------------------------------------------------------------------


@admin_router.post("/asset-sweeps")
async def admin_asset_sweep_start(
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Run a provider's sweep -- or its apply-changes entry -- once, now. Body: ``{provider, job,
    settings, collection?}`` -- the same choices a schedule of it is made from, and checked the same
    way. ``collection`` (or a ``collections`` list) runs only those; absent or empty, every
    collection the provider offers. ``scope`` (from before the provider store) is accepted and
    ignored: the run is driven in, and recorded under, the store."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    job = str(body.get("job") or "").strip()
    if not provider or not job:
        raise HTTPException(status_code=400, detail="provider and job are required")
    settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    named = body.get("collections") if isinstance(body.get("collections"), list) else [body.get("collection")]
    chosen = [str(c).strip() for c in named if isinstance(c, str) and c.strip()] or None
    scope_obj = PROVIDER_STORE
    specs = (await provider_specs(ctx)).get(provider) or []
    found = find_entry(specs, job)
    if found is not None:
        # Validates every chosen value against its choices; the options themselves are rebuilt
        # per collection by the driver. The run keeps the settings as the declaration types them
        # (a bool setting's "false" from a form is stored, and sent to every job, as `false`).
        settings = settings_of(
            found[1],
            await build_options(
                pool,
                found[0],
                found[1],
                scope=scope_obj.prefix(),
                provider=provider,
                collection=None,
                settings=settings,
            ),
        )
    try:
        run = await start_sweep(
            ctx,
            pool,
            scope=PROVIDER_STORE_WIRE,
            scope_obj=scope_obj,
            provider=provider,
            job=job,
            settings=settings,
            user=user,
            via="user",
            collections=chosen,
        )
    except SweepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(run, status_code=202)


@admin_router.get("/asset-sweeps/defaults")
async def admin_asset_sweep_defaults(
    request: Request, provider: str, ctx: RestContext = Depends(rest_context)
) -> JSONResponse:
    """A provider's default sweep list: every collection it offers now, and which are ticked off.
    A collection ticked off that the provider no longer offers is listed too, so it can be cleared."""
    pool = require_pool(request)
    offered = _all_collections((await provider_specs(ctx)).get(provider) or [])
    excluded = await default_excluded(pool, provider)
    return JSONResponse(
        {"provider": provider, "collections": offered + [c for c in excluded if c not in offered], "excluded": excluded}
    )


@admin_router.put("/asset-sweeps/defaults")
async def admin_asset_sweep_defaults_set(
    request: Request, user: User = Depends(auth_module.current_user)
) -> JSONResponse:
    """Body: ``{provider, excluded: [collection, ...]}``. Applies to sweeps started from now on."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    excluded = body.get("excluded")
    if not provider or not isinstance(excluded, list):
        raise HTTPException(status_code=400, detail="provider and an excluded list are required")
    clean = sorted({str(c).strip() for c in excluded if str(c).strip()})
    await db_module.set_setting(
        pool, DEFAULTS_SETTING.format(provider=provider), json.dumps({"excluded": clean}), updated_by=user_label(user)
    )
    return JSONResponse({"provider": provider, "excluded": clean})


@admin_router.patch("/asset-sweeps/{run_id}/items/{position}")
async def admin_asset_sweep_item_run(run_id: str, position: int, request: Request) -> JSONResponse:
    """Body: ``{run: bool}`` -- tick a collection the sweep has not reached on or off. 409 when the
    item is in flight or finished (skip the one in flight instead) or the sweep is not running."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    if not isinstance(body.get("run"), bool):
        raise HTTPException(status_code=400, detail="run (true or false) is required")
    status = await db_module.set_asset_sweep_item_run(pool, run_id, position, run=body["run"])
    if status is None:
        raise HTTPException(
            status_code=409, detail="only a collection the running sweep has not reached can be ticked on or off"
        )
    return JSONResponse({"position": position, "status": status})


@admin_router.get("/asset-sweeps")
async def admin_asset_sweeps(request: Request, provider: str | None = None, limit: int = 20) -> JSONResponse:
    pool = require_pool(request)
    runs = await db_module.list_asset_sweep_runs(pool, provider=provider or None, limit=max(1, min(int(limit), 100)))
    return JSONResponse({"runs": runs})


@admin_router.get("/asset-sweeps/{run_id}")
async def admin_asset_sweep(run_id: str, request: Request, ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """One sweep with its items. An item in flight carries its job's live ``stage`` and
    ``progress``: a whole-project fetch is tens of minutes, and "fetching" alone cannot be told
    apart from stuck."""
    pool = require_pool(request)
    run = await sweep_with_progress(ctx, pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    return JSONResponse(run)


async def sweep_with_progress(ctx: RestContext, pool, run_id: str) -> dict | None:
    """The run with its items, each one in flight carrying its job's live ``stage`` / ``progress``;
    None when there is no such run. Shared by the admin read and a requester's read of a
    collection request it started (``asset_exports``)."""
    run = await db_module.get_asset_sweep_run(pool, run_id)
    if run is None:
        return None
    for item in run["items"]:
        # An apply-changes item keeps its answer beside its timings (the sweep tables have no other
        # free column); it is read as the item's own: `apply_changes` and `roles`.
        timings = item.get("timings") or {}
        for name in ("apply_changes", "roles"):
            if name in timings:
                item[name] = timings[name]
        job_id = {"fetching": item["fetch_job_id"], "publishing": item["publish_job_id"]}.get(item["status"])
        if not job_id:
            continue
        try:
            snap = await ctx.jobs.status(job_id)
        except Exception:  # noqa: BLE001 - live progress is a nicety; the row is still right
            continue
        if snap is not None:
            item["job_status"] = snap.status
            item["stage"] = (snap.payload or {}).get("stage")
            item["progress"] = (snap.payload or {}).get("progress")
    return run


@admin_router.post("/asset-sweeps/{run_id}/cancel")
async def admin_asset_sweep_cancel(
    run_id: str, request: Request, ctx: RestContext = Depends(rest_context)
) -> JSONResponse:
    """Stop a sweep: the job in flight is cancelled and the collections not reached are skipped.
    Also how an ``interrupted`` sweep (its driver gone) is put to rest."""
    pool = require_pool(request)
    run = await db_module.get_asset_sweep_run(pool, run_id)
    if not await db_module.cancel_asset_sweep_run(pool, run_id):
        raise HTTPException(status_code=409, detail="that sweep is not running")
    # Here as well as in the driver, which an interrupted sweep no longer has.
    await db_module.skip_pending_asset_sweep_items(pool, run_id, "the sweep was cancelled")
    # THE JOBS IN FLIGHT ARE CANCELLED HERE TOO, not only by the driver. A driver that is alive
    # sees the cancel on its next poll and cancels its job; one that died with its API process
    # (a restart) never will, and its queued job then sat in the worker's pool until a worker
    # took it -- a whole-project export nobody wanted, holding a single-seat provider while the
    # next sweep queued behind it.
    cancelled_jobs = []
    for item in (run or {}).get("items") or ():
        if item["status"] not in _IN_FLIGHT:
            continue
        for job_id in (item.get("fetch_job_id"), item.get("publish_job_id")):
            if not job_id:
                continue
            try:
                if await ctx.jobs.cancel(job_id):
                    cancelled_jobs.append(job_id)
            except Exception:  # noqa: BLE001 - the sweep is cancelled either way
                logger.warning("asset sweep %s: could not cancel job %s", run_id, job_id, exc_info=True)
        await db_module.update_asset_sweep_item(
            pool, run_id, item["position"], status="skipped", message="the sweep was cancelled"
        )
    return JSONResponse({"cancelled": run_id, "jobs": cancelled_jobs})


async def resume_sweep(ctx: RestContext, pool, run_id: str, *, user) -> dict:
    """Drive an INTERRUPTED sweep on from where it stopped. Returns the run. Raises
    :class:`SweepRefused` (and LookupError when there is no such run).

    The same run goes on -- its finished collections, their timings and its settings are kept --
    under a new driver in THIS process: the collections not reached run as the old driver would
    have run them, and the ones in flight when it died run again from the start, since their
    jobs' answers were never recorded. Those jobs are cancelled first, as a cancel of the sweep
    would: one still queued would otherwise run for nobody, holding the provider's worker.

    The provider must be online and declare the sweep still, and no other sweep of it may be
    running -- the same guards a new sweep has."""
    run = await db_module.get_asset_sweep_run(pool, run_id)
    if run is None:
        raise LookupError(run_id)
    if run["status"] != "interrupted":
        raise SweepRefused(f"only an interrupted sweep can be resumed; this one is {run['status']}")
    provider = run["provider"]
    specs, found = await sweep_entry(ctx, provider, run["job"])
    if not specs:
        raise SweepRefused(f"no online worker advertises provider {provider!r}")
    if found is None:
        raise SweepRefused(f"provider {provider!r} no longer declares the sweep {run['job']!r}")
    spec, entry = found
    busy = await db_module.running_asset_sweep(pool, provider=provider)
    if busy is not None and busy["id"] != run_id:
        raise SweepRefused(f"a {provider} sweep is already running (started {busy['created_at']})")
    # The store, whatever the run is recorded under: a run from before the store (recorded under
    # `shared` or a project) goes on where every provider job runs now.
    scope_obj = PROVIDER_STORE
    rearmed = await db_module.resume_asset_sweep_run(pool, run_id)
    if rearmed is None:
        # Between the read and here: someone else resumed it, or it was cancelled.
        raise SweepRefused("that sweep is no longer interrupted -- refresh and look again")
    for item in rearmed:
        for job_id in (item.get("fetch_job_id"), item.get("publish_job_id")):
            if not job_id:
                continue
            try:
                await ctx.jobs.cancel(job_id)
            except Exception:  # noqa: BLE001 - it runs again either way
                logger.warning("asset sweep %s: could not cancel job %s", run_id, job_id, exc_info=True)
    run = await db_module.get_asset_sweep_run(pool, run_id)
    collections = [i["collection"] for i in sorted(run.pop("items"), key=lambda i: i["position"])]
    spawn(
        _drive(ctx, pool, run, scope_obj=scope_obj, spec=spec, entry=entry, collections=collections, user=user),
        name=f"asset-sweep-{run_id}",
    )
    logger.info(
        "asset sweep %s: resumed by %s (%d collection(s) in flight run again)", run_id, user_label(user), len(rearmed)
    )
    return {**run, "resumed": [i["collection"] for i in rearmed]}


@admin_router.post("/asset-sweeps/{run_id}/resume")
async def admin_asset_sweep_resume(
    run_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Go on with an ``interrupted`` sweep (its driver died with its API process). 409 when the
    sweep is not interrupted, its provider is offline, or another sweep of it is running."""
    pool = require_pool(request)
    try:
        run = await resume_sweep(ctx, pool, run_id, user=user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="sweep not found") from exc
    except SweepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(run, status_code=202)


#: Item states in which a sweep's collection is "the one in flight".
_IN_FLIGHT = ("fetching", "publishing")


@admin_router.post("/asset-sweeps/{run_id}/skip")
async def admin_asset_sweep_skip(
    run_id: str,
    request: Request,
    position: int | None = None,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Skip the collection in flight and carry on with the next -- unlike cancel, which stops the
    sweep. ``position`` (optional) names the item the caller saw running: when the sweep has moved
    on since, the request is refused (409) rather than skipping a collection nobody looked at.

    The request is stored on the run; the driver sees it on its next poll, cancels the item's job,
    records the item as skipped by whoever asked, and clears it."""
    pool = require_pool(request)
    run = await db_module.get_asset_sweep_run(pool, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="sweep not found")
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail=f"that sweep is {run['status']}, not running")
    flying = [i["position"] for i in run["items"] if i["status"] in _IN_FLIGHT]
    if not flying:
        raise HTTPException(status_code=409, detail="no collection of that sweep is in flight right now")
    target = flying[0] if position is None else position
    if target not in flying:
        raise HTTPException(
            status_code=409,
            detail=f"item {target} is not the one in flight (that is {flying[0]}); refresh and try again",
        )
    by = user_label(user)
    if not await db_module.request_asset_sweep_skip(pool, run_id, target, by=by):
        raise HTTPException(status_code=409, detail="that sweep is not running")
    return JSONResponse({"skip_requested": target})
