"""Asset browser routes: ``/api/scopes/{scope}/assets/...``.

The browser asks four questions here, and none of them requires it to list a scope. That is the
whole reason the index fold lives on the server: a scope holds tens of thousands of derived blobs,
and "which revisions exist" is a few hundred bytes of answer.

Why these routes drive the pure helpers rather than instantiating the built-in provider: core's
storage is async and the provider protocol is sync (a third-party provider may be a live
catalogue client). Rather than split the protocol into sync and async halves, the built-in
``published`` path is assembled here from the same pure functions the provider uses --
``fold_listing`` / ``parse_hierarchy`` / ``parse_manifest`` -- so there is one implementation of
the resolution rules and no event-loop blocking. A registered third-party provider is driven
through ``asyncio.to_thread`` for the same reason.

``_derived/`` is not reachable from here. A build output is keyed by fingerprint and served by the
blob route; exposing it as an asset would make a derived thing look restorable.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ada.assets.attributes import (
    ATTRIBUTES_ROLE,
    AttributesDocument,
    AttributesError,
    NodeAttributes,
    parse_attributes,
)
from ada.assets.build import (
    DERIVED_ASSET_PREFIX,
    BuildError,
    build_fingerprint,
    derived_asset_key,
    derived_asset_prefix,
)
from ada.assets.index import fold_listing
from ada.assets.keys import (
    ASSET_PREFIX,
    STAGING_SEGMENT,
    AssetKeyError,
    asset_key,
    is_valid_segment,
)
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ManifestError,
    manifest_summary,
    parse_manifest,
)
from ada.assets.projection import HierarchyError, parse_hierarchy
from ada.assets.provider import BuildDelivery, MeshDelivery
from ada.assets.provider_labels import (
    PROVIDER_LABELS_SETTING,
    declared_provider_labels,
    parse_provider_aliases,
    resolve_provider_labels,
)
from ada.assets.publish import staged_prefix
from ada.assets.registry import (
    AssetProviderError,
    asset_provider,
    asset_providers,
    registered_provider_ids,
    registered_provider_labels,
)
from ada.assets.rollup import (
    ROLLUP_SCHEMA,
    TreeDocument,
    TreePlacement,
    listing_token,
    plan_rollup,
    rollup_body,
    rollup_delivery,
)
from ada.assets.unpublish import plan_unpublish
from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path
from .plugins import online_plugin_specs

router = APIRouter()

PUBLISHED_PROVIDER_ID = "published"


#: Bodies above this are gzipped for a client that accepts it. A hierarchy slice is row after row of
#: near-identical JSON and compresses about tenfold; a small one is not worth the round of work.
_GZIP_MIN_BYTES = 32 * 1024


def _json_response(request: Request, body: object) -> Response:
    """``body`` as JSON, gzipped when it is large and the client says it accepts gzip.

    Per route rather than an app-wide middleware: the blob route forwards objects that are ALREADY
    gzip-at-rest with their own ``Content-Encoding``, and streams GLBs -- neither may be wrapped again.
    """
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    if len(data) < _GZIP_MIN_BYTES or "gzip" not in request.headers.get("accept-encoding", "").lower():
        return Response(content=data, media_type="application/json")
    return Response(
        content=gzip.compress(data, compresslevel=5),
        media_type="application/json",
        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
    )


async def _list_asset_keys(ctx: RestContext, scope: Scope, prefix: str) -> list[str]:
    entries = await ctx.storage.list_prefix(scope, prefix)
    return [e.key for e in entries]


#: What a scope reader is told about a provider whose factory raised. It still appears -- a silently
#: missing provider looks like one that was never installed -- but the exception text stays in the
#: server log: built from the provider's configuration, it can carry hosts, paths or credentials.
PROVIDER_LOAD_FAILED = "provider failed to load; see the server log"


def _public_provider(entry: dict) -> dict:
    if "error" not in entry:
        return entry
    logger.warning("asset provider %r failed to load: %s", entry["id"], entry["error"])
    return {**entry, "error": PROVIDER_LOAD_FAILED}


def _is_published(provider: str) -> bool:
    """The built-in path serves any provider that publishes under the key grammar.

    A provider only needs a registration when it answers LIVE; a publish-only provider rides the
    store and needs none, which is exactly what keeps a private-format provider cheap.
    """
    return provider == PUBLISHED_PROVIDER_ID or provider not in registered_provider_ids()


async def _scope_provider_labels(request: Request, ctx: RestContext, scope_obj: Scope) -> dict[str, str]:
    """``provider id -> display name`` for this scope, resolved: the scope's admin alias, else what
    an online plugin spec declares, else the label the provider registered with. Absent when none.

    Every source is best effort -- a display name is never worth failing the route over -- and a
    failure is logged, so a missing alias is explainable.
    """
    try:
        declared = declared_provider_labels((await online_plugin_specs(ctx)).values())
    except Exception as exc:  # noqa: BLE001 - names are a courtesy; the provider list is the answer
        logger.warning("asset providers: could not read plugin specs for display names: %s", exc)
        declared = {}
    aliases: dict[str, dict[str, str]] = {}
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:
        try:
            aliases = parse_provider_aliases(await db_module.get_setting(pool, PROVIDER_LABELS_SETTING))
        except Exception as exc:  # noqa: BLE001 - as above
            logger.warning("asset providers: could not read %s: %s", PROVIDER_LABELS_SETTING, exc)
    try:
        scope_key: str | None = scope_obj.wire()
    except ValueError:
        # A personal scope has no admin aliases (the Providers tab leaves personal scopes out).
        scope_key = None
    return resolve_provider_labels(scope_key, aliases, declared, registered_provider_labels())


@router.get("/scopes/{scope}/assets/providers")
async def api_asset_providers(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Registered providers. The built-in ``published`` one is always offered, because a scope may
    hold published assets from a provider this process has never heard of.

    ``provider_labels`` is the display name of every provider -- registered or only published --
    that has one in this scope (``ada.assets.provider_labels``), already resolved, so a viewer shows
    one answer and never re-derives the order. A provider with none is absent: show its id.
    """
    providers = [
        {
            "id": PUBLISHED_PROVIDER_ID,
            "label": "Published",
            "live": True,
            "delivery": ["mesh", "build"],
            "capabilities": ["tree"],
        }
    ]
    providers.extend(_public_provider(p) for p in asset_providers() if p["id"] != PUBLISHED_PROVIDER_ID)
    labels = await _scope_provider_labels(request, ctx, scope_obj)
    return JSONResponse({"providers": providers, "provider_labels": labels})


