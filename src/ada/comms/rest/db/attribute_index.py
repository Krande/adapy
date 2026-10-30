"""The provider-neutral attribute index: ingest of ``attribute-index.ndjson.gz`` and the search
behind ``POST /scopes/{scope}/assets/search``. See migration 037 for the storage layout.

INGEST STREAMS. An index of a large collection is a gigabyte of NDJSON (a hundred megabytes
gzipped): it is decompressed as it arrives, split into lines, parsed a batch at a time on a
thread (so the worker's event loop keeps answering its heartbeats) and COPYed in batches into a
table nobody reads yet. Nothing ever holds the whole file, or all of its rows.

THE SWAP IS ONE TRANSACTION. The loaded table is indexed and analysed while it is still
standalone; then the old partition is detached and dropped and the new one renamed into its name
and attached, together. A search that started before the swap finishes against the old rows; one
that starts after it sees only the new ones. Two ingests of the same collection are serialised on
a session advisory lock keyed by the partition, which is also what makes clearing a crashed
ingest's leftover table safe.

SEARCH NEVER INTERPOLATES WHAT A CALLER SENT. Keys and values travel as bind parameters --
equality and IN as a JSON containment object (``attrs @> $n::jsonb``, which the GIN
``jsonb_path_ops`` index answers), prefix as ``attrs->>$k LIKE $p`` with the LIKE wildcards in the
prefix escaped. The only identifiers spliced into SQL are partition names this module generated,
checked against their own grammar before use.

NEGATIVE FILTERS ARE EXACT COMPLEMENTS. ``ne`` / ``not_in`` are ``NOT (<the eq/in probes>)``: they
match every node whose value differs *or that has no such key*, and a value matched by either
spelling (``3`` and ``"3"``) is excluded under both. ``exists`` / ``missing`` are ``attrs ? $k`` and
its negation. None of these can be answered by the ``jsonb_path_ops`` index; next to a positive
filter they only refine what the index narrowed, alone they scan the partition (still bounded by
the statement timeout). Excluded providers / collections are dropped when the partitions are
chosen, so they are never read at all.

A NAME IS A COLUMN, NOT AN ATTRIBUTE. ``name`` matches ``full_name`` (and with ``refs``,
``source_ref``). A case-sensitive ``eq`` / ``prefix`` of ``full_name`` is answered by its btree (the
column is ``COLLATE "C"``, so ``LIKE 'x%'`` is a range scan of it); ``contains``, ``ignore_case`` and
``refs`` are ``(I)LIKE`` scans, so the parser refuses them unless the query names what it reads
(:func:`name_scans`), and they stay under the statement timeout.

PAGINATION IS A KEYSET over (provider, collection, node_id), walked partition by partition in
that order with ``node_id > $last ORDER BY node_id`` on each -- the btree on node_id makes a page of
a broad query as cheap as a page of a narrow one. The cursor is opaque base64 carrying the last
row's position, a fingerprint of the filters (a cursor replayed against a different query is
refused rather than silently skipping rows) and the per-partition totals counted for the first
page, so later pages do not count again.

A PARTITION CAN BE SHARED INTO ANOTHER SCOPE (migration 039): a search of the target scope reads
it where it lies, beside the scope's own. The same (provider, collection) can then be read twice
-- owned here and shared from elsewhere, or shared from two scopes -- and the two are different
partitions with different rows, so everything that walks partitions keys them by (origin scope,
provider, collection): the order is (provider, collection, own before shared, origin), the cursor
carries the origin (``s``), and every row says which scope it came from. Whether the caller may
read a share is decided per request, against the ORIGIN scope (:func:`readable_share_origins`):
the share says where a partition may be read, never who may read it. A share can also name a
whole (scope, provider) group (migration 040): every partition of it, the ones a later sweep adds
included, reads as shared into the target, by the same rule.

ONE COPY PER (provider, collection, revision). The same collection swept into a project and into
the shared scope at the same revision is the same rows twice. So an ingest that finds the shared
scope already holding that (provider, collection) at the header's revision does not copy it: it
shares the shared partition into the ingesting scope instead (and folds away an older copy the
scope held), and says so (``deduplicated_into``). An ingest INTO the shared scope folds the copies
other scopes hold at the same revision the other way. Only ever toward the shared scope -- which
everyone may read, so nothing becomes visible that was not -- and only at an equal, known
revision: a project's own sweep may use its own options and credentials, and its rows never move
anywhere else on their own. ``ADA_ATTR_INDEX_DEDUP=0`` turns this off (every ingest copies).
An admin folds copies at DIFFERENT revisions by hand (:func:`fold_attr_duplicates`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, AsyncIterable, AsyncIterator, Iterable, Optional

import asyncpg

from ada.config import logger

from ._common import _loads_jsonb

ATTRIBUTE_INDEX_SCHEMA = "ada.assets/attribute-index@1"
#: The staged role (its filename under ``assets/_staging/<id>/``) a provider stages the index as.
ATTRIBUTE_INDEX_ROLE = "attribute-index.ndjson.gz"

#: Lines parsed and COPYed per round trip. Bounds memory at a batch, not a file.
INGEST_BATCH_ROWS = 20_000
#: A key with more distinct values than this is not histogrammed at ingest (``high_card_keys``):
#: its values are answered by scanning. Bounds ingest memory at keys x this.
VALUE_STATS_MAX_DISTINCT = 2_000
#: A value longer than this is never a useful autocomplete entry; its key is treated as high-card.
VALUE_STATS_MAX_LEN = 200

SEARCH_DEFAULT_LIMIT = 200
SEARCH_MAX_LIMIT = 1000
#: Most values one ``in`` filter may list, and most filters one query may carry.
SEARCH_MAX_IN_VALUES = 1000
SEARCH_MAX_WHERE = 32
#: Ceiling on any one search statement: a pathological query fails instead of holding a
#: connection of the API's pool.
SEARCH_STATEMENT_TIMEOUT = "30s"
#: Ancestor walks stop here; a parent cycle in a provider's data ends instead of spinning.
PATH_MAX_DEPTH = 256

#: The scope every caller may read: the one copy a duplicated index is folded into.
SHARED_SCOPE = "shared"
#: Env switch for the one-copy rule at ingest (module docstring); anything but 0/false/no/off is on.
DEDUP_ENV = "ADA_ATTR_INDEX_DEDUP"
#: ``shared_by`` of the shares an ingest makes on its own.
DEDUP_SHARED_BY = "system:dedup"


def dedup_enabled() -> bool:
    """Whether an ingest keeps one copy per (provider, collection, revision). Read per ingest."""
    return os.environ.get(DEDUP_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


_GZIP_MAGIC = b"\x1f\x8b"
_PARTITION_RE = re.compile(r"^attr_p_[a-z0-9_]{1,40}_[0-9a-f]{16}$")
_BUILD_PREFIX = "attr_n_"
_NODE_COLUMNS = ("node_id", "parent_id", "full_name", "source_ref", "kind", "depth", "attrs")
_SELECT_COLUMNS = "node_id, parent_id, full_name, source_ref, kind, depth, attrs"


class AttributeIndexError(ValueError):
    """The staged index is not an ``ada.assets/attribute-index@1`` file core can ingest."""


class SearchQueryError(ValueError):
    """A search body (or cursor) that cannot be answered as asked -- a 400, worded for the caller."""


# --- naming -----------------------------------------------------------------------------------


def part_key_for(scope: str, provider: str, collection: str) -> str:
    """The partition's list value: stable per (scope, provider, collection), opaque, fixed length.

    A hash rather than the names themselves because a scope key carries project ids and user subs
    that have no business in a table name, and because a collection name is whatever its provider
    calls it -- any length, any alphabet.
    """
    raw = "\x00".join((scope, provider, collection)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def partition_name_for(provider: str, collection: str, part_key: str) -> str:
    """``attr_p_<readable slug>_<part_key>``: the slug is for a human reading ``\\d+``, the hash
    makes it unique. Lower-case ``[a-z0-9_]`` only and at most 63 bytes, so it is a valid
    identifier quoted or not and never truncated by Postgres into a collision."""
    slug = re.sub(r"[^a-z0-9]+", "_", f"{provider}_{collection}".lower()).strip("_")[:40] or "x"
    return f"attr_p_{slug}_{part_key}"


def _ident(name: str) -> str:
    """A partition name this module generated, quoted. Anything else is refused: the one place a
    name is spliced into SQL is guarded by the grammar that produced it."""
    if not (_PARTITION_RE.match(name) or re.match(r"^attr_n_[0-9a-f]{16}_[0-9a-f]{8}$", name)):
        raise ValueError(f"not an attribute-index table name: {name!r}")
    return f'"{name}"'


def _lock_id(part_key: str) -> int:
    """A signed int8 advisory-lock key for one partition."""
    n = int(part_key, 16)
    return n - (1 << 64) if n >= (1 << 63) else n


# --- reading the staged file ------------------------------------------------------------------


async def _gunzip(chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    """Decompress a gzip byte stream as it arrives (every member of a multi-member file); bytes
    that do not start with the gzip magic pass through untouched, so applying this twice peels a
    file the store compressed again at rest and costs nothing on one it did not. A stream that
    ends inside a member is an error: a truncated upload must not ingest as a shorter index."""
    it = chunks.__aiter__()
    buf = b""
    exhausted = False

    async def fill(n: int) -> None:
        nonlocal buf, exhausted
        while len(buf) < n and not exhausted:
            try:
                buf += bytes(await it.__anext__())
            except StopAsyncIteration:
                exhausted = True

    await fill(2)
    if buf[:2] != _GZIP_MAGIC:
        if buf:
            yield buf
        async for chunk in it:
            yield bytes(chunk)
        return

    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    while True:
        if not buf:
            await fill(1)
            if not buf:
                break
        if d.eof:
            # One member ended; what follows is the next member, or trailing padding.
            await fill(2)
            if buf[:2] != _GZIP_MAGIC:
                return
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = d.decompress(buf)
        if out:
            yield out
        buf = d.unused_data if d.eof else b""
    if not d.eof:
        raise AttributeIndexError("the attribute index ends inside a gzip member (truncated upload?)")


async def _lines(chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    """Non-blank lines of a byte stream, without holding more than one partial line."""
    buf = b""
    async for chunk in chunks:
        buf += chunk
        if b"\n" not in chunk:
            continue
        *complete, buf = buf.split(b"\n")
        for line in complete:
            if line.strip():
                yield line
    if buf.strip():
        yield buf


def ndjson_lines(chunks: AsyncIterable[bytes]) -> AsyncIterator[bytes]:
    """The lines of a (possibly gzipped, possibly twice) NDJSON byte stream."""
    return _lines(_gunzip(_gunzip(chunks)))


def parse_header(line: bytes) -> dict:
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise AttributeIndexError(f"the first line of the attribute index is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema") != ATTRIBUTE_INDEX_SCHEMA:
        got = header.get("schema") if isinstance(header, dict) else type(header).__name__
        raise AttributeIndexError(f"the attribute index header names schema {got!r}, not {ATTRIBUTE_INDEX_SCHEMA!r}")
    collection = header.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise AttributeIndexError("the attribute index header names no 'collection'")
    # Collections are case-insensitive and keyed lower-case, as the asset tree and
    # `assets/_options/<collection>.json` key them: one provider writing `alpha` and another `ALPHA`
    # name the same collection. Attribute KEYS stay verbatim -- they are case-sensitive.
    header["collection"] = collection.strip().lower()
    exclude = header.get("index_exclude") or []
    if not isinstance(exclude, list) or not all(isinstance(e, str) for e in exclude):
        raise AttributeIndexError("'index_exclude' must be a list of key names or 'prefix*' patterns")
    return header


async def read_attribute_index_header(chunks: AsyncIterable[bytes]) -> dict:
    """Just the header line -- what a dry run reports, without reading the rest."""
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise AttributeIndexError("the attribute index is empty") from None
    finally:
        await lines.aclose()
    return parse_header(first)


def _exclusion(patterns: Iterable[str]):
    exact = {p for p in patterns if not p.endswith("*")}
    prefixes = tuple(p[:-1] for p in patterns if p.endswith("*"))

    def excluded(key: str) -> bool:
        return key in exact or (bool(prefixes) and key.startswith(prefixes))

    return excluded


def _value_text(v: Any) -> str:
    """A value as ``attrs->>key`` renders it -- what the values endpoint lists and matches."""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


@dataclass
class _ValueStats:
    """Per-key value histogram, bounded: a key that outgrows it is dropped to ``high``."""

    keys: Counter = field(default_factory=Counter)
    values: dict[str, Counter] = field(default_factory=dict)
    high: set[str] = field(default_factory=set)

    def add(self, attrs: dict) -> None:
        for k, v in attrs.items():
            self.keys[k] += 1
            if k in self.high:
                continue
            text = _value_text(v)
            counter = self.values.setdefault(k, Counter())
            if len(text) > VALUE_STATS_MAX_LEN or (text not in counter and len(counter) >= VALUE_STATS_MAX_DISTINCT):
                self.high.add(k)
                del self.values[k]
                continue
            counter[text] += 1


def _clean_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = v if isinstance(v, str) else str(v)
    return s.replace("\x00", "") if "\x00" in s else s


def _parse_batch(lines: list[bytes], excluded, stats: _ValueStats) -> tuple[list[tuple], int]:
    """Lines -> COPY records. Runs on a thread. Malformed lines are counted, not fatal: one bad
    node in a million is the provider's bug to fix, not a reason to leave the collection unsearchable."""
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
        if not isinstance(node_id, str) or not node_id:
            skipped += 1
            continue
        raw = obj.get("attrs")
        attrs = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if v is None or v == "" or excluded(k):
                    continue
                attrs[k] = v
        stats.add(attrs)
        depth = obj.get("depth")
        if isinstance(depth, bool) or not isinstance(depth, (int, float)):
            depth = None
        doc = json.dumps(attrs, ensure_ascii=False, separators=(",", ":"))
        if "\\u0000" in doc:  # jsonb refuses NUL; a provider string carrying one loses it
            doc = doc.replace("\\u0000", "")
        records.append(
            (
                _clean_text(node_id),
                _clean_text(obj.get("parent_id")),
                _clean_text(obj.get("full_name")),
                _clean_text(obj.get("source_ref")),
                _clean_text(obj.get("kind")),
                int(depth) if depth is not None else None,
                doc,
            )
        )
    return records, skipped


