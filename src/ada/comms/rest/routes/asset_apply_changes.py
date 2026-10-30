"""Apply changes -- the ``apply-changes`` kind of ``asset_schedules``: only what changed, per element.

The contract is ``docs/design/apply-changes.md``. A provider that can answer "what changed since a
cursor" per element declares::

    {"id": "apply-changes", "kind": "apply-changes", "label": "...",
     "options": {"action": "apply-changes", "schema_version": N},  # fixed job options
     "collection_option": "project",
     "settings": [{"name": "apply_targets"}, {"name": "exclude_users", "choices": "change_users"}],
     "parallel": 5, "fetch_timeout_s": 3600}

with ``apply_targets`` a MULTI choice in its ``job_options`` (``"multiple": true``; never empty).

DRIVEN AS A SWEEP. Firing it -- its schedule, "Run now", or a one-off start from Admin -> Providers
-- records an ordinary sweep run (``asset_sweep_runs``, its ``job`` this entry) with one item per
collection: the schedule's one collection, or every collection the provider offers. The sweep
driver (``routes/asset_sweeps.py``) runs the lanes (``parallel``, capped at the live workers), the
heartbeat, cancel, skip and resume, and hands each collection to :func:`apply_one` instead of the
sweep's fetch-and-publish. It runs beside the provider's other runs, sweeps included: one collection
is worked on by one run at a time, and all of them share the provider's worker slots.

PER COLLECTION, CORE OWNS THE CURSOR (``asset_apply_cursors``, migration 046):

1. read the cursor -- none means this is the FIRST run, and ``since`` is null;
2. enqueue the provider's job in the provider store with the fixed options + the collection +
   ``apply_targets`` + ``since`` + ``exclude_users`` + ``requested_at``, and wait for it;
3. read its summary and check the ``apply_changes`` block (``ada.assets/apply-changes@1``);
4. when it staged something (``asset_staging_id``), publish that through ``submit_asset_publish``
   in the store, as the provider, and wait: the geometry of the changed elements goes through the
   provider's publisher, the patches through core (``formats/asset_publish.py``), each role's
   outcome in the publish summary's ``patches``;
5. then -- and only if every step succeeded and no patch FAILED -- store ``next_cursor``.

A baseline (``since`` null) or a ``nothing`` answer advances the cursor with no publish. A bounds
or relations patch REFUSED for want of a baseline set is a warning, not a failure: it can never
apply until a full export builds the set, and that export carries the change anyway. Anything else that goes wrong
leaves the cursor where it was and the item ``error``: the next run asks the same question again,
which is safe because every patch is an idempotent upsert or delete.

NO FULL EXPORT, NO APPLY. Applying changes presumes the collection is already in the store. Before
step 1, core checks that the provider store holds a complete revision of the collection's INDEX
subject published by this provider (:func:`has_full_export`, one listing per run). A collection
without one is never sent to the provider and gets no cursor: its item ends ``no_full_export``
("<collection> has not had a full project export yet") and the run's ``warnings`` name it. The same
happens -- and the cursor stays -- when the provider itself answers that it found no index, or core
refuses the tree or index patch for want of one. Core's own setting ``missing_full_export`` (every
apply-changes entry carries it; never sent to the provider) chooses ``warn`` (the default) or
``export``: when the run has finished, start the provider's full export of exactly those
collections (:func:`after_apply_run`), the two runs linked through their ``summary``.

WHAT AN ITEM RECORDS. The sweep tables have one free JSON column per item, ``timings``; beside the
phases (``queued_ms``, ``fetch_ms``, ``publish_ms``, ``total_ms``, the provider's own ``provider``
phases, the publish's ``publish``) it holds ``apply_changes`` -- the answer's counts, targets,
since / next_cursor, baseline / nothing, and whether the cursor moved -- and ``roles``, the publish
outcome per staged role (``{status: applied|refused|failed|skipped, warning?, upserted?,
deleted?}``). The run read (``GET /asset-sweeps/{id}``) lifts both onto the item.

NO SCOPE, ADMIN ONLY: as every provider job (``routes/provider_store.py``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.assets.keys import ASSET_PREFIX, STAGING_SEGMENT
from ada.assets.manifest import MANIFEST_FILENAME
from ada.config import logger

from .. import db as db_module
from ..db.attribute_index import ATTRIBUTE_INDEX_PATCH_ROLE, ATTRIBUTE_INDEX_ROLE
from ..db.node_bounds import NODE_BOUNDS_PATCH_ROLE, NODE_BOUNDS_ROLE
from ..db.node_relations import NODE_RELATIONS_PATCH_ROLE, NODE_RELATIONS_ROLE
from ..run_driver import ItemSkipped, RunCancelled, json_of, ms
from .asset_schedules import (
    APPLY_TARGETS,
    MISSING_FULL_EXPORT,
    MISSING_FULL_EXPORT_EXPORT,
    MISSING_FULL_EXPORT_WARN,
    PREBUILD_MESHES,
    build_options,
    entry_settings,
    job_keys,
)
from .asset_prebuild import ANCESTORS_NOTE, not_attempted, prebuild, prebuild_line, prebuild_wanted
from .assets import staged_by_role, submit_asset_publish
from .deps import require_pool
from .plugin_jobs import enqueue_plugin_job

admin_router = APIRouter()

APPLY_CHANGES_SCHEMA = "ada.assets/apply-changes@1"
#: The patch roles and every core-handled role, as ``formats/asset_publish.py`` names them --
#: spelled out here because the API image does not ship the publish handler (a test keeps the two
#: in step).
PATCH_ROLES = ("hierarchy-patch.json", NODE_RELATIONS_PATCH_ROLE, ATTRIBUTE_INDEX_PATCH_ROLE, NODE_BOUNDS_PATCH_ROLE)
CORE_ROLES = frozenset({ATTRIBUTE_INDEX_ROLE, NODE_BOUNDS_ROLE, NODE_RELATIONS_ROLE, *PATCH_ROLES})
#: The element counts an answer reports (each at its own level, never its level or SITE).
COUNT_KEYS = ("changed", "inserted", "deleted", "unplaceable")
#: The item status of a collection left out because the store holds no full export of it.
NO_FULL_EXPORT = "no_full_export"
#: Patch roles whose refusal means the collection has no full export at all (no index, no tree):
#: the cursor must not move past changes nothing could hold. A refused bounds or relations patch
#: only means that one set was never exported -- a warning, and the cursor moves.
INDEX_ROLES = ("hierarchy-patch.json", ATTRIBUTE_INDEX_PATCH_ROLE)
#: ``requested_via`` of the full export an apply-changes run queues for what it left out.
VIA_APPLY_CHANGES = "apply-changes"
#: Revisions of the index subject read, newest first, before "no full export by this provider".
MAX_REVISIONS_READ = 50


def no_full_export_message(collection: str) -> str:
    return f"{collection} has not had a full project export yet"


@dataclass
class ApplyRunState:
    """What the collections of one apply-changes run share: the provider store's collection names
    (one listing per run, lower-case -> the store's own spellings), each collection's answer to
    "has a full export" (cached), and the collections left out for want of one."""

    spellings: dict[str, list[str]] | None = None
    full: dict[str, bool] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def has_full_export(ctx, scope_obj, *, provider: str, collection: str, state: ApplyRunState) -> bool:
    """Whether the provider store holds a FULL export of ``collection`` by ``provider``: a complete
    revision of the collection's INDEX subject -- ``assets/<collection>/<collection>/<rev>/`` with
    its manifest, which lands last, so its presence is completeness -- whose manifest names this
    provider. The collection is matched case-insensitively against the store's own spelling (a
    provider may offer ``ALPHA`` and have published ``alpha``). Cached per run."""
    key = collection.lower()
    if key in state.full:
        return state.full[key]
    async with state.lock:
        if key in state.full:
            return state.full[key]
        if state.spellings is None:
            names = await ctx.storage.list_child_names(scope_obj, f"{ASSET_PREFIX}/")
            spellings: dict[str, list[str]] = {}
            for name in names:
                if name != STAGING_SEGMENT:
                    spellings.setdefault(name.lower(), []).append(name)
            state.spellings = spellings
        found = False
        for stored in state.spellings.get(key, ()):
            subjects = await ctx.storage.list_child_names(scope_obj, f"{ASSET_PREFIX}/{stored}/")
            for subject in (s for s in subjects if s.lower() == key):
                prefix = f"{ASSET_PREFIX}/{stored}/{subject}/"
                revisions = sorted(await ctx.storage.list_child_names(scope_obj, prefix), reverse=True)
                for revision in revisions[:MAX_REVISIONS_READ]:
                    try:
                        doc = json_of(await ctx.storage.get_bytes(scope_obj, f"{prefix}{revision}/{MANIFEST_FILENAME}"))
                    except Exception:  # noqa: BLE001 - no manifest: an incomplete revision
                        continue
                    if isinstance(doc, dict) and doc.get("provider") == provider:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        state.full[key] = found
        return found


def _mode(run: dict) -> str:
    value = (run.get("settings") or {}).get(MISSING_FULL_EXPORT)
    return MISSING_FULL_EXPORT_EXPORT if value == MISSING_FULL_EXPORT_EXPORT else MISSING_FULL_EXPORT_WARN


async def _left_out(pool, run: dict, collection: str, state: ApplyRunState, reason: str | None = None) -> str:
    """Record ``collection`` as left out of the run for want of a full export: a run-level warning
    naming it (logged at WARNING), and the message its item ends with."""
    text = no_full_export_message(collection)
    if collection not in state.missing:
        state.missing.append(collection)
    logger.warning("apply changes %s: %s%s", run["id"], text, f" ({reason})" if reason else "")
    try:
        await db_module.add_asset_sweep_run_warnings(pool, run["id"], [text])
    except Exception:  # noqa: BLE001 - the item still says it
        logger.exception("apply changes %s: could not record the run warning", run["id"])
    message = text + (f" ({reason})" if reason else "")
    if _mode(run) == MISSING_FULL_EXPORT_EXPORT:
        message += "; a full project export is queued when this run ends"
    return message + "; not asked for changes, no cursor"


def _provider_says_no_baseline(summary: dict) -> bool:
    """The provider's own "no index for this collection" answer (the E3D provider warns ``no baseline
    for collection ...`` when the store holds no tree it could patch)."""
    return any("no baseline" in str(w).lower() for w in summary.get("warnings") or ())


class AnswerError(ValueError):
    """The provider's summary is not an ``ada.assets/apply-changes@1`` answer core can act on."""


