"""Geometry comparison -- the ``geometry-compare`` kind of ``asset_schedules`` (docs/design/geometry-compare.md).

A provider declares a sweep entry of this kind; a run of it compares, per collection, the provider's
tessellation of every element with ANOTHER provider's (the entry's ``compare_with`` setting) and keeps
the verdicts. It is driven by the sweep driver like any sweep (lanes, heartbeat, cancel, skip, resume
-- ``asset_sweeps._drive``); only the per-collection work differs, and it is this module's
:func:`compare_one`:

1. **Both sides' GLBs.** Each provider's published subjects of the collection whose newest complete
   manifest claims ``delivery: build`` are built through ``submit_node_build`` -- the build route's own
   work, so a cached build is free and the GLB is the one a viewer loads. No provider job runs: a
   provider that can be compared is one core can build.
2. **The comparison** runs on a worker (``formats/geometry_compare.py``, generic ``ada.geom_compare``):
   elements joined by name (a build's ``node_ids`` -> the spine's full name), measured, classified
   pass / uncertain / fail / missing, pictures for what did not pass.
3. **The state** (``geometry_compare_elements``) is replaced for the collection: the gallery's source
   and the next run's memory -- an element whose two fingerprints did not move keeps its verdict.

Core names no provider here: both ids come from the run (its provider) and its settings.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from ada.assets.keys import ASSET_PREFIX
from ada.assets.manifest import MANIFEST_FILENAME
from ada.config import logger

from .. import db as db_module
from ..job_transport import JobRequest
from ..run_driver import ItemSkipped, RunCancelled, json_of, ms, wait_for_job
from .asset_prebuild import BUILD_TIMEOUT_S, build_subjects, build_width
from .assets import submit_node_build
from .deps import RestContext, require_pool, rest_context
from .provider_store import PROVIDER_STORE

admin_router = APIRouter()

#: The schedule kind.
GEOMETRY_COMPARE = "geometry-compare"
#: The entry setting naming the provider compared against.
COMPARE_WITH = "compare_with"
#: Settings read as thresholds (``ada.geom_compare.Thresholds``) and the picture cap.
THRESHOLD_SETTINGS = ("box_tol_mm", "vol_tol", "fail_mm", "vol_fail", "dist_tol_mm")
MAX_PICTURES = "max_pictures"
#: Where a comparison's files go in the provider store (derived: regenerable, hidden from listings).
DERIVED_PREFIX = "_derived/geometry-compare"
#: How long one collection's comparison job may run.
COMPARE_TIMEOUT_S = 6 * 3600


def _prefix(provider_a: str, provider_b: str, collection: str) -> str:
    return f"{DERIVED_PREFIX}/{provider_a}/{provider_b}/{collection.lower()}"


async def _hierarchy_key(ctx, collection: str, subject: str, revision: str) -> str | None:
    """The spine of a subject's revision, as its manifest names it."""
    base = f"{ASSET_PREFIX}/{collection}/{subject}/{revision}/"
    try:
        doc = json_of(await ctx.storage.get_bytes(PROVIDER_STORE, f"{base}{MANIFEST_FILENAME}"))
    except Exception:  # noqa: BLE001 - no spine: the GLB's own names join
        return None
    for art in (doc or {}).get("artefacts") or ():
        if isinstance(art, dict) and art.get("role") == "hierarchy":
            if art.get("key"):
                return str(art["key"])
            if art.get("file"):
                return f"{base}{art['file']}"
    return None