# --- ingest -----------------------------------------------------------------------------------


async def _drop_leftovers(conn, part_key: str) -> None:
    """Build tables a crashed ingest of this partition left behind. Safe under the partition's
    advisory lock: no live ingest of it can own one."""
    names = await conn.fetch(
        "SELECT relname FROM pg_class WHERE relkind = 'r' AND relname LIKE $1",
        f"attr\\_n\\_{part_key}\\_%",
    )
    for r in names:
        await conn.execute(f"DROP TABLE IF EXISTS {_ident(r['relname'])}")


async def ingest_attribute_index(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Stream one ``attribute-index.ndjson.gz`` into its collection's partition, replacing it.

    ``provider`` is the publishing job's provider -- the authorised one; a header naming another is
    reported in ``warnings`` and ignored. Returns what was ingested and the phase timings
    (``ingest_ms`` download + parse + COPY, ``index_ms`` indexes + analyse, ``swap_ms``).

    The one-copy rule (module docstring): ``deduplicated_into`` is ``"shared"`` when the shared
    scope already held this revision and nothing was copied (``rows`` 0, ``partition`` the shared
    one); ``folded`` lists the copies other scopes held that an ingest into the shared scope (or
    the scope's own older copy, on a deduplicated ingest) replaced with a share.
    """
    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise AttributeIndexError("the attribute index is empty") from None
    header = parse_header(first)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the attribute index header names provider {header['provider']!r}; indexed under the "
            f"publishing provider {provider!r}"
        )
    collection = str(header["collection"])
    revision = _str_or_none(header.get("revision"))
    # The provider store keeps its OWN partitions: it is what grants read and what apply-changes
    # patches, so it is never deduplicated into (or folded toward) the shared scope.
    dedup = dedup_enabled() and revision is not None and scope != PROVIDER_STORE_SCOPE

    if dedup and scope != SHARED_SCOPE:
        deduped = await _share_instead_of_copy(
            pool, scope=scope, provider=provider, collection=collection, revision=revision
        )
        if deduped is not None:
            await lines.aclose()
            logger.info(
                "attribute index: %s/%s@%s is already in %s; shared into %s instead of copied%s",
                provider,
                collection,
                revision,
                SHARED_SCOPE,
                scope,
                " (its older copy there folded)" if deduped["folded"] else "",
            )
            return {
                "provider": provider,
                "collection": collection,
                "revision": revision,
                "source_etag": _str_or_none(header.get("source_etag")),
                "rows": 0,
                "skipped": 0,
                "duplicates": 0,
                "keys": deduped["keys"],
                "partition": deduped["partition"],
                "deduplicated_into": SHARED_SCOPE,
                "folded": deduped["folded"],
                "warnings": warnings,
                "timings": {"ingest_ms": round((time.perf_counter() - t0) * 1000), "index_ms": 0, "swap_ms": 0},
            }

    excluded = _exclusion(header.get("index_exclude") or [])
    part_key = part_key_for(scope, provider, collection)
    canonical = partition_name_for(provider, collection, part_key)
    build = f"{_BUILD_PREFIX}{part_key}_{secrets.token_hex(4)}"
    loop = asyncio.get_running_loop()
    stats = _ValueStats()
    rows = skipped = duplicates = 0
    timings: dict[str, int] = {}

    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _lock_id(part_key))
        try:
            await _drop_leftovers(conn, part_key)
            # The partition constraint holds by construction (DEFAULT + CHECK), so ATTACH does
            # not scan the table to prove it.
            await conn.execute(
                f"""
                CREATE TABLE {_ident(build)} (
                    part_key    TEXT NOT NULL DEFAULT '{part_key}' CHECK (part_key = '{part_key}'),
                    node_id     TEXT COLLATE "C" NOT NULL,
                    parent_id   TEXT COLLATE "C",
                    full_name   TEXT COLLATE "C",
                    source_ref  TEXT,
                    kind        TEXT,
                    depth       INT,
                    attrs       JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
                """
            )
            try:

                async def flush(batch: list[bytes]) -> None:
                    nonlocal rows, skipped
                    records, bad = await loop.run_in_executor(None, _parse_batch, batch, excluded, stats)
                    skipped += bad
                    if records:
                        await conn.copy_records_to_table(build, records=records, columns=list(_NODE_COLUMNS))
                        rows += len(records)

                batch: list[bytes] = []
                async for line in lines:
                    batch.append(line)
                    if len(batch) >= batch_rows:
                        await flush(batch)
                        batch = []
                if batch:
                    await flush(batch)
                timings["ingest_ms"] = round((time.perf_counter() - t0) * 1000)

                t1 = time.perf_counter()
                await conn.execute("SET maintenance_work_mem = '256MB'")
                b = _ident(build)
                try:
                    await conn.execute(f"CREATE UNIQUE INDEX ON {b} (node_id)")
                except asyncpg.UniqueViolationError:
                    # A node listed twice: keep its first line, say how many were dropped.
                    status = await conn.execute(
                        f"DELETE FROM {b} a USING {b} d WHERE a.node_id = d.node_id AND a.ctid > d.ctid"
                    )
                    duplicates = int(status.split()[-1])
                    rows -= duplicates
                    warnings.append(f"{duplicates} duplicate node_id line(s) dropped (first one kept)")
                    await conn.execute(f"CREATE UNIQUE INDEX ON {b} (node_id)")
                await conn.execute(f"CREATE INDEX ON {b} USING gin (attrs jsonb_path_ops)")
                await conn.execute(f"CREATE INDEX ON {b} (full_name)")
                await conn.execute(f"CREATE INDEX ON {b} (kind, depth)")
                await conn.execute(f"ANALYZE {b}")
                await conn.execute("RESET maintenance_work_mem")
                timings["index_ms"] = round((time.perf_counter() - t1) * 1000)

                t2 = time.perf_counter()
                keys = sorted(stats.keys)
                high = sorted(stats.high)
                value_records = [(part_key, k, v, n) for k, counter in stats.values.items() for v, n in counter.items()]
                async with conn.transaction():
                    await conn.execute("SET LOCAL lock_timeout = '60s'")
                    await _detach_and_drop(conn, canonical)
                    await conn.execute(f"ALTER TABLE {b} RENAME TO {_ident(canonical)}")
                    await conn.execute(
                        f"ALTER TABLE attr_nodes ATTACH PARTITION {_ident(canonical)} FOR VALUES IN ('{part_key}')"
                    )
                    await conn.execute("DELETE FROM attr_values WHERE part_key = $1", part_key)
                    if value_records:
                        await conn.copy_records_to_table(
                            "attr_values", records=value_records, columns=["part_key", "key", "value", "n"]
                        )
                    await conn.execute(
                        """
                        INSERT INTO attr_collections
                            (scope, provider, collection, revision, rows, source_etag, depth_base, ingested_at,
                             partition_name, part_key, keys, high_card_keys)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8, $9, $10, $11)
                        ON CONFLICT (scope, provider, collection) DO UPDATE SET
                            revision = EXCLUDED.revision, rows = EXCLUDED.rows, source_etag = EXCLUDED.source_etag,
                            depth_base = EXCLUDED.depth_base, ingested_at = EXCLUDED.ingested_at,
                            partition_name = EXCLUDED.partition_name, part_key = EXCLUDED.part_key,
                            keys = EXCLUDED.keys, high_card_keys = EXCLUDED.high_card_keys
                        """,
                        scope,
                        provider,
                        collection,
                        revision,
                        rows,
                        _str_or_none(header.get("source_etag")),
                        _str_or_none(header.get("depth_base")),
                        canonical,
                        part_key,
                        keys,
                        high,
                    )
                timings["swap_ms"] = round((time.perf_counter() - t2) * 1000)
            except BaseException:
                try:
                    await conn.execute(f"DROP TABLE IF EXISTS {_ident(build)}")
                except Exception:  # noqa: BLE001 - the next ingest of this partition clears it
                    pass
                raise
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _lock_id(part_key))

    folded: list[dict] = []
    if dedup and scope == SHARED_SCOPE:
        folded = await _fold_same_revision_into_shared(
            pool, provider=provider, collection=collection, revision=revision
        )

    return {
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "source_etag": _str_or_none(header.get("source_etag")),
        "rows": rows,
        "skipped": skipped,
        "duplicates": duplicates,
        "keys": len(keys),
        "partition": canonical,
        "deduplicated_into": None,
        "folded": folded,
        "warnings": warnings,
        "timings": timings,
    }


def _str_or_none(v: Any) -> Optional[str]:
    return None if v is None else str(v)


async def _detach_and_drop(conn, name: str) -> None:
    exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)
    if not exists:
        return
    attached = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid = $1::regclass AND inhparent = 'attr_nodes'::regclass)",
        name,
    )
    if attached:
        await conn.execute(f"ALTER TABLE attr_nodes DETACH PARTITION {_ident(name)}")
    await conn.execute(f"DROP TABLE {_ident(name)}")


async def drop_attr_collection(pool: asyncpg.Pool, *, scope: str, provider: str, collection: str) -> bool:
    """Remove one collection's index (partition, histogram, catalogue row -- and with that row,
    by the cascade, every share of it). True when it existed."""
    collection = collection.lower()
    part_key = part_key_for(scope, provider, collection)
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _lock_id(part_key))
        try:
            await _drop_leftovers(conn, part_key)
            async with conn.transaction():
                dropped = await _drop_in_tx(conn, scope=scope, provider=provider, collection=collection)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _lock_id(part_key))
    return dropped


async def _drop_in_tx(conn, *, scope: str, provider: str, collection: str) -> bool:
    """:func:`drop_attr_collection`'s work, inside the caller's transaction and under the caller's
    hold of the partition's advisory lock."""
    part_key = part_key_for(scope, provider, collection)
    await _detach_and_drop(conn, partition_name_for(provider, collection, part_key))
    await conn.execute("DELETE FROM attr_values WHERE part_key = $1", part_key)
    status = await conn.execute(
        "DELETE FROM attr_collections WHERE scope = $1 AND provider = $2 AND collection = $3",
        scope,
        provider,
        collection,
    )
    return status.split()[-1] != "0"


# --- patches: element-level changes into an existing set (docs/design/apply-changes.md) --------------
#
# A provider that answers "what changed since a cursor" stages, beside its geometry, a PATCH of each
# core-held set: ``<kind>-patch.ndjson.gz``, a header line
#
#     {"schema": "ada.assets/<kind>-patch@1", "provider", "collection", "since", "next_cursor"}
#
# then one line per row in the set's own row format, or ``{"node": "<id>", "deleted": true}``. A
# node's lines REPLACE every row of that node in the set; a deletion removes them. Lines apply in
# file order, so a node deleted and then listed again ends with the rows listed after the deletion.
#
# A patch is applied in ONE transaction into the set the full ingest built for (scope, provider,
# collection) -- never into a set it creates: a patch is a delta, and a delta of nothing is not a
# collection. With no set the patch is REFUSED with a warning and the other roles of the publish go
# on (:func:`patch_refused`). The rows are COPYed into temp tables first (the file streams, as the
# ingests do), and the set is changed by three statements over them: delete the touched nodes' rows,
# insert the surviving lines, adjust the set's catalogue.

#: The staged role and schema of an attribute-index patch.
ATTRIBUTE_INDEX_PATCH_ROLE = "attribute-index-patch.ndjson.gz"
ATTRIBUTE_INDEX_PATCH_SCHEMA = "ada.assets/attribute-index-patch@1"


def parse_patch_header(line: bytes, schema: str, error: type[ValueError] = ValueError) -> dict:
    """A patch file's header line, checked: ``schema`` exactly, a ``collection`` (lower-cased, as
    every set keys it), ``since`` / ``next_cursor`` as strings or None. Raises ``error``."""
    what = schema.split("/", 1)[-1].split("@", 1)[0]
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise error(f"the first line of the {what} is not JSON: {exc}") from exc
    if not isinstance(header, dict) or header.get("schema") != schema:
        got = header.get("schema") if isinstance(header, dict) else type(header).__name__
        raise error(f"the {what} header names schema {got!r}, not {schema!r}")
    collection = header.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise error(f"the {what} header names no 'collection'")
    header["collection"] = collection.strip().lower()
    for name in ("since", "next_cursor"):
        header[name] = _str_or_none(header.get(name))
    return header


@dataclass
class PatchBatch:
    """One batch of patch lines, classified. ``records`` are the set's COPY records with the line's
    sequence number appended; ``deleted`` is ``(node_id, seq)`` per deletion line."""

    records: list[tuple] = field(default_factory=list)
    deleted: list[tuple[str, int]] = field(default_factory=list)
    skipped: int = 0


def plan_patch_batch(lines: list[bytes], parse, start_seq: int) -> PatchBatch:
    """Classify ``lines`` (the ``start_seq``-th line of the file first): a deletion marker, or a row
    ``parse`` -- the set's own batch parser, ``lines -> (records, skipped)`` -- turns into a record.
    A line that is neither is counted, not fatal, as in the ingests. Runs on a thread."""
    out = PatchBatch()
    for i, line in enumerate(lines):
        seq = start_seq + i
        try:
            obj = json.loads(line)
        except ValueError:
            out.skipped += 1
            continue
        if isinstance(obj, dict) and obj.get("deleted") is True:
            node = _clean_text(obj.get("node"))
            if node and node.strip():
                out.deleted.append((node, seq))
            else:
                out.skipped += 1
            continue
        records, bad = parse([line])
        out.skipped += bad
        out.records.extend((*r, seq) for r in records)
    return out


def patch_refused(*, provider: str, collection: str, header: Optional[dict] = None, t0: Optional[float] = None) -> dict:
    """The answer of a patch for a set that does not exist: nothing written, a warning saying why."""
    return {
        "provider": provider,
        "collection": collection,
        "since": (header or {}).get("since"),
        "next_cursor": (header or {}).get("next_cursor"),
        "applied": False,
        "refused": True,
        "upserted": 0,
        "deleted": 0,
        "removed_rows": 0,
        "skipped": 0,
        "warnings": [f"no baseline for {provider}/{collection}; run a full export first"],
        "timings": {"patch_ms": round((time.perf_counter() - t0) * 1000) if t0 is not None else 0},
    }


async def load_patch(
    conn,
    lines: AsyncIterator[bytes],
    *,
    load_table: str,
    columns: Iterable[str],
    parse,
    batch_rows: int,
) -> dict:
    """Stream the body of a patch into ``load_table`` (``columns`` + ``seq``, created by the caller)
    and the deletions into a temp ``patch_deletes (node_id, seq)`` this creates. Inside the caller's
    transaction. Returns ``{lines, rows, deletions, skipped}``."""
    await conn.execute(
        'CREATE TEMP TABLE patch_deletes (node_id TEXT COLLATE "C" NOT NULL, seq INT NOT NULL) ON COMMIT DROP'
    )
    loop = asyncio.get_running_loop()
    cols = [*columns, "seq"]
    counts = {"lines": 0, "rows": 0, "deletions": 0, "skipped": 0}

    async def flush(batch: list[bytes]) -> None:
        plan = await loop.run_in_executor(None, plan_patch_batch, batch, parse, counts["lines"])
        counts["lines"] += len(batch)
        counts["skipped"] += plan.skipped
        if plan.records:
            await conn.copy_records_to_table(load_table, records=plan.records, columns=cols)
            counts["rows"] += len(plan.records)
        if plan.deleted:
            await conn.copy_records_to_table("patch_deletes", records=plan.deleted, columns=["node_id", "seq"])
            counts["deletions"] += len(plan.deleted)

    batch: list[bytes] = []
    async for line in lines:
        batch.append(line)
        if len(batch) >= batch_rows:
            await flush(batch)
            batch = []
    if batch:
        await flush(batch)
    return counts


#: The rows a patch leaves standing: of each node, its first line after its last deletion. Over a
#: load table aliased ``l`` with a ``node_id`` column (the caller names it ``{node}``).
_SURVIVES = "NOT EXISTS (SELECT 1 FROM patch_deletes d WHERE d.node_id = l.{node} AND d.seq > l.seq)"


async def patch_attribute_index(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    chunks: AsyncIterable[bytes],
    batch_rows: int = INGEST_BATCH_ROWS,
) -> dict:
    """Apply one ``attribute-index-patch.ndjson.gz`` to the partition the full ingest built for
    (scope, provider, collection), in one transaction. Refused (:func:`patch_refused`) when there is
    no such partition.

    The partition's catalogue stays consistent with its rows: ``attr_values`` (the value histogram
    the search's autocomplete reads) loses the touched nodes' old values and gains their new ones --
    except for the ``high_card_keys`` the full ingest found too varied to histogram -- a key no row
    carried before joins ``keys``, ``rows`` follows the delta, and ``ingested_at`` moves (anything
    cached on it is stale now). ``revision`` stays: it names the full export the patches build on.
    Serialised with a full ingest of the same partition by the same advisory lock key."""
    t0 = time.perf_counter()
    lines = ndjson_lines(chunks)
    try:
        first = await lines.__anext__()
    except StopAsyncIteration:
        raise AttributeIndexError("the attribute index patch is empty") from None
    header = parse_patch_header(first, ATTRIBUTE_INDEX_PATCH_SCHEMA, AttributeIndexError)
    warnings: list[str] = []
    if header.get("provider") and header["provider"] != provider:
        warnings.append(
            f"the attribute index patch header names provider {header['provider']!r}; applied under the "
            f"publishing provider {provider!r}"
        )
    collection = str(header["collection"])
    excluded = _exclusion(header.get("index_exclude") or [])
    part_key = part_key_for(scope, provider, collection)

    def parse(batch: list[bytes]) -> tuple[list[tuple], int]:
        return _parse_batch(batch, excluded, _ValueStats())

    async with pool.acquire() as conn, conn.transaction():
        # Waits for a full ingest of this partition (session lock, same key) to finish its swap.
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _lock_id(part_key))
        held = await conn.fetchrow(
            "SELECT part_key, keys, high_card_keys FROM attr_collections "
            "WHERE scope = $1 AND provider = $2 AND collection = $3 FOR UPDATE",
            scope,
            provider,
            collection,
        )
        if held is None:
            await lines.aclose()
            return patch_refused(provider=provider, collection=collection, header=header, t0=t0)
        part_key = held["part_key"]
        high = list(held["high_card_keys"] or [])
        await conn.execute(
            """
            CREATE TEMP TABLE attr_patch_load (
                node_id TEXT COLLATE "C" NOT NULL, parent_id TEXT COLLATE "C", full_name TEXT COLLATE "C",
                source_ref TEXT, kind TEXT, depth INT, attrs JSONB NOT NULL DEFAULT '{}'::jsonb, seq INT NOT NULL
            ) ON COMMIT DROP
            """
        )
        loaded = await load_patch(
            conn, lines, load_table="attr_patch_load", columns=_NODE_COLUMNS, parse=parse, batch_rows=batch_rows
        )
        t1 = time.perf_counter()
        await conn.execute(
            """
            CREATE TEMP TABLE patch_touched ON COMMIT DROP AS
                SELECT node_id FROM attr_patch_load UNION SELECT node_id FROM patch_deletes
            """
        )
        await conn.execute(
            f"""
            CREATE TEMP TABLE patch_rows ON COMMIT DROP AS
                SELECT DISTINCT ON (l.node_id) l.* FROM attr_patch_load l
                WHERE {_SURVIVES.format(node="node_id")}
                ORDER BY l.node_id, l.seq
            """
        )
        # The histogram first, while the old rows are still there to say what they held.
        await conn.execute(
            """
            WITH old AS (
                SELECT e.key COLLATE "C" AS key, e.value COLLATE "C" AS value, count(*) AS n
                FROM attr_nodes a JOIN patch_touched t ON t.node_id = a.node_id
                CROSS JOIN LATERAL jsonb_each_text(a.attrs) e
                WHERE a.part_key = $1 AND e.value IS NOT NULL AND NOT (e.key = ANY($2::text[]))
                GROUP BY 1, 2
            )
            UPDATE attr_values v SET n = v.n - old.n FROM old
            WHERE v.part_key = $1 AND v.key = old.key AND v.value = old.value
            """,
            part_key,
            high,
        )
        await conn.execute("DELETE FROM attr_values WHERE part_key = $1 AND n <= 0", part_key)
        removed = await conn.execute(
            "DELETE FROM attr_nodes a USING patch_touched t WHERE a.part_key = $1 AND a.node_id = t.node_id",
            part_key,
        )
        inserted = await conn.execute(
            """
            INSERT INTO attr_nodes (part_key, node_id, parent_id, full_name, source_ref, kind, depth, attrs)
            SELECT $1, node_id, parent_id, full_name, source_ref, kind, depth, attrs FROM patch_rows
            """,
            part_key,
        )
        await conn.execute(
            """
            INSERT INTO attr_values (part_key, key, value, n)
            SELECT $1, e.key COLLATE "C", e.value COLLATE "C", count(*)
            FROM patch_rows r CROSS JOIN LATERAL jsonb_each_text(r.attrs) e
            WHERE e.value IS NOT NULL AND length(e.value) <= $3 AND NOT (e.key = ANY($2::text[]))
            GROUP BY 2, 3
            ON CONFLICT (part_key, key, value) DO UPDATE SET n = attr_values.n + EXCLUDED.n
            """,
            part_key,
            high,
            VALUE_STATS_MAX_LEN,
        )
        new_keys = [
            r["k"] for r in await conn.fetch("SELECT DISTINCT k FROM patch_rows r, jsonb_object_keys(r.attrs) k")
        ]
        deleted = await conn.fetchval(
            "SELECT count(DISTINCT d.node_id) FROM patch_deletes d "
            "WHERE NOT EXISTS (SELECT 1 FROM patch_rows r WHERE r.node_id = d.node_id)"
        )
        n_removed, n_inserted = int(removed.split()[-1]), int(inserted.split()[-1])
        keys = sorted(set(held["keys"] or []) | set(new_keys))
        await conn.execute(
            """
            UPDATE attr_collections SET rows = GREATEST(rows - $4 + $5, 0), keys = $6, ingested_at = NOW()
            WHERE scope = $1 AND provider = $2 AND collection = $3
            """,
            scope,
            provider,
            collection,
            n_removed,
            n_inserted,
            keys,
        )
    if loaded["skipped"]:
        warnings.append(f"{loaded['skipped']} patch line(s) that were neither a row nor a deletion skipped")
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
        "new_keys": len(keys) - len(set(held["keys"] or [])),
        "warnings": warnings,
        "timings": {
            "load_ms": round((t1 - t0) * 1000),
            "apply_ms": round((time.perf_counter() - t1) * 1000),
            "patch_ms": round((time.perf_counter() - t0) * 1000),
        },
    }