def read_answer(summary: Any) -> dict:
    """The ``apply_changes`` block of a job summary, checked and normalised. Raises
    :class:`AnswerError` -- and then nothing is applied and the cursor stays.

    ``next_cursor`` is required: it is the only thing the run stores, and an answer without one
    cannot say where the next run starts."""
    if not isinstance(summary, dict):
        raise AnswerError("the provider's job answered without a summary")
    block = summary.get("apply_changes")
    if not isinstance(block, dict):
        raise AnswerError(
            "the job answered without an apply_changes block -- the provider does not apply changes in this version"
        )
    if block.get("schema") != APPLY_CHANGES_SCHEMA:
        raise AnswerError(f"the apply_changes block names schema {block.get('schema')!r}, not {APPLY_CHANGES_SCHEMA!r}")
    next_cursor = block.get("next_cursor")
    if not isinstance(next_cursor, str) or not next_cursor.strip():
        raise AnswerError("the apply_changes block names no next_cursor")
    raw_counts = block.get("counts") if isinstance(block.get("counts"), dict) else {}
    counts = {}
    for k in COUNT_KEYS:
        v = raw_counts.get(k, 0)
        counts[k] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    targets = block.get("targets")
    return {
        "since": block.get("since") if isinstance(block.get("since"), str) else None,
        "next_cursor": next_cursor.strip(),
        "baseline": block.get("baseline") is True,
        "nothing": block.get("nothing") is True,
        "targets": [str(t) for t in targets] if isinstance(targets, list) else [],
        "counts": counts,
    }