@router.get("/scopes/{scope}/assets/index")
async def api_asset_index(
    collection: str | None = None,
    manifests: bool = False,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The folded index: subjects -> revisions -> files, plus ``malformed``.

    One bounded ``list_prefix`` -- scoped to a collection when one is named, so browsing a
    collection costs its own keys rather than a walk of every asset in the scope.

    ``manifests=true`` (collection required) also folds in, per revision, the few manifest fields
    the browser's badges are derived from -- the delivery claim, the producing provider and the
    ``hierarchy_revision`` a leaf was published against. Read here rather than by the browser
    because the alternative is one fetch per subject-revision from the tab, and a manifest is
    immutable at its key (barring ``replace``), so the server read is the cheap one.
    """
    if manifests and not collection:
        raise HTTPException(status_code=400, detail="manifests=true needs a collection")
    prefix = f"{ASSET_PREFIX}/{collection}/" if collection else f"{ASSET_PREFIX}/"
    index = fold_listing(await _list_asset_keys(ctx, scope_obj, prefix))
    body = index.to_dict()
    if manifests:
        await _fold_manifest_summaries(ctx, scope_obj, collection, body)
    return JSONResponse(body)


@router.get("/scopes/{scope}/assets/geometry/{collection}")
async def api_asset_geometry(
    collection: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> Response:
    """The collection's GEOMETRY ROLL-UP: per provider (and for ``any``), the subjects whose latest
    content carries loadable geometry (``here``), every row with such a subject somewhere below it
    in the merged collection tree (``below``), and the ones no tree places (``unplaced``).

    What lets the browser mark a row it has never expanded: the tree arrives a level at a time, so
    without this every unopened branch reads "unknown" (see ``ada.assets.rollup`` for the rules).

    Computed from the stored documents only -- manifests and hierarchy slices -- and cached under
    ``_derived/assets/_geometry/<collection>/<token>.json``, where the token hashes the collection's
    listing (keys, sizes, times): any publish or unpublish moves it, so a cached answer is never
    stale and needs no invalidation.
    """
    if not is_valid_segment(collection):
        raise HTTPException(status_code=400, detail=f"invalid collection {collection!r}")
    entries = await ctx.storage.list_prefix(scope_obj, f"{ASSET_PREFIX}/{collection}/")
    if not entries:
        raise HTTPException(status_code=404, detail=f"no published collection {collection!r}")
    token = _rollup_token(entries)
    cache_key = f"{_ROLLUP_PREFIX}/{collection}/{token}.json"

    try:
        cached = json.loads(await ctx.storage.get_bytes(scope_obj, cache_key))
        if isinstance(cached, dict) and cached.get("index_token") == token:
            return _json_response(request, {**cached, "cached": True})
    except (FileNotFoundError, KeyError):
        pass
    except Exception as exc:  # noqa: BLE001 - an unreadable cache entry is recomputed, never served
        logger.warning("geometry roll-up cache %s unreadable: %s", cache_key, exc)

    inflight_key = (scope_obj.prefix(), cache_key)
    pending = _ROLLUP_INFLIGHT.get(inflight_key)
    if pending is None:
        pending = asyncio.ensure_future(_compute_rollup(ctx, scope_obj, collection, entries, token, cache_key))
        _ROLLUP_INFLIGHT[inflight_key] = pending
        pending.add_done_callback(lambda _f: _ROLLUP_INFLIGHT.pop(inflight_key, None))
    body = await asyncio.shield(pending)
    return _json_response(request, {**body, "cached": False})


# -- geometry roll-up -------------------------------------------------------------------------

#: Where roll-ups are cached. ``_geometry`` cannot collide with a provider id: a segment never starts
#: with ``_``, the same reservation ``_publish`` relies on.
_ROLLUP_PREFIX = f"{DERIVED_ASSET_PREFIX}/_geometry"
#: Bumped when the roll-up's RULES change, so an answer cached under the old ones is not served.
_ROLLUP_VERSION = "1"
#: Hierarchy documents read at once. Low on purpose: a site spine can be tens of MB parsed.
_ROLLUP_SPINE_CONCURRENCY = 4
_ROLLUP_INFLIGHT: "dict[tuple[str, str], asyncio.Future]" = {}


def _rollup_token(entries) -> str:
    return listing_token(entries, salt=f"{ROLLUP_SCHEMA}|{_ROLLUP_VERSION}")


def _hierarchy_edges(raw: bytes) -> list[tuple[str, str | None]]:
    """``(id, parent)`` of every row -- all the roll-up needs of a document, so the parsed slice
    is dropped as soon as this returns."""
    return _slice_edges(parse_hierarchy(raw))


def _slice_edges(slice_) -> list[tuple[str, str | None]]:
    id_at, parent_at = slice_.column("id"), slice_.column("parent")
    return [(str(r[id_at]), None if r[parent_at] is None else str(r[parent_at])) for r in slice_.rows]


async def _compute_rollup(
    ctx: RestContext, scope_obj: Scope, collection: str, entries, token: str, cache_key: str
) -> dict:
    started = time.perf_counter()
    subjects = fold_listing([e.key for e in entries]).subjects(collection)

    gate = asyncio.Semaphore(_MANIFEST_READ_CONCURRENCY)

    async def manifest(subject: str, revision: str):
        key = asset_key(collection, subject, revision, MANIFEST_FILENAME)
        async with gate:
            try:
                raw = await ctx.storage.get_bytes(scope_obj, key)
            except (FileNotFoundError, KeyError):
                return None
        try:
            m = parse_manifest(raw)
        except ManifestError:
            return None  # not content, as in the browser: a mark has to be a fact
        return (m.provider, rollup_delivery(m))

    wanted = [(s.subject, r.revision) for s in subjects for r in s.revisions if r.has_manifest]
    summaries = await asyncio.gather(*(manifest(s, r) for s, r in wanted))
    plan = plan_rollup(collection, subjects, dict(zip(wanted, summaries)))
    placement = TreePlacement(collection)

    tree_gate = asyncio.Semaphore(_ROLLUP_SPINE_CONCURRENCY)
    targets = [s for s in plan.any_here if s != collection]

    def done() -> bool:
        return all(placement.placed(s) for s in targets)

    async def read(doc: TreeDocument, *, skip_when_done: bool = False) -> None:
        key = asset_key(collection, doc.subject, doc.revision, HIERARCHY_FILENAME)
        hit = _SPINE_CACHE.get((scope_obj.prefix(), key))
        try:
            if hit is not None:
                edges = _slice_edges(hit.slice_)
            else:
                async with tree_gate:
                    if skip_when_done and done():
                        return  # an earlier spine of this wave placed the last one
                    raw = await ctx.storage.get_bytes(scope_obj, key)
                    # Off the event loop: a whole-site spine takes a while to parse.
                    edges = await asyncio.to_thread(_hierarchy_edges, raw)
        except (FileNotFoundError, KeyError):
            return
        except HierarchyError as exc:
            logger.warning("geometry roll-up: %s unreadable: %s", key, exc)
            return
        placement.add(doc, edges)

    await asyncio.gather(*(read(d) for d in plan.index_documents))
    # Spines in WAVES, each one the spines whose subject the tree now reaches -- exactly what the
    # browser could open -- and only while some subject with geometry is still not placed: once
    # every one is, its ancestry is complete and the rest of the spines cannot change the answer.
    while not done():
        wave = placement.spines_to_read(plan)
        if not wave:
            break
        await asyncio.gather(*(read(d, skip_when_done=True) for d in wave))

    body = rollup_body(plan, placement, index_token=token)
    body["stats"]["computed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    try:
        await ctx.storage.put_bytes(
            scope_obj, cache_key, json.dumps(body, separators=(",", ":")).encode("utf-8"), content_encoding="gzip"
        )
        # Older tokens of this collection answer listings that no longer exist.
        for old in await ctx.storage.list_prefix(scope_obj, f"{_ROLLUP_PREFIX}/{collection}/"):
            if old.key != cache_key:
                await ctx.storage.delete(scope_obj, old.key)
    except Exception as exc:  # noqa: BLE001 - the answer stands without its cache
        logger.warning("geometry roll-up cache %s not written: %s", cache_key, exc)
    return body


@router.get("/scopes/{scope}/assets/tree/{provider}/{collection}")
async def api_asset_tree(
    provider: str,
    collection: str,
    request: Request,
    root: str | None = None,
    depth: int = 1,
    revision: str | None = None,
    parent: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> Response:
    """A hierarchy slice. Published or live -- the browser cannot tell which, by design.

    ``provider`` in the path selects how the slice is OBTAINED, not who produced each node. A
    published collection may be mixed: its rows can carry a per-node ``provider`` column naming a
    different producer per branch, and the delivery claim for each node names the same.

    ``parent`` (published path only) narrows the answer to ONE LEVEL of the stored spine: the rows
    whose parent is ``parent`` -- that node's direct children -- plus a trailing ``children``
    column counting each returned row's own children in the spine, so the browser knows which
    rows can expand without holding the level below. ``parent`` equal to the subject answers the
    subject's first level (the spine's own top row, stored with no parent, is not repeated). A
    node the spine does not hold answers with NO rows and a 200, same as a leaf: the browser asks
    per expanded row, and "nothing under it in this spine" is the answer either way -- a 404 stays
    reserved for a spine that is not there. Why it exists: one site's spine can be tens of MB, and
    shipping it whole to expand one row froze the browser parsing it.
    """
    if not _is_published(provider):
        slice_ = await _live_hierarchy(provider, scope_obj, collection, root, depth)
        return JSONResponse(_slice_to_dict(slice_))

    subject = root or collection
    revision = revision or await _latest_complete_revision(ctx, scope_obj, collection, subject)
    if revision is None:
        raise HTTPException(
            status_code=404,
            detail=f"no published hierarchy for collection={collection!r} subject={subject!r}",
        )
    try:
        key = asset_key(collection, subject, revision, HIERARCHY_FILENAME)
    except AssetKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    spine = await _spine_cached(ctx, scope_obj, key, subject)
    if parent is None:
        return _json_response(request, _slice_to_dict(spine.slice_))
    return _json_response(request, spine.children_slice(parent))


# -- spines -----------------------------------------------------------------------------------
#
# The tree route answers one LEVEL at a time out of a spine that can be a whole site (hundreds of
# thousands of rows, tens of MB of JSON), so the spine is read and parsed once and the level is
# what crosses the wire. Same trade as the attributes cache below, and for the same reason it
# needs no invalidation: a spine is immutable at its key -- the revision is IN the key -- and a
# republish is a new revision at a new key.
#
# Bounded two ways. At most `_SPINE_CACHE_ENTRIES` spines are held, and their RAW (decoded) bytes
# together stay under `_SPINE_CACHE_MAX_BYTES`, evicted least-recently-used; a spine larger than
# the whole budget is served without being retained. Unlike the attributes budget this one is
# sized to HOLD very large documents -- the 48 MB site spine is the case the cache exists for --
# so the default fits a few of them. Parsed rows are larger than their JSON, so this bounds how
# many big spines are resident rather than resident memory itself.
_SPINE_CACHE_ENTRIES = int(os.environ.get("ADA_ASSET_SPINE_CACHE_ENTRIES", "16"))
_SPINE_CACHE_MAX_BYTES = int(os.environ.get("ADA_ASSET_SPINE_CACHE_MAX_BYTES", str(192 * 1024 * 1024)))


class _Spine:
    """One parsed spine plus its children index (parent id -> row indices), so answering a level
    is O(children) rather than a scan of every row."""

    __slots__ = ("slice_", "children", "raw_bytes")

    def __init__(self, slice_, subject: str, raw_bytes: int) -> None:
        self.slice_ = slice_
        self.raw_bytes = raw_bytes
        id_at = slice_.column("id")
        parent_at = slice_.column("parent")
        children: dict[str, list[int]] = {}
        for i, row in enumerate(slice_.rows):
            up = row[parent_at]
            if up is None:
                # A spine names its own top with no parent. Any OTHER parentless row is a top of
                # this document -- a collection index's roots -- and sits under the subject.
                if str(row[id_at]) == subject:
                    continue
                up = subject
            children.setdefault(str(up), []).append(i)
        self.children = children

    def children_slice(self, parent: str) -> dict:
        slice_ = self.slice_
        id_at = slice_.column("id")
        rows = [slice_.rows[i] for i in self.children.get(parent, ())]
        body = _slice_to_dict(slice_, rows=rows)
        body["cols"].append("children")
        for out, row in zip(body["rows"], rows):
            out.append(len(self.children.get(str(row[id_at]), ())))
        body["parent"] = parent
        body["depth"] = 1
        return body


_SPINE_CACHE: "OrderedDict[tuple[str, str], _Spine]" = OrderedDict()
_SPINE_CACHE_BYTES = 0
#: A cold spine is read once however many rows ask for it at the same time.
_SPINE_INFLIGHT: "dict[tuple[str, str], asyncio.Future]" = {}


def _spine_retain(cache_key: tuple[str, str], spine: _Spine) -> None:
    global _SPINE_CACHE_BYTES
    if spine.raw_bytes > _SPINE_CACHE_MAX_BYTES or cache_key in _SPINE_CACHE:
        return
    _SPINE_CACHE[cache_key] = spine
    _SPINE_CACHE_BYTES += spine.raw_bytes
    while _SPINE_CACHE and (len(_SPINE_CACHE) > _SPINE_CACHE_ENTRIES or _SPINE_CACHE_BYTES > _SPINE_CACHE_MAX_BYTES):
        _, evicted = _SPINE_CACHE.popitem(last=False)
        _SPINE_CACHE_BYTES -= evicted.raw_bytes


async def _spine_cached(ctx: RestContext, scope_obj: Scope, key: str, subject: str) -> _Spine:
    """The parsed spine at ``key``: from the cache, or read once (404 missing, 502 unreadable)."""
    cache_key = (scope_obj.prefix(), key)
    hit = _SPINE_CACHE.get(cache_key)
    if hit is not None:
        _SPINE_CACHE.move_to_end(cache_key)
        return hit

    async def _load() -> _Spine:
        try:
            raw = await ctx.storage.get_bytes(scope_obj, key)
        except (FileNotFoundError, KeyError) as exc:
            raise HTTPException(status_code=404, detail=f"no {HIERARCHY_FILENAME} at {key}") from exc
        try:
            # Off the event loop: a whole-site spine takes seconds to parse.
            spine = await asyncio.to_thread(lambda: _Spine(parse_hierarchy(raw), subject, len(raw)))
        except HierarchyError as exc:
            # A stored blob core cannot read is a 502, not a 404: the object IS there, and calling
            # it missing would send the caller looking for the wrong problem.
            raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc
        _spine_retain(cache_key, spine)
        return spine

    pending = _SPINE_INFLIGHT.get(cache_key)
    if pending is None:
        pending = asyncio.ensure_future(_load())
        _SPINE_INFLIGHT[cache_key] = pending
        pending.add_done_callback(lambda _f: _SPINE_INFLIGHT.pop(cache_key, None))
    # Shielded: one caller going away must not cancel the read the others are waiting on.
    return await asyncio.shield(pending)


def clear_asset_spine_cache() -> None:
    """Test hook. The cache is keyed by an immutable revision, so nothing in production needs it."""
    global _SPINE_CACHE_BYTES
    _SPINE_CACHE.clear()
    _SPINE_CACHE_BYTES = 0


# -- attributes -------------------------------------------------------------------------------
#
# One document covers a whole subject, and a selection wants ONE node out of it, so the blob is
# read here and the node is what crosses the wire. That trade only works because the document is
# immutable: it is keyed by a revision, and a revision's bytes never change. So it is cached, and
# the cache needs no invalidation -- a republish is a NEW revision at a new key.
#
# Bounded two ways, because a subject can be a whole storey. Documents larger than the ceiling are
# served without being retained (one pathological subject must not pin the process), and at most
# `_ATTRS_CACHE_ENTRIES` of the rest are held, evicted least-recently-used. The budget counts RAW
# bytes; the parsed objects are larger, so this bounds how many big documents are resident rather
# than resident memory itself.
_ATTRS_CACHE_ENTRIES = int(os.environ.get("ADA_ASSET_ATTRIBUTES_CACHE_ENTRIES", "4"))
_ATTRS_CACHE_MAX_BYTES = int(os.environ.get("ADA_ASSET_ATTRIBUTES_CACHE_MAX_BYTES", str(32 * 1024 * 1024)))
_ATTRS_CACHE: "OrderedDict[tuple[str, str], AttributesDocument]" = OrderedDict()


def _attributes_cached(cache_key: tuple[str, str], raw: bytes) -> AttributesDocument:
    """Parse once per (scope, key), or not at all for a document too big to keep."""
    doc = _ATTRS_CACHE.get(cache_key)
    if doc is not None:
        _ATTRS_CACHE.move_to_end(cache_key)
        return doc
    doc = parse_attributes(raw)
    if len(raw) > _ATTRS_CACHE_MAX_BYTES:
        return doc
    _ATTRS_CACHE[cache_key] = doc
    _ATTRS_CACHE.move_to_end(cache_key)
    while len(_ATTRS_CACHE) > _ATTRS_CACHE_ENTRIES:
        _ATTRS_CACHE.popitem(last=False)
    return doc


def clear_asset_attributes_cache() -> None:
    """Test hook. The cache is keyed by an immutable revision, so nothing in production needs it."""
    _ATTRS_CACHE.clear()


def _attributes_to_dict(node: str, attrs: NodeAttributes, revision: str | None, provider: str) -> dict:
    return {
        "node": node,
        "provider": provider,
        "revision": revision,
        "kind": attrs.kind,
        "own": dict(attrs.own),
        "groups": {name: dict(props) for name, props in attrs.groups.items()},
        "quantities": {name: dict(props) for name, props in attrs.quantities.items()},
    }


@router.get("/scopes/{scope}/assets/attributes/{provider}/{collection}/{node}")
async def api_asset_attributes(
    provider: str,
    collection: str,
    node: str,
    subject: str | None = None,
    revision: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """What one node IS -- its own attributes, its property groups and its quantities.

    Fetched per selection rather than carried in the spine: most nodes are never selected, and a
    spine that carried every node's properties would pay for all of them to answer for the few.

    ``subject`` names the subject whose publish COVERS this node, exactly as the build request
    does. It defaults to the node itself, which is right when the node was published in its own
    right; a node covered by a publish rooted above it has no manifest of its own, and the caller
    already resolved the covering subject to draw the row's badge.

    404 means "nothing recorded for this node", which is the same answer for a provider that
    publishes no attributes, a document that does not mention the node, and a node that is not
    published at all. A caller asking what something is cannot act differently on those three.
    """
    live = _is_published(provider) is False and hasattr(asset_provider(provider), "attributes")
    if live:
        attrs = await asyncio.to_thread(
            lambda: asset_provider(provider).attributes(scope_obj, collection, node, revision=revision)
        )
        if attrs is None:
            raise HTTPException(status_code=404, detail=f"no attributes recorded for node {node!r}")
        return JSONResponse(_attributes_to_dict(node, attrs, revision, provider))

    if not _is_published(provider):
        # A live provider that does not answer attributes is not an error in the abstraction --
        # the capability is optional -- so it reads as "nothing recorded" like any other absence.
        raise HTTPException(status_code=404, detail=f"provider {provider!r} records no attributes")

    manifest, resolved_revision = await _manifest_for_node(
        ctx, scope_obj, collection, subject or node, revision, provider
    )
    entry = next((a for a in manifest.artefacts if a.role == ATTRIBUTES_ROLE), None)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"subject {manifest.subject!r} at {resolved_revision} publishes no attributes",
        )
    key = entry.key or asset_key(collection, manifest.subject, manifest.revision, entry.file)
    try:
        raw = await ctx.storage.get_bytes(scope_obj, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no attributes blob at {key}") from exc
    try:
        doc = _attributes_cached((scope_obj.prefix(), key), raw)
    except AttributesError as exc:
        # The blob IS there and core cannot read it: a 502, not a 404, or the caller goes looking
        # for a missing file that is not missing.
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc
    attrs = doc.node(node)
    if attrs is None:
        raise HTTPException(status_code=404, detail=f"no attributes recorded for node {node!r}")
    return JSONResponse(_attributes_to_dict(node, attrs, manifest.revision, manifest.provider))


@router.get("/scopes/{scope}/assets/delivery/{provider}/{collection}/{node}")
async def api_asset_delivery(
    provider: str,
    collection: str,
    node: str,
    subject: str | None = None,
    revision: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The delivery claim for one node, or 404 when it has none.

    A ``mesh`` URL is minted per request and never cached -- it may be presigned and expire.

    ``subject`` names the subject whose publish COVERS the node, as the build and attributes
    routes take it; it defaults to the node itself. For a store-backed read, a ``provider`` other
    than ``published`` selects that provider's newest complete revision of the subject (see
    ``_manifest_for_node``); ``published`` takes the newest of any provider.
    """
    if not _is_published(provider):
        claim = await asyncio.to_thread(
            lambda: asset_provider(provider).delivery(scope_obj, collection, node, revision=revision)
        )
        if claim is None:
            raise HTTPException(status_code=404, detail=f"node {node!r} has no delivery claim")
        return JSONResponse(_claim_to_dict(claim))

    manifest, revision = await _manifest_for_node(ctx, scope_obj, collection, subject or node, revision, provider)

    if manifest.delivery == "none":
        raise HTTPException(status_code=404, detail=f"node {node!r} has no delivery claim")
    if manifest.delivery == "build":
        spec = manifest.build
        return JSONResponse(
            {
                "kind": "build",
                "capability": spec.capability,
                "options": dict(spec.options),
                "fingerprint_inputs": list(spec.fingerprint_inputs),
                "revision": manifest.revision,
                # The provider that PRODUCED this subject-revision, which is not necessarily the
                # one in the route: a collection may be mixed, with one branch fed by a provider
                # whose source is one format and a sibling branch by another, each with its own
                # build capability. The claim has to say which, or the browser cannot tell the
                # caller who will build it.
                "provider": manifest.provider,
            }
        )
    mesh = next((a for a in manifest.artefacts if a.role == "mesh"), None)
    if mesh is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"{asset_key(collection, manifest.subject, manifest.revision, MANIFEST_FILENAME)}: "
                f"delivery='mesh' but no artefact with role 'mesh'"
            ),
        )
    mesh_key = mesh.key or asset_key(collection, manifest.subject, manifest.revision, mesh.file)
    return JSONResponse(
        {
            "kind": "mesh",
            "url": mesh_key,
            "headers": {},
            "source_up_axis": "z",
            "revision": manifest.revision,
            "provider": manifest.provider,
        }
    )