# --- one copy per (provider, collection, revision) ----------------------------------------------


async def _fold_in_tx(
    conn, *, drop_scope: str, provider: str, collection: str, folded_by: Optional[str]
) -> Optional[dict]:
    """Replace ``drop_scope``'s partition of (provider, collection) by a share of the shared one.

    Inside the caller's transaction, under its hold of ``drop_scope``'s partition lock, with the
    shared partition known to exist. Every scope the dropped copy was searchable from keeps
    searching the collection: each target of its own shares and of its group's (040) gets a share
    of the shared copy -- which widens nothing, everyone may read the shared scope -- and so does
    ``drop_scope`` itself. Then the copy is dropped, its shares going with it by the cascade.
    None when ``drop_scope`` holds no such partition."""
    part_key = part_key_for(drop_scope, provider, collection)
    name = partition_name_for(provider, collection, part_key)
    held = await conn.fetchrow(
        "SELECT revision, rows FROM attr_collections WHERE scope = $1 AND provider = $2 AND collection = $3 FOR UPDATE",
        drop_scope,
        provider,
        collection,
    )
    if held is None:
        return None
    size = await conn.fetchval("SELECT COALESCE(pg_total_relation_size(to_regclass($1)), 0)", name)
    targets = await conn.fetch(
        """
        SELECT target_scope FROM attr_shares WHERE scope = $1 AND provider = $2 AND collection = $3
        UNION SELECT target_scope FROM attr_provider_shares WHERE scope = $1 AND provider = $2
        """,
        drop_scope,
        provider,
        collection,
    )
    repointed = sorted({t["target_scope"] for t in targets} - {SHARED_SCOPE, drop_scope})
    for target in [*repointed, drop_scope]:
        await conn.execute(
            """
            INSERT INTO attr_shares (scope, provider, collection, target_scope, shared_by)
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT (scope, provider, collection, target_scope) DO NOTHING
            """,
            SHARED_SCOPE,
            provider,
            collection,
            target,
            folded_by,
        )
    await _drop_in_tx(conn, scope=drop_scope, provider=provider, collection=collection)
    return {
        "scope": drop_scope,
        "provider": provider,
        "collection": collection,
        "revision": held["revision"],
        "rows": int(held["rows"]),
        "bytes_freed": int(size),
        "repointed": repointed,
    }


