"""Provider sweeps -- the ``sweep`` kind of ``asset_schedules``: every collection, one job each.

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

SEVERAL RUNS OF ONE PROVIDER GO SIDE BY SIDE -- an apply-changes run beside a full export of another
collection, two sweeps over different projects. Two rules, both kept in the database so they hold
across runs and API replicas (``db/asset_sweeps.py``, migration 048):

* ONE COLLECTION, ONE RUN AT A TIME. A lane CLAIMS a collection before working on it, under a lock
  keyed by (provider, collection); while another live run's item of it is in flight the claim is
  refused, the item says ``waiting`` and names that run, and the lane starts the next free
  collection and comes back to it. One still held once the run has nothing else left and
  ``fetch_timeout + PUBLISH_TIMEOUT_S`` have passed ends ``busy``. Two identical runs started back to
  back thus do each collection one after the other (every job is idempotent), never both at once.
* A SHARED WORKER BUDGET. Every provider job (not the publishes: those run on core's own workers)
  holds one of the provider's worker slots while it runs (:class:`worker_slot`). The cap is
  :func:`provider_budget`: the provider's declared ``parallel`` capped at the live workers serving it
  (recomputed every ``CAP_TTL_S``), optionally lowered by an admin (``/asset-runs/max-parallel``).
  A run's own lanes are still ``sweep_width`` at most.

A JOB MAY STAGE A COLLECTION IN PARTS: ``asset_staging_ids`` instead of one ``asset_staging_id``,
each published in turn (a provider that exports a collection a piece at a time, to keep each file
small). And an entry may declare ``"fetch_timeout_s"`` when one collection's job runs longer than
:data:`FETCH_TIMEOUT_S`.

SEVERAL TARGETS PER COLLECTION. An entry may declare ``"targets_setting": "sweep_target"``, naming a
``multiple`` enum setting (checkboxes: tree, geometry, attributes, node bounds, ...). Each collection
then runs one provider job PER TICKED TARGET, in the enum's declared order, each answer published
before the next job starts -- one item, one lane. A target that fails fails only itself; one that
reads what it publishes (``target_requires``, by default node bounds after the tree) is skipped
with the reason, and the others still run. The item keeps each target's outcome under
``timings.targets`` (read as the item's ``targets``). A stored single value is a list of one; a
provider whose setting is still a single choice runs one job per collection, as before.

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
from typing import Any

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
    sweep_targets,
    target_requires,
    targets_setting,
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
    summary: dict | None = None,
) -> dict:
    """Record a sweep and start driving it. Returns the run. Raises :class:`SweepRefused`.

    ``summary`` seeds the run's run-level facts -- the full export an apply-changes run queues
    carries ``apply_changes_run_id``.

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
    # No one-run-per-provider guard any more: runs of one provider go side by side, a collection
    # claimed by one run at a time and the provider's workers shared (module docstring).
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
        **({"summary": summary} if summary else {}),
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


# --- the provider's shared worker budget ------------------------------------------------------------

#: ``app_settings`` key of an admin's cap on a provider's concurrent jobs across all its runs.
MAX_PARALLEL_SETTING = "asset_runs.max_parallel.{provider}"
#: How long a computed cap is trusted: workers come and go, so it is recomputed this often.
CAP_TTL_S = 30.0
_CAPS: dict[str, tuple[float, dict]] = {}


async def live_workers(ctx: RestContext, spec: dict) -> int | None:
    """How many live workers serve the provider's capability; None when the fleet is unknown."""
    cap = str(spec.get("worker_capability") or "").strip().lower()
    try:
        rows = await ctx.jobs.capabilities()
    except Exception:  # noqa: BLE001 - unknown fleet
        return None
    return sum(1 for r in rows if cap and cap in {str(c).lower() for c in (r.get("capabilities") or ())})


def declared_parallel(spec: dict) -> int:
    """The most collections any of the provider's runs may work on at once, as it declares
    (``parallel`` on its sweep / apply-changes entries; 1 -- one licensed seat -- when none does)."""
    from .asset_schedules import schedule_entries

    best = 1
    for e in schedule_entries(spec):
        try:
            best = max(best, min(int(e.get("parallel") or 1), MAX_SWEEP_WIDTH))
        except (TypeError, ValueError):
            continue
    return best