@router.post("/scopes/{scope}/assets/build")
async def api_asset_build(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Build one node's geometry on demand. Body: ``{provider, collection, node, subject?, revision?}``.

    THE CLAIM IS READ SERVER-SIDE, from the node's own manifest -- the caller names a node, never
    a capability or a set of options. That is what keeps the layering rule true at the route: a
    browser cannot ask for a build of something that was not published as buildable, and it
    cannot influence the key, because core composes it from
    ``(provider, collection, subject, revision, node, fingerprint)`` and from nothing inside the
    provider's options.

    A REPEAT IS NOT A JOB. Identical requests fingerprint identically, so the summary is already
    at the key core just composed; the answer is that key with ``cached: true`` and no job. The
    alternative -- enqueueing and letting the worker's cached-blob short circuit notice -- costs a
    round trip through the queue to learn what the store already knew.
    """
    asked_provider = str(body.get("provider") or PUBLISHED_PROVIDER_ID)
    collection = str(body.get("collection") or "")
    node = str(body.get("node") or "")
    # WHICH SUBJECT SPEAKS FOR THE NODE. A node published in its own right is its own subject and
    # this is absent. A node COVERED by a publish rooted above it has no manifest of its own --
    # coverage is the whole point of Decision 3 -- so the caller names the covering subject, which
    # it already resolved to draw the row's badge. The route cannot work it out: it reads one
    # manifest by its exact key and holds no hierarchy to walk.
    #
    # The NODE still rides into the fingerprint and the derived key, so a covered build is scoped
    # to the node asked for rather than to the whole subject. That is Decision 3's "scoping must
    # be a property of every call, not an exception path" -- and without it two rows under one
    # publish would load byte-identical geometry under two names.
    subject = str(body.get("subject") or node)
    revision = body.get("revision")
    if not collection or not node:
        raise HTTPException(status_code=400, detail="'collection' and 'node' are required")

    ctx.jobs.require("asset_build")

    # WHICH PROVIDER'S PUBLISH. One subject can carry publishes from several providers, so a
    # store-backed provider id SELECTS the newest complete revision that provider wrote (an
    # explicit revision it did not write is a 409). ``published`` takes the newest of any.
    selecting = asked_provider if _is_published(asked_provider) else None
    manifest, revision = await _manifest_for_node(ctx, scope_obj, collection, subject, revision, selecting)
    if manifest.delivery != "build":
        raise HTTPException(
            status_code=409,
            detail=(
                f"subject {manifest.subject!r} at {revision} claims delivery {manifest.delivery!r}, "
                f"not 'build' -- a mesh claim is loaded directly and a subject with no claim "
                f"cannot be built"
            ),
        )
    # A LIVE-registered provider is not a store selector, but a build still reads the store: a
    # manifest another provider wrote is worth refusing rather than quietly building against --
    # the caller is acting on a view that has moved.
    if selecting is None and asked_provider != manifest.provider:
        raise HTTPException(
            status_code=409,
            detail=(
                f"subject {manifest.subject!r} at {revision} was produced by provider {manifest.provider!r}, "
                f"not {asked_provider!r}"
            ),
        )
    spec = manifest.build
    if spec is None:  # parse_manifest already refuses this pairing; belt and braces at the route
        raise HTTPException(status_code=502, detail="manifest claims 'build' but carries no build spec")

    # Which hierarchy placed this node. A leaf published against an older collection index and
    # the same leaf re-published against a newer one are two builds, because the node can sit
    # under a different parent -- so the tree the placement came from is part of the identity.
    hierarchy_source = manifest.hierarchy_revision or manifest.revision
    try:
        fingerprint = build_fingerprint(
            options=dict(spec.options),
            fingerprint_inputs=spec.fingerprint_inputs,
            node=node,
            hierarchy_source=hierarchy_source,
        )
        prefix = derived_asset_prefix(
            provider=manifest.provider,
            collection=collection,
            subject=manifest.subject,
            revision=revision,
            node=node,
            fingerprint=fingerprint,
        )
        derived_key = derived_asset_key(
            provider=manifest.provider,
            collection=collection,
            subject=manifest.subject,
            revision=revision,
            node=node,
            fingerprint=fingerprint,
        )
    except BuildError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    answer = {
        "derived_key": derived_key,
        "capability": spec.capability,
        "provider": manifest.provider,
        "subject": manifest.subject,
        "revision": revision,
        "node": node,
        "fingerprint": fingerprint,
    }
    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({**answer, "job_id": None, "cached": True})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=f"_synthetic/asset_build/{manifest.provider}/{collection}/{manifest.subject}/{fingerprint}",
            target_format="asset_build",
            scope=scope_obj,
            feature="asset_build",
            derived_prefix=prefix,
            derived_key=derived_key,
            # The provider's options stay opaque and travel whole; everything core will validate
            # the summary against travels beside them, composed here.
            conversion_options={
                "provider": manifest.provider,
                "collection": collection,
                "subject": manifest.subject,
                "revision": revision,
                "node": node,
                "fingerprint": fingerprint,
                "hierarchy_source": hierarchy_source,
                "capability": spec.capability,
                "options": dict(spec.options),
                "derived_prefix": prefix,
            },
            target_capability=spec.capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "asset_build",
            key=derived_key,
            target_format="asset_build",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({**answer, "job_id": submitted.job_id, "cached": False})


@router.post("/scopes/{scope}/assets/publish")
async def api_asset_publish(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Publish staged blobs. Body: ``{provider, staging_id | staged, collection?, options?,
    dry_run?, replace?}``.

    The provider DERIVES and core WRITES (``ada.assets.publish``), which is what makes the owner
    gate and the manifests-last ordering properties of the store rather than habits of each
    provider. Authorship is stamped HERE, from the same authenticated caller the audit row uses:
    a provider that returned ``change.published_by`` is refused by name.

    ``staging_id`` is the ordinary case -- everything under ``assets/_staging/<id>/`` is handed
    to the provider by role (its filename), which is why staging survives a reload: the keys are
    in the store, not in a browser's memory (see ``GET /assets/staging``).
    """
    provider = str(body.get("provider") or "")
    if not provider:
        raise HTTPException(
            status_code=400, detail="'provider' is required: it names whose format the staged bytes are in"
        )
    ctx.jobs.require("asset_publish")

    staged: dict[str, str] = {}
    staging_id = body.get("staging_id")
    if staging_id:
        prefix = staged_prefix(str(staging_id))
        entries = await ctx.storage.list_prefix(scope_obj, prefix)
        for entry in entries:
            staged[entry.key[len(prefix) :]] = entry.key
        if not staged:
            raise HTTPException(status_code=404, detail=f"nothing staged under {prefix}")
    else:
        raw = body.get("staged") or {}
        if not isinstance(raw, dict) or not raw:
            raise HTTPException(status_code=400, detail="one of 'staging_id' or a non-empty 'staged' map is required")
        for role, key in raw.items():
            key = str(key)
            # A staged key must BE staged: publishing "from" an arbitrary key in the scope would
            # let a caller re-derive over blobs it never uploaded, and the grammar reserves
            # `_staging/` for exactly this handover.
            if not key.startswith(f"{ASSET_PREFIX}/{STAGING_SEGMENT}/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"staged key {key!r} is outside {ASSET_PREFIX}/{STAGING_SEGMENT}/",
                )
            staged[str(role)] = key

    dry_run = bool(body.get("dry_run"))
    derived_key = f"_derived/assets/_publish/{provider}/{uuid.uuid4().hex[:16]}/summary.json"
    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=f"_synthetic/asset_publish/{provider}/{sorted(staged.values())[0]}",
            target_format="asset_publish",
            scope=scope_obj,
            feature="asset_publish",
            derived_key=derived_key,
            conversion_options={
                "provider": provider,
                "staged": staged,
                "collection": body.get("collection"),
                "options": body.get("options") or {},
                "dry_run": dry_run,
                "replace": bool(body.get("replace")),
                # Decision 6's three trust levels: this is the CORE-STAMPED one, taken from the
                # authenticated caller and never from the request body.
                "published_by_id": getattr(user, "sub", None) or getattr(user, "id", None) or "unknown",
                "published_by_display": getattr(user, "display_name", None) or getattr(user, "email", None),
                "published_via": _published_via(user),
            },
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "asset_publish",
            key=derived_key,
            target_format="asset_publish",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "dry_run": dry_run})


