"""The provider-neutral attribute index (migration 037) and ``POST .../assets/search``.

Always-on tests cover the query builder (no SQL ever carries a caller's key or value), the cursor,
the streaming gunzip and the publish hook's no-database path. The live-Postgres tests (skipped
unless ADA_TEST_POSTGRES_URL is set) ingest a small synthetic index into a throwaway scope, search
it, page it, reingest it, and drop everything they made.
"""

from __future__ import annotations

import contextlib
import gzip
import inspect
import json
import os
import random
import tempfile
import time
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest

from ada.comms.rest import db as dbm
from ada.comms.rest.db import attribute_index as ai
from ada.comms.rest.formats import asset_publish as ap
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

CANONICAL = {
    "depth": {"eq": 3},
    "where": [{"key": "TYPE", "eq": "EQUI"}, {"key": "FUNC", "eq": "MS"}, {"key": "PURP", "in": ["NOD", "SUPP"]}],
}


# --- a synthetic index ------------------------------------------------------------------------


def _nodes(collection: str, n_items: int, *, seed: int = 0, extra: dict | None = None) -> list[dict]:
    """SITE (1) -> ZONE (2) -> items (3): items carry TYPE / FUNC / PURP / DESC."""
    rnd = random.Random(seed)
    site = f"/{collection}"
    out = [
        {"node_id": f"{collection}-site", "parent_id": None, "full_name": site, "kind": "SITE", "depth": 1, "attrs": {}}
    ]
    zones = [f"{collection}-z{i}" for i in range(4)]
    for z in zones:
        out.append(
            {"node_id": z, "parent_id": f"{collection}-site", "full_name": f"{site}/{z}", "kind": "ZONE", "depth": 2,
             "attrs": {"OWNER": "TEAM"}}
        )  # fmt: skip
    for i in range(n_items):
        z = zones[i % len(zones)]
        attrs = {
            "TYPE": rnd.choice(["EQUI", "PIPE", "STRU"]),
            "FUNC": rnd.choice(["MS", "PS", "XS"]),
            "PURP": rnd.choice(["NOD", "SUPP", "OTHR"]),
            "DESC": rnd.choice(["STEEL", "STAIR", "VALVE", "pump"]) + f"-{i}",
            "WEIGHT": i,
            "UNSET": "",
            "Internal_A": "x",
            "Internal_B": "y",
            "SECRET": "s",
            **(extra or {}),
        }
        out.append(
            {"node_id": f"{collection}-n{i:05d}", "parent_id": z, "full_name": f"{site}/{z}/N{i}",
             "source_ref": f"=1/{i}", "kind": attrs["TYPE"], "depth": 3, "attrs": attrs}
        )  # fmt: skip
    return out


def _index_bytes(provider: str, collection: str, nodes: list[dict], *, revision: str = "r1") -> bytes:
    header = {
        "schema": ai.ATTRIBUTE_INDEX_SCHEMA,
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "source_etag": f"etag-{revision}",
        "depth_base": "SITE=1",
        "index_exclude": ["SECRET", "Internal_*"],
    }
    lines = [json.dumps(header)] + [json.dumps(n) for n in nodes]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


