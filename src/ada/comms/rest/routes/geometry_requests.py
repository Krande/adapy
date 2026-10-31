"""Geometry a search-result export needs and the geometry provider has not published yet -- found
before the export starts (the PREFLIGHT) and, when the user says so, asked for and published before
the export writes its files (the REQUEST).

WHY. An export writes the geometry provider's own CSG (``search_export.GEOMETRY_PROVIDER``) and
nothing else. A provider that publishes a collection's TREE first and its geometry on demand -- the
ordinary case for a design system, where geometry is an export per node -- leaves most hits of a
fresh search without any: a run then "succeeds" writing no file at all. The Sources tab already
knows how to fix that for a node the user clicked (``assets/collectionRequest.ts``: the provider's
declared ``asset_node_request``, then core's publish of what it staged). This module is that, server
side, for every hit of an export that lacks it.

THREE ANSWERS PER HIT, from the same resolution the export job makes (``resolve_geometry``):

* ``ready`` -- a readable publish covers it, the job will write it;
* ``listed`` / ``unpublished`` -- the provider's tree lists the node without geometry, or the
  provider published nothing in the collection at all: a request by node is the fix (MISSING);
* ``unknown`` -- the provider published a tree for the collection and the node is not in it. A
  request names the node by its full name and the source may still find it, but the tree says the
  source did not export it, so it is reported apart and not requested.

THE PROMPT IS THE USER'S. ``POST .../assets/search/export/preflight`` answers those counts per
collection, the number of request jobs it would take, whether this user may run the provider's job,
and whether a sweep of the same provider is running (the seat is shared: requests queue behind and
between its jobs). It writes nothing. The dialog asks; ``request_missing_geometry: true`` on the
export is the answer "queue it first".

ONE PROJECT SESSION PER COLLECTION. A design system's request pays a project open (seconds warm,
minutes cold) per job unless the job is for the project its session already holds. So a
collection's missing nodes go out as ONE job when they fit :data:`NODES_PER_REQUEST` -- the
provider's declared ``max_nodes`` sizes a selection of whole SITES for a browser click, and a
search's hits are single elements, far cheaper per node -- and as several jobs ENQUEUED BACK TO
BACK when they do not, so the worker drains them one after another on one session. Across
collections the export's driver serialises the request phase (one collection at a time), so its
own requests never make the session swap back and forth. Each job's ``login`` mode and timings are
recorded per batch, so whether that held is visible on the run, not assumed.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from fastapi import HTTPException

from ada.config import logger

from ..collection_splits import copy_names
from ..run_driver import ItemSkipped, RunCancelled, json_of, ms
from ..search_export import (
    GEOMETRY_PROVIDER,
    NoGeometry,
    _node_id_for_full_name,
    resolve_geometry,
)

__all__ = [
    "MISSING_STATES",
    "NODES_PER_REQUEST",
    "HitState",
    "NodeRequest",
    "RequestOutcome",
    "classify_hits",
    "classify_in_scope",
    "geometry_node_request",
    "node_request_of",
    "request_chunks",
    "request_missing",
    "request_options",
    "request_target",
]

#: As the Sources tab's request: an export from a live design system is tens of minutes, and with
#: a sweep on the same seat it also waits for the sweep's job in flight.
REQUEST_TIMEOUT_S = 90 * 60
PUBLISH_TIMEOUT_S = 15 * 60
#: Most nodes ONE request job names (see the module docstring). Above it, back-to-back jobs.
NODES_PER_REQUEST = 500
#: The states a request by node is the fix for.
MISSING_STATES = ("listed", "unpublished")


# --- the provider's declaration ---------------------------------------------------------------------


@dataclass(frozen=True)
class NodeRequest:
    """``asset_node_request`` on a plugin spec, as ``assets/assetScopeCollections.ts`` reads it."""

    plugin_id: str
    options: Mapping[str, Any]
    collection_option: str
    node_option: str
    label_option: str | None
    max_nodes: int
    label: str
    requires_admin: bool
    spec: Mapping[str, Any] = field(repr=False)

    def collection_name(self, collection: str) -> str:
        """The collection as the provider offers it (``ALPHA`` for core's ``alpha``): the option a
        request carries is the provider's own spelling, and it also picks the worker pool."""
        field_name = self.spec.get("asset_collections_field")
        offered = self.spec.get(field_name) if isinstance(field_name, str) else None
        for name in offered if isinstance(offered, list) else ():
            if isinstance(name, str) and name.lower() == collection.lower():
                return name
        # A split collection's copy (``collection_splits``) is not in the offered list itself.
        return copy_names(self.spec).get(collection.lower(), collection)


def node_request_of(spec: Mapping[str, Any]) -> NodeRequest | None:
    raw = spec.get("asset_node_request")
    plugin_id = str(spec.get("id") or spec.get("slug") or "")
    if not plugin_id or not isinstance(raw, Mapping):
        return None
    collection_option, node_option = raw.get("collection_option"), raw.get("node_option")
    if not (isinstance(collection_option, str) and collection_option.strip()):
        return None
    if not (isinstance(node_option, str) and node_option.strip()):
        return None
    label_option = raw.get("label_option")
    max_nodes = raw.get("max_nodes")
    return NodeRequest(
        plugin_id=plugin_id,
        options=dict(raw.get("options") or {}) if isinstance(raw.get("options"), Mapping) else {},
        collection_option=collection_option.strip(),
        node_option=node_option.strip(),
        label_option=label_option.strip() if isinstance(label_option, str) and label_option.strip() else None,
        max_nodes=max_nodes if isinstance(max_nodes, int) and not isinstance(max_nodes, bool) and max_nodes > 1 else 1,
        label=str(raw.get("label") or "Request"),
        requires_admin=spec.get("requires_admin") is True,
        spec=spec,
    )


async def geometry_node_request(ctx, provider: str = GEOMETRY_PROVIDER) -> NodeRequest | None:
    """The live node request of ``provider``: the first online spec declaring one, else None."""
    for spec in (await ctx.jobs.advertised_specs("plugin_specs")).values():
        if isinstance(spec, Mapping) and spec.get("asset_provider_id") == provider:
            req = node_request_of(spec)
            if req is not None:
                return req
    return None


# --- which hits lack geometry -----------------------------------------------------------------------


@dataclass
class HitState:
    hit: dict
    #: ``ready`` | ``listed`` | ``unpublished`` | ``unknown``
    state: str
    #: The id the geometry provider's tree lists the node under, when it does.
    node_id: str | None = None
    message: str | None = None
    #: The subject whose publish covers the node (``ready`` only) -- what a build of it names.
    subject: str | None = None
    revision: str | None = None


def classify_hits(
    index: Any, collection: str, hits: Iterable[dict], provider: str = GEOMETRY_PROVIDER
) -> list[HitState]:
    """Each hit's state against ``index`` (an ``ada.clash.geometry_source.GeometrySourceIndex``)."""
    out = []
    for hit in hits:
        try:
            manifest, node = resolve_geometry(index, collection, hit, provider=provider)
        except NoGeometry as exc:
            state = getattr(exc, "reason", "unknown")
            message = str(exc)
            if state == "unknown":
                message = _unknown_reason(index.catalog(collection, provider), hit) or message
            out.append(HitState(hit=hit, state=state, node_id=getattr(exc, "node_id", None), message=message))
            continue
        out.append(
            HitState(
                hit=hit,
                state="ready",
                node_id=node,
                subject=getattr(manifest, "subject", None),
                revision=getattr(manifest, "revision", None),
            )
        )
    return out


def _unknown_reason(catalog: Any, hit: Mapping[str, Any]) -> str | None:
    """Why a node is not in the provider's tree, as far as the tree can say: is even its TOP-LEVEL
    ancestor missing? A tree without the hit's top-level container is a tree exported from a
    source that did not have that part loaded (a design database left deferred, or the wrong
    project database) -- which no request by node can fix; re-exporting the tree with that part
    loaded can."""
    from ada.clash.geometry_source import normalise_label

    path = [p for p in hit.get("path") or () if isinstance(p, Mapping) and p.get("full_name")]
    if not path:
        return None
    top = normalise_label(path[0]["full_name"])
    if top in catalog.exact or top.casefold() in catalog.folded:
        return (
            f"{path[0]['full_name']!r} is in the provider's tree but {hit.get('full_name')!r} is not: the tree "
            f"was exported before it existed, or not deep enough"
        )
    return (
        f"the provider's tree has no {path[0]['full_name']!r} (the top-level container of "
        f"{hit.get('full_name')!r}): the source it was exported from did not have that part loaded -- e.g. a "
        f"design database left deferred (see the collection's provider options) or the wrong project database. "
        f"Re-export the tree with it loaded; a request by node cannot find it either"
    )


class _SyncScopeReader:
    """The synchronous read view :class:`GeometrySourceIndex` takes (``list_keys`` by prefix,
    ``get_bytes``), over the async storage, for use from an executor thread.

    Deliberately self-contained: the API process runs the slim image, which does not ship the
    worker-side ``formats`` package (its handlers import the CAD stack), so importing the worker's
    equivalent from a request handler failed with a ModuleNotFoundError -- a bare 500."""

    def __init__(self, storage, scope, loop) -> None:
        self._s, self._scope, self._loop = storage, scope, loop

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_keys(self, prefix: str = "") -> list[str]:
        return [e.key for e in self._run(self._s.list_prefix(self._scope, prefix)) if e.key.startswith(prefix)]

    def get_bytes(self, key: str) -> bytes:
        return self._run(self._s.get_bytes(self._scope, key))


async def classify_in_scope(
    storage, scope_obj, groups: Sequence[tuple[str, list[dict]]], provider: str = GEOMETRY_PROVIDER
) -> list[list[HitState]]:
    """:func:`classify_hits` for each ``(collection, hits)`` of ``groups`` over the scope's published
    content, read once (one index), off the event loop -- it reads manifests and spines blob by blob."""
    from ada.clash.geometry_source import GeometrySourceIndex

    loop = asyncio.get_running_loop()
    index = GeometrySourceIndex(_SyncScopeReader(storage, scope_obj, loop))
    return await loop.run_in_executor(None, lambda: [classify_hits(index, c, h, provider) for c, h in groups])


# --- the request ------------------------------------------------------------------------------------


def request_target(state: HitState) -> dict:
    """``{id, label}`` a node is requested by: the id the provider's tree lists it under when it
    does, else the name-derived id the providers share; and its full name, which a provider that
    cannot turn a shared id back into its own finds the node by.

    Not the hit's ``source_ref``, although a design system's ids are often its refs: the hit comes
    from ANOTHER provider's index, which may be months older than the source (refs are reissued,
    and differ between two copies of one project), and a stale ref names some other element without
    failing. The name either finds the element or says it cannot."""
    hit = state.hit
    full_name = str(hit.get("full_name") or "").strip()
    node_id = state.node_id or (_node_id_for_full_name(full_name) if full_name else str(hit.get("node_id") or ""))
    label = full_name if not full_name or full_name.startswith("/") else f"/{full_name}"
    return {"id": node_id, "label": label}


def request_chunks(targets: Sequence[dict], per_job: int = NODES_PER_REQUEST) -> list[list[dict]]:
    """``targets`` (deduplicated by id, in order) in jobs of at most ``per_job`` nodes."""
    seen: set[str] = set()
    unique = []
    for t in targets:
        if t["id"] and t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    size = max(1, int(per_job))
    return [unique[i : i + size] for i in range(0, len(unique), size)]


def request_options(
    req: NodeRequest, collection: str, nodes: Sequence[dict], *, extra: Mapping[str, Any] | None, requested_at: str
) -> dict:
    """As ``nodesRequestOptions`` in ``collectionRequest.ts``: the collection's provider options
    UNDER the declared ones, the collection, the ids and -- when declared -- the labels index for
    index, and a ``requested_at`` stamp so a second request is a second job."""
    options = {
        **dict(extra or {}),
        **dict(req.options),
        req.collection_option: req.collection_name(collection),
        req.node_option: [n["id"] for n in nodes],
        "requested_at": requested_at,
    }
    if req.label_option and any(n.get("label") for n in nodes):
        options[req.label_option] = [n.get("label") or "" for n in nodes]
    return options


def request_estimate(n_missing: int, per_job: int | None = None) -> int:
    """How many request jobs ``n_missing`` nodes take (an upper bound: duplicates are merged)."""
    return math.ceil(n_missing / max(1, per_job or NODES_PER_REQUEST)) if n_missing else 0


@dataclass
class RequestOutcome:
    #: One per job: nodes, status (published | unchanged | nothing | error), message, job ids,
    #: login mode, the provider's timings and ours.
    batches: list[dict] = field(default_factory=list)
    #: hit node id -> the provider's own words, for every hit of a job that answered "nothing".
    nothing: dict[str, str] = field(default_factory=dict)
    request_ms: int = 0
    publish_ms: int = 0
    missing: int = 0
    unknown: int = 0

    def summary(self) -> dict:
        logins = [b.get("login_mode") for b in self.batches if b.get("status") != "error"]
        modes: dict[str, int] = {}
        for mode in logins:
            modes[str(mode or "unknown")] = modes.get(str(mode or "unknown"), 0) + 1
        return {
            "missing": self.missing,
            "unknown": self.unknown,
            "jobs": len(self.batches),
            "published": sum(1 for b in self.batches if b.get("status") == "published"),
            "logins": modes,
        }

    def sentence(self) -> str:
        s = self.summary()
        parts = [f"requested geometry for {self.missing} node(s) in {s['jobs']} job(s)"]
        by_status: dict[str, int] = {}
        for b in self.batches:
            by_status[b["status"]] = by_status.get(b["status"], 0) + 1
        parts.append(", ".join(f"{n} {st}" for st, n in by_status.items()))
        if self.unknown:
            parts.append(f"{self.unknown} not in the provider's tree, not requested")
        return "; ".join(p for p in parts if p)


#: ``wait(job_id, timeout_s) -> (queued_ms, ran_ms)``, raising RunCancelled / ItemSkipped.
Wait = Callable[[str, float], Awaitable[tuple[int, int]]]
#: ``progress(message, job_id)`` -- the item row told what is in flight.
Progress = Callable[[str, "str | None"], Awaitable[None]]


async def _provider_options(ctx, scope_obj, provider: str, collection: str) -> dict:
    from .asset_sweeps import _provider_options as read

    return await read(ctx, scope_obj, provider, collection)


async def _enqueue(ctx, **kw) -> str:
    from .plugin_jobs import enqueue_plugin_job

    return await enqueue_plugin_job(ctx, **kw)


async def _publish(ctx, scope_obj, *, provider: str, staging_id: str, user) -> tuple[str, str]:
    from .assets import staged_by_role, submit_asset_publish

    staged = await staged_by_role(ctx, scope_obj, staging_id)
    return await submit_asset_publish(ctx, scope_obj, provider=provider, staged=staged, user=user)


async def _cancel_quietly(ctx, job_ids: Iterable[str]) -> None:
    for job_id in job_ids:
        try:
            await ctx.jobs.cancel(job_id)
        except Exception:  # noqa: BLE001 - best effort, as in run_driver
            logger.warning("geometry request: could not cancel job %s", job_id, exc_info=True)


async def request_missing(
    ctx,
    pool,
    *,
    scope_obj,
    user,
    req: NodeRequest,
    collection: str,
    states: Sequence[HitState],
    wait: Wait,
    progress: Progress,
    provider: str = GEOMETRY_PROVIDER,
    per_job: int | None = None,
) -> RequestOutcome:
    """Ask ``req``'s plugin for every MISSING hit of ``states`` and publish what it stages.

    Every job is enqueued before the first is waited for (one session drains them back to back),
    each answer is published as soon as it arrives (the publish runs while the next export does),
    and one job's failure is that batch's outcome, not the export's. Raises only RunCancelled /
    ItemSkipped -- after cancelling every request job not yet finished.

    AN ADMIN OPERATION, IN THE PROVIDER STORE. The geometry provider's node request is a provider
    job: it runs in ``Scope.providers()`` whatever ``scope_obj`` the export was started from, its
    summary is read and its staging published there (``job_scope``), and a member asks for none
    of it -- provider data is kept current by the admins' scheduled jobs and read through the
    grants on the member's scope. A member's request therefore enqueues nothing: each batch is
    reported refused, with the reason, and the export writes what the store already holds."""
    from .asset_schedules import job_keys
    from .provider_store import ADMIN_ONLY_REQUEST, is_asset_provider_spec, job_scope

    out = RequestOutcome(
        missing=sum(1 for s in states if s.state in MISSING_STATES),
        unknown=sum(1 for s in states if s.state == "unknown"),
    )
    if is_asset_provider_spec(dict(req.spec)) and not getattr(user, "is_admin", False):
        n = sum(len(c) for c in request_chunks([request_target(s) for s in states if s.state in MISSING_STATES]))
        if n:
            out.batches.append({"nodes": n, "status": "error", "message": ADMIN_ONLY_REQUEST.format(provider=provider)})
        return out
    requested_from, scope_obj = scope_obj, job_scope(dict(req.spec), scope_obj)
    wanted = [s for s in states if s.state in MISSING_STATES]
    by_target: dict[str, list[HitState]] = {}
    targets = []
    for s in wanted:
        t = request_target(s)
        by_target.setdefault(t["id"], []).append(s)
        targets.append(t)
    # Read at call time, not bound as a default: a test narrows the module's size.
    chunks = request_chunks(targets, per_job or NODES_PER_REQUEST)
    if not chunks:
        return out

    t0 = time.monotonic()
    # The store's options for the collection; the requesting scope's copy from before the store
    # answers only while the store has none (asset_sweeps._provider_options).
    extra = await _provider_options(ctx, requested_from, provider, collection)
    queued: list[tuple[list[dict], str, str]] = []
    for chunk in chunks:
        options = request_options(
            req, collection, chunk, extra=extra, requested_at=datetime.now(timezone.utc).isoformat()
        )
        # NO derived_prefix, as for the Sources tab's request: the provider stages on its own.
        derived_key, _ = job_keys(req.plugin_id, options)
        try:
            job_id = await _enqueue(
                ctx,
                plugin_id=req.plugin_id,
                options=options,
                scope_obj=scope_obj,
                user=user,
                pool=pool,
                derived_key=derived_key,
                plugin_spec=dict(req.spec),
            )
        except Exception as exc:  # noqa: BLE001 - the batch's outcome
            out.batches.append({"nodes": len(chunk), "status": "error", "message": f"could not enqueue: {exc}"})
            continue
        queued.append((chunk, job_id, derived_key))

    pending = [job_id for _, job_id, _ in queued]
    try:
        for n, (chunk, job_id, derived_key) in enumerate(queued, 1):
            batch: dict[str, Any] = {"nodes": len(chunk), "job_id": job_id}
            out.batches.append(batch)
            await progress(f"requesting geometry: job {n}/{len(queued)} ({len(chunk)} node(s))", job_id)
            t_req = time.monotonic()
            try:
                batch["queued_ms"], batch["fetch_ms"] = await wait(job_id, REQUEST_TIMEOUT_S)
            except (RunCancelled, ItemSkipped):
                raise
            except Exception as exc:  # noqa: BLE001
                batch.update(status="error", message=str(exc))
                continue
            finally:
                pending.remove(job_id)
                out.request_ms += ms(time.monotonic() - t_req)
            try:
                summary = json_of(await ctx.storage.get_bytes(scope_obj, derived_key))
            except Exception as exc:  # noqa: BLE001
                batch.update(status="error", message=f"no summary from the request: {exc}")
                continue
            summary = summary if isinstance(summary, dict) else {}
            login = summary.get("login") if isinstance(summary.get("login"), dict) else {}
            ptimings = summary.get("timings") if isinstance(summary.get("timings"), dict) else {}
            batch["login_mode"] = login.get("mode")
            for key in ("login_ms", "export_ms", "stage_ms"):
                if isinstance(ptimings.get(key), (int, float)):
                    batch[key] = ptimings[key]
            if summary.get("asset_unchanged") is True:
                batch.update(status="unchanged", message=summary.get("message"), revision=summary.get("revision"))
                continue
            if summary.get("asset_nothing") is True:
                message = str(summary.get("message") or f"{req.plugin_id} has nothing for these nodes")
                batch.update(status="nothing", message=message)
                for t in chunk:
                    for s in by_target.get(t["id"], ()):
                        out.nothing[str(s.hit.get("node_id"))] = message
                continue
            staging_id = summary.get("asset_staging_id")
            if not isinstance(staging_id, str) or not staging_id.strip():
                batch.update(status="error", message="the request finished without an asset_staging_id")
                continue
            await progress(f"publishing geometry: job {n}/{len(queued)}", None)
            t_pub = time.monotonic()
            try:
                publish_job, publish_key = await _publish(
                    ctx, scope_obj, provider=provider, staging_id=staging_id.strip(), user=user
                )
                batch["publish_job_id"] = publish_job
                await wait(publish_job, PUBLISH_TIMEOUT_S)
                outcome = json_of(await ctx.storage.get_bytes(scope_obj, publish_key))
                outcome = outcome if isinstance(outcome, dict) else {}
                batch.update(status="published", revision=outcome.get("revision"))
            except (RunCancelled, ItemSkipped):
                raise
            except HTTPException as exc:
                batch.update(status="error", message=f"publish: {exc.status_code}: {exc.detail}")
            except Exception as exc:  # noqa: BLE001
                batch.update(status="error", message=f"publish: {exc}")
            finally:
                batch["publish_ms"] = ms(time.monotonic() - t_pub)
                out.publish_ms += batch["publish_ms"]
    except (RunCancelled, ItemSkipped):
        await _cancel_quietly(ctx, pending)
        raise
    logger.info("geometry request %s/%s: %s in %d ms", provider, collection, out.sentence(), ms(time.monotonic() - t0))
    return out
