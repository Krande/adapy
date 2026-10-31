"""Pre-build meshes -- core's ``prebuild_meshes`` setting on sweeps and apply-changes runs.

A provider that delivers by ``build`` (its manifest claims ``delivery: build``) has its meshes made
on demand: the first viewer to open a subject waits for the build, and the next finds it in the
build cache (``_derived/assets/...`` in the provider store, keyed by the build's fingerprint). A run
with "Pre-build meshes" ticked fills that cache while nobody is waiting: per collection, once its
provider jobs have published, every published subject whose newest complete manifest asks for a
build is built through ``submit_node_build`` -- the build route's own work, so its cache check makes
a repeat free (``cached``, no job) and the key is exactly the one a viewer will ask for.

BUILDS RUN ON THE BUILD CAPABILITY'S WORKERS, not the provider's: they take none of the provider's
worker slots. Their fan-out has its own bound per collection: at most :data:`MAX_BUILD_WIDTH`, and
no more than the live workers advertising the build's capability. Each build is polled like any
run job -- heartbeat, cancel, and a skip of the item stop it. A build that fails is counted, never
the collection's failure: its data is published, and the build is made on demand as before.

What a collection did is kept on its item: ``prebuild: {subjects, cached, built, failed, skipped,
ms, errors?, reason?, note?}`` (``reason`` when it was not attempted -- its geometry did not publish).

Core never names a provider here; the ``delivery`` and the capability are the manifest's.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import HTTPException

from ada.assets.keys import ASSET_PREFIX, STAGING_SEGMENT
from ada.assets.manifest import MANIFEST_FILENAME
from ada.config import logger

from .. import db as db_module
from ..run_driver import ItemSkipped, RunCancelled, json_of, ms, wait_for_job
from .assets import submit_node_build
from .provider_store import PROVIDER_STORE

#: Core's setting: fill the build cache after a collection has published.
PREBUILD_MESHES = "prebuild_meshes"
#: Most builds one collection has in flight at once, whatever the fleet.
MAX_BUILD_WIDTH = 8
#: How long one build may take before it counts as failed (it is not cancelled).
BUILD_TIMEOUT_S = 30 * 60
#: Newest revisions of a subject read for its newest COMPLETE manifest.
MAX_REVISIONS_READ = 20
#: Said on an apply-changes item: what pre-building its elements does not cover.
ANCESTORS_NOTE = (
    "only the changed elements are pre-built; builds of the levels above them rebuild on demand "
    "(their excluded descendants changed)"
)


def prebuild_wanted(settings: dict | None) -> bool:
    """Whether a run's settings ask for it (a stored ``true`` / ``"true"``)."""
    from .asset_schedules import same_choice

    return same_choice((settings or {}).get(PREBUILD_MESHES), True)


async def build_subjects(ctx, scope_obj, collection: str) -> list[dict]:
    """``[{collection, subject, provider, capability}]`` -- the collection's published subjects whose
    newest complete manifest (the manifest lands last, so its presence is completeness) claims
    ``delivery: build``. The collection is matched case-insensitively against the store's spelling,
    which is the one returned (the build route keys by it)."""
    names = await ctx.storage.list_child_names(scope_obj, f"{ASSET_PREFIX}/")
    out: list[dict] = []
    for stored in (n for n in names if n != STAGING_SEGMENT and n.lower() == collection.lower()):
        for subject in await ctx.storage.list_child_names(scope_obj, f"{ASSET_PREFIX}/{stored}/"):
            prefix = f"{ASSET_PREFIX}/{stored}/{subject}/"
            revisions = sorted(await ctx.storage.list_child_names(scope_obj, prefix), reverse=True)
            for revision in revisions[:MAX_REVISIONS_READ]:
                try:
                    doc = json_of(await ctx.storage.get_bytes(scope_obj, f"{prefix}{revision}/{MANIFEST_FILENAME}"))
                except Exception:  # noqa: BLE001 - no manifest: an incomplete revision
                    continue
                if isinstance(doc, dict) and doc.get("delivery") == "build":
                    build = doc.get("build") if isinstance(doc.get("build"), dict) else {}
                    out.append(
                        {
                            "collection": stored,
                            "subject": subject,
                            "provider": str(doc.get("provider") or ""),
                            "capability": build.get("capability"),
                        }
                    )
                break  # the newest complete revision decides
    return out


