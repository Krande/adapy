"""The reference data (migration 049): parse, ingest, the browser's query and its usage counts, and
which sets a scope reads (its own, and the provider store's through a grant).

The live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set) ingest synthetic sets into
throwaway scopes and drop everything they made.
"""

from __future__ import annotations

import gzip
import inspect
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest

from ada.comms.rest import db as dbm
from ada.comms.rest.db import attribute_index as ai
from ada.comms.rest.db import reference_data as rd

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

ROWS = [
    {"kind": "spec", "ref": "/SEC", "name": "SEC", "owner": "/SPWL", "use": "section", "attrs": {"purpose": "STL"}},
    {"kind": "spco", "ref": "/SEC/HEA200", "owner": "/SEC", "use": "section",
     "attrs": {"gtype": "BEAM", "plines": ["NA", "TOS"]}},
    {"kind": "spco", "ref": "/SEC/HEA300", "owner": "/SEC", "use": "section", "attrs": {"plines": ["NA"]}},
    {"kind": "spco", "ref": "/SEC/HEA300", "owner": "/SEC", "use": "section", "attrs": {"repeat": True}},
    {"kind": "spco", "ref": "/PLT/PL10", "owner": "/PLT", "use": "plate",
     "attrs": {"params": [{"key": "THK", "value": 10.0}]}},
    {"kind": "material", "ref": "/STEEL-A", "name": "STEEL-A", "use": "material", "attrs": {"density": 7850.0}},
    {"kind": "material", "ref": "/STEEL-B", "use": "material", "attrs": {"description": "unused grade"}},
    {"kind": "BAD KIND", "ref": "/X"},
    {"kind": "spco", "ref": "  "},
]  # fmt: skip


def _bytes(provider: str, collection: str, rows: list[dict], revision: str = "r1") -> bytes:
    header = {"schema": rd.REFERENCE_DATA_SCHEMA, "provider": provider, "collection": collection, "revision": revision}
    return gzip.compress(("\n".join(json.dumps(x) for x in [header, *rows]) + "\n").encode("utf-8"))


async def _chunks(data: bytes, size: int = 1024):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def test_a_batch_keeps_usable_lines_and_counts_the_rest():
    records, skipped = rd.parse_reference_batch(
        [json.dumps(r).encode() for r in ROWS], scope="s", provider="p", collection="c"
    )
    assert skipped == 2, "a kind that is not a word and a blank ref"
    assert [r[3] for r in records][:2] == ["spec", "spco"]
    assert records[1][4] == "/SEC/HEA200", "the ref is kept verbatim"
    assert json.loads(records[1][8]) == {"gtype": "BEAM", "plines": ["NA", "TOS"]}


def test_the_header_is_checked():
    with pytest.raises(rd.ReferenceDataError):
        rd.parse_reference_header(json.dumps({"schema": "other", "collection": "c"}).encode())
    with pytest.raises(rd.ReferenceDataError):
        rd.parse_reference_header(json.dumps({"schema": rd.REFERENCE_DATA_SCHEMA}).encode())
    header = rd.parse_reference_header(json.dumps({"schema": rd.REFERENCE_DATA_SCHEMA, "collection": "Col"}).encode())
    assert header["collection"] == "col"


def test_the_query_is_parsed_and_bounded():
    q = rd.parse_reference_query({"provider": "p", "collection": "Col", "kind": "spco", "limit": "5000", "usage": "1"})
    assert q.collection == "col" and q.kind == "spco" and q.limit == rd.QUERY_MAX_LIMIT and q.usage is True
    for bad in ({"collection": "c"}, {"provider": "p", "collection": "c", "kind": "Not A Word"},
                {"provider": "p", "collection": "c", "limit": "0"}):  # fmt: skip
        with pytest.raises(rd.ReferenceQueryError):
            rd.parse_reference_query(bad)


def test_the_publish_job_and_the_apply_changes_route_agree_it_is_a_core_role():
    from ada.comms.rest.formats.asset_publish import _CORE_ROLES
    from ada.comms.rest.routes.asset_apply_changes import CORE_ROLES

    assert rd.REFERENCE_DATA_ROLE in _CORE_ROLES and rd.REFERENCE_DATA_ROLE in CORE_ROLES


def with_pg(fn):
    params = list(inspect.signature(fn).parameters.values())[1:]

    async def run(**kwargs):
        pool = await dbm.init_pool(POSTGRES_URL)
        scope = f"projects/test-ref-{uuid.uuid4().hex[:8]}"
        store = f"projects/test-ref-store-{uuid.uuid4().hex[:8]}"
        provider = f"test-{uuid.uuid4().hex[:6]}"
        try:
            await fn(SimpleNamespace(pool=pool, scope=scope, store=store, provider=provider), **kwargs)
        finally:
            for s in (scope, store):
                await pool.execute("DELETE FROM reference_sets WHERE scope = $1", s)
                for r in await pool.fetch("SELECT provider, collection FROM attr_collections WHERE scope = $1", s):
                    await ai.drop_attr_collection(pool, scope=s, provider=r["provider"], collection=r["collection"])
            left = await pool.fetchval(
                "SELECT COUNT(*) FROM reference_items WHERE scope = ANY($1::text[])", [scope, store]
            )
            await dbm.close_pool(pool)
            assert left == 0

    run.__name__ = fn.__name__
    run.__signature__ = inspect.Signature(params)
    return run


