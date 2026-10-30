"""Typed, directed relations between nodes: ingest of ``node-relations.ndjson.gz`` and the batched
lookup a writer reads them through. See migration 044 for the storage.

THE FILE. ``ada.assets/node-relations@1``, gzip NDJSON, staged beside whatever else a provider stages
and ingested by core's publish job after the plan is written (``formats/asset_publish.py``). The
first line is the header::

    {"schema": "ada.assets/node-relations@1", "provider": ..., "collection": ..., "revision": ...}

and every further line one relation::

    {"from_node_id", "from_full_name"?, "relation", "to_node_id"?, "to_full_name"?, "props"?}

``relation`` is a lower-case word (:data:`RELATIONS` are the ones core's writers act on; others are
stored and returned). A relation names its target by node id, by full name, or both: the target
need not be a node of any published tree (a system, an opening). ``props`` is an object.

THE INGEST REPLACES the (scope, provider, collection) set in one transaction. A line without a
``from_node_id``, a usable ``relation`` or any target is skipped and counted; an exact duplicate
is kept once.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, AsyncIterable, Iterable, Optional, Sequence

import asyncpg

from .attribute_index import SHARED_SCOPE, ndjson_lines

NODE_RELATIONS_SCHEMA = "ada.assets/node-relations@1"
#: The staged role (its filename under ``assets/_staging/<id>/``) a provider stages relations as.
NODE_RELATIONS_ROLE = "node-relations.ndjson.gz"

#: The relations core's writers act on. ``serves_system``: the node belongs to the system named by
#: the target. ``connects_to``: the node is joined to the target element. ``fills_opening``: the
#: node fills the opening named by the target, whose host is ``props.host_full_name``.
#: ``penetrates``: the node passes through the target element. ``flows_to``: the target is the next
#: element downstream in the same run; ``props.at`` / ``props.dir`` are the joint's world position
#: (metres) and direction, ``props.basis`` is ``flow`` when that is the medium's direction and
#: ``modelled`` when it is only the order the run was modelled in.
RELATIONS = ("serves_system", "connects_to", "fills_opening", "penetrates", "flows_to")

INGEST_BATCH_ROWS = 20_000
#: Node ids plus full names one lookup may name.
LOOKUP_MAX = 5000
LOOKUP_STATEMENT_TIMEOUT = "30s"

_RELATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_COLUMNS = (
    "scope",
    "provider",
    "collection",
    "from_node_id",
    "from_full_name",
    "relation",
    "to_node_id",
    "to_full_name",
    "props",
)


class NodeRelationsError(ValueError):
    """The staged file is not an ``ada.assets/node-relations@1`` file core can ingest."""


def parse_relations_header(line: bytes) -> dict:
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise NodeRelationsError(f"the first line of the node relations is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema") != NODE_RELATIONS_SCHEMA:
        got = header.get("schema") if isinstance(header, dict) else type(header).__name__
        raise NodeRelationsError(f"the node relations header names schema {got!r}, not {NODE_RELATIONS_SCHEMA!r}")
    collection = header.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise NodeRelationsError("the node relations header names no 'collection'")
    header["collection"] = collection.strip().lower()
    return header


async def read_node_relations_header(chunks: AsyncIterable[bytes]) -> dict:
    """Just the header line -- what a dry run reports."""
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeRelationsError("the node relations file is empty") from None
    finally:
        await lines.aclose()
    return parse_relations_header(first)


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = (v if isinstance(v, str) else str(v)).replace("\x00", "").strip()
    return s or None


def parse_relations_batch(
    lines: Sequence[bytes], *, scope: str, provider: str, collection: str
) -> tuple[list[tuple], int]:
    """Lines -> COPY records (in :data:`_COLUMNS` order). Malformed lines are counted, not fatal."""
    records: list[tuple] = []
    skipped = 0
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(obj, dict):
            skipped += 1
            continue
        from_id = _text(obj.get("from_node_id"))
        relation = _text(obj.get("relation"))
        to_id, to_name = _text(obj.get("to_node_id")), _text(obj.get("to_full_name"))
        props = obj.get("props") if isinstance(obj.get("props"), dict) else {}
        if not from_id or not relation or not _RELATION_RE.match(relation) or not (to_id or to_name):
            skipped += 1
            continue
        records.append(
            (
                scope,
                provider,
                collection,
                from_id,
                _text(obj.get("from_full_name")),
                relation,
                to_id,
                to_name,
                json.dumps(props, sort_keys=True).replace("\\u0000", ""),
            )
        )
    return records, skipped


async def ingest_node_relations(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Stream one ``node-relations.ndjson.gz`` into its (scope, provider, collection) set, replacing it.

    ``provider`` is the publishing job's provider; a header naming another is reported in
    ``warnings`` and ignored."""
    import asyncio

    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeRelationsError("the node relations file is empty") from None
    header = parse_relations_header(first)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the node relations header names provider {header['provider']!r}; ingested under the publishing "
            f"provider {provider!r}"
        )
    collection = str(header["collection"])
    revision = None if header.get("revision") is None else str(header["revision"])
    loop = asyncio.get_running_loop()
    skipped = 0
    loaded = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "CREATE TEMP TABLE node_relations_load (LIKE node_relations INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            await conn.execute("ALTER TABLE node_relations_load ALTER COLUMN props TYPE TEXT")

            async def flush(batch: list[bytes]) -> None:
                nonlocal skipped, loaded
                records, bad = await loop.run_in_executor(
                    None,
                    lambda: parse_relations_batch(batch, scope=scope, provider=provider, collection=collection),
                )
                skipped += bad
                if records:
                    await conn.copy_records_to_table("node_relations_load", records=records, columns=list(_COLUMNS))
                    loaded += len(records)

            batch: list[bytes] = []
            async for line in lines:
                batch.append(line)
                if len(batch) >= batch_rows:
                    await flush(batch)
                    batch = []
            if batch:
                await flush(batch)
            t1 = time.perf_counter()

            await conn.execute(
                "DELETE FROM node_relations_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
                scope,
                provider,
                collection,
            )
            await conn.execute(
                """
                INSERT INTO node_relations_sets (scope, provider, collection, revision, rows, ingested_at)
                VALUES ($1, $2, $3, $4, 0, clock_timestamp())
                """,
                scope,
                provider,
                collection,
                revision,
            )
            cols = ", ".join(_COLUMNS)
            status = await conn.execute(
                f"""
                INSERT INTO node_relations ({cols})
                SELECT DISTINCT scope, provider, collection, from_node_id, from_full_name, relation,
                       to_node_id, to_full_name, props::jsonb
                FROM node_relations_load
                """
            )
            rows = int(status.split()[-1])
            await conn.execute(
                "UPDATE node_relations_sets SET rows = $4 WHERE scope = $1 AND provider = $2 AND collection = $3",
                scope,
                provider,
                collection,
                rows,
            )
    if loaded - rows:
        warnings.append(f"{loaded - rows} duplicate relation line(s) dropped")
    if skipped:
        warnings.append(f"{skipped} line(s) without a from_node_id, a relation or a target skipped")
    return {
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "rows": rows,
        "skipped": skipped,
        "warnings": warnings,
        "timings": {
            "ingest_ms": round((t1 - t0) * 1000),
            "swap_ms": round((time.perf_counter() - t1) * 1000),
        },
    }


