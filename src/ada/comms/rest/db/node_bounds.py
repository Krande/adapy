"""Per-node world bounds: ingest of ``node-bounds.ndjson.gz`` and the box query behind
``POST /scopes/{scope}/assets/bounds/search``. See migration 042 for the storage.

WHY. "What is in this box" over what the viewer has DRAWN only finds what is loaded. A provider that
knows where its nodes are -- an axis-aligned box per node, on every level, a parent's box the union
of its children's -- publishes those boxes once, and the question is answered over the whole
collection, loaded or not.

THE FILE. ``ada.assets/node-bounds@1``, gzip NDJSON, staged beside whatever else a provider stages
and ingested by core's publish job after the plan is written -- the attribute index's path
(``formats/asset_publish.py``). The first line is the header::

    {"schema": "ada.assets/node-bounds@1", "provider": ..., "collection": ..., "revision": ...,
     "frame": {"up": "z", "units": "m"}}

and every further line one node::

    {"node_id", "parent_id", "subject", "label", "full_name", "kind", "depth",
     "bbox": [min_x, min_y, min_z, max_x, max_y, max_z], "own_bbox"?: [...]}

``bbox`` is in the provider's MODEL frame: Z up, the units of the geometry it delivers, before the
viewer's scene offset -- so the viewer's box, less that offset, is in the same frame. ``subject`` is
the published subject a request or a load of the node goes through (a site, for a provider that
publishes per site); null above every subject. A line with no usable box is skipped and counted.
``own_bbox``, optional, is the box of the geometry the node draws ITSELF, for a node that also has
children (migration 043); an unusable one is dropped, the line kept.

THE INGEST REPLACES the (scope, provider, collection) set in one transaction: the rows are COPYed
into a temp table, then moved in (a node listed twice keeps its first line). A query that started
before it commits sees the old rows.

THE QUERY folds what it finds the way a selection is folded (``utils/groups/groupScene.ts``):
"partially within" takes every LEAF whose box meets the query box, "fully within" every node whose
box lies inside it; then a node all of whose bounded children are taken stands for them, and only
the topmost such nodes are answered. A node with no ``subject`` (above every published subject) is
never answered itself; its taken children are. So a box around one site answers the site, a box
through a corner of it the deepest nodes the corner meets.

A node with geometry of its own is more than its children: in "partially within" it is also taken
when its ``own_bbox`` meets the query box, whatever its children's boxes do -- a plate with a fitting
on it, the box through the plate and not the fitting. Taken that way it stands for what is below it,
as any answered node does.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, AsyncIterable, Iterable, Optional, Sequence

import asyncpg

from .attribute_index import SHARED_SCOPE, ndjson_lines

NODE_BOUNDS_SCHEMA = "ada.assets/node-bounds@1"
#: The staged role (its filename under ``assets/_staging/<id>/``) a provider stages the bounds as.
NODE_BOUNDS_ROLE = "node-bounds.ndjson.gz"

#: Lines parsed and COPYed per round trip.
INGEST_BATCH_ROWS = 20_000
#: Most candidate rows one query reads before folding; a box that meets more is refused as too
#: big (``truncated``) rather than answered partially.
QUERY_MAX_CANDIDATES = 250_000
#: Most nodes one answer lists, after folding.
QUERY_MAX_RESULT = 20_000
QUERY_STATEMENT_TIMEOUT = "30s"

MODES = ("partial", "within")

_COLUMNS = (
    "scope",
    "provider",
    "collection",
    "node_id",
    "parent_id",
    "subject",
    "label",
    "full_name",
    "kind",
    "depth",
    "min_x",
    "min_y",
    "min_z",
    "max_x",
    "max_y",
    "max_z",
    "own_min_x",
    "own_min_y",
    "own_min_z",
    "own_max_x",
    "own_max_y",
    "own_max_z",
)

_NO_OWN = (None,) * 6


class NodeBoundsError(ValueError):
    """The staged file is not an ``ada.assets/node-bounds@1`` file core can ingest."""


class BoundsQueryError(ValueError):
    """A box query core cannot answer as asked (400)."""


# --- the file -----------------------------------------------------------------------------------


def parse_bounds_header(line: bytes) -> dict:
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise NodeBoundsError(f"the first line of the node bounds is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema") != NODE_BOUNDS_SCHEMA:
        got = header.get("schema") if isinstance(header, dict) else type(header).__name__
        raise NodeBoundsError(f"the node bounds header names schema {got!r}, not {NODE_BOUNDS_SCHEMA!r}")
    collection = header.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise NodeBoundsError("the node bounds header names no 'collection'")
    header["collection"] = collection.strip().lower()
    frame = header.get("frame")
    header["frame"] = dict(frame) if isinstance(frame, dict) else {}
    return header


async def read_node_bounds_header(chunks: AsyncIterable[bytes]) -> dict:
    """Just the header line -- what a dry run reports."""
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeBoundsError("the node bounds file is empty") from None
    finally:
        await lines.aclose()
    return parse_bounds_header(first)


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = v if isinstance(v, str) else str(v)
    return s.replace("\x00", "") if "\x00" in s else s


def parse_bbox(raw: Any) -> Optional[tuple[float, float, float, float, float, float]]:
    """``[min_x, min_y, min_z, max_x, max_y, max_z]`` as floats, or None when it is not a finite,
    non-inverted box."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 6:
        return None
    try:
        vals = tuple(float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vals):
        return None
    if vals[0] > vals[3] or vals[1] > vals[4] or vals[2] > vals[5]:
        return None
    return vals  # type: ignore[return-value]