def role_outcomes(staged: dict, publish: dict) -> dict[str, dict]:
    """``{role: outcome}`` for one publish: every patch role as the publish job reported it
    (``patches``), and every other staged role -- the provider's own publish input, the geometry --
    ``applied`` with the subjects it published. A patch role the summary does not mention was not
    applied (an older publish handler): ``failed``, so the cursor stays."""
    reported = publish.get("patches") if isinstance(publish.get("patches"), dict) else {}
    out: dict[str, dict] = {}
    for role in staged:
        if role in PATCH_ROLES:
            got = reported.get(role)
            if not isinstance(got, dict):
                out[role] = {"status": "failed", "error": "the publish did not report applying this patch"}
                continue
            outcome = {k: got[k] for k in ("status", "warning", "error", "upserted", "deleted") if k in got}
            outcome.setdefault("status", "failed")
            out[role] = outcome
        elif role not in CORE_ROLES:
            out[role] = {"status": "applied", "subjects": len(publish.get("subjects") or [])}
    return out


def _message(answer: dict, *, advanced: bool, refused: list[str]) -> str:
    if answer["baseline"]:
        text = f"baseline: changes are applied from {answer['next_cursor']} on"
    elif answer["nothing"]:
        text = f"nothing changed since {answer['since'] or 'the last run'}"
    else:
        c = answer["counts"]
        text = f"{c['changed']} changed, {c['inserted']} inserted, {c['deleted']} deleted"
        if c["unplaceable"]:
            text += f", {c['unplaceable']} unplaceable"
    if refused:
        text += f"; refused (no baseline): {', '.join(refused)}"
    return text + (f"; cursor -> {answer['next_cursor']}" if advanced else "")


