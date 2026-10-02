"""Clashes routes: ``/api/scopes/{scope}/clash-check``, ``/clash-detail`` and the
``connection-specs`` listing (Decision 10, Phase 6).

THE ROUTE NEVER OPENS THE SOURCE. Composing a derived key costs a ``head()`` at most (a cheap
version discriminator, same rationale as the worker's own source-blob cache); the worker is what
downloads the file and computes a real content hash, because that is the one place the bytes are
already local (see ``formats/clash_check.py``).

Cache discipline mirrors ``POST /assets/build`` exactly: identical ``(source_key, options)`` ->
identical derived key, so a repeat is answered from the store without enqueueing anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.assets.keys import ASSET_PREFIX
from ada.assets.rollup import listing_token

# The LIGHT modules only: this router is imported at startup by the slim api, which has no
# numpy and no reader. `ada.clash.identify` (and the passes it imports) runs on a worker, and so
# does `ada.clash.group_model`; `ada.clash.group` is its stdlib-only request half, and
# `ada.clash.geometry_source` reads JSON only (manifests and hierarchy documents).
from ada.clash.builtin_names import BUILTIN_CHECKER, BUILTIN_SPEC_NAMES
from ada.clash.geometry_source import node_group, plan_geometry
from ada.clash.group import (
    GroupError,
    group_derived_prefix,
    group_token,
    normalise_group,
)
from ada.clash.options import ClashOptions
from ada.clash.result import ClashResultError, parse_clash_result

from .. import auth as auth_module
from ..auth import User
from ..catalog import merge_catalog_specs
from ..job_transport import JobRequest
from ..queue import capability_token
from ..scope import Scope
from .deps import RestContext, rest_context, scope_from_path

router = APIRouter()


def _options_from_body(raw: object) -> ClashOptions:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")
    try:
        return ClashOptions.from_dict(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"bad options: {exc}") from exc


def _group_from_body(raw: object) -> dict:
    try:
        return normalise_group(raw)
    except GroupError as exc:
        raise HTTPException(status_code=400, detail=f"bad group: {exc}") from exc


async def _source_content_token(ctx: RestContext, scope: Scope, source_key: str) -> str:
    """A cheap token that changes when the source's bytes change, used ONLY to shape the derived
    key -- never stored as the result's ``source_sha256`` (the worker computes a real one from
    the downloaded bytes; see ``formats/clash_check.py``). ``head()``'s ``e_tag`` is a real
    content discriminator when the backend reports one (S3/Garage always do); falling back to the
    key itself means a same-content re-upload under a NEW key is a cache MISS, which is the safe
    direction -- never serving a stale check silently."""
    head = await ctx.storage.head(scope, source_key)
    token = (head or {}).get("e_tag") or source_key
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:16]


def _options_token(options: ClashOptions) -> str:
    return hashlib.sha256(json.dumps(options.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


#: Separates the clash check's use of a collection listing token from the roll-up's.
_GEOMETRY_STATE_SALT = "ada.clash/geometry-state@1"


async def _geometry_options_token(ctx: RestContext, scope: Scope, options: ClashOptions, collections) -> str:
    """The options token, with -- when the check reads through a geometry provider -- what is
    published in every collection its members are matched in.

    WHY. A check through another provider's geometry is a function of that provider's publishes as
    much as of the members: a member it had no node for is left out, and once the provider publishes
    the node the same request has to be a cache MISS, or the user who just asked for the geometry
    would be served the check that left it out. The token is the collection's LISTING token (the one
    the geometry roll-up keys on, ``ada.assets.rollup.listing_token``): any publish into the
    collection moves it, the chosen provider's included -- coarser than that provider alone, which is
    the safe direction, and one ``list_prefix`` per collection rather than a manifest read per
    subject.

    Without a geometry provider the plain options token is returned unchanged, so the derived key of
    every default check -- and every result cached before this -- stays where it was.
    """
    token = _options_token(options)
    if options.geometry_provider is None:
        return token
    parts = [token]
    for collection in sorted(set(collections)):
        entries = await ctx.storage.list_prefix(scope, f"{ASSET_PREFIX}/{collection}/")
        parts.append(f"{collection}={listing_token(entries, salt=_GEOMETRY_STATE_SALT)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


class _ScopeStorageBridge:
    """The async scope storage as the SYNCHRONOUS facade ``ada.clash.geometry_source`` reads
    (``list_keys``, ``get_bytes``, and the batched ``get_many``).

    For code run in a worker thread (``asyncio.to_thread``) while the event loop serves its reads:
    each call is handed to the loop and waited for. One implementation of the matching rules -- the
    worker's -- then serves the API too, instead of an async copy that could drift from it.
    ``get_many`` reads concurrently, bounded, so a plan over a collection of a few thousand subjects
    costs a few round trips' worth of time rather than one per manifest.
    """

    def __init__(self, ctx: RestContext, scope: Scope, loop: asyncio.AbstractEventLoop, *, concurrency: int = 16):
        self._ctx = ctx
        self._scope = scope
        self._loop = loop
        self._concurrency = concurrency

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def list_keys(self, prefix: str = "") -> list[str]:
        return [e.key for e in self._run(self._ctx.storage.list_prefix(self._scope, prefix))]

    def get_bytes(self, key: str) -> bytes:
        return self._run(self._ctx.storage.get_bytes(self._scope, key))

    def get_many(self, keys) -> dict[str, bytes | None]:
        return self._run(self._gather(list(keys)))

    async def _gather(self, keys: list[str]) -> dict[str, bytes | None]:
        gate = asyncio.Semaphore(self._concurrency)

        async def one(key: str) -> tuple[str, bytes | None]:
            async with gate:
                try:
                    return key, await self._ctx.storage.get_bytes(self._scope, key)
                except (FileNotFoundError, KeyError):
                    return key, None

        return dict(await asyncio.gather(*(one(k) for k in keys)))


def _readable_pools(entry: dict | None) -> set[str | None]:
    """The pools (capability tokens; ``None`` = the default pool) where some live worker can read
    a provider into members, from its advertised ``asset_concept_readers`` entry.

    ``readable_pools`` is unioned across every worker advertising the provider (the heartbeat
    declares it a ``union_fields`` key), so one worker that cannot read it -- a missing dependency
    -- no longer hides another that can. An entry from a worker that predates the field speaks for
    its own pool only, through ``readable`` and ``capability``.
    """
    if not entry:
        return set()
    pools = entry.get("readable_pools")
    if isinstance(pools, list):
        return {(capability_token(p) or None) if isinstance(p, str) else None for p in pools}
    if entry.get("available") is False or not entry.get("readable", entry.get("available", True)):
        return set()
    cap = entry.get("capability")
    return {capability_token(cap) or None if isinstance(cap, str) and cap.strip() else None}


async def _reader_pools(ctx: RestContext, provider: str) -> set[str | None]:
    if ctx.jobs.kind == "local":
        # No pools: the reader is in this process or nowhere.
        row = next((r for r in _local_concept_readers(ctx) if r["slug"] == provider), None)
        return {None} if row is not None and row["readable"] else set()
    return _readable_pools((await ctx.jobs.advertised_specs("asset_concept_readers")).get(provider))


def _pool_name(pool: str | None) -> str:
    return "default" if pool is None else repr(pool)


async def _route_with_reader(ctx: RestContext, pool: str | None, provider: str | None, *, needs: str) -> str | None:
    """Where a job that reads members through ``provider`` has to run, given the ``pool`` the rest of
    it already needs (``None``: anywhere core runs).

    A check through a geometry provider READS that provider's nodes into objects, and only a worker
    with its reader can: routed by the checker alone, it lands wherever the checker runs and fails
    there with "cannot be read into objects". So the reader's pools are a second constraint --

    * no live worker reads the provider: refused by name (409), not enqueued to fail;
    * the rest of the job runs anywhere: the reader's pool (the default pool when one reads it there);
    * the rest needs a pool that also reads the provider: that pool;
    * the rest needs a pool that does not: refused (409) naming both, because one job runs on one pool.
    """
    if not provider:
        return pool
    pools = await _reader_pools(ctx, provider)
    if not pools:
        where = "this viewer cannot" if ctx.jobs.kind == "local" else "no live worker can"
        raise HTTPException(
            status_code=409,
            detail=(
                f"{where} read provider {provider!r} into members, so a check cannot take its geometry "
                "from it. Choose a provider that publishes members, or start a pool that reads this one."
            ),
        )
    if pool is None:
        return None if None in pools else sorted(p for p in pools if p is not None)[0]
    if pool in pools:
        return pool
    readers = ", ".join(_pool_name(p) for p in sorted(pools, key=lambda p: (p is not None, p or "")))
    raise HTTPException(
        status_code=409,
        detail=(
            f"{needs} runs on the {_pool_name(pool)} pool, but provider {provider!r} can be read into "
            f"members only on the {readers} pool(s), and one job cannot run on both. Choose another "
            "geometry provider, or run a worker that carries both."
        ),
    )


async def _checker_capability(ctx: RestContext, checker: str | None) -> str | None:
    """The pool a check with this checker has to run on. ``None`` for core's own.

    A contributed checker is routed by what a LIVE worker advertises for it, exactly as a
    contributed spec is. One nobody advertises is refused here, by name, rather than enqueued for
    the default pool: that pool does not carry the checker's code, and the job would either sit
    unclaimed or come back with nothing checked.
    """
    if not checker or checker == BUILTIN_CHECKER:
        return None
    if ctx.jobs.kind == "local":
        # No pools to route to: the checker runs in this process or not at all.
        try:
            from ada.clash.passes import get_checker
        except ImportError:
            get_checker = None  # type: ignore[assignment]
        if get_checker is not None and get_checker(checker) is not None:
            return None
        raise HTTPException(
            status_code=409,
            detail=f"clash checker {checker!r} is not available on this viewer (it has no worker pools)",
        )
    entry = (await ctx.jobs.advertised_specs("clash_checkers")).get(checker)
    if entry is None:
        raise HTTPException(
            status_code=409,
            detail=f"clash checker {checker!r} is not advertised by any live worker; is its pool running?",
        )
    cap = entry.get("capability")
    if not (isinstance(cap, str) and cap.strip()):
        raise HTTPException(
            status_code=409,
            detail=(
                f"clash checker {checker!r} is advertised without a pool, so a check cannot be routed "
                "to it. Its worker must declare exactly one capability besides 'base'."
            ),
        )
    return capability_token(cap)


@router.post("/scopes/{scope}/clash-check")
async def api_clash_check(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Identify, classify and group the joints in a model. Returns ``{job_id, derived_key, cached}``.

    TWO WAYS TO NAME THE MODEL, and exactly one of them per request:

    * ``{source_key, options?}`` -- a SOURCE, never a GLB (see ``ada.clash.identify``'s module
      docstring): the same key the scene was loaded from, an IFC/Genie-XML/FEM file, or a compiled
      procedural model's neutral artifact. A source whose reader yields no members finishes
      ``done`` with ``counts.members == 0`` and a warning -- "this source cannot be checked" is an
      answer, not an error.
    * ``{collection, subject, revision?, node?, options?}`` -- a PUBLISHED ASSET NODE, whose
      provider reads its own format into ``ada`` objects (``ada.assets.concepts``). This is the
      way in for every format core has no reader for, and core learns nothing about that format
      here: it resolves the manifest only to find which blob the check will run against, so the
      derived key still changes when the source bytes do.

    * ``{group: {name, members: [...]}, options?}`` -- a NAMED GROUP of members, each a whole
      file, a whole node, or one element of either (``ada.clash.group``), checked TOGETHER as one
      model so the joints BETWEEN members of different sources are found. See
      :func:`_submit_group_check`.

    A SUBJECT IS NOT A FILE, which is why the second form does not simply take the source's key:
    one blob is commonly shared by many subjects, and a check over a storey is not a check over
    the export that contains it.
    """
    source_key = str(body.get("source_key") or "")
    collection = str(body.get("collection") or "")
    subject = str(body.get("subject") or "")
    group_raw = body.get("group")
    named = sum(bool(x) for x in (source_key, collection or subject, group_raw is not None))
    if named > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "name the model ONE way: 'source_key' for a file core reads, "
                "'collection' + 'subject' for a published node whose provider reads it, or 'group' "
                "for several of those checked together. Passing more than one leaves which one was "
                "checked to the order this route happens to read them in"
            ),
        )
    if group_raw is None and not source_key and not (collection and subject):
        raise HTTPException(
            status_code=400,
            detail="'source_key', 'collection' and 'subject' together, or 'group' is required",
        )
    group = _group_from_body(group_raw) if group_raw is not None else None
    options = _options_from_body(body.get("options"))

    ctx.jobs.require("clash_check")
    capability = await _checker_capability(ctx, options.checker)

    if group is not None:
        return await _submit_group_check(
            body=body,
            request=request,
            ctx=ctx,
            scope_obj=scope_obj,
            user=user,
            group=group,
            options=options,
            capability=capability,
        )

    if not source_key:
        return await _submit_asset_node_check(
            body=body,
            request=request,
            ctx=ctx,
            scope_obj=scope_obj,
            user=user,
            collection=collection,
            subject=subject,
            options=options,
            capability=capability,
        )

    if options.geometry_provider is not None:
        # A file has one reader and no provider, so the choice cannot change what is checked; it is
        # dropped rather than let it split one check into two cache entries.
        options = replace(options, geometry_provider=None)
    key_token = await _source_content_token(ctx, scope_obj, source_key)
    options_token = _options_token(options)
    prefix = f"_derived/clash/{key_token}/{options_token}"
    derived_key = f"{prefix}/result.json"

    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=source_key,
            target_format="clash_check",
            scope=scope_obj,
            feature="clash_check",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={"source_key": source_key, "options": options.to_dict()},
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_check",
            key=derived_key,
            target_format="clash_check",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "cached": False})


