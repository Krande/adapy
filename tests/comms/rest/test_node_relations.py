"""The node relations index (migration 044): parse, ingest, batched lookup, cache token, publish hook.

The live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set) ingest synthetic relations into
a throwaway scope and drop everything they made.
"""

from __future__ import annotations

import gzip
import inspect
import json
import os
import tempfile
import time
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest

from ada.comms.rest import db as dbm
from ada.comms.rest.db import node_relations as nr
from ada.comms.rest.selection_export import group_export_derived_key, selection_derived_key

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

ROWS = [
    {"from_node_id": "a", "from_full_name": "/A", "relation": "serves_system", "to_full_name": "/SYS",
     "props": {"system_kind": "duct"}},
    {"from_node_id": "a", "from_full_name": "/A", "relation": "connects_to", "to_node_id": "b", "to_full_name": "/B"},
    {"from_node_id": "a", "from_full_name": "/A", "relation": "connects_to", "to_node_id": "b", "to_full_name": "/B"},
    {"from_node_id": "c", "relation": "fills_opening", "to_full_name": "/OPEN", "props": {"host_full_name": "/H"}},
    {"from_node_id": "d", "relation": "BAD WORD", "to_full_name": "/X"},
    {"from_node_id": "e", "relation": "penetrates"},
]  # fmt: skip


def _bytes(provider: str, collection: str, rows: list[dict], revision: str = "r1") -> bytes:
    header = {"schema": nr.NODE_RELATIONS_SCHEMA, "provider": provider, "collection": collection, "revision": revision}
    return gzip.compress(("\n".join(json.dumps(x) for x in [header, *rows]) + "\n").encode("utf-8"))


async def _chunks(data: bytes, size: int = 1024):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def test_a_batch_keeps_usable_lines_and_counts_the_rest():
    records, skipped = nr.parse_relations_batch(
        [json.dumps(r).encode() for r in ROWS], scope="s", provider="p", collection="c"
    )
    assert skipped == 2, "a bad relation word and a relation with no target"
    assert [r[5] for r in records] == ["serves_system", "connects_to", "connects_to", "fills_opening"]
    assert json.loads(records[0][8]) == {"system_kind": "duct"}


def test_the_header_is_checked():
    with pytest.raises(nr.NodeRelationsError):
        nr.parse_relations_header(json.dumps({"schema": "other", "collection": "c"}).encode())
    assert nr.parse_relations_header(json.dumps({"schema": nr.NODE_RELATIONS_SCHEMA, "collection": "C"}).encode())[
        "collection"
    ] == "c"


def test_the_relations_token_moves_an_ifc_key_and_nothing_else():
    target = {"collection": "c", "subject": "s"}
    base = selection_derived_key("t", target, fmt="ifc", element=None, path=[], filename="x.ifc")
    assert selection_derived_key("t", target, fmt="ifc", element=None, path=[], filename="x.ifc", relations_token=None) == base
    assert selection_derived_key("t", target, fmt="ifc", element=None, path=[], filename="x.ifc", relations_token="r") != base
    step = selection_derived_key("t", target, fmt="step", element=None, path=[], filename="x.step")
    assert selection_derived_key("t", target, fmt="step", element=None, path=[], filename="x.step", relations_token="r") == step
    assert group_export_derived_key("g", "geo", "x.ifc", None, relations_token="r") != group_export_derived_key(
        "g", "geo", "x.ifc", None
    )


def with_pg(fn):
    params = list(inspect.signature(fn).parameters.values())[1:]

    async def run(**kwargs):
        pool = await dbm.init_pool(POSTGRES_URL)
        scope = f"projects/test-rel-{uuid.uuid4().hex[:8]}"
        provider = f"test-{uuid.uuid4().hex[:6]}"
        try:
            await fn(SimpleNamespace(pool=pool, scope=scope, provider=provider), **kwargs)
        finally:
            await pool.execute("DELETE FROM node_relations_sets WHERE scope = $1", scope)
            left = await pool.fetchval("SELECT COUNT(*) FROM node_relations WHERE scope = $1", scope)
            await dbm.close_pool(pool)
            assert left == 0

    run.__name__ = fn.__name__
    run.__signature__ = inspect.Signature(params)
    return run


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_ingest_then_one_lookup_by_id_or_name_and_a_token_that_moves(pg):
    res = await nr.ingest_node_relations(pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "Col", ROWS)))
    assert res["rows"] == 3 and res["skipped"] == 2 and res["collection"] == "col"
    assert any("duplicate" in w for w in res["warnings"])

    answer = await nr.relations_for(pg.pool, scopes=[pg.scope], collection="col", full_names=["/A"], node_ids=["c"])
    assert answer["indexed"] and answer["token"]
    got = sorted((r["from_node_id"], r["relation"], r["to_full_name"]) for r in answer["relations"])
    assert got == [("a", "connects_to", "/B"), ("a", "serves_system", "/SYS"), ("c", "fills_opening", "/OPEN")]
    assert next(r for r in answer["relations"] if r["relation"] == "fills_opening")["props"] == {"host_full_name": "/H"}
    assert (await nr.relations_for(pg.pool, scopes=[pg.scope], collection="other", node_ids=["a"]))["indexed"] is False

    token = await nr.relations_token_for(pg.pool, scopes=[pg.scope], collections=["col"])
    assert token and await nr.relations_token_for(pg.pool, scopes=[pg.scope], collections=["other"]) is None
    # A new ingest replaces the set and moves the token.
    time.sleep(0.01)
    res = await nr.ingest_node_relations(
        pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "col", ROWS[:1], "r2"))
    )
    assert res["rows"] == 1
    assert await nr.relations_token_for(pg.pool, scopes=[pg.scope], collections=["col"]) != token
    answer = await nr.relations_for(pg.pool, scopes=[pg.scope], collection="col", node_ids=["a", "c"])
    assert [r["relation"] for r in answer["relations"]] == ["serves_system"]


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_the_workers_pool_lookup_answers_a_thread(pg):
    import asyncio

    from ada.comms.rest.worker.relation_lookup import PoolRelationLookup

    await nr.ingest_node_relations(pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "col", ROWS)))
    scope = SimpleNamespace(prefix=lambda: pg.scope)
    lookup = PoolRelationLookup(pg.pool, scope, asyncio.get_running_loop(), chunk=1)
    answer = await asyncio.get_running_loop().run_in_executor(
        None, lambda: lookup.relations("col", node_ids=["c"], full_names=["/A"])
    )
    assert answer.indexed and len(answer.relations) == 3, "chunks merged"
    assert {r.relation for r in answer.relations} == {"serves_system", "connects_to", "fills_opening"}
