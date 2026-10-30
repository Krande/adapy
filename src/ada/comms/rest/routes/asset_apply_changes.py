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
sweep's fetch-and-publish. One run per provider at a time, sweeps included: they share the guard.

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

A baseline (``since`` null) or a ``nothing`` answer advances the cursor with no publish. A patch
REFUSED for want of a baseline set is a warning, not a failure: it can never apply until a full
export builds the set, and that export carries the change anyway. Anything else that goes wrong
leaves the cursor where it was and the item ``error``: the next run asks the same question again,
which is safe because every patch is an idempotent upsert or delete.

WHAT AN ITEM RECORDS. The sweep tables have one free JSON column per item, ``timings``; beside the
phases (``queued_ms``, ``fetch_ms``, ``publish_ms``, ``total_ms``, the provider's own ``provider``
phases, the publish's ``publish``) it holds ``apply_changes`` -- the answer's counts, targets,
since / next_cursor, baseline / nothing, and whether the cursor moved -- and ``roles``, the publish
outcome per staged role (``{status: applied|refused|failed|skipped, warning?, upserted?,
deleted?}``). The run read (``GET /asset-sweeps/{id}``) lifts both onto the item.

NO SCOPE, ADMIN ONLY: as every provider job (``routes/provider_store.py``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import db as db_module
from ..db.attribute_index import ATTRIBUTE_INDEX_PATCH_ROLE, ATTRIBUTE_INDEX_ROLE
from ..db.node_bounds import NODE_BOUNDS_PATCH_ROLE, NODE_BOUNDS_ROLE
from ..db.node_relations import NODE_RELATIONS_PATCH_ROLE, NODE_RELATIONS_ROLE
from ..run_driver import ItemSkipped, RunCancelled, json_of, ms
from .asset_schedules import APPLY_TARGETS, build_options, entry_settings, job_keys
from .assets import staged_by_role, submit_asset_publish
from .deps import require_pool
from .plugin_jobs import enqueue_plugin_job

admin_router = APIRouter()

APPLY_CHANGES_SCHEMA = "ada.assets/apply-changes@1"
#: The patch roles and every core-handled role, as ``formats/asset_publish.py`` names them --
#: spelled out here because the API image does not ship the publish handler (a test keeps the two
#: in step).
PATCH_ROLES = ("hierarchy-patch.json", ATTRIBUTE_INDEX_PATCH_ROLE, NODE_BOUNDS_PATCH_ROLE, NODE_RELATIONS_PATCH_ROLE)
CORE_ROLES = frozenset({ATTRIBUTE_INDEX_ROLE, NODE_BOUNDS_ROLE, NODE_RELATIONS_ROLE, *PATCH_ROLES})
#: The element counts an answer reports (each at its own level, never its level or SITE).
COUNT_KEYS = ("changed", "inserted", "deleted", "unplaceable")


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
) -> bool:
    """Ask for, and apply, one collection's changes since core's cursor. Returns False when it
    failed (the cursor then stays); raises only ``RunCancelled``. The sweep driver's per-item hook."""
    from . import asset_sweeps as sweeps

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

        staging_ids = sweeps.staging_ids_of(summary)
        if not staging_ids and not (answer["baseline"] or answer["nothing"]):
            c = answer["counts"]
            if c["changed"] + c["inserted"] + c["deleted"]:
                raise RuntimeError("the provider counted changes but staged nothing to apply")

        roles: dict[str, dict] = {}
        revision = None
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
            timings["publish_ms"] = ms(time.monotonic() - t_pub)
            timings["roles"] = roles

        not_applied = [r for r, o in roles.items() if o.get("status") not in ("applied", "refused")]
        if not_applied:
            details = "; ".join(
                f"{r}: {roles[r].get('error') or roles[r].get('warning') or roles[r]['status']}" for r in not_applied
            )
            await finish("error", revision=revision, error=f"not applied, cursor kept: {details}")
            return False

        await db_module.set_asset_apply_cursor(
            pool, provider=provider, collection=collection, cursor=answer["next_cursor"], run_id=run_id
        )
        timings["apply_changes"]["advanced"] = True
        refused = [r for r, o in roles.items() if o.get("status") == "refused"]
        await finish(
            "nothing" if answer["nothing"] and not roles else "done",
            revision=revision,
            message=_message(answer, advanced=True, refused=refused),
        )
        return True
    except RunCancelled:
        await finish("skipped", message="the run was cancelled")
        raise
    except ItemSkipped as exc:
        await db_module.clear_asset_sweep_skip(pool, run_id, position)
        await finish("skipped", message=f"{exc}; cursor kept")
        logger.info("apply changes %s: %s %s", run_id, collection, exc)
        return True
    except HTTPException as exc:
        await finish("error", error=f"{exc.status_code}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - one collection's failure is its row's, not the run's
        logger.warning("apply changes %s: %s failed: %s", run_id, collection, exc)
        await finish("error", error=str(exc))
    return False


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