async def _asset_manifest(ctx: RestContext, scope: Scope, collection: str, subject: str, revision):
    """A published subject's manifest at ``revision`` (latest complete when None) -- WITHOUT asking
    for a source artefact.

    What a group or a geometry plan needs from a node member is its revision and provider, to pin
    and to check. Whether the member can be READ as it is, is a different question, and the wrong
    one to refuse on here: with a geometry provider chosen the member is re-addressed to that
    provider's node before anything is read, and a provider that publishes meshes (no source
    artefact) is exactly the case that choice exists for. A member read as-is and unreadable is
    still reported -- by the worker, per member, as one it had to leave out.
    """
    from ada.assets.keys import asset_key
    from ada.assets.manifest import MANIFEST_FILENAME, ManifestError, parse_manifest

    revision = revision or await _latest_asset_revision(ctx, scope, collection, subject)
    if revision is None:
        raise HTTPException(
            status_code=404,
            detail=f"no published revision for collection={collection!r} subject={subject!r}",
        )
    key = asset_key(collection, subject, revision, MANIFEST_FILENAME)
    try:
        raw = await ctx.storage.get_bytes(scope, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no manifest at {key}") from exc
    try:
        return parse_manifest(raw)
    except ManifestError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc


async def _asset_source_key(ctx: RestContext, scope: Scope, collection: str, subject: str, revision):
    """The blob a published subject's check will run against, and the revision it resolved to.

    Read from the manifest rather than taken from the caller: the source may be one upload shared
    by many subjects, referenced by absolute key, so only the manifest knows which bytes this
    subject means. Core reads the manifest for exactly this -- the derived key has to move when
    the source does -- and learns nothing about the format inside.
    """
    from ada.assets.keys import asset_key
    from ada.assets.manifest import MANIFEST_FILENAME, ManifestError, parse_manifest

    revision = revision or await _latest_asset_revision(ctx, scope, collection, subject)
    if revision is None:
        raise HTTPException(
            status_code=404,
            detail=f"no published revision for collection={collection!r} subject={subject!r}",
        )
    key = asset_key(collection, subject, revision, MANIFEST_FILENAME)
    try:
        raw = await ctx.storage.get_bytes(scope, key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no manifest at {key}") from exc
    try:
        manifest = parse_manifest(raw)
    except ManifestError as exc:
        raise HTTPException(status_code=502, detail=f"{key}: {exc}") from exc

    entry = next((a for a in manifest.artefacts if a.role == "source"), None)
    if entry is None:
        # Nothing to check and nothing to cache on: a subject with no source artefact publishes
        # a tree and no model, which a check cannot be run over at all.
        raise HTTPException(
            status_code=409,
            detail=(
                f"subject {subject!r} at {revision} publishes no source artefact, so there is " f"no model to check"
            ),
        )
    return (entry.key or asset_key(collection, subject, revision, entry.file)), manifest


async def _latest_asset_revision(ctx: RestContext, scope: Scope, collection: str, subject: str):
    """Newest revision that HAS a manifest -- written last, so a manifest-less newer one is a
    half-written publish and must not shadow the last good revision."""
    from ada.assets.index import fold_listing
    from ada.assets.keys import ASSET_PREFIX

    entries = await ctx.storage.list_prefix(scope, f"{ASSET_PREFIX}/{collection}/{subject}/")
    entry = fold_listing([e.key for e in entries]).subject(collection, subject)
    if entry is None:
        return None
    complete = entry.latest_complete
    return complete.revision if complete is not None else None


async def _submit_asset_node_check(
    *,
    body: dict,
    request: Request,
    ctx: RestContext,
    scope_obj: Scope,
    user: User,
    collection: str,
    subject: str,
    options: ClashOptions,
    capability: str | None = None,
) -> JSONResponse:
    """Enqueue a check over a published node. Same derived-key shape as the file path."""
    asked_revision = body.get("revision") or None
    manifest = await _asset_manifest(ctx, scope_obj, collection, subject, asked_revision)

    if options.geometry_provider == manifest.provider:
        # Its own provider IS the geometry provider: the same check as asking for none, and the same
        # cache entry.
        options = replace(options, geometry_provider=None)
    if options.geometry_provider:
        # Re-addressed to another provider's node before anything is read, so this node's own
        # source is never opened -- and need not exist (a mesh-only publish has none). Its manifest
        # stands in for it in the key; the chosen provider's state rides the options token.
        from ada.assets.keys import asset_key
        from ada.assets.manifest import MANIFEST_FILENAME

        source_key = asset_key(collection, subject, manifest.revision, MANIFEST_FILENAME)
    else:
        source_key, manifest = await _asset_source_key(ctx, scope_obj, collection, subject, manifest.revision)
    asked_provider = str(body.get("provider") or "")
    if asked_provider and asked_provider != manifest.provider:
        # The caller is acting on a view that has moved. Refused rather than checked against the
        # other provider, which is the same rule the build request applies.
        raise HTTPException(
            status_code=409,
            detail=(
                f"subject {subject!r} at {manifest.revision} was produced by provider "
                f"{manifest.provider!r}, not {asked_provider!r}"
            ),
        )

    capability = await _route_with_reader(
        ctx, capability, options.geometry_provider, needs=f"clash checker {options.checker or BUILTIN_CHECKER!r}"
    )
    key_token = await _source_content_token(ctx, scope_obj, source_key)
    # The SUBJECT rides the token as well as the blob: one export is commonly shared by many
    # subjects, so a token over the bytes alone would serve one storey's joints for another's.
    node = str(body.get("node") or subject)
    scope_token = hashlib.sha256(f"{collection}/{subject}/{manifest.revision}/{node}".encode()).hexdigest()[:16]
    options_token = await _geometry_options_token(ctx, scope_obj, options, [collection])
    prefix = f"_derived/clash/{key_token}/{scope_token}/{options_token}"
    derived_key = f"{prefix}/result.json"

    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=source_key,
            # The kind as a LITERAL, like every other route names its own: importing it from
            # `..formats` would pull the worker's handler chain -- and through it the CAD
            # readers -- into the API process, which the slim viewer image does not carry.
            target_format="clash_check_asset",
            scope=scope_obj,
            feature="clash_check",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={
                "collection": collection,
                "subject": subject,
                "revision": manifest.revision,
                "node": node,
                "options": options.to_dict(),
            },
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_check",
            key=derived_key,
            target_format="clash_check_asset",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "cached": False})


