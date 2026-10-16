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
from datetime import datetime
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


def test_negative_filters_are_the_exact_complement_of_the_positive_ones():
    body = {
        "where": [
            {"key": "TYPE", "eq": "EQUI"},
            {"key": "W", "ne": "3"},
            {"key": "F", "not_in": ["MS", True]},
            {"key": "H", "exists": True},
            {"key": "G", "missing": True},
        ]
    }
    sql, args = ai.build_filters(ai.parse_search_query(body))
    assert sql == (
        "attrs @> $1::jsonb AND attrs ? $2::text AND NOT (attrs ? $3::text)"
        " AND NOT (attrs @> $4::jsonb OR attrs @> $5::jsonb)"
        " AND NOT (attrs @> $6::jsonb OR attrs @> $7::jsonb OR attrs @> $8::jsonb)"
    )
    # ne "3" excludes both spellings eq "3" matches; not_in true excludes the text "true" too
    assert [json.loads(a) for a in args[3:5]] == [{"W": "3"}, {"W": 3}]
    assert [json.loads(a) for a in args[5:]] == [{"F": "MS"}, {"F": True}, {"F": "true"}]
    assert args[1:3] == ["H", "G"]
    eq_sql, _ = ai.build_filters(ai.parse_search_query({"where": [{"key": "W", "eq": "3"}]}))
    ne_sql, _ = ai.build_filters(ai.parse_search_query({"where": [{"key": "W", "ne": "3"}]}))
    assert ne_sql == f"NOT {eq_sql}"
    single, _ = ai.build_filters(ai.parse_search_query({"where": [{"key": "W", "ne": "x"}]}))
    assert single == "NOT (attrs @> $1::jsonb)"