async def _ingest(pg, scope: str, collection: str = "Col", rows=ROWS, revision: str = "r1") -> dict:
    return await rd.ingest_reference_data(
        pg.pool, scope=scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, collection, rows, revision))
    )


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_ingest_replaces_the_set_and_the_query_filters_and_pages(pg):
    res = await _ingest(pg, pg.scope)
    assert res["collection"] == "col" and res["rows"] == 6 and res["skipped"] == 2
    assert res["counts"] == {"spec": 1, "spco": 3, "material": 2}
    assert any("repeated" in w for w in res["warnings"])

    def q(**kw):
        return rd.parse_reference_query({"provider": pg.provider, "collection": "col", **kw})

    page = await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(kind="spco"))
    assert [i["ref"] for i in page["items"]] == ["/PLT/PL10", "/SEC/HEA200", "/SEC/HEA300"]
    assert page["items"][2]["attrs"] == {"plines": ["NA"]}, "a repeat keeps its first line"
    assert page["facets"]["kinds"] == {"spec": 1, "spco": 3, "material": 2}
    assert page["facets"]["uses"]["section"] == 3 and page["usage"] is None

    assert [
        i["ref"] for i in (await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(use="plate")))["items"]
    ] == ["/PLT/PL10"]
    assert (await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(prefix="/SEC/")))["total"] == 2
    assert (await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(owner="/SEC")))["total"] == 2
    # `q` reads the attributes too, in any case.
    assert [
        i["ref"] for i in (await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(q="UNUSED")))["items"]
    ] == ["/STEEL-B"]
    first = await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(limit=4))
    assert len(first["items"]) == 4 and first["next_offset"] == 4 and first["total"] == 6
    rest = await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(limit=4, offset=4))
    assert len(rest["items"]) == 2 and rest["next_offset"] is None

    # A new ingest replaces the set whole.
    res = await _ingest(pg, pg.scope, rows=ROWS[:1], revision="r2")
    page = await rd.query_reference_items(pg.pool, scope=pg.scope, query=q())
    assert page["total"] == 1 and page["set"]["revision"] == "r2"
    nothing = await rd.query_reference_items(pg.pool, scope=pg.scope, query=q(**{"collection": "other"}))
    assert nothing["set"] is None and nothing["items"] == []


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_usage_is_counted_from_the_attribute_index_and_unused_entries_are_listed(pg):
    await _ingest(pg, pg.scope)
    nodes = [
        {"node_id": "g1", "full_name": "/S/G1", "kind": "GENSEC", "depth": 3, "attrs": {"SPRE": "/SEC/HEA200", "MATR": "/STEEL-A"}},
        {"node_id": "g2", "full_name": "/S/G2", "kind": "GENSEC", "depth": 3, "attrs": {"SPRE": "/SEC/HEA200", "MATR": "/STEEL-A"}},
        {"node_id": "s1", "full_name": "/S/S1", "kind": "SCTN", "depth": 3, "attrs": {"SPRE": "/SEC/HEA200"}},
        {"node_id": "p1", "full_name": "/S/P1", "kind": "PANEL", "depth": 3, "attrs": {"SPRE": "/PLT/PL10", "MATR": "/STEEL-A"}},
    ]  # fmt: skip
    header = {"schema": ai.ATTRIBUTE_INDEX_SCHEMA, "provider": pg.provider, "collection": "col", "revision": "a1"}
    data = gzip.compress(("\n".join(json.dumps(x) for x in [header, *nodes]) + "\n").encode())
    await ai.ingest_attribute_index(pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(data))

    page = await rd.query_reference_items(
        pg.pool,
        scope=pg.scope,
        query=rd.parse_reference_query({"provider": pg.provider, "collection": "col", "usage": "true"}),
    )
    used = {i["ref"]: i["used"] for i in page["items"]}
    assert page["usage"]["indexed"] is True
    assert used["/SEC/HEA200"] == {"count": 3, "by_kind": {"GENSEC": 2, "SCTN": 1}}
    assert used["/PLT/PL10"] == {"count": 1, "by_kind": {"PANEL": 1}}
    assert used["/STEEL-A"]["count"] == 3
    # Listed although nothing uses them: the point of the set.
    assert used["/SEC/HEA300"] == {"count": 0, "by_kind": {}} and used["/STEEL-B"]["count"] == 0
    assert used["/SEC"] is None, "no attribute names a spec"


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_a_scope_reads_the_stores_set_through_a_grant_and_it_shadows_its_own(pg):
    await _ingest(pg, pg.scope, rows=ROWS[:1], revision="own")
    await _ingest(pg, pg.store, revision="store")
    await _ingest(pg, pg.store, collection="ungranted", revision="store")

    def keyed(sets):
        return {(s["collection"], s["scope"]) for s in sets}

    # No grant: the scope's own set only.
    sets = await rd.list_reference_sets(pg.pool, scopes=[pg.scope], store=pg.store, granted=[])
    assert keyed(sets) == {("col", pg.scope)}
    # A grant of the collection: the store's set, shadowing the scope's copy; the other stays out.
    sets = await rd.list_reference_sets(pg.pool, scopes=[pg.scope], store=pg.store, granted=[(pg.provider, "col")])
    assert keyed(sets) == {("col", pg.store)}
    # A provider-wide grant covers every collection of the provider.
    sets = await rd.list_reference_sets(pg.pool, scopes=[pg.scope], store=pg.store, granted=[(pg.provider, None)])
    assert keyed(sets) == {("col", pg.store), ("ungranted", pg.store)}
    # The store reading itself sees everything it holds.
    sets = await rd.list_reference_sets(pg.pool, scopes=[pg.store], store=pg.store, granted=[])
    assert keyed(sets) == {("col", pg.store), ("ungranted", pg.store)}