async def _resolve_group(ctx: RestContext, scope: Scope, group: dict) -> tuple[dict, dict[str, str]]:
    """The group with every node's revision (and provider) resolved, and a content token per source.

    Resolved HERE rather than on the worker for the same reason the node form resolves its
    manifest: the derived key has to move when a source does. A file's token is its head token,
    a node's is the revision its manifest resolved to -- so a re-upload or a re-publish is a cache
    miss -- and the job carries the resolved revisions, so a "latest" member cannot drift between
    the check and a later detail of it.
    """
    content: dict[str, str] = {}
    manifests: dict[tuple, object] = {}
    members = []
    for member in group["members"]:
        target = member["target"]
        if target["kind"] == "file":
            key = f"file:{target['source_key']}"
            if key not in content:
                content[key] = await _source_content_token(ctx, scope, target["source_key"])
            members.append(member)
            continue
        asked = (target["collection"], target["subject"], target["revision"])
        if asked not in manifests:
            # The manifest only: revision and provider. See `_asset_manifest` for why a member
            # with no source artefact is not refused here.
            manifests[asked] = await _asset_manifest(ctx, scope, *asked)
        manifest = manifests[asked]
        if target["provider"] and target["provider"] != manifest.provider:
            # Same rule as the node form: the caller is acting on a view that has moved.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"subject {target['subject']!r} at {manifest.revision} was produced by provider "
                    f"{manifest.provider!r}, not {target['provider']!r}"
                ),
            )
        content[f"node:{target['collection']}/{target['subject']}"] = manifest.revision
        members.append({**member, "target": {**target, "revision": manifest.revision, "provider": manifest.provider}})
    # Normalised again: a member that named "latest" and one that named the revision it resolves
    # to are now the same member, and the order has to be the sorted one either way.
    return normalise_group({"name": group["name"], "members": members}), content