@router.get("/scopes/{scope}/assets/staging")
async def api_asset_staging(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """What is staged and not yet published, grouped by staging id.

    STAGING RECOVERY IS A LISTING, not a session. An upload that finished and a publish that was
    never started leave bytes in the scope with nobody's browser remembering them; without this
    the only trace is a prefix nothing lists, and the user re-uploads a file that is already
    there. The store is the memory.
    """
    prefix = f"{ASSET_PREFIX}/{STAGING_SEGMENT}/"
    entries = await ctx.storage.list_prefix(scope_obj, prefix)
    staged: dict[str, dict] = {}
    for entry in entries:
        rest = entry.key[len(prefix) :]
        staging_id, _, filename = rest.partition("/")
        if not staging_id or not filename:
            continue
        group = staged.setdefault(staging_id, {"staging_id": staging_id, "files": [], "size": 0})
        group["files"].append({"file": filename, "key": entry.key, "size": getattr(entry, "size", None)})
        group["size"] += getattr(entry, "size", 0) or 0
    return JSONResponse({"staged": [staged[k] for k in sorted(staged)]})


def _classify_asset_file(key: str) -> dict | None:
    """What an asset-area key IS: a published file, a staged one, or a cached build.

    Three areas, three different answers to "may this one file go":
      published  ``assets/<collection>/<subject>/<revision>/<file>`` -- NOT on its own: a revision's
                 manifest names its files, and one missing is a publish that lists and badges like
                 a working one and fails at load. It goes with its revision (the unpublish route).
      staged     ``assets/_staging/<id>/<file>`` -- yes: nothing references staging.
      derived    ``_derived/assets/<provider>/<collection>/<subject>/<revision>/<node>/<fp>/<file>``
                 -- yes: a cached build, made again on the next load that asks for it.
    """
    if key.startswith(f"{ASSET_PREFIX}/{STAGING_SEGMENT}/"):
        staging_id, _, filename = key[len(f"{ASSET_PREFIX}/{STAGING_SEGMENT}/") :].partition("/")
        if staging_id and filename:
            return {"area": "staged", "staging_id": staging_id, "file": filename}
        return None
    if key.startswith(f"{ASSET_PREFIX}/"):
        parts = key[len(ASSET_PREFIX) + 1 :].split("/", 3)
        if len(parts) == 4 and all(parts):
            collection, subject, revision, filename = parts
            return {
                "area": "published",
                "collection": collection,
                "subject": subject,
                "revision": revision,
                "file": filename,
            }
        return None
    if key.startswith(f"{DERIVED_ASSET_PREFIX}/_publish/"):
        # A publish job's outcome summary: what a request reads back once the job is done.
        provider, _, rest = key[len(f"{DERIVED_ASSET_PREFIX}/_publish/") :].partition("/")
        job, _, filename = rest.partition("/")
        if provider and job and filename:
            return {"area": "derived", "kind": "publish-summary", "provider": provider, "job": job, "file": filename}
        return None
    if key.startswith(f"{_ROLLUP_PREFIX}/"):
        # A collection's geometry roll-up: derived from the whole collection rather than one
        # source, so no source owns it; a newer listing replaces it (see api_asset_geometry).
        collection, _, filename = key[len(f"{_ROLLUP_PREFIX}/") :].partition("/")
        if collection and filename:
            return {"area": "derived", "kind": "geometry-rollup", "collection": collection, "file": filename}
        return None
    if key.startswith(f"{DERIVED_ASSET_PREFIX}/"):
        parts = key[len(DERIVED_ASSET_PREFIX) + 1 :].split("/", 6)
        if len(parts) == 7 and all(parts):
            provider, collection, subject, revision, node, fingerprint, filename = parts
            return {
                "area": "derived",
                "kind": "build",
                "provider": provider,
                "collection": collection,
                "subject": subject,
                "revision": revision,
                "node": None if node == "all" else node,
                "fingerprint": fingerprint,
                "file": filename,
            }
    return None


@router.get("/scopes/{scope}/assets/files")
async def api_asset_files(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Every file in this scope's asset areas -- published, staged and cached builds -- with size
    and time, each classified (see :func:`_classify_asset_file`). What a "manage files" view lists;
    a key that fits no area's grammar is reported under ``unrecognised`` rather than dropped."""
    files: list[dict] = []
    unrecognised: list[dict] = []
    for prefix in (f"{ASSET_PREFIX}/", f"{DERIVED_ASSET_PREFIX}/"):
        for entry in await ctx.storage.list_prefix(scope_obj, prefix):
            row = {
                "key": entry.key,
                "size": getattr(entry, "size", 0) or 0,
                "last_modified": getattr(entry, "last_modified", None),
            }
            kind = _classify_asset_file(entry.key)
            if kind is None:
                unrecognised.append(row)
            else:
                files.append({**row, **kind})
    totals: dict[str, dict] = {}
    for f in files:
        t = totals.setdefault(f["area"], {"files": 0, "size": 0})
        t["files"] += 1
        t["size"] += f["size"]
    return JSONResponse({"files": files, "unrecognised": unrecognised, "totals": totals})


@router.delete("/scopes/{scope}/assets/files")
async def api_asset_file_delete(
    key: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Delete ONE staged file or cached-build file. A published file is refused with 409 naming the
    revision it belongs to: it goes only with that revision, through the unpublish route, which
    also refuses while another manifest still names it."""
    kind = _classify_asset_file(key)
    if kind is None:
        raise HTTPException(status_code=400, detail=f"{key!r} is not in an asset area")
    if kind["area"] == "published":
        return JSONResponse(
            {
                "ok": False,
                "reason": (
                    f"{key} belongs to the published revision {kind['collection']}/{kind['subject']}@"
                    f"{kind['revision']}; a published file goes with its revision -- unpublish that"
                ),
                **kind,
            },
            status_code=409,
        )
    if not await ctx.storage.exists(scope_obj, key):
        raise HTTPException(status_code=404, detail=f"no file {key!r} in this scope")
    await ctx.storage.delete(scope_obj, key)
    await ctx.audit(request, user, scope_obj, "asset_file_delete", key=key, status="done")
    return JSONResponse({"ok": True, "deleted": [key], **kind})


# --- sources: one publish, and everything derived from it -----------------------------------------
#
# What a person manages is not files but SOURCES: one provider publishing into one collection at one
# revision -- an exported model file, a provider's project tree. Everything else is derived from one of
# them: the per-subject hierarchies, attributes and manifests the publish wrote, the builds made
# from it, the publish job's own summary. A source is listed as one entry and deleted as one, and
# what was derived from it goes with it -- a derived file whose source is gone is only a way to
# show something that is no longer there.


async def _publish_sources(ctx: RestContext, scope_obj: Scope) -> tuple[dict, list]:
    """``{(collection, revision, provider): group}`` over every published revision, and the derived
    files no remaining source claims (``orphans``).

    A group is ``{subjects, published: [FileEntry], derived: [FileEntry], manifests: {subject: m}}``.
    The provider is read off each subject-revision's manifest; a revision folder with no manifest
    (a publish that died partway) is grouped under provider ``"?"``.
    """
    groups: dict[tuple[str, str, str], dict] = {}
    by_revision: dict[tuple[str, str, str], list] = {}
    for entry in await ctx.storage.list_prefix(scope_obj, f"{ASSET_PREFIX}/"):
        kind = _classify_asset_file(entry.key)
        if kind and kind["area"] == "published":
            by_revision.setdefault((kind["collection"], kind["subject"], kind["revision"]), []).append(entry)

    async def _manifest(coll: str, subject: str, rev: str):
        try:
            return parse_manifest(
                await ctx.storage.get_bytes(scope_obj, asset_key(coll, subject, rev, MANIFEST_FILENAME))
            )
        except Exception:  # noqa: BLE001 - absent or unreadable: grouped as unknown, not dropped
            return None

    keys = list(by_revision)
    manifests = await asyncio.gather(*(_manifest(*k) for k in keys))
    for (coll, subject, rev), manifest in zip(keys, manifests):
        provider = manifest.provider if manifest is not None else "?"
        g = groups.setdefault(
            (coll, rev, provider), {"subjects": set(), "published": [], "derived": [], "manifests": {}}
        )
        g["subjects"].add(subject)
        g["published"].extend(by_revision[(coll, subject, rev)])
        if manifest is not None:
            g["manifests"][subject] = manifest

    orphans: list = []
    summaries: list = []
    for entry in await ctx.storage.list_prefix(scope_obj, f"{DERIVED_ASSET_PREFIX}/"):
        kind = _classify_asset_file(entry.key)
        if not kind:
            orphans.append(entry)
        elif kind.get("kind") == "publish-summary":
            summaries.append((entry, kind))
        elif kind.get("kind") == "geometry-rollup":
            continue  # a cache over the whole collection, not derived from any one source
        else:
            g = groups.get((kind["collection"], kind["revision"], kind["provider"]))
            if g is not None and kind["subject"] in g["subjects"]:
                g["derived"].append(entry)
            else:
                orphans.append(entry)

    # A publish job's summary says which collection and revision it wrote; read it to link it.
    async def _summary(entry):
        try:
            raw = await ctx.storage.get_bytes(scope_obj, entry.key)
            # Derived JSON is gzip-at-rest.
            doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
            return doc if isinstance(doc, dict) else None
        except Exception:  # noqa: BLE001
            return None

    docs = await asyncio.gather(*(_summary(e) for e, _ in summaries))
    for (entry, kind), doc in zip(summaries, docs):
        g = groups.get(
            (str((doc or {}).get("collection") or ""), str((doc or {}).get("revision") or ""), kind["provider"])
        )
        (g["derived"] if g is not None else orphans).append(entry)
    return groups, orphans


def _source_label(collection: str, provider: str, group: dict) -> str:
    """What the entry is called: the source file(s) the publish stored, else what it is -- a
    project tree when it wrote the collection's own index (``tree-<provider>``)."""
    stored = sorted(
        {
            (a.key or a.file or "").rsplit("/", 1)[-1]
            for m in group["manifests"].values()
            for a in m.artefacts
            if a.role in ("source", "tree-source") and (a.key or a.file)
        }
    )
    if stored:
        return ", ".join(stored)
    if collection in group["subjects"]:
        return f"tree-{provider}"
    return f"{provider}-publish"


def _source_row(collection: str, revision: str, provider: str, group: dict) -> dict:
    published = group["published"]
    derived = group["derived"]
    return {
        "collection": collection,
        "revision": revision,
        "provider": provider,
        "label": _source_label(collection, provider, group),
        "subjects": len(group["subjects"]),
        "size": sum(getattr(e, "size", 0) or 0 for e in published),
        "derived_files": len(published) + len(derived),
        "derived_size": sum(getattr(e, "size", 0) or 0 for e in published + derived),
        "last_modified": max((getattr(e, "last_modified", None) or "" for e in published), default="") or None,
    }


@router.get("/scopes/{scope}/assets/sources")
async def api_asset_sources(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Every source in this scope (one per publish), with how much is derived from it, plus the
    derived files whose source is gone (``orphans``) -- which should not exist, and are listed so
    they can be removed."""
    groups, orphans = await _publish_sources(ctx, scope_obj)
    sources = [_source_row(c, r, p, g) for (c, r, p), g in groups.items()]
    sources.sort(key=lambda s: (s["collection"], s["revision"]), reverse=True)
    return JSONResponse(
        {
            "sources": sources,
            "orphans": [{"key": e.key, "size": getattr(e, "size", 0) or 0} for e in orphans],
        }
    )


@router.get("/scopes/{scope}/assets/sources/{collection}/{revision}")
async def api_asset_source_files(
    collection: str,
    revision: str,
    provider: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One source's files: what it published and what was derived from it."""
    groups, _ = await _publish_sources(ctx, scope_obj)
    group = groups.get((collection, revision, provider))
    if group is None:
        raise HTTPException(status_code=404, detail=f"no source {provider} {collection}@{revision} in this scope")

    def rows(entries: list) -> list[dict]:
        return [{"key": e.key, "size": getattr(e, "size", 0) or 0} for e in sorted(entries, key=lambda e: e.key)]

    return JSONResponse(
        {
            **_source_row(collection, revision, provider, group),
            "published": rows(group["published"]),
            "derived": rows(group["derived"]),
        }
    )


@router.delete("/scopes/{scope}/assets/sources/{collection}/{revision}")
async def api_asset_source_delete(
    collection: str,
    revision: str,
    provider: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Delete one source and EVERYTHING derived from it: every subject it published at this revision
    (manifests first, so a part-done delete reads as unpublished rather than broken), then its
    cached builds and job summaries.

    Refused (409, naming them) while a manifest of ANOTHER source still references one of its files:
    deleting it would leave that one listing and badging like a working asset that fails at load.
    """
    groups, _ = await _publish_sources(ctx, scope_obj)
    group = groups.get((collection, revision, provider))
    if group is None:
        raise HTTPException(status_code=404, detail=f"no source {provider} {collection}@{revision} in this scope")
    targets = {e.key for e in group["published"]}

    holders: set[str] = set()
    for (c, r, p), other in groups.items():
        if (c, r, p) == (collection, revision, provider):
            continue
        for subject, manifest in other["manifests"].items():
            if any((a.key or "") in targets for a in manifest.artefacts):
                holders.add(f"{p} {c}/{subject}@{r}")
    if holders:
        return JSONResponse(
            {
                "ok": False,
                "reason": f"{len(holders)} other publish(es) still reference files of this source "
                f"(e.g. {sorted(holders)[0]}); delete those first",
                "held_by": sorted(holders),
            },
            status_code=409,
        )

    manifests_first = sorted(k for k in targets if k.endswith("/" + MANIFEST_FILENAME))
    ordered = manifests_first + sorted(targets - set(manifests_first)) + sorted(e.key for e in group["derived"])
    deleted: list[str] = []
    errors: dict[str, str] = {}
    for key in ordered:
        try:
            await ctx.storage.delete(scope_obj, key)
            deleted.append(key)
        except Exception as exc:  # noqa: BLE001 - one key's failure is not the others'
            errors[key] = str(exc)
    await ctx.audit(
        request,
        user,
        scope_obj,
        "asset_source_delete",
        key=f"{ASSET_PREFIX}/{collection}/*/{revision}/ ({provider})",
        status="error" if errors else "done",
    )
    return JSONResponse({"ok": not errors, "deleted": deleted, "errors": errors})


@router.delete("/scopes/{scope}/assets/{collection}/{subject}/{revision}")
async def api_asset_unpublish(
    collection: str,
    subject: str,
    revision: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Unpublish one subject-revision, refusing while another manifest still names its blobs.

    The refcount check the prior art left as "the publisher's obligation" (Decision 3), moved to
    the route: an obligation every publisher must remember is one that will eventually be
    forgotten, and what it costs is a manifest pointing at a deleted blob -- an asset that lists,
    resolves and badges like a working one and fails only at load.

    Answers ``{deleted, kept, reason}``: a refusal is a 409 whose reason names who is holding it.
    """
    try:
        keys = await _list_asset_keys(ctx, scope_obj, f"{ASSET_PREFIX}/{collection}/")
    except (FileNotFoundError, KeyError):
        keys = []
    manifests: dict[str, bytes] = {}
    for key in keys:
        if key.rsplit("/", 1)[-1] != MANIFEST_FILENAME:
            continue
        try:
            manifests[key] = await ctx.storage.get_bytes(scope_obj, key)
        except (FileNotFoundError, KeyError):
            continue

    plan = plan_unpublish(
        collection=collection,
        subject=subject,
        revision=revision,
        collection_keys=keys,
        manifest_bytes=manifests,
    )
    if plan.refused:
        status = 404 if not plan.kept and not plan.held_by else 409
        return JSONResponse({**plan.to_dict(), "ok": False}, status_code=status)

    deleted: list[str] = []
    errors: dict[str, str] = {}
    for key in plan.deleted:
        try:
            await ctx.storage.delete(scope_obj, key)
            deleted.append(key)
        except Exception as exc:  # noqa: BLE001 - one key's failure is not the others'
            errors[key] = str(exc)
    await ctx.audit(
        request,
        user,
        scope_obj,
        "asset_unpublish",
        key=f"{ASSET_PREFIX}/{collection}/{subject}/{revision}/",
        status="error" if errors else "done",
    )
    return JSONResponse({**plan.to_dict(), "deleted": deleted, "errors": errors, "ok": not errors})


def _published_via(user: object) -> str:
    """``user`` or ``service`` -- Decision 6's first two trust levels, told apart by WHO CALLED.

    A scheduled firing, a mirror and a worker with no human in the path all arrive as the
    deployment's own identity (``SystemUser``, ``sub == "system"``), and recording those as a
    user publish would put a person's name on a revision nobody pushed. The distinction is read
    from the authenticated principal rather than from anything in the request body, for the same
    reason ``published_by`` is: a caller that could state it could also misstate it.
    """
    return "service" if getattr(user, "sub", None) == "system" else "user"


# --- helpers -------------------------------------------------------------------------------------

# Bounded fan-out for the manifest fold: enough to hide per-object latency, few enough that one
# index request cannot monopolise the storage client.
_MANIFEST_READ_CONCURRENCY = 16


async def _fold_manifest_summaries(ctx: RestContext, scope: Scope, collection: str, body: dict) -> None:
    """Attach ``manifest`` (or ``manifest_error``) to every revision entry that has one.

    A manifest that cannot be read is reported on its revision, never dropped: the revision is
    still listed, and the tab has to be able to say why it cannot badge it.
    """
    gate = asyncio.Semaphore(_MANIFEST_READ_CONCURRENCY)

    async def one(subject: str, rev: dict) -> None:
        key = asset_key(collection, subject, rev["revision"], MANIFEST_FILENAME)
        async with gate:
            try:
                raw = await ctx.storage.get_bytes(scope, key)
            except (FileNotFoundError, KeyError):
                rev["manifest_error"] = f"listed but not readable: {key}"
                return
        try:
            rev["manifest"] = manifest_summary(parse_manifest(raw))
        except ManifestError as exc:
            rev["manifest_error"] = str(exc)

    await asyncio.gather(
        *(
            one(entry["subject"], rev)
            for entry in body["collections"].get(collection, [])
            for rev in entry["revisions"]
            if MANIFEST_FILENAME in rev["files"]
        )
    )


def _selecting_provider(provider: str | None) -> str | None:
    """The provider a store-backed read must SELECT by, or ``None`` for "any provider".

    ``published`` is the provider-agnostic sentinel: newest complete revision, whoever wrote it.
    Any other id names whose publish the caller wants, because one subject can carry publishes
    from several providers (the key grammar has no provider segment).
    """
    if not provider or provider == PUBLISHED_PROVIDER_ID:
        return None
    return provider


async def _read_manifest(ctx: RestContext, scope: Scope, collection: str, subject: str, revision: str):
    key = asset_key(collection, subject, revision, MANIFEST_FILENAME)
    try:
        raw = await ctx.storage.get_bytes(scope, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no {MANIFEST_FILENAME} at {key}") from exc
    try:
        return parse_manifest(raw)
    except ManifestError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc


async def _manifest_for_node(
    ctx: RestContext,
    scope: Scope,
    collection: str,
    node: str,
    revision: str | None,
    provider: str | None = None,
):
    """The manifest that speaks for ``node``, and the revision it was read at.

    One reader for the delivery claim, the build request and the attributes, so they cannot
    disagree about which revision a node resolves to -- a build keyed on one revision while the
    claim shown came from another is exactly the inconsistency the single-resolution discipline
    exists to stop.

    ``provider`` (anything but ``published``) SELECTS: the newest complete revision whose manifest
    names that provider. An explicit ``revision`` written by another provider is a 409 -- the
    caller is acting on a view that has moved -- and a subject that provider never published is a
    404 naming it.
    """
    wanted = _selecting_provider(provider)
    if wanted is None:
        revision = revision or await _latest_complete_revision(ctx, scope, collection, node)
        if revision is None:
            raise HTTPException(status_code=404, detail=f"no published revision for node {node!r}")
        return await _read_manifest(ctx, scope, collection, node, revision), revision

    if revision:
        manifest = await _read_manifest(ctx, scope, collection, node, revision)
        if manifest.provider != wanted:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"subject {node!r} at {revision} was produced by provider {manifest.provider!r}, " f"not {wanted!r}"
                ),
            )
        return manifest, revision

    for candidate in await _complete_revisions(ctx, scope, collection, node):
        try:
            manifest = await _read_manifest(ctx, scope, collection, node, candidate)
        except HTTPException:
            # A manifest core cannot read says nothing about who wrote it; an older revision of
            # the wanted provider is still a valid answer.
            continue
        if manifest.provider == wanted:
            return manifest, candidate
    raise HTTPException(
        status_code=404,
        detail=f"no published revision of subject {node!r} in {collection!r} by provider {wanted!r}",
    )


async def _complete_revisions(ctx: RestContext, scope: Scope, collection: str, subject: str) -> list[str]:
    """Every revision of ``subject`` that HAS a manifest, newest first."""
    keys = await _list_asset_keys(ctx, scope, f"{ASSET_PREFIX}/{collection}/{subject}/")
    entry = fold_listing(keys).subject(collection, subject)
    if entry is None:
        return []
    return [r.revision for r in entry.revisions if r.has_manifest]


async def _latest_complete_revision(ctx: RestContext, scope: Scope, collection: str, subject: str) -> str | None:
    """Newest revision that actually HAS a manifest.

    Manifests are written last, so a newer manifest-less revision is a half-written publish and
    must not shadow the last good one.
    """
    keys = await _list_asset_keys(ctx, scope, f"{ASSET_PREFIX}/{collection}/{subject}/")
    entry = fold_listing(keys).subject(collection, subject)
    if entry is None:
        return None
    complete = entry.latest_complete
    return complete.revision if complete is not None else None


async def _live_hierarchy(provider: str, scope: Scope, collection: str, root: str | None, depth: int):
    try:
        return await asyncio.to_thread(
            lambda: asset_provider(provider).hierarchy(scope, collection, root=root, depth=depth)
        )
    except AssetProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _slice_to_dict(slice_, rows=None) -> dict:
    """``rows`` narrows the body to a subset of the slice's rows (one level of a cached spine)."""
    return {
        "schema": slice_.schema,
        "provider": slice_.provider,
        "collection": slice_.collection,
        "root": slice_.root,
        "produced_at": slice_.produced_at,
        "depth": slice_.depth,
        "cols": list(slice_.cols),
        "rows": [list(r) for r in (slice_.rows if rows is None else rows)],
        # The provider's drawing suggestion rides through untouched (additive at hierarchy@1).
        **({"view": dict(slice_.view)} if getattr(slice_, "view", None) else {}),
    }


def _claim_to_dict(claim) -> dict:
    if isinstance(claim, MeshDelivery):
        return {
            "kind": "mesh",
            "url": claim.url,
            "headers": dict(claim.headers),
            "source_up_axis": claim.source_up_axis,
            "revision": claim.revision,
        }
    if isinstance(claim, BuildDelivery):
        return {
            "kind": "build",
            "capability": claim.capability,
            "options": dict(claim.options),
            "fingerprint_inputs": list(claim.fingerprint_inputs),
            "revision": claim.revision,
        }
    raise HTTPException(status_code=502, detail=f"provider returned an unknown delivery claim {type(claim).__name__}")
