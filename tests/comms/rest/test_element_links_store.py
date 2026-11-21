"""The element links' stores (``ada.assoc.store``): the files backend over an in-memory storage, the
retention pin, intents, and -- with ADA_TEST_POSTGRES_URL -- the Postgres backend (migration 055)
answering the same queries the same way."""

from __future__ import annotations

import asyncio
import gzip
import os
import uuid
from types import SimpleNamespace

import pytest

from ada.assoc.records import LinkRow, SourceState
from ada.assoc.store import FilesLinkStore, Intent, LinkQuery, MirroredLinkStore
from ada.comms.rest import clash_details
from ada.comms.rest.assoc_links import pinned_detail_ids, scope_of_prefix
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


class MemoryStorage:
    """The async scope storage's surface the links use, in a dict."""

    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.writes = 0

    async def put_bytes(self, scope, key, data, *, content_encoding=None, pre_compressed=False):
        self.writes += 1
        self.blobs[(scope.prefix(), key)] = gzip.compress(data) if content_encoding == "gzip" else data

    async def get_bytes(self, scope, key):
        try:
            raw = self.blobs[(scope.prefix(), key)]
        except KeyError:
            raise FileNotFoundError(key) from None
        return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    async def exists(self, scope, key):
        return (scope.prefix(), key) in self.blobs

    async def head(self, scope, key):
        raw = self.blobs.get((scope.prefix(), key))
        return None if raw is None else {"size": len(raw), "last_modified": str(hash(raw)), "e_tag": None}

    async def delete(self, scope, key):
        self.blobs.pop((scope.prefix(), key), None)

    async def delete_many(self, scope, keys):
        for k in keys:
            self.blobs.pop((scope.prefix(), k), None)
        return []

    async def list_prefix(self, scope, prefix):
        return [
            SimpleNamespace(key=k, size=len(v), last_modified=str(hash(v)))
            for (s, k), v in sorted(self.blobs.items())
            if s == scope.prefix() and k.startswith(prefix)
        ]

    async def list_child_names(self, scope, prefix):
        names = {
            k[len(prefix) :].split("/", 1)[0] for (s, k) in self.blobs if s == scope.prefix() and k.startswith(prefix)
        }
        return sorted(n for n in names if n)


SCOPE = Scope("user", "me")
TOP = "node:/c/rack"


def _rows(doc, kind="connection", ck="a" * 16, n=3, created="2026-01-01T00:00:00Z", **data):
    return [
        LinkRow(
            kind=kind,
            element_key=f"node:/c/m{i}",
            doc_key=doc,
            anc=(TOP,),
            ck=ck,
            source="src",
            fp=f"fp{i}",
            created_at=created,
            data={"status": "built", **data},
        )
        for i in range(n)
    ]


async def _exercise(store) -> None:
    """The same story against any backend."""
    await store.put_sheet("d1", ("connection", "weld"), _rows("d1") + _rows("d1", kind="weld"))
    await store.put_sheet("d2", ("connection",), _rows("d2", ck="b" * 16, created="2026-01-02T00:00:00Z"))
    page = await store.query(LinkQuery(under=[TOP], limit=1))
    assert page["summary"]["rows"] == 9 and page["summary"]["groups"] == 2
    assert page["summary"]["kinds"] == {"connection": 6, "weld": 3}
    assert [g["group"] for g in page["groups"]] == ["a" * 16] and page["next"] == "a" * 16
    assert page["groups"][0]["kinds"] == {"connection": 3, "weld": 3}
    page2 = await store.query(LinkQuery(under=[TOP], limit=1, cursor=page["next"]))
    assert [g["group"] for g in page2["groups"]] == ["b" * 16] and page2["next"] is None

    # Writing a document again replaces its rows; a kind it no longer gives is gone.
    await store.put_sheet("d1", ("connection", "weld"), _rows("d1", n=1))
    page = await store.query(LinkQuery(under=[TOP]))
    assert page["summary"]["kinds"] == {"connection": 4}

    # One element exactly; a ck; nothing under another node.
    assert (await store.query(LinkQuery(element_keys=["node:/c/m0"])))["summary"]["rows"] == 2
    assert len(await store.rows(LinkQuery(cks=["b" * 16]))) == 3
    assert (await store.query(LinkQuery(under=["node:/c/other"])))["summary"]["rows"] == 0

    # The source measured again: m0 changed, m2 is gone -- stale and orphan.
    await store.put_source_state(
        SourceState("src", "2026-02-01T00:00:00Z", fps={"node:/c/m0": "new", "node:/c/m1": "fp1"})
    )
    page = await store.query(LinkQuery(cks=["b" * 16]))
    flags = {d["element_key"]: (d["stale"], d["orphan"]) for d in page["groups"][0]["rows"]}
    assert flags == {"node:/c/m0": (True, False), "node:/c/m1": (False, False), "node:/c/m2": (False, True)}
    assert (await store.query(LinkQuery(cks=["b" * 16], include_stale=False)))["summary"]["rows"] == 1

    # Intents: taken once.
    intent = Intent("b" * 16, "prov", "p0")
    assert await store.take_intent(intent) is True
    assert await store.take_intent(intent) is False
    assert await store.intent_run(intent) is None
    await store.set_intent_run(intent, "r1")
    assert await store.intent_run(intent) == "r1"
    await store.release_intent(intent)
    assert await store.intent_run(intent) is False