def parse_bounds_batch(
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
        node_id = obj.get("node_id")
        bbox = parse_bbox(obj.get("bbox"))
        if not isinstance(node_id, str) or not node_id or bbox is None:
            skipped += 1
            continue
        depth = obj.get("depth")
        if isinstance(depth, bool) or not isinstance(depth, (int, float)):
            depth = None
        own = parse_bbox(obj.get("own_bbox")) if obj.get("own_bbox") is not None else None
        records.append(
            (
                scope,
                provider,
                collection,
                _text(node_id),
                _text(obj.get("parent_id")),
                _text(obj.get("subject")),
                _text(obj.get("label")),
                _text(obj.get("full_name")),
                _text(obj.get("kind")),
                int(depth) if depth is not None else None,
                *bbox,
                *(own or _NO_OWN),
            )
        )
    return records, skipped


# --- ingest -------------------------------------------------------------------------------------


async def ingest_node_bounds(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Stream one ``node-bounds.ndjson.gz`` into its (scope, provider, collection) set, replacing it.

    ``provider`` is the publishing job's provider -- the authorised one; a header naming another is
    reported in ``warnings`` and ignored."""
    import asyncio

    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeBoundsError("the node bounds file is empty") from None
    header = parse_bounds_header(first)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the node bounds header names provider {header['provider']!r}; ingested under the publishing "
            f"provider {provider!r}"
        )
    collection = str(header["collection"])
    revision = None if header.get("revision") is None else str(header["revision"])
    loop = asyncio.get_running_loop()
    skipped = 0
    loaded = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("CREATE TEMP TABLE node_bounds_load (LIKE node_bounds) ON COMMIT DROP")

            async def flush(batch: list[bytes]) -> None:
                nonlocal skipped, loaded
                records, bad = await loop.run_in_executor(
                    None,
                    lambda: parse_bounds_batch(batch, scope=scope, provider=provider, collection=collection),
                )
                skipped += bad
                if records:
                    await conn.copy_records_to_table("node_bounds_load", records=records, columns=list(_COLUMNS))
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

            # Replacing the set row drops the old rows with it (the cascade).
            await conn.execute(
                "DELETE FROM node_bounds_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
                scope,
                provider,
                collection,
            )
            await conn.execute(
                """
                INSERT INTO node_bounds_sets (scope, provider, collection, revision, rows, frame, ingested_at)
                VALUES ($1, $2, $3, $4, 0, $5::jsonb, NOW())
                """,
                scope,
                provider,
                collection,
                revision,
                json.dumps(header["frame"]),
            )
            cols = ", ".join(_COLUMNS)
            status = await conn.execute(
                f"""
                INSERT INTO node_bounds ({cols})
                SELECT DISTINCT ON (node_id) {cols} FROM node_bounds_load ORDER BY node_id, ctid
                """
            )
            rows = int(status.split()[-1])
            await conn.execute(
                "UPDATE node_bounds_sets SET rows = $4 WHERE scope = $1 AND provider = $2 AND collection = $3",
                scope,
                provider,
                collection,
                rows,
            )
    duplicates = loaded - rows
    if duplicates:
        warnings.append(f"{duplicates} duplicate node_id line(s) dropped (first one kept)")
    if skipped:
        warnings.append(f"{skipped} line(s) without a node_id or a usable bbox skipped")
    return {
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "frame": header["frame"],
        "rows": rows,
        "skipped": skipped,
        "duplicates": duplicates,
        "warnings": warnings,
        "timings": {
            "ingest_ms": round((t1 - t0) * 1000),
            "swap_ms": round((time.perf_counter() - t1) * 1000),
        },
    }


#: The staged role and schema of a node-bounds patch (``db/attribute_index.py``, "patches").
NODE_BOUNDS_PATCH_ROLE = "node-bounds-patch.ndjson.gz"
NODE_BOUNDS_PATCH_SCHEMA = "ada.assets/node-bounds-patch@1"


async def patch_node_bounds(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Apply one ``node-bounds-patch.ndjson.gz`` to the (scope, provider, collection) set the full
    ingest built, in one transaction: each listed node's row is replaced, each deleted node's row
    removed. Refused (``patch_refused``) when there is no such set. A parent's box is NOT widened
    here: the provider re-sends an ancestor whose box changed, as a row of its own.

    ``ingested_at`` and ``rows`` of the set follow; its ``revision`` and ``frame`` stay those of the
    full export."""
    from .attribute_index import expand_subtree_deletes, load_patch, parse_patch_header, patch_refused

    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise NodeBoundsError("the node bounds patch is empty") from None
    header = parse_patch_header(first, NODE_BOUNDS_PATCH_SCHEMA, NodeBoundsError)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the node bounds patch header names provider {header['provider']!r}; applied under the publishing "
            f"provider {provider!r}"
        )
    collection = str(header["collection"])
    key = (scope, provider, collection)

    def parse(batch: list[bytes]) -> tuple[list[tuple], int]:
        return parse_bounds_batch(batch, scope=scope, provider=provider, collection=collection)

    async with pool.acquire() as conn, conn.transaction():
        held = await conn.fetchval(
            "SELECT rows FROM node_bounds_sets WHERE scope = $1 AND provider = $2 AND collection = $3 FOR UPDATE",
            *key,
        )
        if held is None:
            await lines.aclose()
            return patch_refused(provider=provider, collection=collection, header=header, t0=t0)
        await conn.execute("CREATE TEMP TABLE node_bounds_patch_load (LIKE node_bounds) ON COMMIT DROP")
        await conn.execute("ALTER TABLE node_bounds_patch_load ADD COLUMN seq INT NOT NULL")
        loaded = await load_patch(
            conn, lines, load_table="node_bounds_patch_load", columns=_COLUMNS, parse=parse, batch_rows=batch_rows
        )
        t1 = time.perf_counter()
        if loaded["subtrees"]:
            # A re-exported unit: the bounds of every node below it go too, walked through the set's
            # own parent_id (indexed: node_bounds_parent_idx).
            await expand_subtree_deletes(
                conn, "node_bounds", "n.scope = $1 AND n.provider = $2 AND n.collection = $3", *key
            )
        cols = ", ".join(_COLUMNS)
        removed = await conn.execute(
            """
            DELETE FROM node_bounds b
            USING (SELECT node_id FROM node_bounds_patch_load UNION SELECT node_id FROM patch_deletes) t
            WHERE b.scope = $1 AND b.provider = $2 AND b.collection = $3 AND b.node_id = t.node_id
            """,
            *key,
        )
        inserted = await conn.execute(
            f"""
            INSERT INTO node_bounds ({cols})
            SELECT DISTINCT ON (l.node_id) {", ".join("l." + c for c in _COLUMNS)} FROM node_bounds_patch_load l
            WHERE NOT EXISTS (SELECT 1 FROM patch_deletes d WHERE d.node_id = l.node_id AND d.seq > l.seq)
            ORDER BY l.node_id, l.seq
            """
        )
        deleted = await conn.fetchval(
            """
            SELECT count(DISTINCT d.node_id) FROM patch_deletes d
            WHERE NOT EXISTS (SELECT 1 FROM node_bounds b
                              WHERE b.scope = $1 AND b.provider = $2 AND b.collection = $3 AND b.node_id = d.node_id)
            """,
            *key,
        )
        n_removed, n_inserted = int(removed.split()[-1]), int(inserted.split()[-1])
        await conn.execute(
            """
            UPDATE node_bounds_sets SET rows = GREATEST(rows - $4 + $5, 0), ingested_at = NOW()
            WHERE scope = $1 AND provider = $2 AND collection = $3
            """,
            *key,
            n_removed,
            n_inserted,
        )
    if loaded["skipped"]:
        warnings.append(f"{loaded['skipped']} patch line(s) without a node_id or a usable bbox skipped")
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


async def drop_node_bounds(pool: asyncpg.Pool, *, scope: str, provider: str, collection: str) -> bool:
    status = await pool.execute(
        "DELETE FROM node_bounds_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
        scope,
        provider,
        collection.lower(),
    )
    return status.split()[-1] != "0"


# --- reading ------------------------------------------------------------------------------------


def readable_scopes(scope: str, shared_from: Iterable[str] = ()) -> list[str]:
    """The scopes a bounds query of ``scope`` reads: its own, the shared scope (everyone's) and the
    origins of the attribute shares into it the caller may read (resolved by the route)."""
    return list(dict.fromkeys([scope, SHARED_SCOPE, *shared_from]))


async def list_bounds_sets(pool: asyncpg.Pool, *, scopes: Sequence[str]) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT scope, provider, collection, revision, rows, frame, ingested_at
        FROM node_bounds_sets WHERE scope = ANY($1::text[])
        ORDER BY provider, collection, scope
        """,
        list(scopes),
    )
    return [_set_wire(r) for r in rows]


def _set_wire(r) -> dict:
    frame = r["frame"]
    if isinstance(frame, str):
        frame = json.loads(frame)
    return {
        "scope": r["scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "revision": r["revision"],
        "rows": r["rows"],
        "frame": frame or {},
        "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] is not None else None,
    }


@dataclass(frozen=True)
class BoundsQuery:
    box: tuple[float, float, float, float, float, float]
    mode: str
    collections: Optional[tuple[str, ...]]
    providers: Optional[tuple[str, ...]]
    limit: int


def _names(body: dict, key: str) -> Optional[tuple[str, ...]]:
    raw = body.get(key)
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        raise BoundsQueryError(f"'{key}' must be a list of names")
    return tuple(dict.fromkeys(v.strip() for v in raw))


def parse_bounds_query(body: Any) -> BoundsQuery:
    """``{box: {min: [x, y, z], max: [x, y, z]}, mode: "partial"|"within", collections?, providers?,
    limit?}``."""
    if not isinstance(body, dict):
        raise BoundsQueryError("the body must be a JSON object")
    box = body.get("box")
    if not isinstance(box, dict):
        raise BoundsQueryError("'box' ({min: [x, y, z], max: [x, y, z]}) is required")
    lo, hi = box.get("min"), box.get("max")
    parsed = parse_bbox([*(lo or ()), *(hi or ())]) if isinstance(lo, list) and isinstance(hi, list) else None
    if parsed is None:
        raise BoundsQueryError("'box' needs finite 'min' and 'max' of three numbers each, min <= max")
    mode = str(body.get("mode") or "partial")
    if mode not in MODES:
        raise BoundsQueryError(f"'mode' must be one of {', '.join(MODES)}")
    collections = _names(body, "collections")
    if collections is not None:
        collections = tuple(dict.fromkeys(c.lower() for c in collections))
    limit = body.get("limit", QUERY_MAX_RESULT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise BoundsQueryError("'limit' must be a positive integer")
    return BoundsQuery(
        box=parsed,
        mode=mode,
        collections=collections,
        providers=_names(body, "providers"),
        limit=min(limit, QUERY_MAX_RESULT),
    )


def fold_hits(
    rows: Sequence[dict],
    *,
    mode: str,
    child_totals: dict[str, int],
    box: Optional[Sequence[float]] = None,
) -> list[dict]:
    """The topmost answerable nodes of one set's candidates (module docstring).

    ``rows`` are the candidates: for ``partial`` every node whose box MEETS the query box (closed
    upward: a parent's box holds its children's), for ``within`` every node whose box lies INSIDE it
    (closed downward). ``child_totals`` counts each candidate's children in the whole set -- bounded
    children, the only ones a box can take. ``box``, the query box, takes a ``partial`` candidate
    whose own geometry's box (``own_*``) meets it."""
    by_id = {str(r["node_id"]): r for r in rows}
    children: dict[str, list[str]] = {}
    for nid, r in by_id.items():
        parent = r.get("parent_id")
        if parent is not None and str(parent) in by_id:
            children.setdefault(str(parent), []).append(nid)

    whole: dict[str, bool] = {}
    if mode == "within":
        whole = {nid: True for nid in by_id}
    else:
        # Bottom-up without recursion: a node is whole when it is a leaf of the set, or when every
        # one of its children is a candidate and whole.
        order: list[str] = []
        seen: set[str] = set()
        for root in by_id:
            if root in seen:
                continue
            stack = [(root, False)]
            while stack:
                nid, done = stack.pop()
                if done:
                    order.append(nid)
                    continue
                if nid in seen:
                    continue
                seen.add(nid)
                stack.append((nid, True))
                stack.extend((c, False) for c in children.get(nid, ()) if c not in seen)
        for nid in order:
            kids = children.get(nid, [])
            total = child_totals.get(nid, 0)
            whole[nid] = total == 0 or (len(kids) == total and all(whole.get(c, False) for c in kids))
        if box is not None:
            for nid, r in by_id.items():
                if not whole[nid] and _own_meets(r, box):
                    whole[nid] = True

    def answerable(nid: str) -> bool:
        return whole.get(nid, False) and by_id[nid].get("subject") is not None

    out: list[dict] = []
    for nid, r in by_id.items():
        if not answerable(nid):
            continue
        # Topmost: no ancestor among the candidates is answerable.
        cursor = r.get("parent_id")
        covered = False
        hops = 0
        while cursor is not None and str(cursor) in by_id and hops < 512:
            if answerable(str(cursor)):
                covered = True
                break
            cursor = by_id[str(cursor)].get("parent_id")
            hops += 1
        if not covered:
            out.append(r)
    out.sort(key=lambda r: (r.get("depth") if r.get("depth") is not None else 1 << 30, str(r["node_id"])))
    return out


def _own_meets(r: dict, box: Sequence[float]) -> bool:
    """Whether the node's own geometry's box, when it has one, meets ``box``."""
    own = [r.get(c) for c in _COLUMNS[-6:]]
    if any(v is None for v in own):
        return False
    x0, y0, z0, x1, y1, z1 = box
    return own[0] <= x1 and own[3] >= x0 and own[1] <= y1 and own[4] >= y0 and own[2] <= z1 and own[5] >= z0


def _hit_wire(r: dict) -> dict:
    return {
        "scope": r["scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "node_id": r["node_id"],
        "parent_id": r["parent_id"],
        "subject": r["subject"],
        "label": r["label"],
        "full_name": r["full_name"],
        "kind": r["kind"],
        "depth": r["depth"],
        "bbox": [r["min_x"], r["min_y"], r["min_z"], r["max_x"], r["max_y"], r["max_z"]],
    }


async def query_node_bounds(pool: asyncpg.Pool, *, scopes: Sequence[str], query: BoundsQuery) -> dict:
    """The nodes in ``query.box`` of every bounds set readable from ``scopes`` that the query's
    collections / providers name, folded per set (:func:`fold_hits`).

    ``{sets: [...], hits: [...], truncated, candidates, took_ms}``: ``sets`` lists the sets read
    (with their frame), ``truncated`` names why the answer is not complete (too many candidates in
    one set, or more answered nodes than ``limit``), else null."""
    t0 = time.perf_counter()
    sets = [
        s
        for s in await list_bounds_sets(pool, scopes=scopes)
        if (query.collections is None or s["collection"] in query.collections)
        and (query.providers is None or s["provider"] in query.providers)
    ]
    x0, y0, z0, x1, y1, z1 = query.box
    hits: list[dict] = []
    truncated: Optional[str] = None
    candidates = 0
    async with pool.acquire() as conn:
        await conn.execute(f"SET statement_timeout = '{QUERY_STATEMENT_TIMEOUT}'")
        try:
            for s in sets:
                key = (s["scope"], s["provider"], s["collection"])
                if query.mode == "within":
                    where = (
                        "box(point(min_x, min_y), point(max_x, max_y)) <@ box(point($4, $5), point($6, $7)) "
                        "AND min_z >= $8 AND max_z <= $9"
                    )
                else:
                    where = (
                        "box(point(min_x, min_y), point(max_x, max_y)) && box(point($4, $5), point($6, $7)) "
                        "AND min_z <= $9 AND max_z >= $8"
                    )
                rows = await conn.fetch(
                    f"""
                    SELECT {", ".join(_COLUMNS)}
                    FROM node_bounds
                    WHERE scope = $1 AND provider = $2 AND collection = $3 AND {where}
                    LIMIT {QUERY_MAX_CANDIDATES + 1}
                    """,
                    *key,
                    x0,
                    y0,
                    x1,
                    y1,
                    z0,
                    z1,
                )
                if len(rows) > QUERY_MAX_CANDIDATES:
                    truncated = (
                        f"more than {QUERY_MAX_CANDIDATES} nodes of {s['provider']}/{s['collection']} are in the "
                        "box; draw a smaller box"
                    )
                    continue
                candidates += len(rows)
                found = [dict(r) for r in rows]
                totals: dict[str, int] = {}
                if found and query.mode == "partial":
                    counted = await conn.fetch(
                        """
                        SELECT parent_id, count(*) AS n FROM node_bounds
                        WHERE scope = $1 AND provider = $2 AND collection = $3 AND parent_id = ANY($4::text[])
                        GROUP BY parent_id
                        """,
                        *key,
                        [r["node_id"] for r in found],
                    )
                    totals = {str(c["parent_id"]): int(c["n"]) for c in counted}
                elif found:
                    totals = {}
                hits.extend(_hit_wire(r) for r in fold_hits(found, mode=query.mode, child_totals=totals, box=query.box))
        finally:
            await conn.execute("RESET statement_timeout")
    if len(hits) > query.limit:
        truncated = truncated or f"{len(hits)} nodes are in the box; the first {query.limit} are listed"
        hits = hits[: query.limit]
    return {
        "sets": sets,
        "hits": hits,
        "mode": query.mode,
        "truncated": truncated,
        "candidates": candidates,
        "took_ms": round((time.perf_counter() - t0) * 1000),
    }
