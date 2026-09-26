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
import os
import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.assets.attributes import (
    ATTRIBUTES_ROLE,
    AttributesDocument,
    AttributesError,
    NodeAttributes,
    parse_attributes,
)
from ada.assets.build import (
    BuildError,
    build_fingerprint,
    derived_asset_key,
    derived_asset_prefix,
)
from ada.assets.index import fold_listing
from ada.assets.keys import ASSET_PREFIX, STAGING_SEGMENT, AssetKeyError, asset_key
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ManifestError,
    manifest_summary,
    parse_manifest,
)
from ada.assets.projection import HierarchyError, parse_hierarchy
from ada.assets.provider import BuildDelivery, MeshDelivery
from ada.assets.publish import staged_prefix
from ada.assets.registry import (
    AssetProviderError,
    asset_provider,
    asset_providers,
    registered_provider_ids,
)
from ada.assets.unpublish import plan_unpublish

from .. import auth as auth_module
from ..auth import User
from ..job_transport import JobRequest
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()

PUBLISHED_PROVIDER_ID = "published"


async def _list_asset_keys(ctx: RestContext, scope: Scope, prefix: str) -> list[str]:
    entries = await ctx.storage.list_prefix(scope, prefix)
    return [e.key for e in entries]


def _is_published(provider: str) -> bool:
    """The built-in path serves any provider that publishes under the key grammar.

    A provider only needs a registration when it answers LIVE; a publish-only provider rides the
    store and needs none, which is exactly what keeps a private-format provider cheap.
    """
    return provider == PUBLISHED_PROVIDER_ID or provider not in registered_provider_ids()


@router.get("/scopes/{scope}/assets/providers")
async def api_asset_providers(scope_obj: Scope = Depends(scope_from_path)) -> JSONResponse:
    """Registered providers. The built-in ``published`` one is always offered, because a scope may
    hold published assets from a provider this process has never heard of."""
    providers = [
        {
            "id": PUBLISHED_PROVIDER_ID,
            "label": "Published",
            "live": True,
            "delivery": ["mesh", "build"],
            "capabilities": ["tree"],
        }
    ]
    providers.extend(p for p in asset_providers() if p["id"] != PUBLISHED_PROVIDER_ID)
    return JSONResponse({"providers": providers})


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


@router.get("/scopes/{scope}/assets/tree/{provider}/{collection}")
async def api_asset_tree(
    provider: str,
    collection: str,
    root: str | None = None,
    depth: int = 1,
    revision: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """A hierarchy slice. Published or live -- the browser cannot tell which, by design.

    ``provider`` in the path selects how the slice is OBTAINED, not who produced each node. A
    published collection may be mixed: its rows can carry a per-node ``provider`` column naming a
    different producer per branch, and the delivery claim for each node names the same.
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
    try:
        raw = await ctx.storage.get_bytes(scope_obj, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no {HIERARCHY_FILENAME} at {key}") from exc
    try:
        return JSONResponse(_slice_to_dict(parse_hierarchy(raw)))
    except HierarchyError as exc:
        # A stored blob core cannot read is a 502, not a 404: the object IS there, and calling it
        # missing would send the caller looking for the wrong problem.
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc


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

    manifest, resolved_revision = await _manifest_for_node(ctx, scope_obj, collection, subject or node, revision)
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
    revision: str | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """The delivery claim for one node, or 404 when it has none.

    A ``mesh`` URL is minted per request and never cached -- it may be presigned and expire.
    """
    if not _is_published(provider):
        claim = await asyncio.to_thread(
            lambda: asset_provider(provider).delivery(scope_obj, collection, node, revision=revision)
        )
        if claim is None:
            raise HTTPException(status_code=404, detail=f"node {node!r} has no delivery claim")
        return JSONResponse(_claim_to_dict(claim))

    manifest, revision = await _manifest_for_node(ctx, scope_obj, collection, node, revision)

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
                f"{asset_key(collection, node, manifest.revision, MANIFEST_FILENAME)}: "
                f"delivery='mesh' but no artefact with role 'mesh'"
            ),
        )
    mesh_key = mesh.key or asset_key(collection, node, manifest.revision, mesh.file)
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

    manifest, revision = await _manifest_for_node(ctx, scope_obj, collection, subject, revision)
    if manifest.delivery != "build":
        raise HTTPException(
            status_code=409,
            detail=(
                f"subject {manifest.subject!r} at {revision} claims delivery {manifest.delivery!r}, "
                f"not 'build' -- a mesh claim is loaded directly and a subject with no claim "
                f"cannot be built"
            ),
        )
    # The caller names the provider it believes produced this node. Core does not need it --
    # the manifest is authoritative, and in a MIXED collection (Decision 18) the producing
    # provider is per node -- but a disagreement is worth refusing rather than quietly building
    # against the other one: the caller is acting on a view that has moved.
    if asked_provider not in (PUBLISHED_PROVIDER_ID, manifest.provider):
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


async def _manifest_for_node(ctx: RestContext, scope: Scope, collection: str, node: str, revision: str | None):
    """The manifest that speaks for ``node``, and the revision it was read at.

    One reader for the delivery claim and the build request, so the two cannot disagree about
    which revision a node resolves to -- a build keyed on one revision while the claim shown came
    from another is exactly the inconsistency the single-resolution discipline exists to stop.
    """
    revision = revision or await _latest_complete_revision(ctx, scope, collection, node)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"no published revision for node {node!r}")
    key = asset_key(collection, node, revision, MANIFEST_FILENAME)
    try:
        raw = await ctx.storage.get_bytes(scope, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no {MANIFEST_FILENAME} at {key}") from exc
    try:
        return parse_manifest(raw), revision
    except ManifestError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc


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


def _slice_to_dict(slice_) -> dict:
    return {
        "schema": slice_.schema,
        "provider": slice_.provider,
        "collection": slice_.collection,
        "root": slice_.root,
        "produced_at": slice_.produced_at,
        "depth": slice_.depth,
        "cols": list(slice_.cols),
        "rows": [list(r) for r in slice_.rows],
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