async def _submit_group_check(
    *,
    body: dict,
    request: Request,
    ctx: RestContext,
    scope_obj: Scope,
    user: User,
    group: dict,
    options: ClashOptions,
    capability: str | None = None,
) -> JSONResponse:
    """Enqueue one check over a named group's members, assembled into one model on the worker.

    No source to pre-download (the members are read by ``ada.clash.group_model``), so the kind is
    synthetic, like the node form's; ``source_key`` is the ``group:<token>`` the result will carry.
    """
    resolved, content = await _resolve_group(ctx, scope_obj, group)
    gp = options.geometry_provider
    if gp is not None and all(
        m["target"]["kind"] == "file" or m["target"].get("provider") == gp for m in resolved["members"]
    ):
        # Nothing to re-address: every node is already that provider's and a file has none. The
        # same check as asking for no provider, so the same cache entry.
        options = replace(options, geometry_provider=None)
    capability = await _route_with_reader(
        ctx, capability, options.geometry_provider, needs=f"clash checker {options.checker or BUILTIN_CHECKER!r}"
    )
    token = group_token(resolved["members"], content)
    collections = [m["target"]["collection"] for m in resolved["members"] if m["target"]["kind"] == "node"]
    prefix = group_derived_prefix(token, await _geometry_options_token(ctx, scope_obj, options, collections))
    derived_key = f"{prefix}/result.json"

    if not bool(body.get("force")) and await ctx.storage.exists(scope_obj, derived_key):
        return JSONResponse({"job_id": None, "derived_key": derived_key, "cached": True})

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=f"group:{token}",
            # A literal, for the reason `_submit_asset_node_check` gives.
            target_format="clash_check_group",
            scope=scope_obj,
            feature="clash_check",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={"group": resolved, "group_token": token, "options": options.to_dict()},
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_check",
            key=derived_key,
            target_format="clash_check_group",
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "cached": False})


