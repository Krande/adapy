"""What a provider's collection offers for a new element to point at: ingest of
``reference-data.ndjson.gz`` and the browser's query over it. See migration 049 for the storage.

THE FILE. ``ada.assets/reference-data@1``, gzip NDJSON, staged beside whatever else a provider stages
and ingested by core's publish job after the plan is written (``formats/asset_publish.py``). The
first line is the header::

    {"schema": "ada.assets/reference-data@1", "provider": ..., "collection": ..., "revision": ...,
     "counts"?: {kind: n}}

and every further line one entry::

    {"kind", "ref", "name"?, "owner"?, "use"?, "attrs"?}

``kind`` is a lower-case word (an E3D provider sends ``spec``, ``spco`` and ``material``). ``ref``
is the entry's name exactly as an element's reference attribute holds it -- what a macro writes
after ``SPRE`` or ``MATR`` -- and with ``kind`` it is the entry's identity: a repeat keeps its first
line. ``owner`` names the entry it belongs to (an SPCO's spec). ``use`` is what the provider says the
entry is for (``section``, ``plate``, ``fitting``, ``joint``, ``material``, ``other``). ``attrs`` is
an object, free form per kind. A line without a usable ``kind`` or ``ref`` is skipped and counted.

THE INGEST REPLACES the (scope, provider, collection) set in one transaction.

USAGE IS READ, NOT STORED. How many elements use an entry -- and which element types -- comes from
the collection's attribute index at query time (:data:`USAGE_KEYS`: an ``spco`` is counted on
``SPRE``, a ``material`` on ``MATR``), for the entries of the page asked for only. Every entry is
listed whether anything uses it or not; that is what the set is for.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterable, Iterable, Optional, Sequence

import asyncpg

from .attribute_index import ndjson_lines

REFERENCE_DATA_SCHEMA = "ada.assets/reference-data@1"
#: The staged role (its filename under ``assets/_staging/<id>/``) a provider stages the set as.
REFERENCE_DATA_ROLE = "reference-data.ndjson.gz"

#: The attribute an element names an entry of each kind by, in the attribute index.
USAGE_KEYS = {"spco": "SPRE", "material": "MATR"}

INGEST_BATCH_ROWS = 20_000
QUERY_MAX_LIMIT = 1000
QUERY_DEFAULT_LIMIT = 200
QUERY_STATEMENT_TIMEOUT = "30s"

_WORD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_COLUMNS = ("scope", "provider", "collection", "kind", "ref", "name", "owner", "use", "attrs")


class ReferenceDataError(ValueError):
    """The staged file is not an ``ada.assets/reference-data@1`` file core can ingest."""


class ReferenceQueryError(ValueError):
    """A query core cannot answer as asked (400)."""


# --- the file -----------------------------------------------------------------------------------


def parse_reference_header(line: bytes) -> dict:
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise ReferenceDataError(f"the first line of the reference data is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema") != REFERENCE_DATA_SCHEMA:
        got = header.get("schema") if isinstance(header, dict) else type(header).__name__
        raise ReferenceDataError(f"the reference data header names schema {got!r}, not {REFERENCE_DATA_SCHEMA!r}")
    collection = header.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise ReferenceDataError("the reference data header names no 'collection'")
    header["collection"] = collection.strip().lower()
    return header


async def read_reference_header(chunks: AsyncIterable[bytes]) -> dict:
    """Just the header line -- what a dry run reports."""
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise ReferenceDataError("the reference data file is empty") from None
    finally:
        await lines.aclose()
    return parse_reference_header(first)


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = (v if isinstance(v, str) else str(v)).replace("\x00", "")
    return s if s.strip() else None


def _word(v: Any) -> Optional[str]:
    s = _text(v)
    if s is None:
        return None
    s = s.strip().lower()
    return s if _WORD_RE.match(s) else None


def parse_reference_batch(
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
        kind, ref = _word(obj.get("kind")), _text(obj.get("ref"))
        if kind is None or ref is None:
            skipped += 1
            continue
        attrs = obj.get("attrs") if isinstance(obj.get("attrs"), dict) else {}
        records.append(
            (
                scope,
                provider,
                collection,
                kind,
                ref,
                _text(obj.get("name")),
                _text(obj.get("owner")),
                _word(obj.get("use")),
                json.dumps(attrs, sort_keys=True, ensure_ascii=False).replace("\\u0000", ""),
            )
        )
    return records, skipped


# --- ingest -------------------------------------------------------------------------------------


async def ingest_reference_data(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Stream one ``reference-data.ndjson.gz`` into its (scope, provider, collection) set, replacing it.

    ``provider`` is the publishing job's provider; a header naming another is reported in
    ``warnings`` and ignored."""
    import asyncio

    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise ReferenceDataError("the reference data file is empty") from None
    header = parse_reference_header(first)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the reference data header names provider {header['provider']!r}; ingested under the publishing "
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
                "CREATE TEMP TABLE reference_items_load (LIKE reference_items INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            await conn.execute("ALTER TABLE reference_items_load ALTER COLUMN attrs TYPE TEXT")

            async def flush(batch: list[bytes]) -> None:
                nonlocal skipped, loaded
                records, bad = await loop.run_in_executor(
                    None,
                    lambda: parse_reference_batch(batch, scope=scope, provider=provider, collection=collection),
                )
                skipped += bad
                if records:
                    await conn.copy_records_to_table("reference_items_load", records=records, columns=list(_COLUMNS))
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
                "DELETE FROM reference_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
                scope,
                provider,
                collection,
            )
            await conn.execute(
                """
                INSERT INTO reference_sets (scope, provider, collection, revision, rows, counts, ingested_at)
                VALUES ($1, $2, $3, $4, 0, '{}'::jsonb, clock_timestamp())
                """,
                scope,
                provider,
                collection,
                revision,
            )
            status = await conn.execute(
                """
                INSERT INTO reference_items (scope, provider, collection, kind, ref, name, owner, use, attrs)
                SELECT DISTINCT ON (kind, ref) scope, provider, collection, kind, ref, name, owner, use, attrs::jsonb
                FROM reference_items_load ORDER BY kind, ref, ctid
                """
            )
            rows = int(status.split()[-1])
            counts = {
                r["kind"]: int(r["n"])
                for r in await conn.fetch(
                    """
                    SELECT kind, count(*) AS n FROM reference_items
                    WHERE scope = $1 AND provider = $2 AND collection = $3 GROUP BY kind
                    """,
                    scope,
                    provider,
                    collection,
                )
            }
            await conn.execute(
                """
                UPDATE reference_sets SET rows = $4, counts = $5::jsonb
                WHERE scope = $1 AND provider = $2 AND collection = $3
                """,
                scope,
                provider,
                collection,
                rows,
                json.dumps(counts),
            )
    if loaded - rows:
        warnings.append(f"{loaded - rows} repeated kind/ref line(s) dropped (first one kept)")
    if skipped:
        warnings.append(f"{skipped} line(s) without a usable kind or ref skipped")
    return {
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "rows": rows,
        "counts": counts,
        "skipped": skipped,
        "warnings": warnings,
        "timings": {
            "ingest_ms": round((t1 - t0) * 1000),
            "swap_ms": round((time.perf_counter() - t1) * 1000),
        },
    }