async def admin_max_parallel(pool, provider: str) -> int | None:
    """An admin's cap on ``provider``'s concurrent jobs (``MAX_PARALLEL_SETTING``), or None."""
    try:
        raw = await db_module.get_setting(pool, MAX_PARALLEL_SETTING.format(provider=provider))
        value = int(raw) if raw not in (None, "") else None
    except Exception:  # noqa: BLE001 - unreadable: no admin cap
        return None
    return value if value and value > 0 else None


async def provider_budget(ctx: RestContext, pool, provider: str, spec: dict, *, fresh: bool = False) -> dict:
    """``{cap, live_workers, declared, max_parallel}`` -- how many provider jobs ALL of ``provider``'s
    runs may have in flight together. The default is what ``sweep_width`` allows one run: its
    declared ``parallel`` (1 for a single seat) capped at the live workers serving it; an admin's
    ``max_parallel`` may lower it, never raise it. Recomputed every ``CAP_TTL_S`` (workers come and
    go while a run lasts hours), or now with ``fresh``."""
    now = time.monotonic()
    hit = _CAPS.get(provider)
    if hit is not None and not fresh and now - hit[0] < CAP_TTL_S:
        return hit[1]
    declared = declared_parallel(spec)
    workers = await live_workers(ctx, spec)
    cap = 1 if declared == 1 or workers is None else max(1, min(declared, workers))
    admin = await admin_max_parallel(pool, provider)
    if admin is not None:
        cap = max(1, min(cap, admin))
    out = {"cap": cap, "live_workers": workers, "declared": declared, "max_parallel": admin}
    _CAPS[provider] = (now, out)
    return out