#: The staged role and schema of a node-relations patch (``db/attribute_index.py``, "patches").
NODE_RELATIONS_PATCH_ROLE = "node-relations-patch.ndjson.gz"
NODE_RELATIONS_PATCH_SCHEMA = "ada.assets/node-relations-patch@1"


async def patch_node_relations(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Apply one ``node-relations-patch.ndjson.gz`` to the (scope, provider, collection) set the full
    ingest built, in one transaction. A node's lines REPLACE every relation it is the SOURCE of (its
    ``from_node_id``); a deleted node loses them. Relations other nodes hold TO a deleted node stay:
    they are those nodes' rows, and their target may still be named by full name. Refused
    (``patch_refused``) when there is no such set.

    ``ingested_at`` moves -- it is the token a cached export reads its relations by
    (:func:`relations_token`), and those relations just changed."""
    from .attribute_index import (
        ensure_parent_index,
        expand_subtree_deletes,
        load_patch,
        parse_patch_header,
        patch_refused,
    )

    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeRelationsError("the node relations patch is empty") from None
    header = parse_patch_header(first, NODE_RELATIONS_PATCH_SCHEMA, NodeRelationsError)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the node relations patch header names provider {header['provider']!r}; applied under the publishing "
            f"provider {provider!r}"
        )
    collection = str(header["collection"])
    key = (scope, provider, collection)

    def parse(batch: list[bytes]) -> tuple[list[tuple], int]:
        return parse_relations_batch(batch, scope=scope, provider=provider, collection=collection)

    async with pool.acquire() as conn, conn.transaction():
        held = await conn.fetchval(
            "SELECT rows FROM node_relations_sets WHERE scope = $1 AND provider = $2 AND collection = $3 FOR UPDATE",
            *key,
        )
        if held is None:
            await lines.aclose()
            return patch_refused(provider=provider, collection=collection, header=header, t0=t0)
        await conn.execute(
            "CREATE TEMP TABLE node_relations_patch_load (LIKE node_relations INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        await conn.execute("ALTER TABLE node_relations_patch_load ALTER COLUMN props TYPE TEXT")
        await conn.execute("ALTER TABLE node_relations_patch_load ADD COLUMN seq INT NOT NULL")
        loaded = await load_patch(
            conn, lines, load_table="node_relations_patch_load", columns=_COLUMNS, parse=parse, batch_rows=batch_rows
        )
        t1 = time.perf_counter()
        if loaded["subtrees"]:
            # A re-exported unit: the relations of every row below it go too. Relations hold no tree,
            # so the rows below are read from the collection's ATTRIBUTE partition -- which is why
            # the publish applies this patch before the attribute patch rewrites that partition.
            part = await conn.fetchrow(
                "SELECT part_key, partition_name FROM attr_collections WHERE scope = $1 AND provider = $2 "
                "AND collection = $3",
                *key,
            )
            if part is None:
                warnings.append(
                    "a subtree deletion could not reach below its node (no attribute index for the collection): "
                    "only the node's own relations were removed"
                )
            else:
                await ensure_parent_index(conn, part["partition_name"])
                await expand_subtree_deletes(conn, "attr_nodes n", "n.part_key = $1", part["part_key"])
        removed = await conn.execute(
            """
            DELETE FROM node_relations r
            USING (SELECT from_node_id AS node_id FROM node_relations_patch_load
                   UNION SELECT node_id FROM patch_deletes) t
            WHERE r.scope = $1 AND r.provider = $2 AND r.collection = $3 AND r.from_node_id = t.node_id
            """,
            *key,
        )
        cols = ", ".join(_COLUMNS)
        inserted = await conn.execute(
            f"""
            INSERT INTO node_relations ({cols})
            SELECT DISTINCT l.scope, l.provider, l.collection, l.from_node_id, l.from_full_name, l.relation,
                   l.to_node_id, l.to_full_name, l.props::jsonb
            FROM node_relations_patch_load l
            WHERE NOT EXISTS (SELECT 1 FROM patch_deletes d WHERE d.node_id = l.from_node_id AND d.seq > l.seq)
            """
        )
        deleted = await conn.fetchval(
            """
            SELECT count(DISTINCT d.node_id) FROM patch_deletes d
            WHERE NOT EXISTS (SELECT 1 FROM node_relations r
                              WHERE r.scope = $1 AND r.provider = $2 AND r.collection = $3 AND r.from_node_id = d.node_id)
            """,
            *key,
        )
        n_removed, n_inserted = int(removed.split()[-1]), int(inserted.split()[-1])
        await conn.execute(
            """
            UPDATE node_relations_sets SET rows = GREATEST(rows - $4 + $5, 0), ingested_at = clock_timestamp()
            WHERE scope = $1 AND provider = $2 AND collection = $3
            """,
            *key,
            n_removed,
            n_inserted,
        )
    if loaded["skipped"]:
        warnings.append(f"{loaded['skipped']} patch line(s) without a from_node_id, a relation or a target skipped")
    return {
        "provider": provider,
        "collection": collection,
        "since": header.get("since"),
        "next_cursor": header.get("next_cursor"),
        "applied": True,
        "refused": False,
        "upserted": n_inserted,
        "deleted": int(deleted or 0),
        "removed_rows": n_removed,
        "skipped": loaded["skipped"],
        "warnings": warnings,
        "timings": {
            "load_ms": round((t1 - t0) * 1000),
            "apply_ms": round((time.perf_counter() - t1) * 1000),
            "patch_ms": round((time.perf_counter() - t0) * 1000),
        },
    }


async def drop_node_relations(pool: asyncpg.Pool, *, scope: str, provider: str, collection: str) -> bool:
    status = await pool.execute(
        "DELETE FROM node_relations_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
        scope,
        provider,
        collection.lower(),
    )
    return status.split()[-1] != "0"


def readable_scopes(scope: str, shared_from: Iterable[str] = ()) -> list[str]:
    """The scopes a lookup of ``scope`` reads: its own, the shared scope and the share origins."""
    return list(dict.fromkeys([scope, SHARED_SCOPE, *shared_from]))


async def relations_for(
    pool: asyncpg.Pool,
    *,
    scopes: Sequence[str],
    collection: str,
    node_ids: Iterable[str] = (),
    full_names: Iterable[str] = (),
    relations: Optional[Iterable[str]] = None,
) -> dict:
    """The relations FROM the nodes named by id or full name, in one query.

    ``{"indexed", "relations": [{provider, from_node_id, from_full_name, relation, to_node_id,
    to_full_name, props}], "token"}``. ``indexed`` says whether any set exists for the collection;
    ``token`` identifies the sets read (their providers and ingest times), so a cached result made
    from them can tell when they change. Of one (provider, collection) the first scope in
    ``scopes`` that holds a set answers."""
    ids = sorted({str(n) for n in node_ids if n})
    names = sorted({str(n) for n in full_names if n})
    if len(ids) + len(names) > LOOKUP_MAX:
        raise ValueError(f"at most {LOOKUP_MAX} node ids and names per lookup")
    wanted = sorted(set(relations)) if relations is not None else None
    collection = collection.lower()
    scopes = list(dict.fromkeys(scopes))
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{LOOKUP_STATEMENT_TIMEOUT}'")
        chosen, stamps = await _chosen_sets(conn, collection, scopes)
        token = relations_token(collection, stamps)
        if not chosen or not (ids or names):
            return {"indexed": bool(chosen), "relations": [], "token": token}
        pairs = sorted(chosen.items())
        rows = await conn.fetch(
            """
            SELECT r.provider, r.from_node_id, r.from_full_name, r.relation, r.to_node_id, r.to_full_name, r.props
            FROM node_relations r
            JOIN unnest($2::text[], $3::text[]) AS c(provider, scope) ON c.provider = r.provider AND c.scope = r.scope
            WHERE r.collection = $1
              AND (r.from_node_id = ANY($4::text[] COLLATE "C") OR r.from_full_name = ANY($5::text[] COLLATE "C"))
              AND ($6::text[] IS NULL OR r.relation = ANY($6::text[]))
            ORDER BY r.from_node_id, r.relation, r.to_node_id, r.to_full_name
            """,
            collection,
            [p for p, _ in pairs],
            [s for _, s in pairs],
            ids,
            names,
            wanted,
        )
    out = []
    for r in rows:
        props = r["props"]
        if isinstance(props, str):
            props = json.loads(props)
        out.append(
            {
                "provider": r["provider"],
                "from_node_id": r["from_node_id"],
                "from_full_name": r["from_full_name"],
                "relation": r["relation"],
                "to_node_id": r["to_node_id"],
                "to_full_name": r["to_full_name"],
                "props": dict(props or {}),
            }
        )
    return {"indexed": True, "relations": out, "token": token}


async def _chosen_sets(conn, collection: str, scopes: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Of each provider's sets for ``collection``, the one the first scope in ``scopes`` holds:
    ``({provider: scope}, {provider: ingested_at})``."""
    sets = await conn.fetch(
        "SELECT scope, provider, ingested_at FROM node_relations_sets WHERE collection = $1 AND scope = ANY($2::text[])",
        collection,
        scopes,
    )
    chosen: dict[str, str] = {}
    stamps: dict[str, str] = {}
    rank = {s: i for i, s in enumerate(scopes)}
    for s in sorted(sets, key=lambda r: rank.get(r["scope"], len(rank))):
        if s["provider"] not in chosen:
            chosen[s["provider"]] = s["scope"]
            stamps[s["provider"]] = s["ingested_at"].isoformat()
    return chosen, stamps