async def built_glbs(
    ctx, pool, run: dict, position: int, targets: list[dict], *, user, poll_s: float
) -> tuple[list[dict], list[str]]:
    """``([{glb_key, hierarchy_key, subject, node, revision}], warnings)``: each build-delivered target
    built (or found cached) in the provider store, bounded per capability like a pre-build."""
    gates: dict[str, asyncio.Semaphore] = {}
    out: list[dict] = []
    warnings: list[str] = []

    async def gate(capability) -> asyncio.Semaphore:
        key = str(capability or "")
        if key not in gates:
            gates[key] = asyncio.Semaphore(await build_width(ctx, capability))
        return gates[key]

    async def one(t: dict) -> None:
        async with await gate(t.get("capability")):
            try:
                answer = await submit_node_build(
                    ctx,
                    PROVIDER_STORE,
                    asked_provider=t["provider"],
                    collection=t["collection"],
                    node=t["subject"],
                    subject=t["subject"],
                    user=user,
                    pool=pool,
                )
                if not answer.get("cached"):
                    await wait_for_job(
                        ctx,
                        answer["job_id"],
                        timeout_s=BUILD_TIMEOUT_S,
                        beat=lambda: db_module.heartbeat_asset_sweep_run(pool, run["id"]),
                        position=position,
                        poll_s=poll_s,
                        label=f"geometry compare {run['id']}",
                    )
                summary = json_of(await ctx.storage.get_bytes(PROVIDER_STORE, answer["derived_key"]))
            except (RunCancelled, ItemSkipped):
                raise
            except HTTPException as exc:
                warnings.append(f"{t['provider']} {t['subject']}: {exc.detail}")
                return
            except Exception as exc:  # noqa: BLE001 - one subject's build costs its elements, said so
                warnings.append(f"{t['provider']} {t['subject']}: {exc}")
                return
            if not (isinstance(summary, dict) and summary.get("ok") and summary.get("glb_key")):
                if not (summary or {}).get("empty"):
                    warnings.append(
                        f"{t['provider']} {t['subject']}: build {(summary or {}).get('error') or 'gave no GLB'}"
                    )
                return
            out.append(
                {
                    "glb_key": summary["glb_key"],
                    "hierarchy_key": await _hierarchy_key(ctx, t["collection"], answer["subject"], answer["revision"]),
                    "subject": answer["subject"],
                    "node": answer.get("node"),
                    "revision": answer["revision"],
                }
            )

    outcomes = await asyncio.gather(*(one(t) for t in targets), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, (RunCancelled, ItemSkipped)):
            raise outcome
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            warnings.append(str(outcome))
    out.sort(key=lambda s: s["glb_key"])
    return out, warnings