def test_exclusions_are_parsed_lower_cased_and_bound_into_the_cursor():
    q = ai.parse_search_query({"exclude_collections": ["ALPHA", "alpha", "Beta"], "exclude_providers": "p"})
    assert q.exclude_collections == ["alpha", "beta"] and q.exclude_providers == ["p"]
    assert ai.build_filters(q) == ("TRUE", [])  # exclusions pick partitions, not rows
    base = ai.parse_search_query(CANONICAL)
    fps = {
        base.fingerprint(),
        ai.parse_search_query({**CANONICAL, "exclude_collections": ["alpha"]}).fingerprint(),
        ai.parse_search_query({**CANONICAL, "exclude_providers": ["p"]}).fingerprint(),
        ai.parse_search_query({**CANONICAL, "where": [{"key": "TYPE", "ne": "EQUI"}]}).fingerprint(),
        ai.parse_search_query({**CANONICAL, "where": [{"key": "TYPE", "exists": True}]}).fingerprint(),
    }
    assert len(fps) == 5
    # no exclusions: the fingerprint is what it was before they existed (live cursors stay valid)
    assert ai.parse_search_query({**CANONICAL, "exclude_collections": []}).fingerprint() == base.fingerprint()


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
        {"where": [{"key": "A", "ne": "x", "eq": "x"}]},
        {"where": [{"key": "A", "not_in": []}]},
        {"where": [{"key": "A", "ne": ["x"]}]},
        {"where": [{"key": "A", "exists": False}]},
        {"where": [{"key": "A", "missing": "yes"}]},
        {"exclude_collections": [""]},
        {"exclude_providers": [3]},
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
async def test_negative_filters_presence_and_exclusions(pg):
    """ne / not_in also match nodes lacking the key and exclude both spellings of a value;
    exists / missing test the key; excluded partitions are neither counted nor searched, and a
    cursor does not survive a change of exclusions."""
    alpha = _nodes("alpha", 40, seed=3, extra={"FLAG": True})
    beta = _nodes("beta", 30, seed=4, extra={"FLAG": "true"})
    gamma = _nodes("gamma", 20, seed=5)
    for name, nodes in (("alpha", alpha), ("beta", beta), ("gamma", gamma)):
        await _ingest(pg, name, nodes)
    everything = {"alpha": alpha, "beta": beta, "gamma": gamma}

    async def ids(body: dict) -> set[tuple[str, str]]:
        rows, pages = await _all_pages(pg, body, 1000)
        assert sum(pages[0]["counts"].values()) == len(rows)
        return {(r["collection"], r["node_id"]) for r in rows}

    def want(pred, only=("alpha", "beta", "gamma")) -> set[tuple[str, str]]:
        return {(c, n["node_id"]) for c in only for n in everything[c] if pred(n["attrs"])}

    # FLAG is a bool in ALPHA and the text "true" in BETA: eq matches both, ne neither -- only the
    # nodes without a FLAG (sites, zones, all of GAMMA)
    for v in (True, "true"):
        assert await ids({"where": [{"key": "FLAG", "eq": v}]}) == want(lambda a: "FLAG" in a)
        assert await ids({"where": [{"key": "FLAG", "ne": v}]}) == want(lambda a: "FLAG" not in a)
    # a number and its text spelling: ne 7 / ne "7" drop WEIGHT 7 from every collection, keep the rest
    for v in (7, "7"):
        assert await ids({"where": [{"key": "WEIGHT", "ne": v}]}) == want(lambda a: a.get("WEIGHT") != 7)
    # not_in with a positive filter beside it: the normal, index-led case
    body = {"where": [{"key": "TYPE", "eq": "EQUI"}, {"key": "FUNC", "not_in": ["MS", "PS"]}]}
    assert await ids(body) == want(lambda a: a.get("TYPE") == "EQUI" and a.get("FUNC") not in ("MS", "PS"))
    body = {"where": [{"key": "FUNC", "not_in": ["MS", "PS"]}]}
    assert await ids(body) == want(lambda a: a.get("FUNC") not in ("MS", "PS"))  # incl. keyless nodes
    # key presence
    assert await ids({"where": [{"key": "OWNER", "exists": True}]}) == want(lambda a: "OWNER" in a)
    assert await ids({"where": [{"key": "OWNER", "missing": True}]}) == want(lambda a: "OWNER" not in a)

    # exclusions: case-insensitive, never counted
    q = {"where": [{"key": "TYPE", "eq": "PIPE"}], "exclude_collections": ["BETA"]}
    res = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query(q))
    assert set(res["counts"]) == {f"{pg.provider}/alpha", f"{pg.provider}/gamma"}
    assert await ids(q) == want(lambda a: a.get("TYPE") == "PIPE", only=("alpha", "gamma"))
    # an exclusion beats an inclusion
    q2 = {"collections": ["alpha", "beta"], "exclude_collections": ["alpha"]}
    res = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query(q2))
    assert res["counts"] == {f"{pg.provider}/beta": len(beta)}
    res = await ai.search_attributes(
        pg.pool, scope=pg.scope, query=ai.parse_search_query({"exclude_providers": [pg.provider]})
    )
    assert res["rows"] == [] and res["counts"] == {}

    # a cursor from one set of exclusions is refused under another
    first = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query({**q, "limit": 3}))
    assert first["next_cursor"]
    with pytest.raises(ai.SearchQueryError, match="different query"):
        await ai.search_attributes(
            pg.pool,
            scope=pg.scope,
            query=ai.parse_search_query({**q, "exclude_collections": ["gamma"], "cursor": first["next_cursor"]}),
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


# --- shares (migration 039) ---------------------------------------------------------------------


def test_scope_wire_names_a_user_scope_only_to_its_owner():
    assert ai.scope_wire("shared") == "shared"
    assert ai.scope_wire("projects/p1") == "project:p1"
    assert ai.scope_wire("corpus/c1") == "corpus:c1"
    assert ai.scope_wire("users/u1", viewer="u1") == "user:me"
    assert ai.scope_wire("users/u1", viewer="u2") is None
    assert ai.scope_wire("users/u1") is None
    assert ai.scope_wire("nonsense") is None


def test_scope_prefixes_parse_back_and_nothing_else_does():
    for s in (Scope.shared(), Scope.project("p1"), Scope.user("u1"), Scope.corpus("c1")):
        assert Scope.from_prefix(s.prefix()) == s
    for bad in ("", "projects", "projects/", "project/p1", "users/a/b", "shared/x", " shared", "projects/ p1"):
        with pytest.raises(ValueError):
            Scope.from_prefix(bad)


def test_counts_keys_of_own_partitions_are_unchanged_and_shared_ones_name_their_origin():
    own = {"scope": "projects/p1", "provider": "prov", "collection": "alpha", "shared": False}
    shared = {**own, "scope": "shared", "shared": True}
    assert ai._label(own) == "prov/alpha"
    assert ai._label(shared) == "prov/alpha@shared"
    # own before shared, within one (provider, collection)
    assert sorted([shared, own], key=ai._order) == [own, shared]


def test_a_cursor_carries_its_origin_and_one_without_it_still_decodes():
    q = ai.parse_search_query(CANONICAL)
    fp = q.fingerprint()
    old = ai.encode_cursor({"v": 1, "f": fp, "p": "prov", "c": "alpha", "n": "id-1", "counts": {}})
    assert "s" not in ai.decode_cursor(old, fp)
    new = ai.encode_cursor({"v": 1, "f": fp, "p": "prov", "c": "alpha", "s": "shared", "n": "id-1", "counts": {}})
    assert ai.decode_cursor(new, fp)["s"] == "shared"
    with pytest.raises(ai.SearchQueryError):
        ai.decode_cursor(ai.encode_cursor({"v": 1, "f": fp, "p": "p", "c": "c", "s": 3, "n": "n"}), fp)


def test_an_export_refuses_hits_shared_in_from_another_scope():
    from ada.comms.rest.routes import asset_exports as ex

    scope = Scope.project("p1")
    ex.refuse_foreign_hits([{"node_id": "a"}, {"node_id": "b", "scope": "projects/p1"}], scope)
    with pytest.raises(ex.ExportRefused, match="from shared"):
        ex.refuse_foreign_hits([{"node_id": "a", "scope": "shared"}], scope)


@pytest.mark.asyncio
async def test_share_requests_are_checked():
    from fastapi import HTTPException

    from ada.comms.rest.routes import admin_db

    ok = {"scope": "projects/p1", "provider": "prov", "collection": "alpha", "target_scope": "shared"}
    assert await admin_db.parse_share(None, ok) == ok
    assert (await admin_db.parse_share(None, {**ok, "target_scope": "users/u1"}))["target_scope"] == "users/u1"
    for bad, status in (
        ({**ok, "target_scope": ""}, 400),
        ({**ok, "target_scope": "project:p1"}, 400),  # a wire form, not a stored prefix
        ({**ok, "target_scope": "corpus/c1"}, 400),
        ({**ok, "target_scope": "projects/p1"}, 400),  # its own scope
        ({**ok, "target_scope": "projects/not-a-uuid"}, 400),
        ({**ok, "scope": "nowhere"}, 400),
        ({**ok, "provider": None}, 400),
    ):
        with pytest.raises(HTTPException) as err:
            await admin_db.parse_share(None, bad)
        assert err.value.status_code == status, bad


async def _ingest_into(pg, scope, collection, nodes, **kw):
    return await ai.ingest_attribute_index(
        pg.pool,
        scope=scope,
        provider=pg.provider,
        chunks=_chunks(_index_bytes(pg.provider, collection, nodes, **kw), 64 * 1024),
        batch_rows=500,
    )


async def _search_all(pg, body: dict, limit: int, **kw) -> tuple[list[dict], list[dict]]:
    rows, pages, cursor = [], [], None
    while True:
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({**body, "limit": limit, "cursor": cursor}), **kw
        )
        pages.append(res)
        rows += res["rows"]
        cursor = res["next_cursor"]
        if cursor is None:
            return rows, pages


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_shared_partitions_are_searched_beside_the_own_ones_while_their_origin_is_reachable(pg):
    """A partition shared into the scope is read where it lies, kept apart from an own partition of
    the same name, paged in order, counted under its own key -- and only for a caller who can still
    reach the scope it was shared from."""
    tag = uuid.uuid4().hex[:8]
    member, outsider = f"test-share-{tag}-a", f"test-share-{tag}-b"
    project = await dbm.create_project(pg.pool, f"test-share-{tag}", f"test share {tag}")
    plant_a = f"projects/{project['id']}"
    personal = f"users/{member}"
    try:
        await dbm.add_project_member(pg.pool, project["id"], member)
        own_alpha = _nodes("alpha", 30, seed=11)
        await _ingest_into(pg, pg.scope, "alpha", own_alpha)
        # The same collection name in the project, with other nodes; and one more of its own.
        theirs = {"alpha": _nodes("alpha", 45, seed=12), "beta": _nodes("beta", 25, seed=13)}
        for name, nodes in theirs.items():
            await _ingest_into(pg, plant_a, name, nodes)
        mine = _nodes("gamma", 10, seed=14)
        await _ingest_into(pg, personal, "gamma", mine)

        assert await ai.share_attr_collection(
            pg.pool, scope=plant_a, provider=pg.provider, collection="missing", target_scope=pg.scope
        ) is None  # fmt: skip
        with pytest.raises(ValueError):
            await ai.share_attr_collection(
                pg.pool, scope=pg.scope, provider=pg.provider, collection="alpha", target_scope=pg.scope
            )
        for scope, name in ((plant_a, "ALPHA"), (plant_a, "beta"), (personal, "gamma")):
            share = await ai.share_attr_collection(
                pg.pool, scope=scope, provider=pg.provider, collection=name, target_scope=pg.scope, shared_by="t"
            )
            assert share["collection"] == name.lower() and share["target_scope"] == pg.scope
        again = await ai.share_attr_collection(
            pg.pool, scope=plant_a, provider=pg.provider, collection="beta", target_scope=pg.scope, shared_by="x"
        )
        assert again["shared_by"] == "t"  # idempotent: the first share stands
        listed = await ai.list_attr_shares(pg.pool, target_scope=pg.scope)
        assert {(s["scope"], s["collection"]) for s in listed} == {
            (plant_a, "alpha"),
            (plant_a, "beta"),
            (personal, "gamma"),
        }
        assert len(await ai.list_attr_shares(pg.pool, scope=plant_a, collection="ALPHA")) == 1

        # Who may read what: the member reaches the project and their own scope, the outsider neither.
        assert await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=member) == sorted([plant_a, personal])
        assert await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=outsider) == []
        origins = await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=member)

        # Everything, paged in 7s: each node once, own alpha before the project's, both kept apart.
        rows, pages = await _search_all(pg, {}, 7, shared_from=origins, viewer=member)
        everything = len(own_alpha) + sum(map(len, theirs.values())) + len(mine)
        assert len(rows) == everything and len(pages) == -(-everything // 7)
        keys = [(r["provider"], r["collection"], r["shared"], r["scope"], r["node_id"]) for r in rows]
        assert len(set(keys)) == len(keys) and keys == sorted(keys)
        alpha_from = [r["scope"] for r in rows if r["collection"] == "alpha"]
        assert alpha_from == [pg.scope] * len(own_alpha) + [plant_a] * len(theirs["alpha"])
        p = pg.provider
        assert pages[0]["counts"] == {
            f"{p}/alpha": len(own_alpha),
            f"{p}/alpha@{plant_a}": len(theirs["alpha"]),
            f"{p}/beta@{plant_a}": len(theirs["beta"]),
            f"{p}/gamma@{personal}": len(mine),
        }
        assert all(page["counts"] == pages[0]["counts"] for page in pages)
        parts = {x["key"]: x for x in pages[0]["partitions"]}
        assert parts[f"{p}/alpha@{plant_a}"] == {
            "key": f"{p}/alpha@{plant_a}",
            "provider": p,
            "collection": "alpha",
            "scope": plant_a,
            "scope_wire": f"project:{project['id']}",
            "shared": True,
            "count": len(theirs["alpha"]),
        }
        assert parts[f"{p}/alpha"]["shared"] is False and parts[f"{p}/alpha"]["scope"] == pg.scope
        gamma = next(r for r in rows if r["collection"] == "gamma")
        assert gamma["scope"] == personal and gamma["scope_wire"] == "user:me" and gamma["shared"] is True
        assert [x["kind"] for x in gamma["path"]] == ["SITE", "ZONE"]  # the path walks its own partition

        # Filters apply to shared partitions too, and a filtered count is per partition.
        q = {"collections": ["alpha"], "where": [{"key": "TYPE", "eq": "EQUI"}]}
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({**q, "limit": 1000}), shared_from=origins
        )
        assert res["counts"] == {
            f"{p}/alpha": sum(_matches(n, q) for n in own_alpha),
            f"{p}/alpha@{plant_a}": sum(_matches(n, q) for n in theirs["alpha"]),
        }
        cols = await ai.list_attr_collections(pg.pool, scope=pg.scope, shared_from=origins, viewer=member)
        assert [(c["collection"], c["scope"], c["shared"]) for c in cols] == [
            ("alpha", pg.scope, False),
            ("alpha", plant_a, True),
            ("beta", plant_a, True),
            ("gamma", personal, True),
        ]
        assert "OWNER" in await ai.list_attr_keys(pg.pool, scope=pg.scope, collection="beta", shared_from=origins)
        assert await ai.list_attr_keys(pg.pool, scope=pg.scope, collection="beta") == []
        vals = await ai.list_attr_values(pg.pool, scope=pg.scope, key="OWNER", shared_from=origins)
        assert vals == [{"value": "TEAM", "count": 4 * 4}]  # four zones in each of four partitions

        # Without the origins -- every caller from before shares, and the outsider -- only the own rows.
        own_only = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 1000}))
        assert own_only["counts"] == {f"{p}/alpha": len(own_alpha)}
        assert {r["scope"] for r in own_only["rows"]} == {pg.scope}

        # Leaving the project hides its shares at the next request; the personal one stays.
        await dbm.remove_project_member(pg.pool, project["id"], member)
        origins = await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=member)
        assert origins == [personal]
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 1000}), shared_from=origins
        )
        assert set(res["counts"]) == {f"{p}/alpha", f"{p}/gamma@{personal}"}

        # Unsharing removes one; dropping a partition removes its shares with it.
        assert await ai.unshare_attr_collection(
            pg.pool, scope=personal, provider=p, collection="gamma", target_scope=pg.scope
        )
        assert not await ai.unshare_attr_collection(
            pg.pool, scope=personal, provider=p, collection="gamma", target_scope=pg.scope
        )
        await ai.drop_attr_collection(pg.pool, scope=plant_a, provider=p, collection="beta")
        left = await ai.list_attr_shares(pg.pool, target_scope=pg.scope)
        assert [(s["scope"], s["collection"]) for s in left] == [(plant_a, "alpha")]
        # A reingest of the origin keeps its share (the catalogue row is updated, not replaced).
        await _ingest_into(pg, plant_a, "alpha", theirs["alpha"][:10], revision="r2")
        assert len(await ai.list_attr_shares(pg.pool, scope=plant_a)) == 1
    finally:
        for scope in (plant_a, personal):
            for r in await pg.pool.fetch("SELECT provider, collection FROM attr_collections WHERE scope = $1", scope):
                await ai.drop_attr_collection(pg.pool, scope=scope, provider=r["provider"], collection=r["collection"])
        await pg.pool.execute("DELETE FROM attr_shares WHERE target_scope = $1", pg.scope)
        await pg.pool.execute("DELETE FROM project_members WHERE project_id = $1", uuid.UUID(project["id"]))
        await pg.pool.execute("DELETE FROM projects WHERE id = $1", uuid.UUID(project["id"]))
        with contextlib.suppress(Exception):  # a placeholder row; another table may have referenced it
            await pg.pool.execute("DELETE FROM users WHERE sub = $1", member)