#: Members one system listing returns.
SYSTEM_MEMBERS_MAX = 20_000


async def systems_in(pool: asyncpg.Pool, *, scopes: Sequence[str], collection: str) -> dict:
    """Every system ``collection``'s relations name (``serves_system`` targets): ``{indexed,
    systems: [{provider, name, kind, system_type, members, runs}]}``, largest first."""
    collection = collection.lower()
    scopes = list(dict.fromkeys(scopes))
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{LOOKUP_STATEMENT_TIMEOUT}'")
        chosen, _ = await _chosen_sets(conn, collection, scopes)
        if not chosen:
            return {"indexed": False, "systems": []}
        pairs = sorted(chosen.items())
        rows = await conn.fetch(
            """
            SELECT r.provider, r.to_full_name AS name,
                   max(r.props->>'system_kind') AS kind, max(r.props->>'system_type') AS system_type,
                   count(DISTINCT r.from_full_name) AS members, count(DISTINCT r.props->>'run') AS runs
            FROM node_relations r
            JOIN unnest($2::text[], $3::text[]) AS c(provider, scope) ON c.provider = r.provider AND c.scope = r.scope
            WHERE r.collection = $1 AND r.relation = 'serves_system' AND r.to_full_name IS NOT NULL
            GROUP BY r.provider, r.to_full_name
            ORDER BY members DESC, name
            """,
            collection,
            [p for p, _ in pairs],
            [s for _, s in pairs],
        )
    return {"indexed": True, "systems": [dict(r) for r in rows]}