class worker_slot:
    """``async with worker_slot(...)``: hold one of the provider's worker slots for one provider job.

    Taken in the DATABASE (``acquire_asset_worker_slot``: a per-provider advisory lock and a count of
    the slots the provider's live runs hold), so the budget is one budget across every run and every
    API replica. While none is free the item says so and the lane polls -- heartbeating the run, and
    giving up on a cancel or a skip of the item. Released when the job is done; publishes run on
    core's own workers, not the provider's, and take no slot."""

    def __init__(self, ctx: RestContext, pool, run: dict, position: int, spec: dict, item=None):
        self.ctx, self.pool, self.run, self.position, self.spec, self.item = ctx, pool, run, position, spec, item

    async def __aenter__(self) -> "worker_slot":
        provider, run_id = self.run["provider"], self.run["id"]
        said = False
        while True:
            budget = await provider_budget(self.ctx, self.pool, provider, self.spec)
            if await db_module.acquire_asset_worker_slot(
                self.pool, run_id, self.position, provider=provider, cap=budget["cap"]
            ):
                if said and self.item is not None:
                    await self.item(message=None)
                return self
            if not said and self.item is not None:
                await self.item(message=f"waiting for a free {provider} worker (all {budget['cap']} busy)")
                said = True
            beat = await db_module.heartbeat_asset_sweep_run(self.pool, run_id)
            if beat != "running":
                raise _Cancelled
            if getattr(beat, "skip_position", None) == self.position:
                raise ItemSkipped(getattr(beat, "skip_by", None))
            await asyncio.sleep(POLL_INTERVAL_S)

    async def __aexit__(self, *exc) -> None:
        try:
            await db_module.release_asset_worker_slot(self.pool, self.run["id"], self.position)
        except Exception:  # noqa: BLE001 - a stale slot frees itself when the run stops beating
            logger.warning("asset sweep %s: could not release a worker slot", self.run["id"], exc_info=True)


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
    queue = iter(list(enumerate(collections)))
    # One collection's work: a sweep fetches and publishes it whole; an apply-changes run asks for
    # what changed since core's cursor and applies the patches (routes/asset_apply_changes.py). The
    # lanes, heartbeat, cancel, skip and resume around it are the same.
    one = _sweep_one
    apply_state = None
    if entry.get("kind") == APPLY_CHANGES:
        from .asset_apply_changes import ApplyRunState, apply_one

        # What every collection of the run shares: the store's listing of full exports (read once)
        # and the collections left out for want of one.
        apply_state = ApplyRunState()

        async def one(*args, **kw) -> bool:
            return await apply_one(*args, run_state=apply_state, **kw)

    # Collections another live run is working on wait here (`waiting`) and are tried again once the
    # lanes have nothing else to start; one still held after BUSY_WAIT_S ends `busy`.
    deferred: dict[int, tuple[str, float]] = {}
    busy_wait = fetch_timeout(entry) + PUBLISH_TIMEOUT_S

    def next_up() -> tuple[int, str] | None:
        for pair in queue:
            return pair
        return None

    async def lane() -> None:
        # Lanes share one iterator, so collections still START in order; with one lane this is
        # exactly the sequential sweep.
        nonlocal failed
        while True:
            pair = next_up()
            retry = pair is None
            if retry:
                if not deferred:
                    return
                # Nothing new to start: try the waiting ones again, after a poll's pause.
                await asyncio.sleep(POLL_INTERVAL_S)
                position = next(iter(deferred), None)
                if position is None:
                    continue
                collection, since = deferred.pop(position)
            else:
                position, collection = pair
            if await db_module.heartbeat_asset_sweep_run(pool, run_id) != "running":
                raise _Cancelled
            # Claimed under a per-(provider, collection) lock (db): read now, not at start -- the
            # item may have been ticked off since, and another run may hold the collection.
            claim = await db_module.claim_asset_sweep_item(
                pool, run_id, position, provider=provider, collection=collection
            )
            if not claim["claimed"]:
                holder = claim.get("holder")
                if holder is None:
                    continue  # ticked off (or otherwise no longer to run)
                started = since if retry else time.monotonic()
                text = (
                    f"{collection} is being processed by run {holder['run_id']} ({holder['job']}, started "
                    f"{holder['created_at']})"
                )
                if time.monotonic() - started > busy_wait:
                    await db_module.update_asset_sweep_item(
                        pool, run_id, position, status="busy", message=text, finished_at=datetime.now(timezone.utc)
                    )
                    continue
                if not retry:
                    await db_module.update_asset_sweep_item(
                        pool, run_id, position, status="waiting", message=f"waiting: {text}"
                    )
                deferred[position] = (collection, started)
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
    if apply_state is not None:
        # After the finish: a follow-up full export of the collections it left out.
        from .asset_apply_changes import after_apply_run

        try:
            await after_apply_run(ctx, pool, run, apply_state, user=user)
        except Exception:  # noqa: BLE001 - the run itself is finished and recorded
            logger.exception("apply changes %s: the follow-up after the run failed", run_id)


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
    """Fetch and publish one collection. Returns False when it failed; raises only _Cancelled.

    A sweep with a TARGETS setting (``targets_setting``: a ``multiple`` enum, e.g. ``sweep_target``)
    runs one provider job per ticked target, in the enum's declared order, each published before the
    next starts, all in this item (:func:`_sweep_targets`)."""
    run_id = run["id"]
    t_item = time.monotonic()
    timings: dict = {}

    async def item(**fields) -> None:
        await db_module.update_asset_sweep_item(pool, run_id, position, **fields)

    async def finish(status: str, **fields) -> None:
        timings["total_ms"] = _ms(time.monotonic() - t_item)
        await item(status=status, timings=timings, finished_at=datetime.now(timezone.utc), **fields)

    await item(status="fetching", started_at=datetime.now(timezone.utc))
    kw = dict(scope_obj=scope_obj, spec=spec, entry=entry, user=user, item=item)
    try:
        targets = sweep_targets(spec, entry, run["settings"])
        if targets is None:
            status, fields = await _fetch_and_publish(
                ctx, pool, run, position, collection, settings=run["settings"], timings=timings, **kw
            )
            await finish(status, **fields)
            return status != "error"
        status, fields = await _sweep_targets(ctx, pool, run, position, collection, targets, timings=timings, **kw)
        await finish(status, **fields)
        return status != "error"
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


