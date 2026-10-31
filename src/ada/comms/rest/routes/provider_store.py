"""The provider store and the reads that go through grants -- one place for both questions:

* WHERE does a provider job write? A plugin whose spec declares ``asset_provider_id`` is an asset
  provider, and every job of it -- a collection or node request, a tree, a change check, a sweep
  step, a scheduled firing -- runs in the provider store (``Scope.providers()``), whatever scope
  the caller had open. Those jobs are ADMIN operations: keeping the provider data current is the
  scheduled jobs' work, and nothing a member does writes provider data.
* WHERE does a scope READ a provider dataset? From the provider store when an admin granted the
  scope that dataset (``db/provider_grants.py``) AND the store holds it; otherwise from the scope
  itself, as before -- so a scope keeps reading its legacy copy until the store has the data, and
  a grant shadows the copy from then on.

See ``docs/design/provider-scope-grants.md`` for the whole design.
"""

from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from fastapi import HTTPException

from ada.config import logger

from .. import db as db_module
from ..scope import Scope

#: The ONE scope provider data lives in.
PROVIDER_STORE = Scope.providers()
#: Its wire form, as API responses name it.
PROVIDER_STORE_WIRE = "providers"

#: Providers whose publishes count as provider data even when no live worker advertises them
#: (comma-separated), e.g. a provider that only publishes from a CLI.
STORE_PROVIDERS_ENV = "ADA_STORE_PROVIDERS"

#: How long "does the store hold this dataset" is remembered. A publish into the store becomes
#: visible through a grant within this long; short, because the answer is cheap to recompute.
_HOLDS_TTL_S = 30.0
_HOLDS: dict[tuple, tuple[float, bool]] = {}


def is_asset_provider_spec(spec: Optional[dict]) -> bool:
    """Whether a plugin spec is an asset provider's: its jobs write the provider store."""
    return (
        isinstance(spec, dict)
        and isinstance(spec.get("asset_provider_id"), str)
        and bool(spec["asset_provider_id"].strip())
    )


def env_store_providers() -> set[str]:
    raw = os.environ.get(STORE_PROVIDERS_ENV) or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def job_scope(spec: Optional[dict], requested: Scope) -> Scope:
    """The scope a plugin job runs in: the provider store for an asset provider's job, else the
    scope the caller named. Every enqueue of a plugin job asks this, so no caller can pick where
    provider data lands."""
    return PROVIDER_STORE if is_asset_provider_spec(spec) else requested


#: Why a member's request of provider data enqueues nothing -- the message a refused request
#: reports where it cannot raise (a run driver's batch). ``{provider}`` is filled in.
ADMIN_ONLY_REQUEST = (
    "requesting {provider!r} data is an admin operation: provider data is kept current by the scheduled "
    "jobs, and a scope reads what an admin granted it -- ask an admin to schedule or run the request"
)


def require_admin_for_provider_job(user, spec: Optional[dict]) -> None:
    """403 unless ``user`` is an admin, for an asset provider's job. Provider data is kept current
    by admins (scheduled jobs and sweeps); a member reads what is granted to their scope."""
    if is_asset_provider_spec(spec) and not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{spec.get('asset_provider_id')!r} jobs are admin operations: provider data is kept current "
                "by the scheduled jobs and read through the grants an admin gives a scope"
            ),
        )


# --- reads through grants ----------------------------------------------------------------------


def _cached(key: tuple) -> Optional[bool]:
    hit = _HOLDS.get(key)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    return None


def _remember(key: tuple, value: bool) -> bool:
    _HOLDS[key] = (time.monotonic() + _HOLDS_TTL_S, value)
    return value


def clear_store_cache() -> None:
    """Forget every "the store holds it" answer (tests; after a publish into the store)."""
    _HOLDS.clear()


async def store_holds_geometry(ctx, collection: str) -> bool:
    """Whether the provider store holds anything published for ``collection``."""
    key = ("geometry", collection.lower())
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        names = await ctx.storage.list_child_names(PROVIDER_STORE, f"assets/{collection}/")
    except Exception as exc:  # noqa: BLE001 - an unreadable store reads as "not there": the scope's own copy answers
        logger.warning("provider store: could not list assets/%s/: %s", collection, exc)
        return False
    return _remember(key, bool(names))