async def system_members(pool: asyncpg.Pool, *, scopes: Sequence[str], collection: str, system: str) -> dict:
    """The elements that serve ``system``: ``{members: [{full_name, node_id, run, sites}], truncated}``,
    ``sites`` naming, per provider that published bounds for the element, the subject (site) and
    node it is in -- what a viewer loads it through."""
    collection = collection.lower()
    scopes = list(dict.fromkeys(scopes))
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{LOOKUP_STATEMENT_TIMEOUT}'")
        chosen, _ = await _chosen_sets(conn, collection, scopes)
        if not chosen:
            return {"members": [], "truncated": False}
        pairs = sorted(chosen.items())
        rows = await conn.fetch(
            """
            WITH m AS (
                SELECT DISTINCT ON (r.from_full_name) r.from_full_name, r.from_node_id, r.props->>'run' AS run
                FROM node_relations r
                JOIN unnest($2::text[], $3::text[]) AS c(provider, scope) ON c.provider = r.provider AND c.scope = r.scope
                WHERE r.collection = $1 AND r.relation = 'serves_system' AND r.to_full_name = $4
                ORDER BY r.from_full_name
                LIMIT $6
            )
            SELECT m.from_full_name, m.from_node_id, m.run,
                   coalesce(jsonb_agg(jsonb_build_object('provider', b.provider, 'subject', b.subject, 'node_id', b.node_id))
                            FILTER (WHERE b.node_id IS NOT NULL), '[]'::jsonb) AS sites
            FROM m
            LEFT JOIN node_bounds b ON b.collection = $1 AND b.scope = ANY($5::text[]) AND b.full_name = m.from_full_name
            GROUP BY m.from_full_name, m.from_node_id, m.run
            ORDER BY m.from_full_name
            """,
            collection,
            [p for p, _ in pairs],
            [s for _, s in pairs],
            system,
            scopes,
            SYSTEM_MEMBERS_MAX + 1,
        )
    members = [
        {
            "full_name": r["from_full_name"],
            "node_id": r["from_node_id"],
            "run": r["run"],
            "sites": json.loads(r["sites"]) if isinstance(r["sites"], str) else list(r["sites"] or []),
        }
        for r in rows[:SYSTEM_MEMBERS_MAX]
    ]
    return {"members": members, "truncated": len(rows) > SYSTEM_MEMBERS_MAX}