async def _sweep_targets(
    ctx: RestContext,
    pool,
    run: dict,
    position: int,
    collection: str,
    targets: list,
    *,
    timings: dict,
    scope_obj: Scope,
    spec: dict,
    entry: dict,
    user,
    item,
) -> tuple[str, dict]:
    """One collection, one provider job per target, in order, each published before the next.
    Returns the item's ``(status, fields)``; raises only a cancel or a skip of the item.

    A target that fails is that target's failure, not the collection's end: the rest still run --
    except those that read what it publishes (:func:`target_requires`: node bounds read the
    published tree), which are skipped with the reason. Each target's status, revision, message or
    error, timings and publish outcome go under ``timings.targets.<target>``; the item's own
    ``queued_ms`` / ``fetch_ms`` / ``publish_ms`` are the sums, so the phase columns still add up."""
    name = targets_setting(spec, entry)
    requires = target_requires(entry)
    per: dict[str, dict] = {}
    timings["targets"] = per
    failed: list[str] = []
    blocked: set[str] = set()
    revision = None
    for target in targets:
        key = str(target)
        missing = [r for r in requires.get(key, ()) if r in blocked]
        if missing:
            per[key] = {"status": "skipped", "message": f"not run: {', '.join(missing)} did not publish in this run"}
            blocked.add(key)
            await item(timings=timings)
            continue
        own: dict = {}
        per[key] = {"status": "fetching"}
        await item(status="fetching", timings=timings)
        try:
            status, fields = await _fetch_and_publish(
                ctx,
                pool,
                run,
                position,
                collection,
                settings={**(run.get("settings") or {}), name: target},
                timings=own,
                scope_obj=scope_obj,
                spec=spec,
                entry=entry,
                user=user,
                item=item,
                target=(name, target),
            )
        except (_Cancelled, ItemSkipped):
            per[key] = {"status": "skipped", "message": "the collection was stopped", "timings": own}
            raise
        except HTTPException as exc:
            status, fields = "error", {"error": f"{exc.status_code}: {exc.detail}"}
        except Exception as exc:  # noqa: BLE001 - this target's failure; the others still run
            logger.warning("asset sweep %s: %s %s failed: %s", run["id"], collection, key, exc)
            status, fields = "error", {"error": str(exc)}
        per[key] = {"status": status, **{k: v for k, v in fields.items() if v is not None}, "timings": own}
        for phase in ("queued_ms", "fetch_ms", "publish_ms"):
            if phase in own:
                timings[phase] = timings.get(phase, 0) + own[phase]
        if status == "error":
            failed.append(key)
            blocked.add(key)
        revision = fields.get("revision") or revision
        await item(timings=timings)
    line = " · ".join(f"{k}: {v['status']}" for k, v in per.items())
    if failed:
        errors = "; ".join(f"{k}: {per[k].get('error')}" for k in failed)
        return "error", {
            "revision": revision,
            "message": line,
            "error": f"{len(failed)} of {len(targets)} target(s) failed: {errors}",
        }
    statuses = {v["status"] for v in per.values()}
    status = "unchanged" if statuses == {"unchanged"} else "nothing" if statuses == {"nothing"} else "done"
    return status, {"revision": revision, "message": line}