async def store_holds_attributes(pool, provider: str, collection: str) -> bool:
    """Whether the provider store holds an attribute partition for (``provider``, ``collection``)."""
    key = ("attributes", provider, collection.lower())
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        row = await pool.fetchrow(
            "SELECT 1 FROM attr_collections WHERE scope = $1 AND provider = $2 AND lower(collection) = $3 LIMIT 1",
            PROVIDER_STORE.prefix(),
            provider,
            collection.lower(),
        )
    except Exception as exc:  # noqa: BLE001 - as above
        logger.warning("provider store: could not look up attributes of %s/%s: %s", provider, collection, exc)
        return False
    return _remember(key, row is not None)


async def resolve_dataset(ctx, pool, scope_obj: Scope, provider: str, collection: str, permission: str) -> Scope:
    """Where ``scope_obj`` reads (``provider``, ``collection``) for ``permission``: the provider
    store when the scope holds that grant and the store holds the dataset, else the scope itself.
    The caller has already checked the reader may open ``scope_obj``."""
    if scope_obj.kind == "providers" or pool is None or not provider or not collection:
        return scope_obj
    access = await db_module.grant_access(
        pool, target_scope=scope_obj.prefix(), provider=provider, collection=collection
    )
    if not access.allows(permission):
        return scope_obj
    if permission == "attributes":
        holds = await store_holds_attributes(pool, provider, collection)
    else:
        holds = await store_holds_geometry(ctx, collection)
    return PROVIDER_STORE if holds else scope_obj


async def granted_providers(pool, scope_obj: Scope, collection: str, permission: str) -> set[str]:
    """The providers whose ``collection`` ``scope_obj`` holds ``permission`` on (provider-wide
    grants included)."""
    if pool is None or scope_obj.kind == "providers":
        return set()
    rows = await db_module.list_provider_grants(pool, target_scope=scope_obj.prefix(), collection=collection)
    return {r["provider"] for r in rows if r.get(permission)}


async def resolve_collection(ctx, pool, scope_obj: Scope, collection: str, permission: str = "geometry") -> Scope:
    """Where ``scope_obj`` reads a COLLECTION-addressed route (index, roll-up, a blob under
    ``assets/<collection>/``): the store when any provider of the collection is granted to the
    scope with ``permission`` and the store holds the collection, else the scope itself.

    A collection may be fed by several providers side by side (a tree from one, geometry from
    another), and these routes read the collection as a whole -- so the grant is honoured per
    collection here, and per provider on the provider-addressed routes (tree, attributes,
    delivery, build)."""
    if scope_obj.kind == "providers" or pool is None or not collection:
        return scope_obj
    if not await granted_providers(pool, scope_obj, collection, permission):
        return scope_obj
    return PROVIDER_STORE if await store_holds_geometry(ctx, collection) else scope_obj


async def granted_store_collections(ctx, pool, scope_obj: Scope, permission: str = "geometry") -> list[str]:
    """The store collections ``scope_obj`` may read with ``permission`` -- what its collection
    picker lists beside its own. A provider-wide grant covers every collection the store holds."""
    if pool is None or scope_obj.kind == "providers":
        return []
    grants = [
        g for g in await db_module.list_provider_grants(pool, target_scope=scope_obj.prefix()) if g.get(permission)
    ]
    if not grants:
        return []
    try:
        held = await ctx.storage.list_child_names(PROVIDER_STORE, "assets/")
    except Exception as exc:  # noqa: BLE001 - the scope's own collections still answer
        logger.warning("provider store: could not list assets/: %s", exc)
        return []
    if any(g["collection"] is None for g in grants):
        return [n for n in held if not n.startswith("_")]
    wanted = {g["collection"] for g in grants}
    return [n for n in held if n.lower() in wanted]


#: The prefixes of a provider dataset's blobs, as ``(prefix, segments before the collection)``.
_DATASET_PREFIXES: tuple[tuple[str, int], ...] = (
    ("assets/", 0),  # assets/<collection>/<subject>/<revision>/<file>
    ("_derived/assets/_geometry/", 0),  # the geometry roll-up cache of <collection>
    ("_derived/assets/", 1),  # builds: _derived/assets/<provider>/<collection>/...
)


