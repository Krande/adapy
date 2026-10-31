"""One-time move of the older scope access rules into provider grants (``db/provider_grants.py``).

Before grants, two things answered "which scope uses which provider collection":

* ``public.assets.scope_collections`` -- ``{scope key: {provider: [collection, ...]}}``, the Providers
  tab's per-scope matrix. A picker filter: Shared or a project with NO entry for a provider was
  UNRESTRICTED (every collection offered), a personal scope with none was offered nothing.
* attribute shares (``attr_shares``, migration 039; ``attr_provider_shares``, 040) -- an origin scope's
  partition, or all of a provider's, searchable from a target scope.

Grants are now the only control, so at startup this writes the grants those rules implied, then
records that it ran (:data:`MIGRATED_SETTING`, holding the matrix as it was) and deletes the matrix:

* an explicit matrix entry -> a read grant (attributes + geometry) per listed collection;
* Shared or a live project with no entry for a provider ("unrestricted") -> a read grant per
  collection of that provider the store holds, plus each one the scope still holds a copy of --
  nobody loses what they could reach. The providers considered are the ones the store's attribute
  catalogue, the scope's own catalogue or the matrix names;
* a personal scope -> its explicit entries only (missing was nothing);
* a share -> an attributes grant to its target: per collection for 039, provider-wide for 040.

Grants are only ever ADDED (:func:`merge_provider_grant`), so running this over grants an admin
already set narrows nothing, and a second run -- after a crash before the marker -- writes nothing
new. The share tables are left in place and still read: until the legacy per-scope copies are moved
into the store (phase 4 of ``docs/design/provider-scope-grants.md``) a share is how a scope reads a
copy that was folded into the shared one, and a grant only answers once the store holds the data.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from ada.config import logger

from .db.provider_grants import merge_provider_grant

#: The matrix setting this replaces.
SCOPE_COLLECTIONS_SETTING = "public.assets.scope_collections"
#: Set once the migration ran: ``{at, counts, scope_collections}`` (the matrix as it was, for the record).
MIGRATED_SETTING = "provider_grants.legacy_migrated"
#: ``granted_by`` / ``note`` of the grants it writes.
MIGRATED_BY = "migration:legacy-access"
_NOTE_MATRIX = "from the per-scope collection matrix"
_NOTE_UNRESTRICTED = "from the per-scope collection matrix (unrestricted)"
_NOTE_SHARE = "from an attribute share"

_PROVIDER_STORE = "providers"


def matrix_target(key: str) -> Optional[str]:
    """The grant target (a storage prefix) of a matrix scope key, or None for one that names no
    single scope (the stale ``user:me``) or is not a scope key at all."""
    key = str(key or "").strip()
    if key == "shared":
        return "shared"
    if key.startswith("project:") and len(key) > len("project:"):
        return "projects/" + key[len("project:") :]
    if key.startswith("user:") and key != "user:me" and len(key) > len("user:"):
        return "users/" + key[len("user:") :]
    return None


def parse_matrix(raw: Optional[str]) -> dict[str, dict[str, list[str]]]:
    """The stored matrix, shape-checked as the frontend reads it: a provider entry that is not a
    list is dropped (that provider then stays unrestricted), blank names are dropped."""
    if not raw:
        return {}
    try:
        doc = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("provider grants: %s is not JSON; read as empty", SCOPE_COLLECTIONS_SETTING)
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    if not isinstance(doc, dict):
        return out
    for scope, providers in doc.items():
        if not isinstance(providers, dict):
            continue
        row = {}
        for provider, listed in providers.items():
            if isinstance(listed, list):
                row[str(provider)] = [str(c).strip() for c in listed if isinstance(c, str) and c.strip()]
        out[str(scope)] = row
    return out


def plan_matrix_grants(
    matrix: dict[str, dict[str, list[str]]],
    *,
    live_projects: list[str],
    store_collections: dict[str, set[str]],
    scope_collections: dict[str, dict[str, set[str]]],
) -> list[tuple[str, str, str, str]]:
    """``[(target, provider, collection, note)]`` the matrix implies. Pure, so the rules are testable
    without a database.

    ``live_projects``: project ids that exist and are not archived (a grant to anything else is
    pointless and a stale key is dropped). ``store_collections``: provider -> collections the store's
    attribute catalogue holds. ``scope_collections``: target -> provider -> collections that scope
    still holds a copy of."""
    out: dict[tuple[str, str, str], str] = {}
    targets_seen: dict[str, dict[str, list[str]]] = {}
    for key, providers in matrix.items():
        target = matrix_target(key)
        if target is None:
            continue
        if target.startswith("projects/") and target[len("projects/") :] not in live_projects:
            continue
        targets_seen[target] = providers
        for provider, listed in providers.items():
            for collection in listed:
                out.setdefault((target, provider, collection.lower()), _NOTE_MATRIX)
    providers = set(store_collections)
    for row in matrix.values():
        providers |= set(row)
    for held in scope_collections.values():
        providers |= set(held)
    # Unrestricted: Shared and live projects, for each provider their matrix entry does not name.
    for target in ["shared", *(f"projects/{p}" for p in live_projects)]:
        named = targets_seen.get(target, {})
        for provider in sorted(providers - set(named)):
            reachable = set(store_collections.get(provider, ())) | set(
                scope_collections.get(target, {}).get(provider, ())
            )
            for collection in reachable:
                out.setdefault((target, provider, collection.lower()), _NOTE_UNRESTRICTED)
    return [(t, p, c, note) for (t, p, c), note in sorted(out.items())]


async def _catalogue(pool) -> tuple[dict[str, set[str]], dict[str, dict[str, set[str]]]]:
    rows = await pool.fetch("SELECT scope, provider, lower(collection) AS collection FROM attr_collections")
    store: dict[str, set[str]] = {}
    scopes: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        if r["scope"] == _PROVIDER_STORE:
            store.setdefault(r["provider"], set()).add(r["collection"])
        else:
            scopes.setdefault(r["scope"], {}).setdefault(r["provider"], set()).add(r["collection"])
    return store, scopes


async def migrate_legacy_access(pool, *, force: bool = False) -> Optional[dict]:
    """Write the grants the matrix and the shares implied, once. Returns the counts, or None when it
    had already run (and ``force`` is off). Never raises past a logged warning when the database is
    missing a table it reads: a fresh database simply has nothing to migrate."""
    if not force and await pool.fetchval("SELECT 1 FROM app_settings WHERE key = $1", MIGRATED_SETTING):
        return None
    raw = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", SCOPE_COLLECTIONS_SETTING)
    matrix = parse_matrix(raw)
    live = [str(r["id"]) for r in await pool.fetch("SELECT id FROM projects WHERE archived_at IS NULL")]
    store, scopes = await _catalogue(pool)
    plan = plan_matrix_grants(matrix, live_projects=live, store_collections=store, scope_collections=scopes)
    shares = await pool.fetch(
        "SELECT DISTINCT provider, lower(collection) AS collection, target_scope FROM attr_shares "
        "WHERE target_scope <> $1 AND target_scope NOT LIKE 'corpus/%'",
        _PROVIDER_STORE,
    )
    group_shares = await pool.fetch(
        "SELECT DISTINCT provider, target_scope FROM attr_provider_shares "
        "WHERE target_scope <> $1 AND target_scope NOT LIKE 'corpus/%'",
        _PROVIDER_STORE,
    )
    counts = {"matrix": 0, "unrestricted": 0, "shares": 0, "provider_shares": 0}
    async with pool.acquire() as conn, conn.transaction():
        for target, provider, collection, note in plan:
            if await merge_provider_grant(
                conn, target_scope=target, provider=provider, collection=collection, granted_by=MIGRATED_BY, note=note
            ):
                counts["unrestricted" if note == _NOTE_UNRESTRICTED else "matrix"] += 1
        for r in shares:
            if await merge_provider_grant(
                conn,
                target_scope=r["target_scope"],
                provider=r["provider"],
                collection=r["collection"],
                attributes=True,
                geometry=False,
                granted_by=MIGRATED_BY,
                note=_NOTE_SHARE,
            ):
                counts["shares"] += 1
        for r in group_shares:
            if await merge_provider_grant(
                conn,
                target_scope=r["target_scope"],
                provider=r["provider"],
                collection=None,
                attributes=True,
                geometry=False,
                granted_by=MIGRATED_BY,
                note=_NOTE_SHARE,
            ):
                counts["provider_shares"] += 1
        record = {
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "counts": counts,
            "scope_collections": matrix,
        }
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_by) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW(), updated_by = EXCLUDED.updated_by
            """,
            MIGRATED_SETTING,
            json.dumps(record),
            MIGRATED_BY,
        )
        await conn.execute("DELETE FROM app_settings WHERE key = $1", SCOPE_COLLECTIONS_SETTING)
    logger.info("provider grants: legacy access migrated (%s)", ", ".join(f"{k} {v}" for k, v in counts.items()))
    return counts