async def relations_token_for(
    pool: asyncpg.Pool, *, scopes: Sequence[str], collections: Iterable[str]
) -> Optional[str]:
    """One token over the relation sets of ``collections`` readable in ``scopes`` -- what an export's
    cache key folds in -- or None when none of them has a set."""
    tokens = {}
    for collection in sorted({c.lower() for c in collections if c}):
        answer = await relations_for(pool, scopes=scopes, collection=collection)
        if answer["token"]:
            tokens[collection] = answer["token"]
    if not tokens:
        return None
    return hashlib.sha256(json.dumps(tokens, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def relations_token(collection: str, stamps: dict[str, str]) -> Optional[str]:
    """A short token over the relation sets read for ``collection`` (provider -> ingest time), or
    None when there are none -- so an export made without relations keeps the key it had."""
    if not stamps:
        return None
    raw = json.dumps({"collection": collection, "sets": stamps}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


__all__ = [
    "LOOKUP_MAX",
    "NODE_RELATIONS_PATCH_ROLE",
    "NODE_RELATIONS_PATCH_SCHEMA",
    "NODE_RELATIONS_ROLE",
    "NODE_RELATIONS_SCHEMA",
    "NodeRelationsError",
    "RELATIONS",
    "drop_node_relations",
    "ingest_node_relations",
    "patch_node_relations",
    "parse_relations_batch",
    "parse_relations_header",
    "read_node_relations_header",
    "readable_scopes",
    "relations_for",
    "relations_token",
    "relations_token_for",
    "system_members",
    "systems_in",
]