async def _spec_capability(ctx: RestContext, spec_name: str) -> str | None:
    """The capability to route a ``clash_detail`` job to: ``None`` for a built-in (it runs on
    the default pool -- ``ada.clash.builtin_specs``), else whatever the live ``connection_specs``
    heartbeat currently advertises for this exact spec name. An unresolvable non-builtin name is
    left as ``None`` too: the default pool will then fail the job with a clear "spec not
    registered here" error (see ``formats/clash_detail.py``) rather than this route guessing at a
    pool that may not exist.
    """
    if spec_name in BUILTIN_SPEC_NAMES:
        return None
    specs = await ctx.jobs.advertised_specs("connection_specs")
    entry = specs.get(spec_name)
    cap = (entry or {}).get("capability")
    return capability_token(cap) if isinstance(cap, str) and cap.strip() else None


async def _detail_capability(
    ctx: RestContext, checker: str | None, spec_name: str, geometry_provider: str | None = None
) -> str | None:
    """Where a detail job runs: the pool that has BOTH the result's checker and the spec -- and,
    for a check that read its members through a geometry provider, that provider's reader.

    A detail job re-runs identification to get real members back, so it needs the checker that
    found the joints as much as it needs the spec. A built-in on either side runs anywhere, so the
    other side decides; two different pools cannot both be satisfied by one job, and that is
    refused by name rather than sent to one of them to fail there. The rebuild reads the members
    the check was re-addressed to (``provenance.group``), all of them the geometry provider's, so
    its reader is the third constraint (:func:`_route_with_reader`).
    """
    checker_cap = await _checker_capability(ctx, checker)
    spec_cap = await _spec_capability(ctx, spec_name)
    if checker_cap and spec_cap and checker_cap != spec_cap:
        raise HTTPException(
            status_code=409,
            detail=(
                f"spec {spec_name!r} runs on the {spec_cap!r} pool, but these joints were found by "
                f"clash checker {checker!r} on the {checker_cap!r} pool, and one detail job cannot "
                "run on both. Re-run the check with a checker that pool carries."
            ),
        )
    needs = f"clash checker {checker!r}" if checker_cap else f"spec {spec_name!r}"
    return await _route_with_reader(ctx, checker_cap or spec_cap, geometry_provider, needs=needs)