async def _shared_revision(conn, provider: str, collection: str) -> Optional[asyncpg.Record]:
    """The shared scope's catalogue row of (provider, collection), locked against a concurrent
    reingest changing its revision until the caller's transaction ends."""
    return await conn.fetchrow(
        "SELECT revision, partition_name, keys FROM attr_collections "
        "WHERE scope = $1 AND provider = $2 AND collection = $3 FOR SHARE",
        SHARED_SCOPE,
        provider,
        collection,
    )


async def _share_instead_of_copy(
    pool: asyncpg.Pool, *, scope: str, provider: str, collection: str, revision: str
) -> Optional[dict]:
    """An ingest into ``scope`` (not the shared one) of what the shared scope already holds at
    ``revision``: share that partition into ``scope`` and fold away an older copy ``scope`` held.
    None -- ingest as usual -- when the shared scope holds no such revision."""
    async with pool.acquire() as conn:
        quick = await conn.fetchval(
            "SELECT revision FROM attr_collections WHERE scope = $1 AND provider = $2 AND collection = $3",
            SHARED_SCOPE,
            provider,
            collection,
        )
        if quick != revision:
            return None
        part_key = part_key_for(scope, provider, collection)
        await conn.execute("SELECT pg_advisory_lock($1)", _lock_id(part_key))
        try:
            await _drop_leftovers(conn, part_key)
            async with conn.transaction():
                shared = await _shared_revision(conn, provider, collection)
                if shared is None or shared["revision"] != revision:
                    return None  # reingested or dropped meanwhile
                fold = await _fold_in_tx(
                    conn, drop_scope=scope, provider=provider, collection=collection, folded_by=DEDUP_SHARED_BY
                )
                if fold is None:
                    await conn.execute(
                        """
                        INSERT INTO attr_shares (scope, provider, collection, target_scope, shared_by)
                        VALUES ($1, $2, $3, $4, $5) ON CONFLICT (scope, provider, collection, target_scope) DO NOTHING
                        """,
                        SHARED_SCOPE,
                        provider,
                        collection,
                        scope,
                        DEDUP_SHARED_BY,
                    )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _lock_id(part_key))
    return {"partition": shared["partition_name"], "keys": len(shared["keys"] or []), "folded": [fold] if fold else []}


async def _fold_same_revision_into_shared(
    pool: asyncpg.Pool, *, provider: str, collection: str, revision: str
) -> list[dict]:
    """After an ingest into the shared scope: every other scope's copy of (provider, collection)
    at the same ``revision`` becomes a share of the shared one (:func:`_fold_in_tx`). A copy at
    another revision is left alone. One failure is logged and skipped: the ingest itself landed."""
    others = await pool.fetch(
        "SELECT scope FROM attr_collections WHERE provider = $1 AND collection = $2 AND scope <> $3 AND revision = $4 "
        "AND scope <> $5 ORDER BY scope",
        provider,
        collection,
        SHARED_SCOPE,
        revision,
        PROVIDER_STORE_SCOPE,
    )
    folded: list[dict] = []
    for r in others:
        try:
            fold = await _fold_one(pool, drop_scope=r["scope"], provider=provider, collection=collection,
                                   revision=revision, folded_by=DEDUP_SHARED_BY)  # fmt: skip
        except Exception:  # noqa: BLE001 - the shared ingest stands; the copy stays a copy
            logger.exception("attribute index: could not fold %s's copy of %s/%s", r["scope"], provider, collection)
            continue
        if fold is not None:
            folded.append(fold)
            logger.info(
                "attribute index: %s/%s@%s ingested into %s; %s's copy folded into a share (%d bytes freed)",
                provider,
                collection,
                revision,
                SHARED_SCOPE,
                r["scope"],
                fold["bytes_freed"],
            )
    return folded


async def _fold_one(
    pool: asyncpg.Pool,
    *,
    drop_scope: str,
    provider: str,
    collection: str,
    revision: Optional[str] = None,
    folded_by: Optional[str],
) -> Optional[dict]:
    """One :func:`_fold_in_tx` in its own transaction under ``drop_scope``'s partition lock. With
    ``revision``, only while both copies are still at it. None when there was nothing to fold."""
    part_key = part_key_for(drop_scope, provider, collection)
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _lock_id(part_key))
        try:
            await _drop_leftovers(conn, part_key)
            async with conn.transaction():
                shared = await _shared_revision(conn, provider, collection)
                if shared is None or (revision is not None and shared["revision"] != revision):
                    return None
                if revision is not None:
                    mine = await conn.fetchval(
                        "SELECT revision FROM attr_collections WHERE scope = $1 AND provider = $2 AND collection = $3",
                        drop_scope,
                        provider,
                        collection,
                    )
                    if mine != revision:
                        return None
                return await _fold_in_tx(
                    conn, drop_scope=drop_scope, provider=provider, collection=collection, folded_by=folded_by
                )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _lock_id(part_key))


class FoldRefused(ValueError):
    """A fold the one-copy rule does not allow -- a 400, worded for the admin."""


async def fold_attr_duplicates(
    pool: asyncpg.Pool,
    *,
    provider: str,
    keep_scope: str,
    drop_scope: str,
    collections: Optional[Iterable[str]] = None,
    dry_run: bool = True,
    folded_by: Optional[str] = None,
) -> dict:
    """The admin's fold of copies at any revision: for each collection ``provider`` holds in both
    scopes (``collections`` narrows it), keep ``keep_scope``'s copy and replace ``drop_scope``'s
    with a share of it (:func:`_fold_in_tx`). ``keep_scope`` must be the shared scope: the copy
    kept must be at least as widely readable as the one dropped, or the fold would hide rows from
    someone. A dry run (the default) changes nothing and answers the plan."""
    if keep_scope != SHARED_SCOPE:
        raise FoldRefused(f"only the {SHARED_SCOPE!r} copy can be kept: it is the one every caller may read")
    if drop_scope == keep_scope:
        raise FoldRefused("the scope to drop must not be the one kept")
    wanted = sorted({c.lower() for c in collections}) if collections is not None else None
    rows = await pool.fetch(
        """
        SELECT d.collection, d.revision AS drop_revision, k.revision AS keep_revision, d.rows,
               d.ingested_at AS drop_ingested_at, k.ingested_at AS keep_ingested_at,
               COALESCE(pg_total_relation_size(to_regclass(d.partition_name)), 0) AS bytes,
               ARRAY(SELECT target_scope FROM attr_shares s
                     WHERE s.scope = d.scope AND s.provider = d.provider AND s.collection = d.collection
                     UNION SELECT target_scope FROM attr_provider_shares g
                     WHERE g.scope = d.scope AND g.provider = d.provider) AS targets
        FROM attr_collections d
        JOIN attr_collections k ON k.scope = $2 AND k.provider = d.provider AND k.collection = d.collection
        WHERE d.scope = $1 AND d.provider = $3 AND ($4::text[] IS NULL OR d.collection = ANY($4))
        ORDER BY d.collection
        """,
        drop_scope,
        keep_scope,
        provider,
        wanted,
    )
    plan = [
        {
            "collection": r["collection"],
            "keep_revision": r["keep_revision"],
            "drop_revision": r["drop_revision"],
            "same_revision": r["keep_revision"] == r["drop_revision"],
            "rows": int(r["rows"]),
            "bytes": int(r["bytes"]),
            "keep_ingested_at": _iso(r["keep_ingested_at"]),
            "drop_ingested_at": _iso(r["drop_ingested_at"]),
            "repointed": sorted(set(r["targets"] or []) - {SHARED_SCOPE, drop_scope}),
        }
        for r in rows
    ]
    held = {p["collection"] for p in plan}
    missing = [c for c in (wanted or []) if c not in held]
    errors: list[str] = []
    if not dry_run:
        done = []
        for p in plan:
            try:
                fold = await _fold_one(
                    pool, drop_scope=drop_scope, provider=provider, collection=p["collection"], folded_by=folded_by
                )
            except Exception as exc:  # noqa: BLE001 - one collection's failure is reported, not fatal
                errors.append(f"{provider}/{p['collection']}: {exc}")
                continue
            if fold is None:
                missing.append(p["collection"])  # dropped meanwhile
                continue
            done.append({**p, "bytes": fold["bytes_freed"], "repointed": fold["repointed"]})
        plan = done
    return {
        "dry_run": dry_run,
        "provider": provider,
        "keep_scope": keep_scope,
        "drop_scope": drop_scope,
        "collections": plan,
        "folded": 0 if dry_run else len(plan),
        "bytes": sum(p["bytes"] for p in plan),
        "missing": missing,
        "errors": errors,
    }


# --- the search query ---------------------------------------------------------------------------