async def _fetch_and_publish(
    ctx: RestContext,
    pool,
    run: dict,
    position: int,
    collection: str,
    *,
    settings: dict,
    timings: dict,
    scope_obj: Scope,
    spec: dict,
    entry: dict,
    user,
    item,
    target: tuple[str, Any] | None = None,
) -> tuple[str, dict]:
    """One provider job for one collection and what it staged, published. Returns ``(status,
    fields)`` -- ``done`` / ``unchanged`` / ``nothing``, or ``error`` when a staged part failed to
    publish -- and raises when the job or the only publish failed. ``target`` sends the targets
    setting as that ONE value (the provider's job does one target)."""
    run_id, provider, plugin_id = run["id"], run["provider"], run["plugin_id"]
    options = await build_options(
        pool,
        spec,
        entry,
        scope=scope_obj.prefix(),
        provider=provider,
        collection=collection,
        settings=settings,
        validate=False,
    )
    if target is not None:
        options[target[0]] = target[1]
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
    # One of the provider's worker slots, shared by all its runs, for as long as its job runs.
    async with worker_slot(ctx, pool, run, position, spec, item):
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
        return "unchanged", {"revision": summary.get("revision"), "message": summary.get("message")}
    if summary.get("asset_nothing") is True:
        return "nothing", {"message": summary.get("message")}
    staging_ids = staging_ids_of(summary)
    if not staging_ids:
        raise RuntimeError("the provider's job finished without an asset_staging_id, so there is nothing to publish")

    # Several staged parts (a provider that stages a collection in pieces) are published one
    # after another; one that fails is counted and the rest still publish.
    await item(status="publishing")
    t_pub = time.monotonic()
    revision = None
    subjects = 0
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
        subjects += len(outcome.get("subjects") or ())
    timings["publish_ms"] = _ms(time.monotonic() - t_pub)
    if len(staging_ids) > 1:
        timings["published_parts"] = len(staging_ids) - len(publish_failed)
    if publish_failed:
        return "error", {
            "revision": revision,
            "error": f"{len(publish_failed)} of {len(staging_ids)} staged part(s) failed to publish: "
            + "; ".join(publish_failed[:3]),
        }
    # `subjects` is recorded per target only (a sweep item's columns are fixed).
    return "done", {"revision": revision, **({"subjects": subjects} if target is not None else {})}


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


@admin_router.get("/asset-runs/max-parallel")
async def admin_asset_runs_max_parallel(
    request: Request, provider: str, ctx: RestContext = Depends(rest_context)
) -> JSONResponse:
    """``{provider, max_parallel, cap, live_workers, declared, in_use}``: how many provider jobs all of
    ``provider``'s runs may have in flight together (``cap``), what it is made of -- the live workers
    serving it, the most it declares, an admin's ``max_parallel`` (null: none) -- and how many slots
    are taken now."""
    pool = require_pool(request)
    specs = await advertised_provider_specs(ctx, provider)
    if specs:
        budget = await provider_budget(ctx, pool, provider, specs[0], fresh=True)
    else:
        budget = {
            "cap": None,
            "live_workers": 0,
            "declared": None,
            "max_parallel": await admin_max_parallel(pool, provider),
        }
    return JSONResponse(
        {"provider": provider, **budget, "in_use": await db_module.asset_worker_slots_in_use(pool, provider=provider)}
    )


@admin_router.put("/asset-runs/max-parallel")
async def admin_asset_runs_max_parallel_set(
    request: Request, user: User = Depends(auth_module.current_user), ctx: RestContext = Depends(rest_context)
) -> JSONResponse:
    """Body: ``{provider, max_parallel: n | null}`` -- lower the provider's shared cap to ``n`` (never
    above what its live workers allow), or ``null`` for "all live workers". Applies within ``CAP_TTL_S``
    to runs in flight too."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str(body.get("provider") or "").strip()
    value = body.get("max_parallel")
    if not provider or not (value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1)):
        raise HTTPException(
            status_code=400, detail="provider and max_parallel (a whole number >= 1, or null) are required"
        )
    await db_module.set_setting(
        pool,
        MAX_PARALLEL_SETTING.format(provider=provider),
        "" if value is None else str(min(value, MAX_SWEEP_WIDTH * 4)),
        updated_by=user_label(user),
    )
    _CAPS.pop(provider, None)
    return await admin_asset_runs_max_parallel(request, provider, ctx)


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
        # free column); it is read as the item's own: `apply_changes` and `roles`. So does a
        # multi-target sweep item its per-target outcome: `targets`.
        timings = item.get("timings") or {}
        for name in ("apply_changes", "roles", "targets"):
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

    The provider must be online and declare the sweep still -- the guards a new sweep has. Other
    runs of the provider may be running: a collection one of them holds waits, as in any run."""
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
