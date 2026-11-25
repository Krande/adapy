"""Plugin access (migration 056): which scope, and which user, may use a scope-gated plugin.

Stores and answers only. What the rows MEAN together -- the user's own row first, then the scope's,
else off -- is :func:`ada.comms.rest.plugin_access.effective_enabled`, kept pure there so it is
tested without a database.
"""

from __future__ import annotations

from typing import Iterable, Optional

import asyncpg

#: The kinds of principal a row names.
PRINCIPAL_KINDS = ("scope", "user")

_COLUMNS = "id, principal_kind, principal, plugin_id, enabled, granted_at, granted_by, note"


def _row(r) -> dict:
    granted_at = r["granted_at"]
    return {
        "id": str(r["id"]),
        "principal_kind": r["principal_kind"],
        "principal": r["principal"],
        "plugin_id": r["plugin_id"],
        "enabled": bool(r["enabled"]),
        "granted_at": granted_at.isoformat() if hasattr(granted_at, "isoformat") else granted_at,
        "granted_by": r["granted_by"],
        "note": r["note"],
    }


async def set_plugin_access(
    pool: asyncpg.Pool,
    *,
    principal_kind: str,
    principal: str,
    plugin_id: str,
    enabled: bool,
    granted_by: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Create the row for (principal, plugin), or replace its ``enabled``. A note given replaces the
    stored one; ``None`` keeps it."""
    if principal_kind not in PRINCIPAL_KINDS:
        raise ValueError(f"unknown principal kind {principal_kind!r}")
    row = await pool.fetchrow(
        f"""
        INSERT INTO plugin_access (principal_kind, principal, plugin_id, enabled, granted_by, note)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (principal_kind, principal, plugin_id) DO UPDATE SET
            enabled = EXCLUDED.enabled, granted_at = NOW(), granted_by = EXCLUDED.granted_by,
            note = COALESCE(EXCLUDED.note, plugin_access.note)
        RETURNING {_COLUMNS}
        """,
        principal_kind,
        principal,
        plugin_id,
        bool(enabled),
        granted_by,
        note,
    )
    return _row(row)


async def clear_plugin_access(pool: asyncpg.Pool, *, principal_kind: str, principal: str, plugin_id: str) -> bool:
    """Remove the row for (principal, plugin): back to what the other rows (or the default, off)
    say. True when there was one."""
    status = await pool.execute(
        "DELETE FROM plugin_access WHERE principal_kind = $1 AND principal = $2 AND plugin_id = $3",
        principal_kind,
        principal,
        plugin_id,
    )
    return status.endswith(" 1")


async def list_plugin_access(
    pool: asyncpg.Pool,
    *,
    principal_kind: Optional[str] = None,
    principal: Optional[str] = None,
    plugin_id: Optional[str] = None,
) -> list[dict]:
    """The rows, each filter optional, by plugin then principal."""
    where, args = [], []
    for column, value in (("principal_kind", principal_kind), ("principal", principal), ("plugin_id", plugin_id)):
        if value is not None:
            args.append(value)
            where.append(f"{column} = ${len(args)}")
    rows = await pool.fetch(
        f"SELECT {_COLUMNS} FROM plugin_access"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " ORDER BY plugin_id, principal_kind, principal",
        *args,
    )
    return [_row(r) for r in rows]


async def plugin_access_rows_for(
    pool: asyncpg.Pool, *, scope: str, user_sub: Optional[str], plugin_ids: Optional[Iterable[str]] = None
) -> list[dict]:
    """The rows that bear on ``user_sub`` working in ``scope`` (a storage prefix): the scope's and the
    user's, optionally only for ``plugin_ids``."""
    args: list = [scope, user_sub or ""]
    sql = (
        f"SELECT {_COLUMNS} FROM plugin_access "
        "WHERE ((principal_kind = 'scope' AND principal = $1) OR (principal_kind = 'user' AND principal = $2))"
    )
    if plugin_ids is not None:
        args.append(sorted(set(plugin_ids)))
        sql += " AND plugin_id = ANY($3::text[])"
    rows = await pool.fetch(sql, *args)
    return [_row(r) for r in rows]