async def build_width(ctx, capability: Any) -> int:
    """How many builds of ``capability`` one collection runs at once: the live workers advertising
    it, at most :data:`MAX_BUILD_WIDTH`; one when the fleet cannot be read."""
    want = str(capability or "").strip().lower()
    try:
        rows = await ctx.jobs.capabilities()
    except Exception:  # noqa: BLE001 - unknown fleet: one at a time
        return 1
    workers = sum(1 for r in rows if want and want in {str(c).lower() for c in (r.get("capabilities") or ())})
    return max(1, min(MAX_BUILD_WIDTH, workers))


async def prebuild(
    ctx,
    pool,
    run: dict,
    position: int,
    targets: list[dict],
    *,
    user,
    poll_s: float,
) -> dict:
    """Build each of ``targets`` (``{collection, subject, provider, capability?}``) in the provider
    store, bounded per capability, and wait for every job. Returns the item's ``prebuild`` record.
    Raises only a cancel of the run or a skip of the item (after the builds in flight stopped)."""
    t0 = time.monotonic()
    summary: dict = {"subjects": len(targets), "cached": 0, "built": 0, "failed": 0, "skipped": 0}
    errors: list[str] = []
    gates: dict[str, asyncio.Semaphore] = {}

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
            except HTTPException as exc:
                if exc.status_code == 409 and "not 'build'" in str(exc.detail):
                    summary["skipped"] += 1  # a subject that is loaded directly: nothing to build
                    return
                summary["failed"] += 1
                errors.append(f"{t['subject']}: {exc.detail}")
                return
            if answer.get("cached"):
                summary["cached"] += 1
                return
            try:
                await wait_for_job(
                    ctx,
                    answer["job_id"],
                    timeout_s=BUILD_TIMEOUT_S,
                    beat=lambda: db_module.heartbeat_asset_sweep_run(pool, run["id"]),
                    position=position,
                    poll_s=poll_s,
                    label=f"pre-build {run['id']}",
                )
            except (RunCancelled, ItemSkipped):
                raise
            except Exception as exc:  # noqa: BLE001 - counted; built on demand as before
                summary["failed"] += 1
                errors.append(f"{t['subject']}: {exc}")
                return
            summary["built"] += 1

    outcomes = await asyncio.gather(*(one(t) for t in targets), return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, (RunCancelled, ItemSkipped)):
            raise outcome
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            summary["failed"] += 1
            errors.append(str(outcome))
    summary["ms"] = ms(time.monotonic() - t0)
    if errors:
        summary["errors"] = errors[:5]
        logger.warning("pre-build %s: %d build(s) failed: %s", run["id"], summary["failed"], "; ".join(errors[:3]))
    return summary


async def prebuild_collection(ctx, pool, run: dict, position: int, collection: str, *, user, poll_s: float) -> dict:
    """A sweep's pre-build of one collection: every build-delivered subject it has published."""
    try:
        targets = await build_subjects(ctx, PROVIDER_STORE, collection)
    except Exception as exc:  # noqa: BLE001 - the collection published; the builds come on demand
        return {"subjects": 0, "cached": 0, "built": 0, "failed": 0, "skipped": 0, "reason": f"could not list: {exc}"}
    return await prebuild(ctx, pool, run, position, targets, user=user, poll_s=poll_s)


def prebuild_line(p: dict) -> str:
    """The item's one-line account: "pre-built 3 of 5 (2 cached, 1 failed)", or why not."""
    if p.get("reason"):
        return f"meshes not pre-built: {p['reason']}"
    parts = [f"{p.get('cached', 0)} cached"] if p.get("cached") else []
    if p.get("failed"):
        parts.append(f"{p['failed']} failed")
    done = p.get("built", 0) + p.get("cached", 0)
    return f"meshes pre-built {done} of {p.get('subjects', 0)}" + (f" ({', '.join(parts)})" if parts else "")


def not_attempted(reason: str) -> dict:
    return {"subjects": 0, "cached": 0, "built": 0, "failed": 0, "skipped": 0, "reason": reason}