# --- group shares (migration 040) and one copy per revision ---------------------------------------


async def _drop_provider_everywhere(pg) -> None:
    """Every partition and group share of the test's provider, the shared scope's included."""
    for r in await pg.pool.fetch("SELECT scope, collection FROM attr_collections WHERE provider = $1", pg.provider):
        await ai.drop_attr_collection(pg.pool, scope=r["scope"], provider=pg.provider, collection=r["collection"])
    await pg.pool.execute("DELETE FROM attr_provider_shares WHERE provider = $1", pg.provider)


def _own(pool, scope, provider):
    return pool.fetch(
        "SELECT collection, revision FROM attr_collections WHERE scope = $1 AND provider = $2 ORDER BY 1",
        scope,
        provider,
    )


async def _targets(pg, scope, collection) -> set[str]:
    return {s["target_scope"] for s in await ai.list_attr_shares(pg.pool, scope=scope, provider=pg.provider,
                                                                 collection=collection)}  # fmt: skip


@pytest.mark.asyncio
async def test_group_share_and_fold_requests_are_checked():
    from fastapi import HTTPException

    from ada.comms.rest.routes import admin_db

    ok = {"scope": "projects/p1", "provider": "prov", "target_scope": "shared"}
    assert await admin_db.parse_share(None, ok, admin_db.GROUP_SHARE_NAMES) == ok
    for bad in ({**ok, "target_scope": "projects/p1"}, {**ok, "provider": ""}, {**ok, "target_scope": "corpus/c1"}):
        with pytest.raises(HTTPException) as err:
            await admin_db.parse_share(None, bad, admin_db.GROUP_SHARE_NAMES)
        assert err.value.status_code == 400, bad

    fold = {"provider": "prov", "keep_scope": "shared", "drop_scope": "projects/p1"}
    assert admin_db.parse_fold(fold) == {**fold, "collections": None, "dry_run": True}
    assert admin_db.parse_fold({**fold, "dry_run": "no", "collections": ["a"]})["dry_run"] is True
    assert admin_db.parse_fold({**fold, "dry_run": False})["dry_run"] is False
    for bad in ({**fold, "drop_scope": "nowhere"}, {**fold, "collections": "alpha"}, {**fold, "provider": None}):
        with pytest.raises(HTTPException) as err:
            admin_db.parse_fold(bad)
        assert err.value.status_code == 400, bad


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_a_group_share_covers_collections_ingested_later_and_its_removal_keeps_explicit_shares(pg):
    tag = uuid.uuid4().hex[:8]
    member, outsider = f"test-gshare-{tag}-a", f"test-gshare-{tag}-b"
    project = await dbm.create_project(pg.pool, f"test-gshare-{tag}", f"test group share {tag}")
    plant_a = f"projects/{project['id']}"
    p = pg.provider
    try:
        await dbm.add_project_member(pg.pool, project["id"], member)
        await _ingest_into(pg, plant_a, "alpha", _nodes("alpha", 12, seed=21))
        assert (
            await ai.share_attr_provider(pg.pool, scope=plant_a, provider=f"none-{tag}", target_scope=pg.scope) is None
        )
        with pytest.raises(ValueError):
            await ai.share_attr_provider(pg.pool, scope=plant_a, provider=p, target_scope=plant_a)
        first = await ai.share_attr_provider(pg.pool, scope=plant_a, provider=p, target_scope=pg.scope, shared_by="t")
        again = await ai.share_attr_provider(pg.pool, scope=plant_a, provider=p, target_scope=pg.scope, shared_by="x")
        assert first["shared_by"] == again["shared_by"] == "t"
        assert [s["target_scope"] for s in await ai.list_attr_provider_shares(pg.pool, scope=plant_a)] == [pg.scope]

        # A collection the next sweep adds is covered without another share.
        await _ingest_into(pg, plant_a, "beta", _nodes("beta", 8, seed=22))
        assert await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=member) == [plant_a]
        assert await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub=outsider) == []
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 1000}), shared_from=[plant_a]
        )
        assert res["counts"] == {f"{p}/alpha@{plant_a}": 17, f"{p}/beta@{plant_a}": 13}
        own_only = await ai.search_attributes(pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 10}))
        assert own_only["counts"] == {}

        # Removing the group share leaves beta's own share.
        await ai.share_attr_collection(pg.pool, scope=plant_a, provider=p, collection="beta", target_scope=pg.scope)
        assert await ai.unshare_attr_provider(pg.pool, scope=plant_a, provider=p, target_scope=pg.scope)
        assert not await ai.unshare_attr_provider(pg.pool, scope=plant_a, provider=p, target_scope=pg.scope)
        res = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 1000}), shared_from=[plant_a]
        )
        assert res["counts"] == {f"{p}/beta@{plant_a}": 13}
        assert await ai.list_attr_provider_shares(pg.pool, scope=plant_a) == []
    finally:
        await _drop_provider_everywhere(pg)
        await pg.pool.execute("DELETE FROM project_members WHERE project_id = $1", uuid.UUID(project["id"]))
        await pg.pool.execute("DELETE FROM projects WHERE id = $1", uuid.UUID(project["id"]))
        with contextlib.suppress(Exception):
            await pg.pool.execute("DELETE FROM users WHERE sub = $1", member)


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_an_ingest_of_what_the_shared_scope_holds_at_that_revision_shares_it_instead_of_copying(pg):
    p = pg.provider
    other = f"projects/test-attr-{uuid.uuid4().hex[:8]}"
    nodes = _nodes("alpha", 20, seed=31)
    try:
        # The scope's older copy, shared on into another scope.
        await _ingest_into(pg, pg.scope, "alpha", nodes, revision="r0")
        await ai.share_attr_collection(pg.pool, scope=pg.scope, provider=p, collection="alpha", target_scope=other)
        # The shared scope at a newer revision: different revisions are never folded.
        into_shared = await _ingest_into(pg, "shared", "alpha", nodes, revision="r1")
        assert into_shared["deduplicated_into"] is None and into_shared["folded"] == []
        assert [tuple(r) for r in await _own(pg.pool, pg.scope, p)] == [("alpha", "r0")]

        # The scope's next sweep reaches r1: the shared copy is shared in, the old copy folded away.
        res = await _ingest_into(pg, pg.scope, "alpha", nodes, revision="r1")
        assert res["deduplicated_into"] == "shared" and res["rows"] == 0
        assert res["partition"] == into_shared["partition"]
        [fold] = res["folded"]
        assert fold["scope"] == pg.scope and fold["revision"] == "r0" and fold["repointed"] == [other]
        assert fold["bytes_freed"] > 0
        assert await _own(pg.pool, pg.scope, p) == []
        assert await _targets(pg, "shared", "alpha") == {pg.scope, other}
        origins = await ai.readable_share_origins(pg.pool, scope=pg.scope, user_sub="anyone")
        assert origins == ["shared"]  # everyone reaches the shared scope
        found = await ai.search_attributes(
            pg.pool, scope=pg.scope, query=ai.parse_search_query({"limit": 1000}), shared_from=origins
        )
        assert found["counts"] == {f"{p}/alpha@shared": len(nodes)}

        # Again at r1: still nothing copied, nothing left to fold. One the shared scope lacks is copied.
        again = await _ingest_into(pg, pg.scope, "alpha", nodes, revision="r1")
        assert again["deduplicated_into"] == "shared" and again["folded"] == []
        beta = await _ingest_into(pg, pg.scope, "beta", _nodes("beta", 5, seed=32), revision="r1")
        assert beta["deduplicated_into"] is None and beta["rows"] == 10
    finally:
        await _drop_provider_everywhere(pg)


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_an_ingest_into_shared_folds_same_revision_copies_and_repoints_their_shares(pg):
    p = pg.provider
    tag = uuid.uuid4().hex[:8]
    other, third, personal = f"projects/test-attr-{tag}-b", f"projects/test-attr-{tag}-c", f"users/test-attr-{tag}"
    nodes = _nodes("alpha", 15, seed=41)
    try:
        await _ingest_into(pg, pg.scope, "alpha", nodes, revision="r1")
        await _ingest_into(pg, pg.scope, "beta", _nodes("beta", 5, seed=42), revision="r1")
        await _ingest_into(pg, other, "alpha", nodes, revision="r2")  # another revision: left alone
        await ai.share_attr_collection(pg.pool, scope=pg.scope, provider=p, collection="alpha", target_scope=third)
        await ai.share_attr_collection(pg.pool, scope=pg.scope, provider=p, collection="alpha", target_scope="shared")
        await ai.share_attr_provider(pg.pool, scope=pg.scope, provider=p, target_scope=personal)

        res = await _ingest_into(pg, "shared", "alpha", nodes, revision="r1")
        assert res["deduplicated_into"] is None and res["rows"] == len(nodes)
        [fold] = res["folded"]
        assert fold["scope"] == pg.scope and fold["repointed"] == sorted([third, personal])
        assert [tuple(r) for r in await _own(pg.pool, pg.scope, p)] == [("beta", "r1")]
        assert [tuple(r) for r in await _own(pg.pool, other, p)] == [("alpha", "r2")]
        assert await _targets(pg, "shared", "alpha") == {pg.scope, third, personal}
        # The group share stays for what is still the scope's own.
        assert [s["target_scope"] for s in await ai.list_attr_provider_shares(pg.pool, scope=pg.scope)] == [personal]
    finally:
        await _drop_provider_everywhere(pg)


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_one_copy_rule_can_be_switched_off(pg, monkeypatch):
    monkeypatch.setenv(ai.DEDUP_ENV, "0")
    p = pg.provider
    nodes = _nodes("alpha", 6, seed=51)
    try:
        await _ingest_into(pg, "shared", "alpha", nodes, revision="r1")
        res = await _ingest_into(pg, pg.scope, "alpha", nodes, revision="r1")
        assert res["deduplicated_into"] is None and res["rows"] == len(nodes)
        again = await _ingest_into(pg, "shared", "alpha", nodes, revision="r1")
        assert again["folded"] == []
        assert [tuple(r) for r in await _own(pg.pool, pg.scope, p)] == [("alpha", "r1")]
        assert await ai.list_attr_shares(pg.pool, provider=p) == []
    finally:
        await _drop_provider_everywhere(pg)


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_the_admin_fold_keeps_only_the_shared_copy_previews_first_and_repoints_shares(pg):
    p = pg.provider
    other = f"projects/test-attr-{uuid.uuid4().hex[:8]}"
    try:
        for coll, seed in (("alpha", 61), ("beta", 62), ("gamma", 63)):
            await _ingest_into(pg, pg.scope, coll, _nodes(coll, 10, seed=seed), revision="r0")
        for coll, seed in (("alpha", 64), ("beta", 65)):
            await _ingest_into(pg, "shared", coll, _nodes(coll, 12, seed=seed), revision="r1")
        await ai.share_attr_collection(pg.pool, scope=pg.scope, provider=p, collection="alpha", target_scope=other)

        with pytest.raises(ai.FoldRefused):
            await ai.fold_attr_duplicates(pg.pool, provider=p, keep_scope=pg.scope, drop_scope="shared")
        with pytest.raises(ai.FoldRefused):
            await ai.fold_attr_duplicates(pg.pool, provider=p, keep_scope="shared", drop_scope="shared")

        plan = await ai.fold_attr_duplicates(pg.pool, provider=p, keep_scope="shared", drop_scope=pg.scope)
        assert plan["dry_run"] is True and plan["folded"] == 0
        assert [(c["collection"], c["same_revision"], c["repointed"]) for c in plan["collections"]] == [
            ("alpha", False, [other]),
            ("beta", False, []),
        ]
        assert plan["bytes"] > 0 and plan["missing"] == []
        assert len(await _own(pg.pool, pg.scope, p)) == 3  # a dry run changes nothing
        named = await ai.fold_attr_duplicates(
            pg.pool, provider=p, keep_scope="shared", drop_scope=pg.scope, collections=["ALPHA", "nope"]
        )
        assert [c["collection"] for c in named["collections"]] == ["alpha"] and named["missing"] == ["nope"]

        done = await ai.fold_attr_duplicates(
            pg.pool, provider=p, keep_scope="shared", drop_scope=pg.scope, dry_run=False, folded_by="t"
        )
        assert done["folded"] == 2 and done["bytes"] > 0 and done["errors"] == []
        assert [tuple(r) for r in await _own(pg.pool, pg.scope, p)] == [("gamma", "r0")]
        assert await _targets(pg, "shared", "alpha") == {pg.scope, other}
        assert await _targets(pg, "shared", "beta") == {pg.scope}
        assert {s["shared_by"] for s in await ai.list_attr_shares(pg.pool, scope="shared", provider=p)} == {"t"}
    finally:
        await _drop_provider_everywhere(pg)