async def compare_one(ctx, pool, run: dict, position: int, collection: str, *, scope_obj, spec, entry, user) -> bool:
    """One collection of a geometry-compare run (the sweep driver's per-item hook). Returns False when
    it failed; raises only ``RunCancelled``."""
    from . import asset_sweeps as sweeps

    run_id = run["id"]
    provider_a = run["provider"]
    settings = run.get("settings") or {}
    provider_b = str(settings.get(COMPARE_WITH) or "").strip()
    t0 = time.monotonic()
    timings: dict[str, Any] = {}

    async def item(**fields) -> None:
        await db_module.update_asset_sweep_item(pool, run_id, position, **fields)

    async def finish(status: str, **fields) -> None:
        timings["total_ms"] = ms(time.monotonic() - t0)
        await item(status=status, timings=timings, finished_at=datetime.now(timezone.utc), **fields)

    await item(status="fetching", started_at=datetime.now(timezone.utc), message="building both sides")
    try:
        if not provider_b or provider_b == provider_a:
            raise ValueError(f"choose another provider to compare with (setting {COMPARE_WITH!r})")
        listed = await build_subjects(ctx, PROVIDER_STORE, collection)
        side_a = [t for t in listed if t["provider"] == provider_a]
        side_b = [t for t in listed if t["provider"] == provider_b]
        timings["subjects"] = {"a": len(side_a), "b": len(side_b)}
        if not side_a or not side_b:
            missing = [p for p, s in ((provider_a, side_a), (provider_b, side_b)) if not s]
            await finish(
                "nothing",
                message=f"nothing to compare: {', '.join(missing)} published no buildable geometry of {collection}",
            )
            return True
        t_build = time.monotonic()
        sources_a, warn_a = await built_glbs(ctx, pool, run, position, side_a, user=user, poll_s=sweeps.POLL_INTERVAL_S)
        sources_b, warn_b = await built_glbs(ctx, pool, run, position, side_b, user=user, poll_s=sweeps.POLL_INTERVAL_S)
        timings["build_ms"] = ms(time.monotonic() - t_build)
        warnings = warn_a + warn_b
        prefix = _prefix(provider_a, provider_b, collection)
        previous = await db_module.geometry_compare_previous(
            pool, provider_a=provider_a, provider_b=provider_b, collection=collection
        )
        previous_key = f"{prefix}/runs/{run_id}/previous.json.gz"
        await ctx.storage.put_bytes(PROVIDER_STORE, previous_key, gzip.compress(json.dumps(previous).encode("utf-8")))
        rows_key = f"{prefix}/runs/{run_id}/rows.ndjson.gz"
        derived_key = f"{prefix}/runs/{run_id}/summary.json"
        await item(status="publishing", message="comparing")
        submitted = await ctx.jobs.submit(
            JobRequest(
                source_key=f"_synthetic/geometry_compare/{provider_a}/{provider_b}/{collection}/{run_id}",
                target_format="geometry_compare",
                scope=PROVIDER_STORE,
                feature="geometry_compare",
                derived_prefix=derived_key.rsplit("/", 1)[0],
                derived_key=derived_key,
                conversion_options={
                    "collection": collection,
                    "provider_a": provider_a,
                    "provider_b": provider_b,
                    "sources_a": sources_a,
                    "sources_b": sources_b,
                    "thresholds": {k: settings[k] for k in THRESHOLD_SETTINGS if k in settings},
                    "max_pictures": settings.get(MAX_PICTURES) or 500,
                    "previous_key": previous_key,
                    "rows_key": rows_key,
                    "pictures_prefix": f"{prefix}/pictures/",
                },
                force_rebuild=True,
            )
        )
        await item(fetch_job_id=submitted.job_id)
        timings["queued_ms"], timings["compare_ms"] = await sweeps._wait(
            ctx, pool, run_id, submitted.job_id, COMPARE_TIMEOUT_S, position=position
        )
        summary = json_of(await ctx.storage.get_bytes(PROVIDER_STORE, derived_key))
        raw = await ctx.storage.get_bytes(PROVIDER_STORE, rows_key)
        rows = [json.loads(line) for line in gzip.decompress(raw).decode("utf-8").splitlines() if line.strip()]
        written = await db_module.replace_geometry_compare(
            pool, provider_a=provider_a, provider_b=provider_b, collection=collection, rows=rows, run_id=run_id
        )
        counts = (summary or {}).get("counts") or {}
        timings["geometry_compare"] = {
            "counts": counts,
            "rows": written,
            "sources": {"a": len(sources_a), "b": len(sources_b)},
            "job": (summary or {}).get("timings"),
        }
        warnings += list((summary or {}).get("warnings") or ())
        if warnings:
            timings["warnings"] = warnings[:20]
            await db_module.add_asset_sweep_run_warnings(pool, run_id, [f"{collection}: {w}" for w in warnings[:5]])
        message = (
            f"{counts.get('compared', 0)} compared: {counts.get('pass', 0)} pass, {counts.get('uncertain', 0)} uncertain, "
            f"{counts.get('fail', 0)} fail; {counts.get('missing', 0)} on one side only"
            f" ({counts.get('reused', 0)} unchanged, {counts.get('pictures', 0)} pictured)"
        )
        await finish("done", message=message)
        return True
    except RunCancelled:
        await finish("skipped", message="the run was cancelled")
        raise
    except ItemSkipped as exc:
        await db_module.clear_asset_sweep_skip(pool, run_id, position)
        await finish("skipped", message=str(exc))
        return True
    except Exception as exc:  # noqa: BLE001 - one collection's failure is its row's, not the run's
        logger.warning("geometry compare %s: %s failed: %s", run_id, collection, exc)
        await finish("error", error=str(exc))
        return False


# --- Admin -> Providers: the gallery -----------------------------------------------------------------


@admin_router.get("/geometry-compare/items")
async def admin_geometry_compare_items(
    request: Request,
    provider_a: str,
    provider_b: str | None = None,
    collection: list[str] | None = Query(default=None),
    verdict: list[str] | None = Query(default=None),
    q: str | None = None,
    sort: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    """``{items, total, counts}``: compared elements of ``provider_a`` against ``provider_b`` (any when
    absent), filtered by collection(s), verdict(s) and a name search, worst first by ``sort`` (a
    metric) or newest first."""
    pool = require_pool(request)
    return JSONResponse(
        await db_module.list_geometry_compare(
            pool,
            provider_a=provider_a,
            provider_b=provider_b,
            collections=collection,
            verdicts=verdict,
            query=q,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    )


@admin_router.get("/geometry-compare/picture")
async def admin_geometry_compare_picture(key: str, ctx: RestContext = Depends(rest_context)) -> Response:
    """One comparison picture from the provider store (PNG). Only keys under the comparison's own
    derived prefix are served."""
    if not key.startswith(f"{DERIVED_PREFIX}/") or ".." in key or not key.endswith(".png"):
        raise HTTPException(status_code=400, detail="not a geometry comparison picture")
    try:
        data = await ctx.storage.get_bytes(PROVIDER_STORE, key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="picture not found") from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