def test_the_files_backend():
    asyncio.run(_exercise(FilesLinkStore(MemoryStorage(), SCOPE)))


def test_concurrent_intents_are_taken_once():
    store = FilesLinkStore(MemoryStorage(), SCOPE)

    async def go():
        got = await asyncio.gather(*(store.take_intent(Intent("c" * 16, "p", "h")) for _ in range(20)))
        return sum(got)

    assert asyncio.run(go()) == 1


def test_a_detail_model_a_subject_is_of_is_never_pruned():
    storage = MemoryStorage()

    async def go():
        ids = []
        for i in range(3):
            paths = clash_details.detail_paths(clash_details.new_detail_id())
            record = clash_details.make_record(
                paths, spec="s", capability=None, source={"kind": "run"}, joint_ids=["j"], options={}, job_id=None
            )
            await clash_details.record_new(storage, SCOPE, record, keep=10)
            ids.append(paths.detail_id)
            await asyncio.sleep(0.002)
        files = FilesLinkStore(storage, SCOPE)
        pinned_ck = "d" * 16
        rows = _rows(clash_details.detail_paths(ids[0]).record, ck=pinned_ck)
        await files.put_sheet(rows[0].doc_key, ("connection",), rows)
        await files.put_sheet(
            "_clash/subjects/x.json", ("subject",), _rows("_clash/subjects/x.json", kind="subject", ck=pinned_ck)
        )
        assert await pinned_detail_ids(storage, SCOPE, ids) == {ids[0]}
        newest = clash_details.detail_paths(clash_details.new_detail_id())
        record = clash_details.make_record(
            newest, spec="s", capability=None, source={"kind": "run"}, joint_ids=["j"], options={}, job_id=None
        )
        dropped = await clash_details.record_new(storage, SCOPE, record, keep=1)
        return ids, dropped

    ids, dropped = asyncio.run(go())
    assert ids[0] not in dropped and set(dropped) == set(ids[1:])


def test_scope_prefixes_parse_back():
    assert scope_of_prefix("shared") == Scope("shared")
    assert scope_of_prefix("projects/p1") == Scope("project", "p1")
    assert scope_of_prefix("users/u") == Scope("user", "u")
    with pytest.raises(ValueError):
        scope_of_prefix("nope/x")


@needs_postgres
def test_the_postgres_backend_answers_as_the_files_do():
    from ada.comms.rest import db as dbm
    from ada.comms.rest.db.element_links import PgLinkStore

    async def go():
        pool = await dbm.init_pool(POSTGRES_URL)
        scope = Scope("user", f"links-{uuid.uuid4().hex[:10]}")
        try:
            await _exercise(PgLinkStore(pool, scope.prefix()))
            # Mirrored: the files hold the same rows Postgres answers from.
            storage = MemoryStorage()
            mirrored = MirroredLinkStore(FilesLinkStore(storage, scope), PgLinkStore(pool, scope.prefix() + "-m"))
            await mirrored.put_sheet("d9", ("weld",), _rows("d9", kind="weld"))
            assert len(await FilesLinkStore(storage, scope).rows(LinkQuery())) == 3
            assert (await mirrored.query(LinkQuery()))["summary"]["rows"] == 3
        finally:
            for table in ("element_links", "element_state", "element_sources", "sim_intents"):
                await pool.execute(f"DELETE FROM {table} WHERE scope LIKE $1", scope.prefix() + "%")
            await dbm.close_pool(pool)

    asyncio.run(go())
