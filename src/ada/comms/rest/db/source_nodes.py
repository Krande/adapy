"""External-source node change tracking (when each node of a source last changed)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

import asyncpg

# ── source nodes — when each node of an external source last changed ─────────
#
# See migrations/028_source_nodes.sql for what the table is for. These helpers
# are the whole query surface: one write, three reads. Everything a consumer
# needs is composed from them, and nothing here understands a ref.


@dataclass(frozen=True)
class SourceNode:
    node_ref: str
    parent_ref: Optional[str]
    name: Optional[str]
    last_changed_at: datetime.datetime
    last_changed_by: Optional[str]
    observed_at: datetime.datetime


def _source_node_row(r) -> SourceNode:
    return SourceNode(
        node_ref=r["node_ref"],
        parent_ref=r["parent_ref"],
        name=r["name"],
        last_changed_at=r["last_changed_at"],
        last_changed_by=r["last_changed_by"],
        observed_at=r["observed_at"],
    )


async def record_source_nodes(
    pool: asyncpg.Pool,
    *,
    scope: str,
    source: str,
    nodes: list,
) -> int:
    """Upsert observed nodes. Returns how many rows were written.

    ``nodes`` is a list of dicts with ``node_ref`` and ``last_changed_at``
    required, and ``parent_ref`` / ``name`` / ``last_changed_by`` optional.

    LAST_CHANGED_AT ONLY EVER MOVES FORWARD. A writer re-observing a node it has
    seen before may hold an older cursor than the row does -- a backfill, a
    re-run over a wider window, two writers with different windows -- and letting
    that overwrite a newer timestamp would mark current assets stale-free when
    they are not. `observed_at` is always taken from the new write, because "when
    was this last confirmed" is precisely the thing that must not be sticky.

    Written as one executemany inside a transaction: an hourly sweep stamps a
    changed leaf and every node above it, so the natural batch is thousands of
    rows and a round trip each would dominate.
    """
    if not nodes:
        return 0
    rows = [
        (
            scope,
            source,
            n["node_ref"],
            n.get("parent_ref"),
            n.get("name"),
            n["last_changed_at"],
            n.get("last_changed_by"),
        )
        for n in nodes
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                """
                INSERT INTO source_nodes
                    (scope, source, node_ref, parent_ref, name, last_changed_at, last_changed_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (scope, source, node_ref) DO UPDATE SET
                    parent_ref      = COALESCE(EXCLUDED.parent_ref, source_nodes.parent_ref),
                    name            = COALESCE(EXCLUDED.name, source_nodes.name),
                    last_changed_at = GREATEST(EXCLUDED.last_changed_at, source_nodes.last_changed_at),
                    last_changed_by = CASE
                        WHEN EXCLUDED.last_changed_at >= source_nodes.last_changed_at
                        THEN EXCLUDED.last_changed_by ELSE source_nodes.last_changed_by END,
                    observed_at     = NOW()
                """,
                rows,
            )
    return len(rows)


async def get_source_node(pool: asyncpg.Pool, *, scope: str, source: str, node_ref: str) -> Optional[SourceNode]:
    """One node, or None. The staleness check: compare `last_changed_at` against
    whatever timestamp the caller's copy was made at."""
    r = await pool.fetchrow(
        "SELECT node_ref, parent_ref, name, last_changed_at, last_changed_by, observed_at "
        "FROM source_nodes WHERE scope = $1 AND source = $2 AND node_ref = $3",
        scope,
        source,
        node_ref,
    )
    return _source_node_row(r) if r else None


async def get_source_nodes(pool: asyncpg.Pool, *, scope: str, source: str, node_refs: list) -> list:
    """Many nodes in one round trip.

    The batch form exists because the interesting caller holds a whole listing of
    published assets and wants every answer at once; asking one at a time turns a
    freshness check into N round trips over a slow link.
    """
    if not node_refs:
        return []
    rows = await pool.fetch(
        "SELECT node_ref, parent_ref, name, last_changed_at, last_changed_by, observed_at "
        "FROM source_nodes WHERE scope = $1 AND source = $2 AND node_ref = ANY($3::text[])",
        scope,
        source,
        list(node_refs),
    )
    return [_source_node_row(r) for r in rows]


async def list_source_nodes_changed_since(
    pool: asyncpg.Pool,
    *,
    scope: str,
    source: str,
    since: Optional[datetime.datetime] = None,
    limit: int = 1000,
) -> list:
    """Nodes whose last change is newer than ``since``, newest first.

    The polling shape: a consumer that keeps its own cursor asks what has moved
    rather than re-checking everything it holds. ``limit`` is capped by the
    caller; the index makes the ordering free.
    """
    if since is None:
        rows = await pool.fetch(
            "SELECT node_ref, parent_ref, name, last_changed_at, last_changed_by, observed_at "
            "FROM source_nodes WHERE scope = $1 AND source = $2 "
            "ORDER BY last_changed_at DESC LIMIT $3",
            scope,
            source,
            limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT node_ref, parent_ref, name, last_changed_at, last_changed_by, observed_at "
            "FROM source_nodes WHERE scope = $1 AND source = $2 AND last_changed_at > $3 "
            "ORDER BY last_changed_at DESC LIMIT $4",
            scope,
            source,
            since,
            limit,
        )
    return [_source_node_row(r) for r in rows]


async def list_source_node_sources(pool: asyncpg.Pool, *, scope: str) -> list:
    """Which sources have recorded anything in this scope, with their row counts
    and freshest observation. What a consumer reads before it knows any refs."""
    rows = await pool.fetch(
        "SELECT source, COUNT(*) AS nodes, MAX(last_changed_at) AS last_changed_at, "
        "MAX(observed_at) AS observed_at FROM source_nodes WHERE scope = $1 GROUP BY source ORDER BY source",
        scope,
    )
    return [
        {
            "source": r["source"],
            "nodes": r["nodes"],
            "last_changed_at": r["last_changed_at"],
            "observed_at": r["observed_at"],
        }
        for r in rows
    ]