async def _chunks(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def _matches(node: dict, q: dict) -> bool:
    a = node["attrs"]
    if q.get("depth", {}).get("eq") is not None and node["depth"] != q["depth"]["eq"]:
        return False
    for w in q.get("where", []):
        v = a.get(w["key"])
        if "eq" in w and v != w["eq"]:
            return False
        if "in" in w and v not in w["in"]:
            return False
        if "prefix" in w and not (isinstance(v, str) and v.startswith(w["prefix"])):
            return False
    return True


# --- the query builder (no database) ----------------------------------------------------------


def test_canonical_query_builds_parameterised_containment():
    q = ai.parse_search_query(CANONICAL)
    sql, args = ai.build_filters(q)
    assert sql == (
        "depth = $1::int AND attrs @> $2::jsonb AND attrs @> $3::jsonb AND (attrs @> $4::jsonb OR attrs @> $5::jsonb)"
    )
    assert args == [3, '{"TYPE": "EQUI"}', '{"FUNC": "MS"}', '{"PURP": "NOD"}', '{"PURP": "SUPP"}']


def test_keys_and_values_never_reach_the_sql_text():
    evil = "x'); DROP TABLE attr_nodes; --"
    q = ai.parse_search_query(
        {"kind": [evil], "where": [{"key": evil, "eq": evil}, {"key": evil, "prefix": "50%_off\\"}]}
    )
    sql, args = ai.build_filters(q)
    assert "DROP" not in sql and "'" not in sql
    assert sql.endswith("attrs->>$3::text LIKE $4::text")
    assert args[-1] == "50\\%\\_off\\\\%"  # LIKE wildcards in a prefix are literal
    assert json.loads(args[1]) == {evil: evil}


def test_equality_on_a_number_typed_as_text_matches_either_spelling():
    sql, args = ai.build_filters(ai.parse_search_query({"where": [{"key": "W", "eq": "3"}]}))
    assert sql == "(attrs @> $1::jsonb OR attrs @> $2::jsonb)"
    assert [json.loads(a) for a in args] == [{"W": "3"}, {"W": 3}]


def test_depth_range_kind_and_unfiltered():
    sql, args = ai.build_filters(ai.parse_search_query({"kind": ["EQUI"], "depth": {"min": 1, "max": 4}}))
    assert sql == "kind = ANY($1::text[]) AND depth >= $2::int AND depth <= $3::int"
    assert args == [["EQUI"], 1, 4]
    assert ai.build_filters(ai.parse_search_query({})) == ("TRUE", [])
    assert ai.parse_search_query(None).limit == ai.SEARCH_DEFAULT_LIMIT
    assert ai.parse_search_query({"limit": 5000}).limit == ai.SEARCH_MAX_LIMIT


@pytest.mark.parametrize(
    "body",
    [
        {"where": [{"key": "A"}]},
        {"where": [{"key": "A", "eq": "x", "in": ["y"]}]},
        {"where": [{"key": "", "eq": "x"}]},
        {"where": [{"key": "A", "in": []}]},
        {"where": [{"key": "A", "eq": {"nested": 1}}]},
        {"where": [{"key": "A", "regex": ".*"}]},
        {"depth": {"eq": "3"}},
        {"depth": {"between": [1, 2]}},
        {"providers": [1]},
        {"limit": "ten"},
        {"cursor": 5},
        [],
    ],
)
def test_malformed_bodies_are_refused(body):
    with pytest.raises(ai.SearchQueryError):
        ai.parse_search_query(body)


def test_cursor_round_trips_and_is_bound_to_its_query():
    q = ai.parse_search_query(CANONICAL)
    c = ai.encode_cursor({"v": 1, "f": q.fingerprint(), "p": "prov", "c": "alpha", "n": "id-1", "counts": {}})
    assert ai.decode_cursor(c, q.fingerprint())["n"] == "id-1"
    # page size and projection do not change what a cursor is valid for; the filters do
    assert ai.parse_search_query({**CANONICAL, "limit": 5, "select": ["DESC"]}).fingerprint() == q.fingerprint()
    with pytest.raises(ai.SearchQueryError, match="different query"):
        ai.decode_cursor(c, ai.parse_search_query({"kind": ["X"]}).fingerprint())
    with pytest.raises(ai.SearchQueryError):
        ai.decode_cursor("not-a-cursor!!", q.fingerprint())


def test_partition_names_are_bounded_sanitised_and_unique_per_scope():
    long_name = "Ä" * 300 + "/; DROP"
    k1 = ai.part_key_for("projects/p1", "prov", long_name)
    k2 = ai.part_key_for("projects/p2", "prov", long_name)
    assert k1 != k2 and len(k1) == 16
    name = ai.partition_name_for("prov", long_name, k1)
    assert len(name) <= 63 and ai._PARTITION_RE.match(name)
    assert ai._ident(name) == f'"{name}"'
    with pytest.raises(ValueError):
        ai._ident('attr_nodes"; DROP TABLE x; --')


@pytest.mark.asyncio
async def test_ndjson_lines_peels_double_gzip_and_multi_member_files():
    body = b'{"a":1}\n{"a":2}\n\n'
    multi = gzip.compress(b'{"h":0}\n') + gzip.compress(body)
    for data in (multi, gzip.compress(multi), b'{"h":0}\n' + body):
        got = [json.loads(line) async for line in ai.ndjson_lines(_chunks(data, 7))]
        assert got == [{"h": 0}, {"a": 1}, {"a": 2}]


def test_parse_batch_drops_unset_and_excluded_keys_and_counts_bad_lines():
    excluded = ai._exclusion(["SECRET", "Internal_*"])
    stats = ai._ValueStats()
    lines = [
        json.dumps({"node_id": "a", "depth": 3, "attrs": {"K": "v", "E": "", "N": None, "SECRET": 1, "Internal_x": 2}}),
        b"{not json",
        json.dumps({"parent_id": "x"}),
    ]
    records, skipped = ai._parse_batch([x if isinstance(x, bytes) else x.encode() for x in lines], excluded, stats)
    assert skipped == 2 and len(records) == 1
    assert json.loads(records[0][-1]) == {"K": "v"} and records[0][5] == 3
    assert dict(stats.keys) == {"K": 1}


class _FakeQueue:
    def __init__(self):
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


class _FakeStorage:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = dict(blobs)
        self.deleted: list[str] = []

    async def open_stream(self, scope, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return SimpleNamespace(stream=_chunks(self.blobs[key], 1024), content_encoding="gzip")

    async def put_bytes(self, scope, key, data, content_encoding=None, **kw):
        self.blobs[key] = data

    async def delete(self, scope, key):
        self.deleted.append(key)
        self.blobs.pop(key, None)


def _publish_job(staged: dict, *, provider: str, dry_run: bool = False):
    return SimpleNamespace(
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        derived_key="_derived/assets/_publish/x/summary.json",
        conversion_options={"provider": provider, "staged": staged, "dry_run": dry_run},
    )


@pytest.fixture
def no_audit(monkeypatch):
    async def _noop(*a, **kw):
        return None

    monkeypatch.setattr(ap, "_audit_done", _noop)

    def _no_publisher(provider):  # an attributes-only publish must never ask for one
        raise AssertionError(f"asset_publisher({provider!r}) was called")

    monkeypatch.setattr(ap, "asset_publisher", _no_publisher)


@pytest.mark.asyncio
async def test_attributes_only_publish_without_a_pool_warns_and_keeps_the_staging(no_audit):
    key = f"assets/_staging/s1/{ai.ATTRIBUTE_INDEX_ROLE}"
    storage = _FakeStorage({key: _index_bytes("prov", "alpha", _nodes("alpha", 3))})
    queue = _FakeQueue()
    job = _publish_job({ai.ATTRIBUTE_INDEX_ROLE: key}, provider="prov")
    await ap._run_asset_publish(
        job=job, scope=Scope.shared(), storage=storage, queue=queue, db_pool=None, started_at=time.time()
    )
    assert queue.updates[-1]["status"] == "done"
    summary = json.loads(storage.blobs[job.derived_key])
    assert summary["collection"] == "alpha" and summary["revision"] == "r1"
    assert summary["attributes"]["ingested"] is False
    assert "no database pool" in summary["warnings"][0]
    assert storage.deleted == []  # not ingested: still staged


# --- live Postgres -------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pg_world():
    pool = await dbm.init_pool(POSTGRES_URL)
    scope = f"projects/test-attr-{uuid.uuid4().hex[:8]}"
    provider = f"test-{uuid.uuid4().hex[:6]}"
    try:
        yield SimpleNamespace(pool=pool, scope=scope, provider=provider)
    finally:
        for r in await pool.fetch("SELECT provider, collection FROM attr_collections WHERE scope = $1", scope):
            await ai.drop_attr_collection(pool, scope=scope, provider=r["provider"], collection=r["collection"])
        left = await pool.fetchval(
            "SELECT COUNT(*) FROM attr_collections WHERE scope = $1", scope
        ) + await pool.fetchval(
            "SELECT COUNT(*) FROM pg_class WHERE relkind = 'r' AND (relname LIKE 'attr\\_n\\_%' OR relname LIKE $1)",
            f"attr_p_{provider.replace('-', '_')}%",
        )
        await dbm.close_pool(pool)
        assert left == 0, "the test left attribute-index tables or rows behind"


def with_pg(fn):
    """Hand the test a throwaway scope + provider on a pool built in the TEST's own event loop
    (an async fixture may run on another loop, and asyncpg connections are bound to theirs)."""
    params = list(inspect.signature(fn).parameters.values())[1:]

    async def run(**kwargs):
        async with _pg_world() as pg:
            await fn(pg, **kwargs)

    run.__name__ = fn.__name__
    run.__signature__ = inspect.Signature(params)
    return run


async def _ingest(pg, collection, nodes, **kw):
    return await ai.ingest_attribute_index(
        pg.pool,
        scope=pg.scope,
        provider=pg.provider,
        chunks=_chunks(_index_bytes(pg.provider, collection, nodes, **kw), 64 * 1024),
        batch_rows=500,
    )


async def _all_pages(pg, body: dict, limit: int) -> tuple[list[dict], list[dict]]:
    rows, pages, cursor = [], [], None
    while True:
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({**body, "limit": limit, "cursor": cursor})
        )
        pages.append(res)
        rows += res["rows"]
        cursor = res["next_cursor"]
        if cursor is None:
            return rows, pages


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_ingest_search_page_path_and_reingest(pg, monkeypatch):
    monkeypatch.setattr(ai, "VALUE_STATS_MAX_DISTINCT", 500)  # DESC (one value per node) outgrows it
    alpha, beta = _nodes("alpha", 1200, seed=1), _nodes("beta", 800, seed=2)
    a = await _ingest(pg, "alpha", alpha)
    b = await _ingest(pg, "beta", beta)
    print(f"\ningest alpha {a['rows']} rows: {a['timings']}; beta {b['rows']} rows: {b['timings']}")
    assert a["rows"] == len(alpha) and a["skipped"] == 0 and a["collection"] == "alpha"

    # The canonical query: TYPE / FUNC / PURP at depth 3.
    t0 = time.perf_counter()
    res = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query(CANONICAL))
    print(f"canonical query: {res['took_ms']} ms reported, {(time.perf_counter() - t0) * 1000:.0f} ms wall")
    want = {("alpha", n["node_id"]) for n in alpha if _matches(n, CANONICAL)} | {
        ("beta", n["node_id"]) for n in beta if _matches(n, CANONICAL)
    }
    got = {(r["collection"], r["node_id"]) for r in res["rows"]}
    assert got == want and res["next_cursor"] is None
    assert res["counts"] == {
        f"{pg.provider}/alpha": sum(_matches(n, CANONICAL) for n in alpha),
        f"{pg.provider}/beta": sum(_matches(n, CANONICAL) for n in beta),
    }
    row = res["rows"][0]
    assert row["provider"] == pg.provider and row["depth"] == 3 and row["source_ref"].startswith("=1/")
    # path: ancestors, root first, the hit itself excluded
    assert [p["kind"] for p in row["path"]] == ["SITE", "ZONE"]
    assert row["path"][0]["node_id"] == f"{row['collection']}-site" and row["path"][1]["node_id"] == row["parent_id"]
    # index_exclude and unset values never reached the index
    assert {"SECRET", "Internal_A", "Internal_B", "UNSET"}.isdisjoint(row["attrs"])

    # prefix + select, one collection
    body = {"collections": ["alpha"], "where": [{"key": "DESC", "prefix": "ST"}], "select": ["DESC"]}
    rows, _ = await _all_pages(pg, body, 1000)
    assert {r["node_id"] for r in rows} == {n["node_id"] for n in alpha if n["attrs"].get("DESC", "").startswith("ST")}
    assert all(set(r["attrs"]) == {"DESC"} for r in rows)

    # IN across both, paged in 37s: every match exactly once, in (provider, collection, node_id) order
    body = {"where": [{"key": "PURP", "in": ["NOD", "SUPP"]}], "kind": ["EQUI", "PIPE"]}
    rows, pages = await _all_pages(pg, body, 37)
    keys = [(r["collection"], r["node_id"]) for r in rows]
    assert len(keys) == len(set(keys)) and keys == sorted(keys)
    q = {"where": [{"key": "PURP", "in": ["NOD", "SUPP"]}]}
    expected = sum(_matches(n, q) and n["kind"] in ("EQUI", "PIPE") for n in alpha + beta)
    assert len(rows) == expected and len(pages) == -(-expected // 37)
    assert all(p["counts"] == pages[0]["counts"] for p in pages)
    assert sum(pages[0]["counts"].values()) == expected

    # numbers match whether asked as number or text
    for v in (7, "7"):
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({"where": [{"key": "WEIGHT", "eq": v}]})
        )
        assert {r["node_id"] for r in res["rows"]} == {"alpha-n00007", "beta-n00007"}

    # helpers
    cols = await ai.list_attr_collections(pg.pool, scope=pg.scope)
    assert [(c["collection"], c["rows"], c["revision"]) for c in cols] == [
        ("alpha", len(alpha), "r1"),
        ("beta", len(beta), "r1"),
    ]
    assert await ai.list_attr_keys(pg.pool, scope=pg.scope) == ["DESC", "FUNC", "OWNER", "PURP", "TYPE", "WEIGHT"]
    vals = await ai.list_attr_values(pg.pool, scope=pg.scope, key="FUNC", prefix="M")
    assert vals == [
        {"value": "MS", "count": sum(n["attrs"].get("FUNC") == "MS" for n in alpha + beta)},
    ]
    # DESC has a distinct value per node: too many to histogram, answered by scanning
    assert "DESC" in (await pg.pool.fetchval("SELECT high_card_keys FROM attr_collections WHERE scope = $1 AND collection = 'alpha'", pg.scope))  # fmt: skip
    vals = await ai.list_attr_values(pg.pool, scope=pg.scope, key="DESC", prefix="VALVE-1", limit=5, collection="alpha")
    assert len(vals) == 5 and all(v["value"].startswith("VALVE-1") and v["count"] == 1 for v in vals)

    # other scopes see nothing
    other = await ai.search_attributes(pg.pool, scope=pg.scope + "-other", query=ai.parse_search_query(CANONICAL))
    assert other["rows"] == [] and other["counts"] == {}

    # Reingest ALPHA: the swap replaces it whole -- old rows gone, new ones searchable.
    alpha2 = _nodes("alpha", 300, seed=9, extra={"NEWKEY": "yes"})
    alpha2.append(dict(alpha2[-1]))  # a node listed twice: kept once, reported
    r2 = await _ingest(pg, "alpha", alpha2, revision="r2")
    print(f"reingest alpha {r2['rows']} rows: {r2['timings']}")
    assert r2["rows"] == len(alpha2) - 1 and r2["duplicates"] == 1 and r2["warnings"]
    assert r2["partition"] == a["partition"]
    res = await ai.search_attributes(
        pg.pool,
        scope=pg.scope,
        query=ai.parse_search_query(
            {"collections": ["alpha"], "where": [{"key": "NEWKEY", "eq": "yes"}], "limit": 1000}
        ),
    )
    assert len(res["rows"]) == 300 and res["counts"] == {f"{pg.provider}/alpha": 300}
    cols = {c["collection"]: c for c in await ai.list_attr_collections(pg.pool, scope=pg.scope)}
    assert cols["alpha"]["revision"] == "r2" and cols["alpha"]["rows"] == len(alpha2) - 1
    assert cols["alpha"]["source_etag"] == "etag-r2"
    attached = await pg.pool.fetchval(
        "SELECT COUNT(*) FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'attr_nodes'::regclass AND c.relname = ANY($1::text[])",
        [a["partition"], b["partition"]],
    )
    assert attached == 2
    assert (
        await pg.pool.fetchval("SELECT COUNT(*) FROM pg_class WHERE relkind = 'r' AND relname LIKE 'attr\\_n\\_%'") == 0
    )


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_attributes_only_publish_ingests_and_clears_its_staging(pg, no_audit):
    key = f"assets/_staging/s2/{ai.ATTRIBUTE_INDEX_ROLE}"
    nodes = _nodes("gamma", 50)
    storage = _FakeStorage({key: _index_bytes(pg.provider, "gamma", nodes, revision="g1")})
    queue = _FakeQueue()
    scope = Scope.project(pg.scope.split("/", 1)[1])
    assert scope.prefix() == pg.scope
    job = _publish_job({ai.ATTRIBUTE_INDEX_ROLE: key}, provider=pg.provider)
    await ap._run_asset_publish(
        job=job, scope=scope, storage=storage, queue=queue, db_pool=pg.pool, started_at=time.time()
    )
    assert queue.updates[-1]["status"] == "done", queue.updates
    summary = json.loads(storage.blobs[job.derived_key])
    assert summary["collection"] == "gamma" and summary["revision"] == "g1" and summary["subjects"] == []
    assert summary["attributes"]["ingested"] is True and summary["attributes"]["rows"] == len(nodes)
    assert {"ingest_ms", "ingest_rows", "index_ms"} <= set(summary["timings"])
    assert summary["timings"]["ingest_rows"] == len(nodes)
    assert storage.deleted == [key]

    # A dry run reads the header only and ingests nothing.
    storage.blobs[key] = _index_bytes(pg.provider, "delta", _nodes("delta", 5))
    job = _publish_job({ai.ATTRIBUTE_INDEX_ROLE: key}, provider=pg.provider, dry_run=True)
    await ap._run_asset_publish(
        job=job, scope=scope, storage=storage, queue=queue, db_pool=pg.pool, started_at=time.time()
    )
    summary = json.loads(storage.blobs[job.derived_key])
    assert (
        summary["dry_run"] is True and summary["collection"] == "delta" and summary["attributes"]["ingested"] is False
    )
    assert [c["collection"] for c in await ai.list_attr_collections(pg.pool, scope=pg.scope)] == ["gamma"]


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_collections_are_case_insensitive_and_keys_verbatim(pg):
    """`GAMMA` from one provider run and `gamma` from another are one collection, keyed lower-case;
    attribute keys keep their case and a UDA's leading colon."""
    upper = _nodes("gamma", 20, extra={":HoldDesc": "Hold A"})
    first = await _ingest(pg, "GAMMA", upper)
    assert first["collection"] == "gamma"
    second = await _ingest(pg, "gamma", upper, revision="r2")  # same partition, replaced
    assert second["partition"] == first["partition"]
    cols = await ai.list_attr_collections(pg.pool, scope=pg.scope)
    assert [(c["collection"], c["revision"]) for c in cols] == [("gamma", "r2")]

    for name in ("GAMMA", "Gamma", "gamma"):
        res = await ai.search_attributes(
            pg.pool,
            scope=pg.scope,
            query=ai.parse_search_query({"collections": [name], "where": [{"key": ":HoldDesc", "eq": "Hold A"}]}),
        )
        assert len(res["rows"]) == 20 and res["counts"] == {f"{pg.provider}/gamma": 20}
        assert {r["collection"] for r in res["rows"]} == {"gamma"}
        assert await ai.list_attr_keys(pg.pool, scope=pg.scope, collection=name) == sorted(
            [":HoldDesc", "DESC", "FUNC", "OWNER", "PURP", "TYPE", "WEIGHT"]
        )
        vals = await ai.list_attr_values(pg.pool, scope=pg.scope, key=":HoldDesc", collection=name)
        assert vals == [{"value": "Hold A", "count": 20}]
    # keys are case-sensitive: `:holddesc` is a different key
    res = await ai.search_attributes(
        pg.pool, scope=pg.scope, query=ai.parse_search_query({"where": [{"key": ":holddesc", "eq": "Hold A"}]})
    )
    assert res["rows"] == []
