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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ada.assets.index import fold_listing
from ada.assets.keys import ASSET_PREFIX, AssetKeyError, asset_key
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ManifestError,
    manifest_summary,
    parse_manifest,
)
from ada.assets.projection import HierarchyError, parse_hierarchy
from ada.assets.provider import BuildDelivery, MeshDelivery
from ada.assets.registry import (
    AssetProviderError,
    asset_provider,
    asset_providers,
    registered_provider_ids,
)

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

    revision = revision or await _latest_complete_revision(ctx, scope_obj, collection, node)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"no published revision for node {node!r}")
    key = asset_key(collection, node, revision, MANIFEST_FILENAME)
    try:
        raw = await ctx.storage.get_bytes(scope_obj, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no {MANIFEST_FILENAME} at {key}") from exc
    try:
        manifest = parse_manifest(raw)
    except ManifestError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc

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
            detail=f"{key}: delivery='mesh' but no artefact with role 'mesh'",
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