@dataclass
class SearchQuery:
    providers: Optional[list[str]] = None
    collections: Optional[list[str]] = None
    kind: Optional[list[str]] = None
    depth_eq: Optional[int] = None
    depth_min: Optional[int] = None
    depth_max: Optional[int] = None
    #: ``{"key", "op": "in"|"not_in"|"prefix"|"exists"|"missing", "values": [...]}`` -- eq is an
    #: in of one, ne a not_in of one; exists / missing carry no values.
    where: list[dict] = field(default_factory=list)
    select: Optional[list[str]] = None
    limit: int = SEARCH_DEFAULT_LIMIT
    cursor: Optional[str] = None
    #: Partitions never read, whatever ``providers`` / ``collections`` include (collections lower-case).
    exclude_providers: Optional[list[str]] = None
    exclude_collections: Optional[list[str]] = None
    #: Partitions by their ``counts`` key (:func:`_label`: ``<provider>/<collection>`` for the scope's
    #: own, ``...@<origin>`` for another scope's) -- the one filter that tells two partitions of one
    #: collection name apart. ``partitions`` includes (``None``: all), ``exclude_partitions`` leaves out.
    partitions: Optional[list[str]] = None
    exclude_partitions: Optional[list[str]] = None
    #: An ADMIN's search of every provider scope's partitions, shared here or not
    #: (:func:`admin_search_origins`). The route refuses it for anyone else; here it is only a flag.
    all_scopes: bool = False
    #: A match on the node's NAME (the ``full_name`` column -- not an attribute), parsed by
    #: :func:`_parse_name`: ``{"op": "eq"|"prefix"|"contains", "value", "ignore_case", "refs"}``.
    name: Optional[dict] = None

    def fingerprint(self) -> str:
        """What a cursor is valid for: the filters, not the page size or the projection. A filter
        added later joins the fingerprint only when set, so a cursor issued before it stays valid."""
        doc = [
            self.providers,
            self.collections,
            self.kind,
            self.depth_eq,
            self.depth_min,
            self.depth_max,
            self.where,
        ]
        if self.exclude_providers or self.exclude_collections:
            doc += [self.exclude_providers, self.exclude_collections]
        if self.partitions or self.exclude_partitions:
            doc += [{"partitions": self.partitions, "exclude_partitions": self.exclude_partitions}]
        if self.all_scopes:
            doc += [{"all_scopes": True}]
        if self.name:
            doc += [{"name": self.name}]
        return hashlib.sha1(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def filtered(self) -> bool:
        return (
            bool(self.kind or self.where or self.name)
            or self.depth_eq is not None
            or self.depth_min is not None
            or (self.depth_max is not None)
        )


#: The operators a ``where`` item may carry beside its ``key`` (exactly one).
_WHERE_OPS = ("eq", "in", "prefix", "ne", "not_in", "exists", "missing")
#: eq is an in of one; ne a not_in of one.
_WHERE_OP_NORMAL = {"eq": "in", "ne": "not_in"}


def _str_list(body: dict, name: str) -> Optional[list[str]]:
    v = body.get(name)
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise SearchQueryError(f"{name!r} must be a list of non-empty strings or null")
    return list(dict.fromkeys(v)) or None


def _lower(names: Optional[list[str]]) -> Optional[list[str]]:
    # Collections are keyed lower-case (see parse_header); a filter naming `ALPHA` means `alpha`.
    return list(dict.fromkeys(c.lower() for c in names)) if names else None


def _int(v: Any, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise SearchQueryError(f"{what} must be an integer")
    return v


def _scalar(v: Any, what: str):
    if isinstance(v, (str, bool, int, float)) and v is not None:
        return v
    raise SearchQueryError(f"{what} must be a string, number or boolean")


#: The operators a ``name`` filter may carry (exactly one), and its flags.
_NAME_OPS = ("eq", "prefix", "contains")
_NAME_FLAGS = ("ignore_case", "refs")
#: Longest name a ``name`` filter may ask for.
NAME_MAX_LEN = 500


def _parse_name(raw: Any) -> Optional[dict]:
    """``name``: ``{"eq"|"prefix"|"contains": str, "ignore_case"?: bool, "refs"?: bool}``.
    ``ignore_case`` defaults to true for ``contains`` and false otherwise."""
    if raw is None:
        return None
    shape = '\'name\' must be {"eq"|"prefix"|"contains": "...", "ignore_case": bool, "refs": bool}'
    if not isinstance(raw, dict):
        raise SearchQueryError(shape)
    ops = [op for op in _NAME_OPS if op in raw]
    if len(ops) != 1 or set(raw) - {*_NAME_OPS, *_NAME_FLAGS}:
        raise SearchQueryError(shape)
    op = ops[0]
    value = raw[op]
    if not isinstance(value, str) or not value:
        raise SearchQueryError(f"name.{op} must be a non-empty string")
    if len(value) > NAME_MAX_LEN:
        raise SearchQueryError(f"name.{op} is longer than {NAME_MAX_LEN} characters")
    flags = {}
    for flag in _NAME_FLAGS:
        v = raw.get(flag)
        if v is not None and not isinstance(v, bool):
            raise SearchQueryError(f"name.{flag} must be true or false")
        flags[flag] = v
    ignore_case = flags["ignore_case"] if flags["ignore_case"] is not None else op == "contains"
    return {"op": op, "value": value, "ignore_case": ignore_case, "refs": flags["refs"] is True}


def name_scans(name: Optional[dict]) -> bool:
    """Whether a ``name`` filter cannot be answered by the partitions' ``full_name`` btree: a
    ``contains``, any case-insensitive match, or one that also tests ``source_ref`` (unindexed)."""
    return bool(name) and (name["op"] == "contains" or name["ignore_case"] or name["refs"])


def parse_search_query(body: Any) -> SearchQuery:
    """The contract's search body, validated. Raises :class:`SearchQueryError` (a 400)."""
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise SearchQueryError("the search body must be a JSON object")
    q = SearchQuery(
        providers=_str_list(body, "providers"),
        collections=_lower(_str_list(body, "collections")),
        kind=_str_list(body, "kind"),
        select=_str_list(body, "select"),
        exclude_providers=_str_list(body, "exclude_providers"),
        exclude_collections=_lower(_str_list(body, "exclude_collections")),
        partitions=_str_list(body, "partitions"),
        exclude_partitions=_str_list(body, "exclude_partitions"),
    )
    all_scopes = body.get("all_scopes")
    if all_scopes not in (None, True, False):
        raise SearchQueryError("'all_scopes' must be true or false")
    q.all_scopes = all_scopes is True
    depth = body.get("depth")
    if depth is not None:
        if not isinstance(depth, dict) or not set(depth) <= {"eq", "min", "max"} or not depth:
            raise SearchQueryError('\'depth\' must be {"eq": n} or {"min": n, "max": m}')
        if "eq" in depth and depth["eq"] is not None:
            q.depth_eq = _int(depth["eq"], "depth.eq")
        if depth.get("min") is not None:
            q.depth_min = _int(depth["min"], "depth.min")
        if depth.get("max") is not None:
            q.depth_max = _int(depth["max"], "depth.max")
    where = body.get("where") or []
    if not isinstance(where, list):
        raise SearchQueryError(f"'where' must be a list of {{key, {'|'.join(_WHERE_OPS)}}}")
    if len(where) > SEARCH_MAX_WHERE:
        raise SearchQueryError(f"at most {SEARCH_MAX_WHERE} 'where' filters")
    for i, w in enumerate(where):
        if not isinstance(w, dict):
            raise SearchQueryError(f"where[{i}] must be an object")
        key = w.get("key")
        if not isinstance(key, str) or not key or len(key) > 200:
            raise SearchQueryError(f"where[{i}].key must be a non-empty string")
        ops = [op for op in _WHERE_OPS if op in w]
        if len(ops) != 1 or set(w) - {"key", *_WHERE_OPS}:
            raise SearchQueryError(f"where[{i}] needs exactly one of {', '.join(map(repr, _WHERE_OPS))} beside 'key'")
        op = ops[0]
        if op in ("eq", "ne"):
            values = [_scalar(w[op], f"where[{i}].{op}")]
        elif op in ("in", "not_in"):
            raw = w[op]
            if not isinstance(raw, list) or not raw:
                raise SearchQueryError(f"where[{i}].{op} must be a non-empty list")
            if len(raw) > SEARCH_MAX_IN_VALUES:
                raise SearchQueryError(f"where[{i}].{op} lists more than {SEARCH_MAX_IN_VALUES} values")
            values = list(dict.fromkeys(_scalar(v, f"where[{i}].{op}[]") for v in raw))
        elif op in ("exists", "missing"):
            if w[op] is not True:
                raise SearchQueryError(f"where[{i}].{op} must be true")
            values = []
        else:
            p = w["prefix"]
            if not isinstance(p, str) or not p:
                raise SearchQueryError(f"where[{i}].prefix must be a non-empty string")
            values = [p]
        q.where.append({"key": key, "op": _WHERE_OP_NORMAL.get(op, op), "values": values})
    q.name = _parse_name(body.get("name"))
    if name_scans(q.name) and not (
        q.providers or q.collections or q.partitions or any(w["op"] == "in" for w in q.where)
    ):
        # An unindexed name match scans every partition it reads: it must name what it reads (or
        # ride on an indexed attribute filter), and it still runs under the statement timeout.
        raise SearchQueryError(
            "a name 'contains', case-insensitive or 'refs' match scans the partitions it reads: name the "
            "'providers', 'collections' or 'partitions' to search, or add an 'eq' / 'in' attribute filter"
        )
    limit = body.get("limit")
    if limit is not None:
        q.limit = max(1, min(_int(limit, "limit"), SEARCH_MAX_LIMIT))
    cursor = body.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise SearchQueryError("'cursor' must be the opaque string a previous page returned")
    q.cursor = cursor or None
    return q


def _candidates(v: Any) -> list[Any]:
    """The JSON values an equality on ``v`` matches. Containment is type-exact, but a caller
    typing ``3`` into a search box means the attribute whether its provider wrote it as a number or
    a string -- and the values endpoint lists both as the text ``"3"``."""
    out: list[Any] = [v]
    if isinstance(v, bool):
        out.append("true" if v else "false")
    elif isinstance(v, (int, float)):
        out.append(_value_text(v))
    elif isinstance(v, str):
        s = v.strip()
        if s in ("true", "false"):
            out.append(s == "true")
        else:
            try:
                n = int(s)
            except ValueError:
                try:
                    n = float(s)
                except ValueError:
                    n = None
            if n is not None and n == n and n not in (float("inf"), float("-inf")):
                out.append(n)
    seen, uniq = set(), []
    for c in out:
        tag = (type(c).__name__, c)
        if tag not in seen:
            seen.add(tag)
            uniq.append(c)
    return uniq


def escape_like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_filters(q: SearchQuery, first_arg: int = 1) -> tuple[str, list[Any]]:
    """``(where_sql, args)`` for one partition's rows; ``$first_arg`` on. ``TRUE`` when unfiltered.

    Equality and ``in`` become containment objects ORed per filter (each a GIN probe); prefixes
    and key presence come after them, the negations last, so the planner applies what the index
    cannot answer to what it already narrowed. ``not_in`` is ``NOT`` of exactly the probes ``in``
    would build: a node without the key, or with a value neither spelling matches, passes.
    """
    clauses: list[str] = []
    args: list[Any] = []

    def arg(v: Any, cast: str = "") -> str:
        args.append(v)
        return f"${first_arg + len(args) - 1}{cast}"

    def probes(w: dict) -> str:
        p = [
            f"attrs @> {arg(json.dumps({w['key']: c}, ensure_ascii=False), '::jsonb')}"
            for v in w["values"]
            for c in _candidates(v)
        ]
        return p[0] if len(p) == 1 else "(" + " OR ".join(p) + ")"

    def of(op: str) -> list[dict]:
        return [w for w in q.where if w["op"] == op]

    if q.kind:
        clauses.append(f"kind = ANY({arg(q.kind, '::text[]')})")
    if q.depth_eq is not None:
        clauses.append(f"depth = {arg(q.depth_eq, '::int')}")
    if q.depth_min is not None:
        clauses.append(f"depth >= {arg(q.depth_min, '::int')}")
    if q.depth_max is not None:
        clauses.append(f"depth <= {arg(q.depth_max, '::int')}")
    for w in of("in"):
        clauses.append(probes(w))
    for w in of("prefix"):
        key = arg(w["key"], "::text")
        clauses.append(f"attrs->>{key} LIKE {arg(escape_like(w['values'][0]) + '%', '::text')}")
    for w in of("exists"):
        clauses.append(f"attrs ? {arg(w['key'], '::text')}")
    for w in of("missing"):
        clauses.append(f"NOT (attrs ? {arg(w['key'], '::text')})")
    for w in of("not_in"):
        s = probes(w)
        clauses.append(f"NOT {s}" if s.startswith("(") else f"NOT ({s})")
    if q.name:
        clause = name_clause(q.name, arg)
        # the btree answers an exact / prefix name first; a scanning match refines what is left last
        clauses.insert(0 if not name_scans(q.name) else len(clauses), clause)
    return (" AND ".join(clauses) if clauses else "TRUE"), args


def name_clause(name: dict, arg) -> str:
    """The SQL of a parsed ``name`` filter; ``arg`` binds a value and returns its placeholder.

    A case-sensitive ``eq`` / ``prefix`` on ``full_name`` alone is ``=`` / ``LIKE 'x%'`` in the
    column's ``"C"`` collation, which its btree answers (the LIKE wildcards in the value escaped).
    Anything else -- ``contains``, ``ignore_case``, ``refs`` -- is a ``(I)LIKE`` scan."""
    value, op = name["value"], name["op"]
    if op == "eq" and not name["ignore_case"]:
        cols = [("full_name", '::text COLLATE "C"')] + ([("source_ref", "::text")] if name["refs"] else [])
        p = arg(value)
        parts = [f"{col} = {p}{cast}" for col, cast in cols]
    else:
        pattern = escape_like(value)
        if op == "prefix":
            pattern += "%"
        elif op == "contains":
            pattern = f"%{pattern}%"
        like = "ILIKE" if name["ignore_case"] else "LIKE"
        p = arg(pattern)
        parts = [f'full_name {like} {p}::text COLLATE "C"'] + ([f"source_ref {like} {p}::text"] if name["refs"] else [])
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def encode_cursor(doc: dict) -> str:
    raw = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, fingerprint: str) -> dict:
    try:
        doc = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as exc:
        raise SearchQueryError("'cursor' is not one this API issued") from exc
    if not isinstance(doc, dict) or doc.get("v") != 1 or not all(isinstance(doc.get(k), str) for k in "pcn"):
        raise SearchQueryError("'cursor' is not one this API issued")
    if "s" in doc and not isinstance(doc["s"], str):
        raise SearchQueryError("'cursor' is not one this API issued")
    if doc.get("f") != fingerprint:
        raise SearchQueryError("'cursor' belongs to a different query; start again without one")
    return doc


# --- reading the index ------------------------------------------------------------------------


def _label(part: dict) -> str:
    """A partition's key in a search's ``counts``. An own partition is ``<provider>/<collection>``,
    as it always was; a shared one is ``<provider>/<collection>@<origin scope>``, so it never
    collides with an own partition of the same name. Clients should not parse it: every response
    lists its partitions (``partitions``) with these keys and the parts spelled out."""
    label = f"{part['provider']}/{part['collection']}"
    return f"{label}@{part['scope']}" if part["shared"] else label


def _order(part: dict) -> tuple:
    """The walk's order: (provider, collection), the scope's own partition before shared ones."""
    return (part["provider"], part["collection"], part["shared"], part["scope"])


def scope_wire(prefix: str, viewer: Optional[str] = None) -> Optional[str]:
    """The API path segment for a stored scope prefix, for ``viewer`` (a user sub) to use -- or
    None where there is none. A user scope has one only for its owner (``user:me``): the API names
    no other user's scope, and handing out ``user:me`` for someone else's would point the caller at
    their own. See :meth:`..scope.Scope.wire`."""
    if prefix == "shared":
        return "shared"
    kind, _, ident = prefix.partition("/")
    if not ident:
        return None
    if kind == "projects":
        return f"project:{ident}"
    if kind == "corpus":
        return f"corpus:{ident}"
    if kind == "users":
        return "user:me" if viewer is not None and ident == viewer else None
    return None


def _origin(part: dict, viewer: Optional[str]) -> dict:
    """Where a partition lies, as a row or collection entry reports it. ``admin``: read only because
    an admin searched every scope (:func:`admin_search_origins`), not through a share -- absent
    otherwise, so a search without that option answers exactly as it always did. ``granted``: read
    from the provider store through a provider grant (:func:`_partitions`' ``granted``) -- its
    ``scope`` is ``providers`` and its ``scope_wire`` null, because nobody but an admin names the
    store; the dataset is read through the searched scope."""
    out = {"scope": part["scope"], "scope_wire": scope_wire(part["scope"], viewer), "shared": part["shared"]}
    if part.get("admin"):
        out["admin"] = True
    if part.get("granted"):
        out["granted"] = True
    return out


#: The provider store's prefix (``Scope.providers().prefix()``), as ``attr_collections.scope`` holds it.
PROVIDER_STORE_SCOPE = "providers"


def _norm_granted(granted: Optional[Iterable[tuple[str, Optional[str]]]]) -> tuple[list[str], list[Optional[str]]]:
    """``granted`` as the two parallel arrays the store query unnests: providers, and collections
    lower-cased (None: every collection of that provider)."""
    providers: list[str] = []
    collections: list[Optional[str]] = []
    for provider, collection in granted or ():
        if not provider:
            continue
        providers.append(str(provider))
        collections.append(str(collection).lower() if collection else None)
    return providers, collections


def merge_granted_partitions(parts: list[dict], store_parts: list[dict], scope: str) -> list[dict]:
    """``parts`` (the scope's own, shared and admin-read partitions) with the provider store's
    granted ``store_parts`` folded in: each store partition is marked ``granted`` (and ``shared``,
    being another scope's), and SHADOWS the scope's own partition of the same (provider,
    collection) -- once the store holds a dataset the scope's legacy copy is never read again
    (design section 4). Shares into the scope are left as they are. A store partition already read
    another way (an admin's ``all_scopes``) is not listed twice. Pure, so it is tested without a
    database."""
    if not store_parts:
        return parts
    held = {(p["provider"], p["collection"]) for p in store_parts}
    known = {p["part_key"] for p in parts}
    out = [p for p in parts if not (p["scope"] == scope and (p["provider"], p["collection"]) in held)]
    for p in store_parts:
        if p["part_key"] in known:
            for q in out:
                if q["part_key"] == p["part_key"]:
                    q["granted"] = True
            continue
        out.append({**p, "shared": p["scope"] != scope, "admin": False, "granted": True})
    return out


async def _partitions(
    conn,
    scope: str,
    providers: Optional[list[str]] = None,
    collections: Optional[list[str]] = None,
    *,
    exclude_providers: Optional[list[str]] = None,
    exclude_collections: Optional[list[str]] = None,
    shared_from: Optional[Iterable[str]] = None,
    read_from: Optional[Iterable[str]] = None,
    partitions: Optional[Iterable[str]] = None,
    exclude_partitions: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> list[dict]:
    """The scope's partitions, narrowed to the included providers / collections (``None``: all) and
    minus the excluded ones -- an excluded partition is never queried.

    ``granted``: ``[(provider, collection | None)]`` the scope holds an ``attributes`` provider
    grant on (``db.granted_datasets``; None: every collection of the provider). The provider
    store's partitions of those datasets are read too, each saying ``granted``, and each shadows the
    scope's own partition of the same (provider, collection) (:func:`merge_granted_partitions`).
    Before the store holds a dataset there is no such partition, and the scope's own copy answers.

    ``shared_from``: origin scopes whose partitions shared INTO ``scope`` are read too. The caller
    names them -- :func:`readable_share_origins`, for a request -- so a share whose origin the
    caller cannot reach is never even listed. A partition is shared into ``scope`` by its own share
    (039) or by its (scope, provider) group's (040). ``None`` (what every caller that predates shares
    passes, the export job's lookups among them) reads the scope's own partitions only. Each partition says its
    ``scope`` (the origin) and whether it is ``shared``; they come in :func:`_order`.

    ``read_from``: origin scopes read WHOLE, shared here or not -- an admin's search of every scope
    (:func:`admin_search_origins`); such a partition without a share says ``admin``. ``partitions`` /
    ``exclude_partitions``: by :func:`_label`, applied last (they name what this listing labels)."""
    if collections is not None:
        collections = sorted({c.lower() for c in collections})  # stored lower-case (see parse_header)
    if exclude_collections:
        exclude_collections = sorted({c.lower() for c in exclude_collections})
    origins = sorted(set(shared_from or ()) - {scope})
    whole = sorted(set(read_from or ()) - {scope})
    rows = await conn.fetch(
        """
        SELECT * FROM (
            SELECT c.scope, c.provider, c.collection, c.revision, c.rows, c.source_etag, c.ingested_at,
                   c.partition_name, c.part_key, c.keys, c.high_card_keys,
                   (c.scope <> $1 AND (c.scope = ANY($6::text[]) AND (
                      EXISTS (
                          SELECT 1 FROM attr_shares s
                          WHERE s.target_scope = $1 AND s.scope = c.scope AND s.provider = c.provider
                            AND s.collection = c.collection)
                      OR EXISTS (
                          SELECT 1 FROM attr_provider_shares g
                          WHERE g.target_scope = $1 AND g.scope = c.scope AND g.provider = c.provider)))) AS by_share
            FROM attr_collections c
            WHERE (c.scope = $1 OR c.scope = ANY($6::text[]) OR c.scope = ANY($7::text[]))
              AND ($2::text[] IS NULL OR c.provider = ANY($2)) AND ($3::text[] IS NULL OR c.collection = ANY($3))
              AND NOT (c.provider = ANY($4::text[])) AND NOT (c.collection = ANY($5::text[]))
        ) x
        WHERE x.scope = $1 OR x.by_share OR x.scope = ANY($7::text[])
        """,
        scope,
        providers,
        collections,
        exclude_providers or [],
        exclude_collections or [],
        origins,
        whole,
    )
    parts = [dict(r) for r in rows if _PARTITION_RE.match(r["partition_name"])]
    for p in parts:
        by_share = bool(p.pop("by_share"))
        p["shared"] = p["scope"] != scope
        p["admin"] = p["shared"] and not by_share
    grant_providers, grant_collections = _norm_granted(granted)
    if grant_providers and scope != PROVIDER_STORE_SCOPE:
        store_rows = await conn.fetch(
            """
            SELECT c.scope, c.provider, c.collection, c.revision, c.rows, c.source_etag, c.ingested_at,
                   c.partition_name, c.part_key, c.keys, c.high_card_keys
            FROM attr_collections c
            WHERE c.scope = $1
              AND EXISTS (
                  SELECT 1 FROM unnest($2::text[], $3::text[]) AS g(provider, collection)
                  WHERE g.provider = c.provider AND (g.collection IS NULL OR g.collection = c.collection))
              AND ($4::text[] IS NULL OR c.provider = ANY($4)) AND ($5::text[] IS NULL OR c.collection = ANY($5))
              AND NOT (c.provider = ANY($6::text[])) AND NOT (c.collection = ANY($7::text[]))
            """,
            PROVIDER_STORE_SCOPE,
            grant_providers,
            grant_collections,
            providers,
            collections,
            exclude_providers or [],
            exclude_collections or [],
        )
        store_parts = [dict(r) for r in store_rows if _PARTITION_RE.match(r["partition_name"])]
        parts = merge_granted_partitions(parts, store_parts, scope)
    if partitions is not None:
        wanted = set(partitions)
        parts = [p for p in parts if _label(p) in wanted]
    if exclude_partitions:
        dropped = set(exclude_partitions)
        parts = [p for p in parts if _label(p) not in dropped]
    parts.sort(key=_order)
    return parts


async def _paths(conn, part: dict, rows: list[dict]) -> None:
    """Ancestors of each row (root first, the row itself excluded), one recursive walk per
    partition for the whole page."""
    starts = [r for r in rows if r["parent_id"]]
    for r in rows:
        r["path"] = []
    if not starts:
        return
    table = _ident(part["partition_name"])
    found = await conn.fetch(
        f"""
        WITH RECURSIVE anc AS (
            SELECT s.start, n.node_id, n.parent_id, n.full_name, n.kind, n.depth, 1 AS lvl
            FROM unnest($1::text[], $2::text[]) AS s(start, pid)
            JOIN {table} n ON n.node_id = s.pid COLLATE "C"
            UNION ALL
            SELECT a.start, n.node_id, n.parent_id, n.full_name, n.kind, n.depth, a.lvl + 1
            FROM anc a JOIN {table} n ON n.node_id = a.parent_id
            WHERE a.lvl < {PATH_MAX_DEPTH}
        )
        SELECT start, node_id, full_name, kind, depth, lvl FROM anc
        """,
        [r["node_id"] for r in starts],
        [r["parent_id"] for r in starts],
    )
    by_start: dict[str, list] = {}
    for f in found:
        by_start.setdefault(f["start"], []).append(f)
    for r in starts:
        chain = sorted(by_start.get(r["node_id"], []), key=lambda f: -f["lvl"])
        r["path"] = [
            {"node_id": f["node_id"], "full_name": f["full_name"], "kind": f["kind"], "depth": f["depth"]}
            for f in chain
        ]


async def search_attributes(
    pool: asyncpg.Pool,
    *,
    scope: str,
    query: SearchQuery,
    shared_from: Optional[Iterable[str]] = None,
    viewer: Optional[str] = None,
    read_from: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> dict:
    """One page of the contract's search response for ``scope``, plus the partitions shared into it
    from ``shared_from``, every partition of ``read_from`` and the provider store's partitions of
    the ``granted`` datasets (see :func:`_partitions`). ``viewer`` (the caller's sub) is only for
    the rows' ``scope_wire`` (:func:`scope_wire`)."""
    t0 = time.perf_counter()
    fp = query.fingerprint()
    cur = decode_cursor(query.cursor, fp) if query.cursor else None
    where_sql, args = build_filters(query)
    after_arg = len(args) + 1
    limit_arg = len(args) + 2

    out_rows: list[dict] = []
    next_cursor = None
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{SEARCH_STATEMENT_TIMEOUT}'")
        parts = await _partitions(
            conn,
            scope,
            query.providers,
            query.collections,
            exclude_providers=query.exclude_providers,
            exclude_collections=query.exclude_collections,
            shared_from=shared_from,
            read_from=read_from,
            partitions=query.partitions,
            exclude_partitions=query.exclude_partitions,
            granted=granted,
        )

        counts: dict[str, int]
        if cur and isinstance(cur.get("counts"), dict):
            counts = {str(k): int(v) for k, v in cur["counts"].items()}
        else:
            counts = {}
            for part in parts:
                label = _label(part)
                if not query.filtered:
                    counts[label] = int(part["rows"])
                    continue
                counts[label] = int(
                    await conn.fetchval(
                        f"SELECT COUNT(*) FROM {_ident(part['partition_name'])} WHERE {where_sql}", *args
                    )
                )

        # A cursor issued before shares existed carries no origin: it stood in an own partition.
        if cur:
            origin = cur.get("s", scope)
            position = _order(
                {"provider": cur["p"], "collection": cur["c"], "shared": origin != scope, "scope": origin}
            )
        else:
            position = None
        page: list[tuple[dict, dict]] = []
        for part in parts:
            pos = _order(part)
            if position is not None and pos < position:
                continue
            label = _label(part)
            if counts.get(label) == 0:
                continue
            need = query.limit + 1 - len(page)
            after = cur["n"] if position is not None and pos == position else None
            sql = (
                f"SELECT {_SELECT_COLUMNS} FROM {_ident(part['partition_name'])} WHERE {where_sql}"
                + (f" AND node_id > ${after_arg}::text" if after is not None else "")
                + f" ORDER BY node_id LIMIT ${limit_arg if after is not None else after_arg}::int"
            )
            params = [*args, after, need] if after is not None else [*args, need]
            for r in await conn.fetch(sql, *params):
                page.append((part, dict(r)))
            if len(page) > query.limit:
                break

        if len(page) > query.limit:
            page = page[: query.limit]
            last_part, last_row = page[-1]
            next_cursor = encode_cursor(
                {
                    "v": 1,
                    "f": fp,
                    "p": last_part["provider"],
                    "c": last_part["collection"],
                    "s": last_part["scope"],
                    "n": last_row["node_id"],
                    "counts": counts,
                }
            )

        by_part: dict[str, tuple[dict, list[dict]]] = {}
        for part, r in page:
            by_part.setdefault(part["part_key"], (part, []))[1].append(r)
        for part, rows in by_part.values():
            await _paths(conn, part, rows)

    select = set(query.select) if query.select else None
    for part, r in page:
        attrs = _loads_jsonb(r["attrs"]) or {}
        if select is not None:
            attrs = {k: v for k, v in attrs.items() if k in select}
        out_rows.append(
            {
                "provider": part["provider"],
                "collection": part["collection"],
                **_origin(part, viewer),
                "node_id": r["node_id"],
                "parent_id": r["parent_id"],
                "full_name": r["full_name"],
                "source_ref": r["source_ref"],
                "kind": r["kind"],
                "depth": r["depth"],
                "attrs": attrs,
                "path": r.get("path", []),
            }
        )
    return {
        "rows": out_rows,
        "next_cursor": next_cursor,
        "counts": counts,
        # `counts` spelled out, so no client has to split its keys.
        "partitions": [
            {
                "key": _label(part),
                "provider": part["provider"],
                "collection": part["collection"],
                **_origin(part, viewer),
                "count": counts.get(_label(part), 0),
            }
            for part in parts
            if _label(part) in counts
        ],
        "took_ms": round((time.perf_counter() - t0) * 1000),
    }


async def node_attributes(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    collection: str,
    node_id: Optional[str] = None,
    full_name: Optional[str] = None,
    shared_from: Optional[Iterable[str]] = None,
    viewer: Optional[str] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> dict:
    """One node's row, by ``node_id`` or by ``full_name`` (exactly one), from the partitions of
    (provider, collection) readable in ``scope`` (:func:`_partitions`), the scope's own first.

    ``{indexed, node, ambiguous}``: ``indexed`` says whether any such partition exists, ``node`` is
    the row (the search hit's fields, without ``path``) or None. A name that names several nodes of
    one partition answers no node and ``ambiguous``. Both lookups are unique / btree probes."""
    if (node_id is None) == (full_name is None):
        raise ValueError("exactly one of node_id and full_name")
    column, value = ("node_id", node_id) if node_id is not None else ("full_name", full_name)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{SEARCH_STATEMENT_TIMEOUT}'")
        parts = await _partitions(conn, scope, [provider], [collection], shared_from=shared_from, granted=granted)
        for part in parts:
            rows = await conn.fetch(
                f"SELECT {_SELECT_COLUMNS} FROM {_ident(part['partition_name'])} "
                f'WHERE {column} = $1::text COLLATE "C" LIMIT 2',
                value,
            )
            if len(rows) > 1:
                return {"indexed": True, "node": None, "ambiguous": True}
            if rows:
                r = rows[0]
                return {
                    "indexed": True,
                    "ambiguous": False,
                    "node": {
                        "provider": part["provider"],
                        "collection": part["collection"],
                        **_origin(part, viewer),
                        "revision": part["revision"],
                        "node_id": r["node_id"],
                        "parent_id": r["parent_id"],
                        "full_name": r["full_name"],
                        "source_ref": r["source_ref"],
                        "kind": r["kind"],
                        "depth": r["depth"],
                        "attrs": _loads_jsonb(r["attrs"]) or {},
                    },
                }
    return {"indexed": bool(parts), "node": None, "ambiguous": False}


#: Node ids plus full names one API lookup (``.../assets/search/nodes``) may name.
NODES_LOOKUP_MAX = 5000


async def nodes_attributes(
    pool: asyncpg.Pool,
    *,
    scope: str,
    collection: str,
    node_ids: Iterable[str] = (),
    full_names: Iterable[str] = (),
    keys: Optional[Iterable[str]] = None,
    ancestors: int = 0,
    providers: Optional[Iterable[str]] = None,
    shared_from: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> dict:
    """Many nodes of ``collection`` at once, by ``node_ids`` and/or ``full_names``, from every
    partition of it readable in ``scope`` (``providers`` narrows them). One query per partition.

    ``{indexed, nodes, ancestors}``: ``nodes`` are the matched rows (``node_id, parent_id,
    full_name, kind, attrs, provider, providers``); ``ancestors`` the distinct rows up to
    ``ancestors`` levels above them, keyed by node id, so a caller rebuilds each chain through
    ``parent_id``. ``keys`` restricts ``attrs`` to those keys.

    ONE ROW PER NODE. Of one provider's partitions the scope's own answers before a shared one,
    whole. Rows of the same node from different providers are merged
    (:func:`ada.assets.attribute_lookup.merge_provider_rows`): the row with the most attributes
    (counted before ``keys`` applies, so the leader does not depend on the keys asked for) leads
    and wins every key both have; the others add the keys it lacks. ``provider`` names the leader,
    ``providers`` every provider that answered."""
    from ada.assets.attribute_lookup import merge_provider_rows

    ids = sorted({str(n) for n in node_ids if n})
    names = sorted({str(n) for n in full_names if n})
    want = sorted({str(k) for k in keys}) if keys is not None else None
    depth = max(0, min(int(ancestors), PATH_MAX_DEPTH))
    attrs_sql = (
        "attrs"
        if want is None
        else "COALESCE((SELECT jsonb_object_agg(e.key, e.value) FROM jsonb_each(t.attrs) e "
        "WHERE e.key = ANY($4::text[])), '{}'::jsonb)"
    )
    # node id -> provider -> (row, attribute count); dicts keep first-seen order.
    hit_rows: dict[str, dict[str, tuple[dict, int]]] = {}
    above_rows: dict[str, dict[str, tuple[dict, int]]] = {}

    def _one(by_provider: dict[str, tuple[dict, int]]) -> dict:
        if len(by_provider) == 1:
            row, _ = next(iter(by_provider.values()))
            return {**row, "providers": [row["provider"]]}
        rows, sizes = zip(*by_provider.values())
        return merge_provider_rows(list(rows), list(sizes))

    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{SEARCH_STATEMENT_TIMEOUT}'")
        parts = await _partitions(
            conn,
            scope,
            list(providers) if providers else None,
            [collection],
            shared_from=shared_from,
            granted=granted,
        )
        if not (ids or names):
            return {"indexed": bool(parts), "nodes": [], "ancestors": {}}
        for part in parts:
            table = _ident(part["partition_name"])
            rows = await conn.fetch(
                f"""
                WITH RECURSIVE hit AS MATERIALIZED (
                    SELECT node_id, parent_id FROM {table} WHERE node_id = ANY($1::text[] COLLATE "C")
                    UNION
                    SELECT node_id, parent_id FROM {table} WHERE full_name = ANY($2::text[] COLLATE "C")
                ), anc(node_id, parent_id, lvl) AS (
                    SELECT p.node_id, p.parent_id, 1
                    FROM {table} p WHERE p.node_id = ANY(ARRAY(SELECT DISTINCT parent_id FROM hit))
                        AND $3::int > 0
                    UNION
                    SELECT p.node_id, p.parent_id, a.lvl + 1
                    FROM anc a JOIN {table} p ON p.node_id = a.parent_id WHERE a.lvl < $3::int
                ), wanted AS (
                    SELECT node_id, 0 AS lvl FROM hit
                    UNION ALL
                    SELECT node_id, min(lvl) FROM anc WHERE node_id NOT IN (SELECT node_id FROM hit) GROUP BY node_id
                )
                SELECT w.lvl, t.node_id, t.parent_id, t.full_name, t.kind, {attrs_sql} AS attrs,
                       (SELECT count(*) FROM jsonb_object_keys(t.attrs))::int AS n_attrs
                FROM {table} t JOIN wanted w ON t.node_id = w.node_id
                WHERE t.node_id = ANY(ARRAY(SELECT node_id FROM wanted))
                """,
                ids,
                names,
                depth,
                *([want] if want is not None else []),
            )
            for r in rows:
                row = {
                    "node_id": r["node_id"],
                    "parent_id": r["parent_id"],
                    "full_name": r["full_name"],
                    "kind": r["kind"],
                    "attrs": _loads_jsonb(r["attrs"]) or {},
                    "provider": part["provider"],
                }
                into = hit_rows if r["lvl"] == 0 else above_rows
                # Partitions come own before shared per provider: the first answer of a provider stands.
                into.setdefault(r["node_id"], {}).setdefault(part["provider"], (row, r["n_attrs"] or 0))
    return {
        "indexed": bool(parts),
        "nodes": [_one(by_provider) for by_provider in hit_rows.values()],
        "ancestors": {node_id: _one(by_provider) for node_id, by_provider in above_rows.items()},
    }


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


async def list_attr_collections(
    pool: asyncpg.Pool,
    *,
    scope: str,
    shared_from: Optional[Iterable[str]] = None,
    viewer: Optional[str] = None,
    read_from: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> list[dict]:
    """One entry per partition searchable in ``scope``: its own, then (per collection) the ones
    shared into it from ``shared_from`` (and every one of ``read_from``, and the provider store's
    of the ``granted`` datasets), each saying its origin and its ``key`` -- the name a search's
    ``partitions`` / ``exclude_partitions`` and ``counts`` use."""
    async with pool.acquire() as conn:
        parts = await _partitions(conn, scope, shared_from=shared_from, read_from=read_from, granted=granted)
    return [
        {
            "key": _label(p),
            "provider": p["provider"],
            "collection": p["collection"],
            **_origin(p, viewer),
            "revision": p["revision"],
            "rows": int(p["rows"]),
            "source_etag": p["source_etag"],
            "ingested_at": _iso(p["ingested_at"]),
        }
        for p in parts
    ]


async def list_attr_keys(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
    shared_from: Optional[Iterable[str]] = None,
    read_from: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> list[str]:
    """Every attribute key of the matching collections, sorted (byte order: keys are case-sensitive)."""
    async with pool.acquire() as conn:
        parts = await _partitions(
            conn,
            scope,
            [provider] if provider else None,
            [collection] if collection else None,
            shared_from=shared_from,
            read_from=read_from,
            granted=granted,
        )
    keys: set[str] = set()
    for p in parts:
        keys.update(p["keys"] or [])
    return sorted(keys)


async def list_attr_values(
    pool: asyncpg.Pool,
    *,
    scope: str,
    key: str,
    prefix: Optional[str] = None,
    limit: int = 50,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
    shared_from: Optional[Iterable[str]] = None,
    read_from: Optional[Iterable[str]] = None,
    granted: Optional[Iterable[tuple[str, Optional[str]]]] = None,
) -> list[dict]:
    """The commonest values of ``key`` (as text) across the matching collections, most frequent
    first. From the ingest histogram where the key has one; by scanning the partition where its
    values were too many to histogram."""
    limit = max(1, min(int(limit), 1000))
    like = escape_like(prefix) + "%" if prefix else None
    totals: Counter = Counter()
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{SEARCH_STATEMENT_TIMEOUT}'")
        parts = await _partitions(
            conn,
            scope,
            [provider] if provider else None,
            [collection] if collection else None,
            shared_from=shared_from,
            read_from=read_from,
            granted=granted,
        )
        tracked = [p["part_key"] for p in parts if key in (p["keys"] or []) and key not in (p["high_card_keys"] or [])]
        scanned = [p for p in parts if key in (p["high_card_keys"] or [])]
        if tracked:
            for r in await conn.fetch(
                """
                SELECT value, SUM(n)::bigint AS n FROM attr_values
                WHERE part_key = ANY($1::text[]) AND key = $2 AND ($3::text IS NULL OR value LIKE $3)
                GROUP BY value
                """,
                tracked,
                key,
                like,
            ):
                totals[r["value"]] += int(r["n"])
        for p in scanned:
            for r in await conn.fetch(
                f"""
                SELECT attrs->>$1::text AS value, COUNT(*) AS n FROM {_ident(p["partition_name"])}
                WHERE attrs->>$1::text IS NOT NULL AND ($2::text IS NULL OR attrs->>$1::text LIKE $2)
                GROUP BY 1 ORDER BY n DESC LIMIT $3
                """,
                key,
                like,
                limit,
            ):
                totals[r["value"]] += int(r["n"])
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"value": v, "count": n} for v, n in ranked]


# --- shares (migration 039) ---------------------------------------------------------------------


async def readable_share_origins(pool: asyncpg.Pool, *, scope: str, user_sub: str, is_admin: bool = False) -> list[str]:
    """The origin scopes of the shares into ``scope`` that ``user_sub`` may read -- what a request
    passes as ``shared_from``.

    One query per request, and the access rule is :func:`..scope.can_access`'s, applied to the
    ORIGIN: the shared scope is everyone's, a personal scope its owner's, a project its members'
    (while it is not archived), a corpus an admin's. Asked afresh every time rather than recorded
    on the share, so leaving a project hides everything shared from it at once. Per-collection
    shares (039) and group shares (040) alike."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT s.scope FROM (
            SELECT scope FROM attr_shares WHERE target_scope = $1
            UNION SELECT scope FROM attr_provider_shares WHERE target_scope = $1
        ) s
        WHERE (
            s.scope = 'shared'
            OR s.scope = 'users/' || $2
            OR (s.scope LIKE 'projects/%' AND EXISTS (
                SELECT 1 FROM project_members m JOIN projects p ON p.id = m.project_id
                WHERE m.user_sub = $2 AND p.archived_at IS NULL AND 'projects/' || p.id::text = s.scope))
            OR ($3 AND s.scope LIKE 'corpus/%'))
        ORDER BY 1
        """,
        scope,
        user_sub,
        is_admin,
    )
    return [r["scope"] for r in rows]


async def admin_search_origins(pool: asyncpg.Pool, *, scope: str) -> list[str]:
    """Every scope but ``scope`` whose attribute partitions an ADMIN may search from ``scope`` without
    a share (``all_scopes`` on a search) -- what a request passes as ``read_from``.

    The provider scopes: the shared scope, every project that is not archived (membership or not:
    this is the admin's view of every provider collection) and the corpora. NOT another user's
    personal scope: admin status is for administering the providers' data, not for reading what a
    user indexed for themselves (:func:`..scope.can_access` draws the same line). The caller checks
    that the user is an admin; this only lists."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT c.scope FROM attr_collections c
        WHERE c.scope <> $1
          AND (c.scope = 'shared'
               OR c.scope = 'providers'
               OR c.scope LIKE 'corpus/%'
               OR (c.scope LIKE 'projects/%' AND EXISTS (
                   SELECT 1 FROM projects p WHERE p.archived_at IS NULL AND 'projects/' || p.id::text = c.scope)))
        ORDER BY 1
        """,
        scope,
    )
    return [r["scope"] for r in rows]


def _share_row(r) -> dict:
    return {
        "scope": r["scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "target_scope": r["target_scope"],
        "shared_at": _iso(r["shared_at"]),
        "shared_by": r["shared_by"],
    }


async def share_attr_collection(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    collection: str,
    target_scope: str,
    shared_by: Optional[str] = None,
) -> Optional[dict]:
    """Make one partition searchable from ``target_scope`` too. Idempotent: sharing it again
    answers the share that exists (its first ``shared_at`` / ``shared_by`` kept). None when the
    partition does not exist. Sharing a partition into its own scope is refused (ValueError)."""
    collection = collection.lower()
    if target_scope == scope:
        raise ValueError("a partition is already searchable in its own scope")
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO attr_shares (scope, provider, collection, target_scope, shared_by)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (scope, provider, collection, target_scope) DO UPDATE SET shared_by = attr_shares.shared_by
            RETURNING scope, provider, collection, target_scope, shared_at, shared_by
            """,
            scope,
            provider,
            collection,
            target_scope,
            shared_by,
        )
    except asyncpg.ForeignKeyViolationError:
        return None
    return _share_row(row)


async def unshare_attr_collection(
    pool: asyncpg.Pool, *, scope: str, provider: str, collection: str, target_scope: str
) -> bool:
    """Stop sharing one partition into ``target_scope``. True when it was shared there."""
    status = await pool.execute(
        "DELETE FROM attr_shares WHERE scope = $1 AND provider = $2 AND collection = $3 AND target_scope = $4",
        scope,
        provider,
        collection.lower(),
        target_scope,
    )
    return status.split()[-1] != "0"


async def list_attr_shares(
    pool: asyncpg.Pool,
    *,
    scope: Optional[str] = None,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
    target_scope: Optional[str] = None,
) -> list[dict]:
    """Shares, narrowed by origin partition (any of ``scope`` / ``provider`` / ``collection``)
    and / or by ``target_scope``; ``None`` matches every one. No access check: this is the admin's
    view (a search reads shares through :func:`readable_share_origins`)."""
    rows = await pool.fetch(
        """
        SELECT scope, provider, collection, target_scope, shared_at, shared_by FROM attr_shares
        WHERE ($1::text IS NULL OR scope = $1) AND ($2::text IS NULL OR provider = $2)
          AND ($3::text IS NULL OR collection = $3) AND ($4::text IS NULL OR target_scope = $4)
        ORDER BY scope, provider, collection, target_scope
        """,
        scope,
        provider,
        collection.lower() if collection else None,
        target_scope,
    )
    return [_share_row(r) for r in rows]


# --- group shares (migration 040) ---------------------------------------------------------------


def _provider_share_row(r) -> dict:
    return {
        "scope": r["scope"],
        "provider": r["provider"],
        "target_scope": r["target_scope"],
        "shared_at": _iso(r["shared_at"]),
        "shared_by": r["shared_by"],
    }


async def share_attr_provider(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    target_scope: str,
    shared_by: Optional[str] = None,
) -> Optional[dict]:
    """Make every partition of (``scope``, ``provider``) -- the ones there now and the ones a later
    sweep adds -- searchable from ``target_scope`` too. Idempotent, like a per-collection share.
    None when the scope holds nothing of that provider: a group share is made of a group that
    exists (it is kept when the group later empties, and then matches nothing)."""
    if target_scope == scope:
        raise ValueError("a partition is already searchable in its own scope")
    async with pool.acquire() as conn, conn.transaction():
        held = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM attr_collections WHERE scope = $1 AND provider = $2)", scope, provider
        )
        if not held:
            return None
        row = await conn.fetchrow(
            """
            INSERT INTO attr_provider_shares (scope, provider, target_scope, shared_by) VALUES ($1, $2, $3, $4)
            ON CONFLICT (scope, provider, target_scope) DO UPDATE SET shared_by = attr_provider_shares.shared_by
            RETURNING scope, provider, target_scope, shared_at, shared_by
            """,
            scope,
            provider,
            target_scope,
            shared_by,
        )
    return _provider_share_row(row)


async def unshare_attr_provider(pool: asyncpg.Pool, *, scope: str, provider: str, target_scope: str) -> bool:
    """Stop sharing the (scope, provider) group into ``target_scope``. Its collections' own
    shares (039) stay: what was shared one by one is still shared. True when it was shared there."""
    status = await pool.execute(
        "DELETE FROM attr_provider_shares WHERE scope = $1 AND provider = $2 AND target_scope = $3",
        scope,
        provider,
        target_scope,
    )
    return status.split()[-1] != "0"


async def list_attr_provider_shares(
    pool: asyncpg.Pool,
    *,
    scope: Optional[str] = None,
    provider: Optional[str] = None,
    target_scope: Optional[str] = None,
) -> list[dict]:
    """Group shares, each filter optional. The admin's view, like :func:`list_attr_shares`."""
    rows = await pool.fetch(
        """
        SELECT scope, provider, target_scope, shared_at, shared_by FROM attr_provider_shares
        WHERE ($1::text IS NULL OR scope = $1) AND ($2::text IS NULL OR provider = $2)
          AND ($3::text IS NULL OR target_scope = $3)
        ORDER BY scope, provider, target_scope
        """,
        scope,
        provider,
        target_scope,
    )
    return [_provider_share_row(r) for r in rows]


# --- keeping only the newest copy ----------------------------------------------------------------


def _numeric(revision: Optional[str]) -> Optional[float]:
    try:
        return float(revision) if revision is not None else None
    except ValueError:
        return None


def newest_copy(members: list[dict]) -> dict:
    """Which of a collection's copies is the freshest: ``members`` carry ``scope``, ``revision`` and
    ``ingested_at``.

    A revision is the provider's own token and core cannot order two of them in general, so they
    are compared only when every one is a number; otherwise the copy ingested last carries the later
    revision (a sweep ingests what its source has now). Copies at the SAME revision hold the same
    rows, so among them the shared scope's is kept -- the one everyone may read -- and then the
    latest ingest."""
    nums = [_numeric(m["revision"]) for m in members]
    numeric = all(n is not None for n in nums)

    def at(m: dict) -> str:
        v = m["ingested_at"]
        return v.isoformat() if hasattr(v, "isoformat") else str(v or "")

    if numeric:
        top = max(nums)
        best = [m for m, n in zip(members, nums) if n == top]
    else:
        latest = max(at(m) for m in members)
        best = [m for m in members if at(m) == latest]
        # Copies sharing the newest revision are one revision, wherever they were ingested.
        revs = {m["revision"] for m in best if m["revision"] is not None}
        best = [m for m in members if m["revision"] in revs] if revs else best
    return max(best, key=lambda m: (m["scope"] == SHARED_SCOPE, at(m), m["scope"]))


async def prune_attr_revisions(
    pool: asyncpg.Pool,
    *,
    provider: Optional[str] = None,
    scopes: Optional[Iterable[str]] = None,
    collections: Optional[Iterable[str]] = None,
    include_narrowing: bool = False,
    dry_run: bool = True,
    pruned_by: Optional[str] = None,
) -> dict:
    """Keep only the freshest copy (:func:`newest_copy`) of every (provider, collection) held in
    more than one scope -- narrowed to ``provider``, to copies in ``scopes`` (at least two of them)
    and to ``collections``.

    Nothing anyone could search is hidden on the quiet. When the copy kept is the shared scope's,
    each older copy is FOLDED (:func:`_fold_in_tx`): its scope and every scope it was shared into
    search the shared copy instead. When the copy kept lies in a narrower scope -- a project's,
    newer than the shared one -- its rows are never shared on (a project's data does not leave it on
    its own), so dropping an older copy would hide the collection from that copy's readers: those
    copies are SKIPPED with ``reason: "narrowing"`` unless ``include_narrowing``, and then simply
    dropped, nothing shared anywhere. A dry run (the default) answers the plan."""
    wanted_scopes = sorted(set(scopes)) if scopes is not None else None
    wanted = sorted({c.lower() for c in collections}) if collections is not None else None
    rows = await pool.fetch(
        """
        SELECT scope, provider, collection, revision, rows, ingested_at,
               COALESCE(pg_total_relation_size(to_regclass(partition_name)), 0) AS bytes
        FROM attr_collections
        WHERE ($1::text IS NULL OR provider = $1) AND ($2::text[] IS NULL OR scope = ANY($2))
          AND ($3::text[] IS NULL OR collection = ANY($3))
        ORDER BY provider, collection, scope
        """,
        provider,
        wanted_scopes,
        wanted,
    )
    by_key: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_key.setdefault((r["provider"], r["collection"]), []).append(
            {
                "scope": r["scope"],
                "revision": r["revision"],
                "rows": int(r["rows"]),
                "bytes": int(r["bytes"]),
                "ingested_at": r["ingested_at"],
            }
        )

    def doc(m: dict, **extra) -> dict:
        return {**m, "ingested_at": _iso(m["ingested_at"]), **extra}

    plan: list[dict] = []
    for (prov, coll), members in sorted(by_key.items()):
        if len(members) < 2:
            continue
        keep = newest_copy(members)
        entry = {"provider": prov, "collection": coll, "kept": doc(keep), "dropped": [], "skipped": []}
        for m in members:
            if m is keep:
                continue
            if keep["scope"] == SHARED_SCOPE:
                entry["dropped"].append(doc(m, action="fold"))
            elif include_narrowing:
                entry["dropped"].append(doc(m, action="drop"))
            else:
                entry["skipped"].append(
                    doc(
                        m,
                        reason="narrowing",
                        note=f"the newest copy is only in {keep['scope']}: dropping this one would hide the "
                        f"collection from whoever reads {m['scope']} -- re-sweep it there instead",
                    )
                )
        plan.append(entry)

    errors: list[str] = []
    if not dry_run:
        for entry in plan:
            done = []
            for d in entry["dropped"]:
                try:
                    if d["action"] == "fold":
                        fold = await _fold_one(
                            pool, drop_scope=d["scope"], provider=entry["provider"], collection=entry["collection"],
                            folded_by=pruned_by,
                        )  # fmt: skip
                        if fold is None:
                            continue
                        done.append({**d, "bytes": fold["bytes_freed"], "repointed": fold["repointed"]})
                    else:
                        if await drop_attr_collection(
                            pool, scope=d["scope"], provider=entry["provider"], collection=entry["collection"]
                        ):
                            done.append(d)
                except Exception as exc:  # noqa: BLE001 - one copy's failure is reported, not fatal
                    errors.append(f"{entry['provider']}/{entry['collection']} in {d['scope']}: {exc}")
            entry["dropped"] = done
    dropped = [d for e in plan for d in e["dropped"]]
    return {
        "dry_run": dry_run,
        "include_narrowing": include_narrowing,
        "collections": plan,
        "dropped": len(dropped),
        "skipped": sum(len(e["skipped"]) for e in plan),
        "bytes": sum(d["bytes"] for d in dropped),
        "errors": errors,
    }