# --- keeping only the newest copy -----------------------------------------------------------------


def test_the_newest_copy_is_by_numeric_revision_else_by_ingest_and_shared_wins_a_tie():
    def m(scope, rev, at):
        return {"scope": scope, "revision": rev, "ingested_at": at}

    plant_a = "projects/p1"
    # Numbers compare as numbers, whenever they were ingested.
    assert ai.newest_copy([m(plant_a, "10", "2026-01-01"), m("shared", "9", "2026-02-01")])["scope"] == plant_a
    # Opaque tokens: the later ingest carries the later revision.
    assert ai.newest_copy([m(plant_a, "abc", "2026-01-01"), m("shared", "abd", "2026-02-01")])["scope"] == "shared"
    assert ai.newest_copy([m(plant_a, "x2", "2026-03-01"), m("shared", "x1", "2026-02-01")])["scope"] == plant_a
    # The same revision is the same rows: the shared copy is kept, wherever was ingested last.
    assert ai.newest_copy([m(plant_a, "r1", "2026-03-01"), m("shared", "r1", "2026-02-01")])["scope"] == "shared"
    assert ai.newest_copy([m(plant_a, "7", "2026-03-01"), m("shared", "7", "2026-02-01")])["scope"] == "shared"


@pytest.mark.asyncio
async def test_prune_revision_requests_are_checked():
    from fastapi import HTTPException

    from ada.comms.rest.routes import admin_db

    assert admin_db.parse_prune_revisions({}) == {
        "provider": None, "scopes": None, "collections": None, "include_narrowing": False, "dry_run": True,
    }  # fmt: skip
    spec = admin_db.parse_prune_revisions(
        {"scopes": ["shared", "projects/p1"], "include_narrowing": "yes", "dry_run": False}
    )
    assert spec["include_narrowing"] is False and spec["dry_run"] is False  # only `true` opts in
    for bad in ({"scopes": ["shared"]}, {"scopes": ["shared", "nowhere"]}, {"provider": ""}, {"collections": "a"}):
        with pytest.raises(HTTPException) as err:
            admin_db.parse_prune_revisions(bad)
        assert err.value.status_code == 400, bad


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_pruning_older_revisions_folds_toward_shared_and_never_widens_a_narrower_copy(pg, monkeypatch):
    monkeypatch.setenv(ai.DEDUP_ENV, "0")  # set up copies the one-copy rule would not let exist
    p = pg.provider
    other = f"projects/test-attr-{uuid.uuid4().hex[:8]}"

    async def put(scope, coll, rev, day, seed):
        await _ingest_into(pg, scope, coll, _nodes(coll, 6, seed=seed), revision=rev)
        await pg.pool.execute(
            "UPDATE attr_collections SET ingested_at = $4::timestamptz WHERE scope = $1 AND provider = $2 "
            "AND collection = $3",
            scope,
            p,
            coll,
            datetime.fromisoformat(f"2026-10-{day:02d}T00:00:00+00:00"),
        )

    try:
        await put(pg.scope, "alpha", "r1", 1, 71)  # older in the project ...
        await put("shared", "alpha", "r2", 5, 72)  # ... newest in shared: fold
        await put("shared", "beta", "r1", 1, 73)  # older in shared ...
        await put(pg.scope, "beta", "r2", 5, 74)  # ... newest only in the project: narrowing
        await put(pg.scope, "gamma", "r1", 6, 75)  # the same revision, the project ingested last ...
        await put("shared", "gamma", "r1", 2, 76)  # ... the shared copy is kept all the same
        await put(pg.scope, "delta", "r1", 1, 77)  # one copy: not a duplicate
        await ai.share_attr_collection(pg.pool, scope=pg.scope, provider=p, collection="alpha", target_scope=other)

        plan = await ai.prune_attr_revisions(pg.pool, provider=p)
        assert plan["dry_run"] is True
        by = {c["collection"]: c for c in plan["collections"]}
        assert set(by) == {"alpha", "beta", "gamma"}
        assert by["alpha"]["kept"]["scope"] == "shared"
        assert [(d["scope"], d["action"]) for d in by["alpha"]["dropped"]] == [(pg.scope, "fold")]
        assert by["beta"]["kept"]["scope"] == pg.scope and by["beta"]["dropped"] == []
        assert [(s["scope"], s["reason"]) for s in by["beta"]["skipped"]] == [("shared", "narrowing")]
        assert by["gamma"]["kept"]["scope"] == "shared"
        assert plan["dropped"] == 2 and plan["skipped"] == 1 and plan["bytes"] > 0
        assert len(await _own(pg.pool, pg.scope, p)) == 4  # a dry run changes nothing

        done = await ai.prune_attr_revisions(pg.pool, provider=p, dry_run=False, pruned_by="t")
        assert done["dropped"] == 2 and done["skipped"] == 1 and done["errors"] == []
        assert [r["collection"] for r in await _own(pg.pool, pg.scope, p)] == ["beta", "delta"]
        assert await _targets(pg, "shared", "alpha") == {pg.scope, other}  # re-pointed, and shared back in
        assert await _targets(pg, "shared", "gamma") == {pg.scope}
        assert {r["collection"] for r in await _own(pg.pool, "shared", p)} == {"alpha", "beta", "gamma"}

        # Opting in drops the older shared copy -- and shares nothing of the project's anywhere.
        narrowed = await ai.prune_attr_revisions(
            pg.pool, provider=p, scopes=["shared", pg.scope], include_narrowing=True, dry_run=False
        )
        [entry] = narrowed["collections"]
        assert entry["collection"] == "beta" and [(d["scope"], d["action"]) for d in entry["dropped"]] == [
            ("shared", "drop")
        ]
        assert {r["collection"] for r in await _own(pg.pool, "shared", p)} == {"alpha", "gamma"}
        assert await ai.list_attr_shares(pg.pool, scope=pg.scope, provider=p) == []
        assert await ai.prune_attr_revisions(pg.pool, provider=p) == {
            "dry_run": True, "include_narrowing": False, "collections": [], "dropped": 0, "skipped": 0, "bytes": 0,
            "errors": [],
        }  # fmt: skip
    finally:
        await _drop_provider_everywhere(pg)