async def drop_reference_data(pool: asyncpg.Pool, *, scope: str, provider: str, collection: str) -> bool:
    status = await pool.execute(
        "DELETE FROM reference_sets WHERE scope = $1 AND provider = $2 AND collection = $3",
        scope,
        provider,
        collection.lower(),
    )
    return status.split()[-1] != "0"


# --- reading ------------------------------------------------------------------------------------


def _set_wire(r) -> dict:
    counts = r["counts"]
    if isinstance(counts, str):
        counts = json.loads(counts)
    return {
        "scope": r["scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "revision": r["revision"],
        "rows": r["rows"],
        "counts": counts or {},
        "ingested_at": r["ingested_at"].isoformat() if r["ingested_at"] is not None else None,
    }


async def list_reference_sets(
    pool: asyncpg.Pool,
    *,
    scopes: Sequence[str],
    store: Optional[str] = None,
    granted: Iterable[tuple[str, Optional[str]]] = (),
) -> list[dict]:
    """The sets readable from ``scopes``, plus those of the provider store (``store``) that
    ``granted`` covers (``(provider, collection | None)``, None for every collection). Where the
    store and a scope both hold a (provider, collection), the store's shadows the scope's -- the
    attribute index's rule (``routes/provider_store.py``)."""
    granted = list(granted)
    wanted = list(dict.fromkeys([*scopes, *([store] if store and granted else [])]))
    rows = await pool.fetch(
        """
        SELECT scope, provider, collection, revision, rows, counts, ingested_at
        FROM reference_sets WHERE scope = ANY($1::text[])
        ORDER BY provider, collection, scope
        """,
        wanted,
    )
    sets = [_set_wire(r) for r in rows]
    if store and store not in scopes:
        sets = [
            s
            for s in sets
            if s["scope"] != store
            or any(p == s["provider"] and (c is None or c.lower() == s["collection"]) for p, c in granted)
        ]
    from_store = {(s["provider"], s["collection"]) for s in sets if s["scope"] == store and store not in scopes}
    return [s for s in sets if s["scope"] == store or (s["provider"], s["collection"]) not in from_store]


@dataclass(frozen=True)
class ReferenceQuery:
    provider: str
    collection: str
    kind: Optional[str]
    use: Optional[str]
    owner: Optional[str]
    prefix: Optional[str]
    contains: Optional[str]
    limit: int
    offset: int
    usage: bool


def _flag(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_reference_query(params: dict) -> ReferenceQuery:
    """``provider``, ``collection`` (required); ``kind``, ``use``, ``owner``, ``prefix`` (of ``ref``,
    case-sensitive), ``q`` (in ``ref``, ``name`` or ``attrs``' text, any case), ``limit`` (default
    200, at most 1000), ``offset``, ``usage`` (count each entry's users in the attribute index)."""
    provider = _text(params.get("provider"))
    collection = _text(params.get("collection"))
    if not provider or not collection:
        raise ReferenceQueryError("'provider' and 'collection' are required")
    kind = _text(params.get("kind"))
    if kind is not None and _word(kind) is None:
        raise ReferenceQueryError("'kind' must be a lower-case word, e.g. spco")
    use = _text(params.get("use"))
    if use is not None and _word(use) is None:
        raise ReferenceQueryError("'use' must be a lower-case word, e.g. section")
    try:
        limit = int(params.get("limit") or QUERY_DEFAULT_LIMIT)
        offset = int(params.get("offset") or 0)
    except (TypeError, ValueError):
        raise ReferenceQueryError("'limit' and 'offset' must be integers") from None
    if limit < 1 or offset < 0:
        raise ReferenceQueryError("'limit' must be positive and 'offset' not negative")
    return ReferenceQuery(
        provider=provider.strip(),
        collection=collection.strip().lower(),
        kind=_word(kind) if kind else None,
        use=_word(use) if use else None,
        owner=_text(params.get("owner")),
        prefix=_text(params.get("prefix")),
        contains=_text(params.get("q")),
        limit=min(limit, QUERY_MAX_LIMIT),
        offset=offset,
        usage=_flag(params.get("usage")),
    )


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def query_reference_items(pool: asyncpg.Pool, *, scope: str, query: ReferenceQuery) -> dict:
    """One page of the (``scope``, provider, collection) set's entries, ``ref`` order.

    ``{set, items: [{kind, ref, name, owner, use, attrs, used?}], total, facets: {kinds, uses},
    next_offset, usage: {indexed, partition?} | null, took_ms}``. ``set`` is null when the scope
    holds no such set. ``used`` (with ``usage``) is ``{count, by_kind}`` from the collection's
    attribute index, or null for a kind no attribute names; ``usage.indexed`` false means the
    collection has no attribute index to count in."""
    t0 = time.perf_counter()
    key = (scope, query.provider, query.collection)
    set_row = await pool.fetchrow(
        """
        SELECT scope, provider, collection, revision, rows, counts, ingested_at
        FROM reference_sets WHERE scope = $1 AND provider = $2 AND collection = $3
        """,
        *key,
    )
    if set_row is None:
        return {"set": None, "items": [], "total": 0, "facets": {}, "next_offset": None, "usage": None,
                "took_ms": round((time.perf_counter() - t0) * 1000)}  # fmt: skip

    where = ["scope = $1", "provider = $2", "collection = $3"]
    args: list[Any] = list(key)

    def arg(v: Any) -> str:
        args.append(v)
        return f"${len(args)}"

    if query.kind:
        where.append(f"kind = {arg(query.kind)}")
    if query.use:
        where.append(f"use = {arg(query.use)}")
    if query.owner:
        where.append(f"owner = {arg(query.owner)}")
    if query.prefix:
        where.append(f"ref LIKE {arg(_like_escape(query.prefix) + '%')}")
    if query.contains:
        needle = arg("%" + _like_escape(query.contains) + "%")
        where.append(f"(ref ILIKE {needle} OR name ILIKE {needle} OR attrs::text ILIKE {needle})")
    clause = " AND ".join(where)

    async with pool.acquire() as conn:
        await conn.execute(f"SET statement_timeout = '{QUERY_STATEMENT_TIMEOUT}'")
        try:
            total = await conn.fetchval(f"SELECT count(*) FROM reference_items WHERE {clause}", *args)
            rows = await conn.fetch(
                f"""
                SELECT kind, ref, name, owner, use, attrs FROM reference_items WHERE {clause}
                ORDER BY kind, ref LIMIT {query.limit} OFFSET {query.offset}
                """,
                *args,
            )
            facet_rows = await conn.fetch(
                """
                SELECT kind, use, count(*) AS n FROM reference_items
                WHERE scope = $1 AND provider = $2 AND collection = $3 GROUP BY kind, use
                """,
                *key,
            )
            items = [_item_wire(r) for r in rows]
            usage = await _usage(conn, key, items) if query.usage else None
        finally:
            await conn.execute("RESET statement_timeout")

    kinds: dict[str, int] = {}
    uses: dict[str, int] = {}
    for r in facet_rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + int(r["n"])
        if r["use"]:
            uses[r["use"]] = uses.get(r["use"], 0) + int(r["n"])
    end = query.offset + len(items)
    return {
        "set": _set_wire(set_row),
        "items": items,
        "total": int(total or 0),
        "facets": {"kinds": kinds, "uses": uses},
        "next_offset": end if end < int(total or 0) else None,
        "usage": usage,
        "took_ms": round((time.perf_counter() - t0) * 1000),
    }


def _item_wire(r) -> dict:
    attrs = r["attrs"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return {
        "kind": r["kind"],
        "ref": r["ref"],
        "name": r["name"],
        "owner": r["owner"],
        "use": r["use"],
        "attrs": attrs or {},
    }


async def _usage(conn, key: tuple[str, str, str], items: list[dict]) -> dict:
    """Count, per item of the page, the attribute-index nodes naming it (:data:`USAGE_KEYS`), by
    node kind. Read from the collection's partition in the same scope -- the same provider's when
    there is one, else any provider's (one collection may be indexed by another provider)."""
    scope, provider, collection = key
    part = await conn.fetchrow(
        """
        SELECT part_key, provider FROM attr_collections
        WHERE scope = $1 AND lower(collection) = $2
        ORDER BY (provider = $3) DESC, ingested_at DESC LIMIT 1
        """,
        scope,
        collection,
        provider,
    )
    if part is None:
        for item in items:
            item["used"] = None
        return {"indexed": False}
    by_attr: dict[str, list[str]] = {}
    for item in items:
        attr = USAGE_KEYS.get(item["kind"])
        item["used"] = {"count": 0, "by_kind": {}} if attr else None
        if attr:
            by_attr.setdefault(attr, []).append(item["ref"])
    found: dict[tuple[str, str], dict] = {}
    for attr, refs in by_attr.items():
        rows = await conn.fetch(
            """
            SELECT attrs->>$2 AS ref, kind, count(*) AS n FROM attr_nodes
            WHERE part_key = $1 AND attrs->>$2 = ANY($3::text[])
            GROUP BY 1, 2
            """,
            part["part_key"],
            attr,
            refs,
        )
        for r in rows:
            entry = found.setdefault((attr, r["ref"]), {"count": 0, "by_kind": {}})
            entry["count"] += int(r["n"])
            entry["by_kind"][r["kind"] or "?"] = int(r["n"])
    for item in items:
        attr = USAGE_KEYS.get(item["kind"])
        if attr and (attr, item["ref"]) in found:
            item["used"] = found[(attr, item["ref"])]
    return {"indexed": True, "provider": part["provider"]}


__all__ = [
    "REFERENCE_DATA_ROLE",
    "REFERENCE_DATA_SCHEMA",
    "ReferenceDataError",
    "ReferenceQuery",
    "ReferenceQueryError",
    "USAGE_KEYS",
    "drop_reference_data",
    "ingest_reference_data",
    "list_reference_sets",
    "parse_reference_batch",
    "parse_reference_header",
    "parse_reference_query",
    "query_reference_items",
    "read_reference_header",
]