def dataset_collection_of_key(key: str) -> Optional[str]:
    """The collection a blob key belongs to when it is part of a published provider dataset, else
    None. Staging, saved provider options and job summaries are not part of a dataset."""
    clean = key.lstrip("/")
    for prefix, skip in _DATASET_PREFIXES:
        if not clean.startswith(prefix):
            continue
        parts = clean[len(prefix) :].split("/")
        if len(parts) <= skip + 1:
            return None
        if skip and parts[0].startswith("_"):
            return None
        name = parts[skip]
        if not name or name.startswith("_"):
            return None
        return name
    return None


async def resolve_blob(ctx, pool, scope_obj: Scope, key: str) -> Scope:
    """Where ``scope_obj`` reads blob ``key``: the store for a granted dataset's blob it holds,
    else the scope. What lets a delivery's ``url`` (a key) load through the scope's own blob route
    without the viewer ever naming the store."""
    collection = dataset_collection_of_key(key)
    if collection is None:
        return scope_obj
    resolved = await resolve_collection(ctx, pool, scope_obj, collection, "geometry")
    if resolved == scope_obj:
        return scope_obj
    # The scope's OWN copy of an ungranted-but-present key never shadows the store; the store's
    # key must exist for the grant to answer.
    try:
        if await ctx.storage.exists(resolved, key.lstrip("/")):
            return resolved
    except Exception:  # noqa: BLE001 - fall back to the scope's copy
        pass
    return scope_obj


def wire_of(scope_obj: Scope) -> str:
    try:
        return scope_obj.wire()
    except ValueError:
        return scope_obj.prefix()


def providers_of(specs: Iterable[dict]) -> set[str]:
    """The asset-provider ids of these live specs, plus :data:`STORE_PROVIDERS_ENV`."""
    out = env_store_providers()
    for spec in specs:
        if is_asset_provider_spec(spec):
            out.add(spec["asset_provider_id"].strip())
    return out


async def store_provider_ids(ctx) -> set[str]:
    """:func:`providers_of` the plugin specs online right now -- the providers whose publishes are
    provider data. A spec read that fails leaves :data:`STORE_PROVIDERS_ENV` alone answering, and
    is logged: a publish of a provider nobody advertises then stays in the caller's scope, as one
    from a provider core never heard of always did."""
    from .plugins import online_plugin_specs

    try:
        specs = list((await online_plugin_specs(ctx)).values())
    except Exception as exc:  # noqa: BLE001 - the env list still answers
        logger.warning("provider store: could not read the online plugin specs: %s", exc)
        specs = []
    return providers_of(specs)


async def is_store_provider(ctx, provider: Optional[str]) -> bool:
    """Whether a publish under ``provider`` is provider data (it lands in the provider store). The
    built-in ``published`` provider -- user uploads -- never is."""
    if not provider or provider == "published":
        return False
    return provider in await store_provider_ids(ctx)


def granted_covers(granted: Iterable[tuple[str, Optional[str]]], provider: str, collection: str) -> bool:
    """Whether ``granted`` (``db.granted_datasets`` rows: ``(provider, collection | None)``, None
    for every collection of the provider) covers (``provider``, ``collection``)."""
    wanted = (collection or "").lower()
    return any(p == provider and (c is None or c.lower() == wanted) for p, c in granted)


async def readable_store_datasets(pool, scope_obj: Scope) -> list[tuple[str, Optional[str]]]:
    """``[(provider, collection | None)]`` ``scope_obj`` may read ANY of (an ``attributes`` or a
    ``geometry`` grant) -- what a scope's change-feed reads (``asset-changes/runs``,
    ``source-nodes``) follow into the store, checked with :func:`granted_covers`. A
    ``request``-only grant reads nothing. Empty for the store itself and without a pool."""
    if pool is None or scope_obj.kind == "providers":
        return []
    rows = await db_module.list_provider_grants(pool, target_scope=scope_obj.prefix())
    return [(r["provider"], r["collection"]) for r in rows if r.get("attributes") or r.get("geometry")]