@router.post("/scopes/{scope}/clash-detail")
async def api_clash_detail(
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Detail one group of joints with a registered spec. Body:
    ``{result_key, joint_ids, spec, options?}``. Returns ``{job_id, derived_key, glb_key}``.

    Routed to the spec's CAPABILITY when it has one (an out-of-tree generator, advertised live);
    to the default pool when it does not (a built-in). ``joint_ids`` are validated against the
    CACHED result before anything is enqueued, so a stale selection fails fast with a 404 naming
    the missing ids rather than a job that errors two polls later.
    """
    result_key = str(body.get("result_key") or "")
    joint_ids = sorted({str(j) for j in (body.get("joint_ids") or []) if str(j).strip()})
    spec_name = str(body.get("spec") or "")
    gen_options = body.get("options")
    if gen_options is None:
        gen_options = {}
    if not result_key or not joint_ids or not spec_name:
        raise HTTPException(status_code=400, detail="'result_key', 'joint_ids' (non-empty) and 'spec' are required")
    if not isinstance(gen_options, dict):
        raise HTTPException(status_code=400, detail="'options' must be an object")

    ctx.jobs.require("clash_detail")

    try:
        raw = await ctx.storage.get_bytes(scope_obj, result_key)
    except (FileNotFoundError, KeyError) as exc:
        raise HTTPException(status_code=404, detail=f"no clash result at {result_key!r}") from exc
    try:
        result = parse_clash_result(raw)
    except ClashResultError as exc:
        raise HTTPException(status_code=502, detail=f"{result_key}: {exc}") from exc
    if not result.source_key:
        raise HTTPException(status_code=502, detail=f"{result_key}: clash result carries no source_key")

    known_ids = {j.id for j in result.joints}
    absent = [jid for jid in joint_ids if jid not in known_ids]
    if absent:
        raise HTTPException(status_code=404, detail=f"joint id(s) not in this result: {', '.join(absent)}")

    geometry_provider = result.provenance.get("geometry_provider")
    capability = await _detail_capability(
        ctx,
        result.checker,
        spec_name,
        geometry_provider if isinstance(geometry_provider, str) and geometry_provider else None,
    )

    ids_token = hashlib.sha256("|".join(joint_ids).encode("utf-8")).hexdigest()[:16]
    opts_token = hashlib.sha256(json.dumps(gen_options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    result_token = hashlib.sha256(result_key.encode("utf-8")).hexdigest()[:16]
    prefix = f"_derived/clash/detail/{spec_name}/{result_token}/{ids_token}/{opts_token}"
    derived_key = f"{prefix}/result.stats.json"
    glb_key = f"{prefix}/model.glb"
    # A result over a named group has no one source to stream: its detail rebuilds the group's
    # model from `provenance.group` instead (formats/clash_detail.py), a synthetic kind.
    kind = "clash_detail_group" if isinstance(result.provenance.get("group"), dict) else "clash_detail"

    submitted = await ctx.jobs.submit(
        JobRequest(
            source_key=result.source_key,
            target_format=kind,
            scope=scope_obj,
            feature="clash_detail",
            derived_prefix=prefix,
            derived_key=derived_key,
            conversion_options={
                "result_key": result_key,
                "joint_ids": joint_ids,
                "spec": spec_name,
                "options": gen_options,
                "glb_key": glb_key,
            },
            target_capability=capability,
        ),
        before_dispatch=lambda submitted: ctx.audit(
            request,
            user,
            scope_obj,
            "clash_detail",
            key=derived_key,
            target_format=kind,
            status="queued",
            job_id=submitted.job_id,
        ),
    )
    return JSONResponse({"job_id": submitted.job_id, "derived_key": derived_key, "glb_key": glb_key})


def _builtin_connection_specs() -> list[dict]:
    """The built-ins core always offers, in the catalog shape ``merge_catalog_specs`` wants --
    the ``code`` half of the union, exactly as ``builtin_plugin_specs()`` /
    ``builtin_detailing_engine_specs()`` are for their own listings."""
    try:
        from ada.api.connections.spec import all_registered, spec_to_form_schema
        from ada.clash.builtin_specs import register_builtin_specs
    except ImportError:
        # The SLIM api has no modelling stack, so it cannot DESCRIBE a spec's roles -- and does
        # not need to: every pool that can run one advertises it on its heartbeat, and the union
        # below is what the panel reads. Returning nothing here is the honest answer from a
        # process that cannot answer, not a claim that core registers none.
        return []

    register_builtin_specs()
    return [
        {
            "slug": reg.spec.name,
            "name": reg.spec.name,
            "tags": sorted(reg.spec.tags),
            "priority": reg.spec.priority,
            "roles": spec_to_form_schema(reg.spec),
            "capability": None,
        }
        for reg in all_registered()
        if reg.spec.name in BUILTIN_SPEC_NAMES
    ]


def _core_clash_passes() -> list[dict]:
    """Core's own passes, in the catalog shape ``merge_catalog_specs`` wants -- the ``code`` half
    of the union, exactly as ``_builtin_connection_specs`` is for the specs."""
    try:
        # Importing `identify` is what REGISTERS them, so this import is the population step and
        # not merely a name lookup.
        import ada.clash.identify  # noqa: F401,PLC0415 - imported for its registration side effect
        from ada.clash.passes import list_passes
    except ImportError:
        # The SLIM api has no modelling stack, so it cannot load the module that registers core's
        # passes -- and does not need to: every pool that can RUN one advertises it on its
        # heartbeat, and the union below is what the panel reads. Nothing here is the honest
        # answer from a process that cannot answer, not a claim that core has no passes.
        return []

    return [{**entry, "slug": entry["name"]} for entry in list_passes()]


@router.get("/scopes/{scope}/clash-check/passes")
async def api_clash_passes(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Which SEARCHES a check could run: core's own, unioned with whatever a live (non-stale)
    pool currently advertises via its heartbeat's ``clash_passes``.

    The panel reads this to offer passes as checkboxes BEFORE the first run. Without it a
    contributed pass could only be discovered by seeing one in a result -- a checkbox that
    appears only after you have already managed to do the thing it turns on.

    A pass naming a ``capability`` runs only on a pool advertising that capability; core's answer
    ``None`` and run anywhere core runs. Which package contributed one is never reported, here or
    anywhere else -- the capability is the whole of what core knows.
    """
    by_slug = merge_catalog_specs(
        _core_clash_passes(),
        await ctx.jobs.advertised_specs("clash_passes"),
        project=lambda slug, spec, origin: {**spec, "slug": slug, "origin": origin},
    )
    return JSONResponse({"passes": list(by_slug.values())})


def _core_clash_checkers() -> list[dict]:
    """Core's own checker, in the catalog shape -- the ``code`` half of the checker union."""
    try:
        import ada.clash.identify  # noqa: F401,PLC0415 - registers core's passes, which its checker lists
        from ada.clash.passes import BUILTIN_CHECKER as _CORE, list_checkers
    except ImportError:
        # The slim api cannot load the passes, but it can still NAME core's checker: it is always
        # there, and a dropdown missing its default would be a worse answer than one missing the
        # pass list underneath it.
        return [
            {
                "slug": BUILTIN_CHECKER,
                "name": BUILTIN_CHECKER,
                "label": "adapy (built-in)",
                "capability": None,
                "passes": [],
                "options": [],
                "uses_core_options": True,
                "priority": 0,
            }
        ]
    return [{**entry, "slug": entry["name"]} for entry in list_checkers() if entry["name"] == _CORE]


@router.get("/scopes/{scope}/clash-check/checkers")
async def api_clash_checkers(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Which ENGINES a check can be run with: core's own, unioned with every checker a live pool
    advertises on its heartbeat's ``clash_checkers``. The panel's checker dropdown reads this; a
    contributed checker appears while its pool is up and disappears when it is not, the same
    contract the spec and pass listings keep."""
    by_slug = merge_catalog_specs(
        _core_clash_checkers(),
        await ctx.jobs.advertised_specs("clash_checkers"),
        project=lambda slug, spec, origin: {**spec, "slug": slug, "origin": origin},
    )
    return JSONResponse({"checkers": list(by_slug.values())})


def _local_concept_readers(ctx: RestContext) -> list[dict]:
    """The readers THIS process registered -- the ``code`` half of the union, and only where this
    process is the one that runs the check (the queue-less viewer). Behind a queue the API's own
    registry says nothing about any worker, the same reason ``local_specs`` answers None there."""
    if ctx.jobs.kind != "local":
        return []
    try:
        from ada.assets.concepts import concept_providers
    except ImportError:
        return []
    return [{**row, "slug": row["id"], "readable": bool(row.get("available", True))} for row in concept_providers()]


@router.get("/scopes/{scope}/clash-check/geometry-providers")
async def api_clash_geometry_providers(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Which providers a check can take its members' GEOMETRY from (``options.geometry_provider``):
    every provider a live pool can read into objects, from its heartbeat's
    ``asset_concept_readers``, unioned with this process's own when it runs checks itself.

    A provider absent here publishes nothing a check can read -- meshes only, or a reader installed
    nowhere that is running -- which is what the panel tells the user rather than letting a run fail
    with "cannot be read into objects". Which package registered a reader is never reported.

    ``readable`` is true when ANY live worker can read the provider -- not whichever worker happened
    to sort first -- and ``capability`` is a pool that can (the default pool, ``None``, when one can
    there), which is where :func:`_route_with_reader` sends a check through it.
    """

    def project(slug: str, spec: dict, origin: str) -> dict:
        pools = _readable_pools(spec)
        named = sorted(p for p in pools if p is not None)
        capability = (None if None in pools else named[0]) if pools else spec.get("capability")
        reason = spec.get("unavailable_reason") if not pools else None
        return {
            "id": slug,
            "label": spec.get("label") or slug,
            "readable": bool(pools),
            "capability": capability,
            "origin": origin,
            **({"unavailable_reason": reason} if reason else {}),
        }

    by_slug = merge_catalog_specs(
        _local_concept_readers(ctx),
        await ctx.jobs.advertised_specs("asset_concept_readers"),
        project=project,
    )
    return JSONResponse({"providers": sorted(by_slug.values(), key=lambda p: p["id"])})


@router.post("/scopes/{scope}/clash-check/geometry-plan")
async def api_clash_geometry_plan(
    body: dict,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Which of a check's members the chosen GEOMETRY PROVIDER has a node for -- asked BEFORE the
    check, so the panel can offer to request the missing ones first.

    Body: ``{group: {name, members}}`` or ``{collection, subject, revision?, node?}`` -- the same two
    addressing forms the check takes for members that have a provider -- plus ``geometry_provider``.
    Answers ``{geometry_provider, matched: [{from, to}], unmatched: [{member, reason, label}],
    unchanged: [member]}``: ``matched`` members are re-addressed to the provider's node of the same
    name, ``unchanged`` ones are read as they are (a file, or a node the provider published itself),
    and ``unmatched`` ones would be left out, with why and the name they were looked for under.

    The members are resolved exactly as the check resolves them (revisions, providers), and matched
    by the SAME function the worker runs (``ada.clash.geometry_source.remap_group``), run here in a
    thread over this process's async storage. Reads only manifests and hierarchy documents, never a
    model, so it needs no worker and enqueues nothing.
    """
    raw_options = body.get("options") if isinstance(body.get("options"), dict) else {}
    provider = str(body.get("geometry_provider") or raw_options.get("geometry_provider") or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="'geometry_provider' is required")
    collection = str(body.get("collection") or "")
    subject = str(body.get("subject") or "")
    group_raw = body.get("group")
    if group_raw is not None and (collection or subject):
        raise HTTPException(status_code=400, detail="name the members ONE way: 'group', or 'collection' + 'subject'")
    if group_raw is not None:
        group, _ = await _resolve_group(ctx, scope_obj, _group_from_body(group_raw))
    elif collection and subject:
        manifest = await _asset_manifest(ctx, scope_obj, collection, subject, body.get("revision") or None)
        group = node_group(
            provider=manifest.provider,
            collection=collection,
            subject=subject,
            revision=manifest.revision,
            node=str(body.get("node") or subject),
        )
    else:
        raise HTTPException(status_code=400, detail="'group', or 'collection' and 'subject' together, is required")

    storage = _ScopeStorageBridge(ctx, scope_obj, asyncio.get_running_loop())
    plan = await asyncio.to_thread(plan_geometry, group, provider, storage=storage)
    return JSONResponse(plan)


@router.get("/scopes/{scope}/clash-check/connection-specs")
async def api_connection_specs(
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Registered connection specs: the built-ins core always offers, unioned with whatever a
    live (non-stale) pool currently advertises via its heartbeat's ``connection_specs`` -- the
    same "appears only while its pool is online" contract the detailing-engine and blueprint
    dropdowns already have (``routes/plugins.py``). The panel reads this to know which specs
    COULD be offered before it has ever run a check; a joint's own ``applicable[]`` (from
    ``POST /clash-check``) is the authority on which ones actually bind a given joint.
    """
    by_slug = merge_catalog_specs(
        _builtin_connection_specs(),
        await ctx.jobs.advertised_specs("connection_specs"),
        project=lambda slug, spec, origin: {**spec, "slug": slug, "origin": origin},
    )
    return JSONResponse({"connection_specs": list(by_slug.values())})