def _targets(spec: dict, entry: dict, options: dict) -> list:
    """The targets to send: as chosen (a list), else the declared default, else every declared one."""
    chosen = options.get(APPLY_TARGETS)
    if isinstance(chosen, list) and chosen:
        return chosen
    if chosen not in (None, "", []):
        return [chosen]
    for s in entry_settings(spec, entry):
        if s["name"] == APPLY_TARGETS:
            if isinstance(s.get("default"), list) and s["default"]:
                return list(s["default"])
            return [c["value"] for c in s.get("choices") or ()]
    return []


async def apply_one(
    ctx,
    pool,
    run: dict,
    position: int,
    collection: str,
    *,
    scope_obj,
    spec: dict,
    entry: dict,
    user,
    run_state: ApplyRunState | None = None,
) -> bool:
    """Ask for, and apply, one collection's changes since core's cursor. Returns False when it
    failed (the cursor then stays); raises only ``RunCancelled``. The sweep driver's per-item hook.

    A collection the provider store holds no full export of (:func:`has_full_export`) is NOT asked:
    its item ends ``no_full_export``, the run warns, and no cursor is made. The same when the
    provider answers that it found no index, or core refuses the index or tree patch for want of
    one -- then the cursor does not move either."""
    from . import asset_sweeps as sweeps

    state = run_state if run_state is not None else ApplyRunState()
    run_id, provider, plugin_id = run["id"], run["provider"], run["plugin_id"]
    t_item = time.monotonic()
    timings: dict = {}

    async def item(**fields) -> None:
        await db_module.update_asset_sweep_item(pool, run_id, position, **fields)

    async def finish(status: str, **fields) -> None:
        timings["total_ms"] = ms(time.monotonic() - t_item)
        await item(status=status, timings=timings, finished_at=datetime.now(timezone.utc), **fields)

    await item(status="fetching", started_at=datetime.now(timezone.utc))
    try:
        if not await has_full_export(ctx, scope_obj, provider=provider, collection=collection, state=state):
            await finish(NO_FULL_EXPORT, message=await _left_out(pool, run, collection, state))
            return True
        held = await db_module.get_asset_apply_cursor(pool, provider=provider, collection=collection)
        since = held["cursor"] if held else None
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
        # Core's own setting: decided here, never sent to the provider.
        options.pop(MISSING_FULL_EXPORT, None)
        options.pop(PREBUILD_MESHES, None)
        # The admin's per-collection pick (a share, say), as in any sweep.
        options = await sweeps.with_collection_choices(pool, run, options, spec, collection, timings)
        # The collection's saved provider options UNDER the entry's, as a sweep sends them: they
        # say how to open the collection, never which job this is. `requested_at` makes it a new
        # job, never a cache hit on the last run's answer.
        options = {
            **await sweeps._provider_options(ctx, scope_obj, provider, collection),
            **options,
            APPLY_TARGETS: _targets(spec, entry, options),
            "since": since,
            "exclude_users": options.get("exclude_users") or [],
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        timings["apply_changes"] = {"since_sent": since, "targets_sent": options[APPLY_TARGETS], "advanced": False}
        derived_key, _ = job_keys(plugin_id, options)
        # One of the provider's worker slots, shared by all its runs, while the job runs.
        async with sweeps.worker_slot(ctx, pool, run, position, spec, item):
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
            timings["queued_ms"], timings["fetch_ms"] = await sweeps._wait(
                ctx, pool, run_id, fetch_job, sweeps.fetch_timeout(entry), position=position
            )
        summary = json_of(await ctx.storage.get_bytes(scope_obj, derived_key))
        answer = read_answer(summary)
        timings["apply_changes"] = {**timings["apply_changes"], **answer}
        if isinstance(summary.get("timings"), dict):
            timings["provider"] = summary["timings"]
        if isinstance(summary.get("login"), dict):
            timings["login"] = summary["login"]
        if _provider_says_no_baseline(summary):
            # The provider found no index to patch: what core's own check should have caught (a
            # store written behind its back, a check that raced a deletion). Same outcome.
            reason = "the provider found no published index"
            await finish(NO_FULL_EXPORT, message=await _left_out(pool, run, collection, state, reason))
            return True

        staging_ids = sweeps.staging_ids_of(summary)
        if not staging_ids and not (answer["baseline"] or answer["nothing"]):
            c = answer["counts"]
            if c["changed"] + c["inserted"] + c["deleted"]:
                raise RuntimeError("the provider counted changes but staged nothing to apply")

        roles: dict[str, dict] = {}
        revision = None
        published_subjects: list[str] = []
        published_collection: str | None = None
        if staging_ids:
            await item(status="publishing")
            t_pub = time.monotonic()
            for staging_id in staging_ids:
                staged = await staged_by_role(ctx, scope_obj, staging_id)
                publish_job, publish_key = await submit_asset_publish(
                    ctx, scope_obj, provider=provider, staged=staged, user=user
                )
                await item(publish_job_id=publish_job)
                await sweeps._wait(ctx, pool, run_id, publish_job, sweeps.PUBLISH_TIMEOUT_S, position=position)
                outcome = json_of(await ctx.storage.get_bytes(scope_obj, publish_key))
                outcome = outcome if isinstance(outcome, dict) else {}
                if isinstance(outcome.get("timings"), dict) and len(staging_ids) == 1:
                    timings["publish"] = outcome["timings"]
                revision = outcome.get("revision") or revision
                roles.update(role_outcomes(staged, outcome))
                published_subjects.extend(str(s) for s in outcome.get("subjects") or ())
                published_collection = outcome.get("collection") or published_collection
            timings["publish_ms"] = ms(time.monotonic() - t_pub)
            timings["roles"] = roles

        not_applied = [r for r, o in roles.items() if o.get("status") not in ("applied", "refused")]
        if not_applied:
            details = "; ".join(
                f"{r}: {roles[r].get('error') or roles[r].get('warning') or roles[r]['status']}" for r in not_applied
            )
            await finish("error", revision=revision, error=f"not applied, cursor kept: {details}")
            return False
        no_index = [r for r in INDEX_ROLES if (roles.get(r) or {}).get("status") == "refused"]
        if no_index:
            reason = f"refused for want of a baseline: {', '.join(no_index)}"
            await finish(
                NO_FULL_EXPORT, revision=revision, message=await _left_out(pool, run, collection, state, reason)
            )
            return True

        await db_module.set_asset_apply_cursor(
            pool, provider=provider, collection=collection, cursor=answer["next_cursor"], run_id=run_id
        )
        timings["apply_changes"]["advanced"] = True
        refused = [r for r, o in roles.items() if o.get("status") == "refused"]
        message = _message(answer, advanced=True, refused=refused)
        if prebuild_wanted(run.get("settings")):
            # "Pre-build meshes": the element subjects this publish created (small, one each). The
            # ancestors' builds -- whose excluded descendants just changed -- rebuild on demand.
            targets = [
                {"collection": published_collection or collection, "subject": s, "provider": provider}
                for s in dict.fromkeys(published_subjects)
            ]
            if targets:
                await item(message="pre-building meshes")
                timings["prebuild"] = await prebuild(
                    ctx, pool, run, position, targets, user=user, poll_s=sweeps.POLL_INTERVAL_S
                )
            else:
                timings["prebuild"] = not_attempted("no subject was published")
            timings["prebuild"]["note"] = ANCESTORS_NOTE
            message = f"{message} · {prebuild_line(timings['prebuild'])}"
        await finish("nothing" if answer["nothing"] and not roles else "done", revision=revision, message=message)
        return True
    except RunCancelled:
        await finish("skipped", message="the run was cancelled")
        raise
    except ItemSkipped as exc:
        await db_module.clear_asset_sweep_skip(pool, run_id, position)
        moved = (timings.get("apply_changes") or {}).get("advanced")
        await finish("skipped", message=f"{exc}; " + ("changes applied, pre-build stopped" if moved else "cursor kept"))
        logger.info("apply changes %s: %s %s", run_id, collection, exc)
        return True
    except HTTPException as exc:
        await finish("error", error=f"{exc.status_code}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - one collection's failure is its row's, not the run's
        logger.warning("apply changes %s: %s failed: %s", run_id, collection, exc)
        await finish("error", error=str(exc))
    return False


async def _queued_elsewhere(pool, provider: str) -> set[str]:
    """Collections (lower-case) a RUNNING full-export follow-up of ``provider`` has not finished."""
    from .asset_sweeps import COLLECTION_REQUEST

    out: set[str] = set()
    for r in await db_module.list_asset_sweep_runs(pool, provider=provider, limit=50):
        if r["status"] != "running" or r.get("requested_via") != VIA_APPLY_CHANGES or r["job"] != COLLECTION_REQUEST:
            continue
        full = await db_module.get_asset_sweep_run(pool, r["id"])
        for i in (full or {}).get("items") or ():
            if i["status"] in ("pending", "waiting", "fetching", "publishing"):
                out.add(str(i["collection"]).lower())
    return out


async def after_apply_run(ctx, pool, run: dict, state: ApplyRunState, *, user) -> dict | None:
    """Once the run has FINISHED: with
    ``missing_full_export: export``, start a full export of exactly the collections it left out --
    the provider's own collection request (``asset_collection_request``, as the Sources tab's
    "Request"), driven as a sweep in the provider store, ``requested_via`` ``apply-changes``, its
    summary naming this run (``apply_changes_run_id``) and this run's naming it
    (``queued_full_exports: {run_id, collections}``). Returns that, or None.

    The collections are read from the run's items, not only this driver's memory, so a resumed run
    queues what its first driver left out too. No second follow-up for a collection while another
    run of the provider -- an earlier follow-up included -- is still going: that is refused, and
    said, as is a provider that declares no collection request (then it is "warn only")."""
    from .asset_sweeps import COLLECTION_REQUEST, SweepRefused, start_sweep
    from .provider_store import PROVIDER_STORE, PROVIDER_STORE_WIRE

    missing = list(state.missing)
    try:
        full = await db_module.get_asset_sweep_run(pool, run["id"])
        missing = list(
            dict.fromkeys(
                [i["collection"] for i in (full or {}).get("items") or () if i.get("status") == NO_FULL_EXPORT]
                + missing
            )
        )
    except Exception:  # noqa: BLE001 - this driver's own list still answers
        logger.exception("apply changes %s: could not read the run's items", run["id"])
    if not missing or _mode(run) != MISSING_FULL_EXPORT_EXPORT:
        return None
    # Runs of a provider no longer exclude each other, so "never twice" is checked here: a
    # collection an earlier follow-up of this provider still has to do is not queued again.
    pending = await _queued_elsewhere(pool, run["provider"])
    again = [c for c in missing if c.lower() in pending]
    missing = [c for c in missing if c.lower() not in pending]
    if again:
        await db_module.add_asset_sweep_run_warnings(
            pool, run["id"], [f"a full project export of {', '.join(again)} is already queued by an earlier run"]
        )
    if not missing:
        return None
    try:
        follow = await start_sweep(
            ctx,
            pool,
            scope=PROVIDER_STORE_WIRE,
            scope_obj=PROVIDER_STORE,
            provider=run["provider"],
            job=COLLECTION_REQUEST,
            settings={},
            user=user,
            via=VIA_APPLY_CHANGES,
            collections=missing,
            summary={"apply_changes_run_id": run["id"]},
        )
    except SweepRefused as exc:
        text = str(exc)
        if "declares no collection request" in text:
            warning = (
                f"no full project export queued for {', '.join(missing)}: the provider declares no full export "
                "(collection request); warned only"
            )
        else:
            warning = f"no full project export queued for {', '.join(missing)}: {text}"
        logger.warning("apply changes %s: %s", run["id"], warning)
        await db_module.add_asset_sweep_run_warnings(pool, run["id"], [warning])
        return None
    queued = {"run_id": follow["id"], "collections": missing}
    await db_module.merge_asset_sweep_run_summary(pool, run["id"], {"queued_full_exports": queued})
    logger.info("apply changes %s: full project export queued as run %s for %s", run["id"], follow["id"], missing)
    return queued


# --- Admin -> Providers: the cursors -----------------------------------------------------------


@admin_router.get("/asset-apply-cursors")
async def admin_asset_apply_cursors(request: Request, provider: str | None = None) -> JSONResponse:
    """Every stored cursor (of ``provider``): ``{cursors: [{provider, collection, cursor, applied_at,
    run_id}]}``. A collection without one runs a baseline next."""
    pool = require_pool(request)
    return JSONResponse({"cursors": await db_module.list_asset_apply_cursors(pool, provider=provider or None)})


@admin_router.delete("/asset-apply-cursors")
async def admin_asset_apply_cursor_reset(request: Request, provider: str = "", collection: str = "") -> JSONResponse:
    """Forget one collection's cursor: its next run is a baseline again (``since`` null), which
    applies nothing and starts counting from then. For after a full export, or a cursor the
    provider no longer understands. Idempotent: ``reset`` says whether there was one."""
    pool = require_pool(request)
    provider, collection = provider.strip(), collection.strip()
    if not provider or not collection:
        raise HTTPException(status_code=400, detail="provider and collection are required")
    reset = await db_module.delete_asset_apply_cursor(pool, provider=provider, collection=collection)
    logger.info("apply changes: cursor of %s/%s %s", provider, collection.lower(), "reset" if reset else "was not set")
    return JSONResponse({"provider": provider, "collection": collection.lower(), "reset": reset})
