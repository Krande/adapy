"""Per-node bounds (migration 042): ingest of ``node-bounds.ndjson.gz`` and the box query.

Always-on tests cover the file parsing, the query parsing and the fold (which nodes a box answers).
The live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set) ingest a small synthetic set
into a throwaway scope through the publish job, query it, and drop what they made.
"""

from __future__ import annotations

import contextlib
import gzip
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
from ada.comms.rest.db import node_bounds as nb
from ada.comms.rest.formats import asset_publish as ap
from ada.comms.rest.scope import Scope

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


# --- a synthetic set ----------------------------------------------------------------------------
#
#   world (no subject)
#   +-- site-a  [0..10] x [0..10] x [0..5]
#   |   +-- zone-a1 [0..4] x [0..10]   -> items a1-0 [0..1], a1-1 [3..4]   (x ranges; y 0..1, z 0..1)
#   |   +-- zone-a2 [6..10] x [0..10]  -> items a2-0 [6..7], a2-1 [9..10]
#   +-- site-b  [100..110] x [0..10]   -> zone-b1 -> item b1-0


def _box(x0, x1, y0=0.0, y1=1.0, z0=0.0, z1=1.0):
    return [x0, y0, z0, x1, y1, z1]


def _nodes() -> list[dict]:
    def n(node_id, parent, subject, depth, bbox, kind="ITEM"):
        return {
            "node_id": node_id,
            "parent_id": parent,
            "subject": subject,
            "label": node_id.upper(),
            "full_name": f"/{node_id}",
            "kind": kind,
            "depth": depth,
            "bbox": bbox,
        }

    return [
        n("world", None, None, 0, [0, 0, 0, 110, 10, 5], kind="WORLD"),
        n("site-a", "world", "site-a", 1, [0, 0, 0, 10, 10, 5], kind="SITE"),
        n("zone-a1", "site-a", "site-a", 2, [0, 0, 0, 4, 10, 1], kind="ZONE"),
        n("zone-a2", "site-a", "site-a", 2, [6, 0, 0, 10, 10, 1], kind="ZONE"),
        n("a1-0", "zone-a1", "site-a", 3, _box(0, 1)),
        n("a1-1", "zone-a1", "site-a", 3, _box(3, 4)),
        n("a2-0", "zone-a2", "site-a", 3, _box(6, 7)),
        n("a2-1", "zone-a2", "site-a", 3, _box(9, 10)),
        n("site-b", "world", "site-b", 1, [100, 0, 0, 110, 10, 5], kind="SITE"),
        n("zone-b1", "site-b", "site-b", 2, [100, 0, 0, 110, 10, 1], kind="ZONE"),
        n("b1-0", "zone-b1", "site-b", 3, _box(100, 110)),
    ]


def _bounds_bytes(provider: str, collection: str, nodes: list[dict], *, revision: str = "r1", extra: list[bytes] = ()):
    header = {
        "schema": nb.NODE_BOUNDS_SCHEMA,
        "provider": provider,
        "collection": collection,
        "revision": revision,
        "frame": {"up": "z", "units": "m"},
    }
    lines = [json.dumps(header).encode()] + [json.dumps(x).encode() for x in nodes] + list(extra)
    return gzip.compress(b"\n".join(lines) + b"\n")


async def _chunks(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i : i + size]


def _candidates(nodes: list[dict], box: list[float], mode: str) -> list[dict]:
    """What the SQL selects, in Python: the rows the fold is handed."""
    x0, y0, z0, x1, y1, z1 = box
    out = []
    for n in nodes:
        a = n["bbox"]
        if mode == "within":
            ok = x0 <= a[0] and a[3] <= x1 and y0 <= a[1] and a[4] <= y1 and z0 <= a[2] and a[5] <= z1
        else:
            ok = a[0] <= x1 and a[3] >= x0 and a[1] <= y1 and a[4] >= y0 and a[2] <= z1 and a[5] >= z0
        if ok:
            out.append(dict(n))
    return out


def _totals(nodes: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for n in nodes:
        if n["parent_id"] is not None:
            out[n["parent_id"]] = out.get(n["parent_id"], 0) + 1
    return out


def _fold(box, mode, nodes=None):
    nodes = nodes or _nodes()
    rows = []
    for r in _candidates(nodes, box, mode):
        own = r.pop("own_bbox", None) or [None] * 6
        rows.append({**r, **dict(zip(nb._COLUMNS[-6:], own))})
    return [r["node_id"] for r in nb.fold_hits(rows, mode=mode, child_totals=_totals(nodes), box=box)]


# --- always on ------------------------------------------------------------------------------------


def test_partial_box_around_one_site_answers_the_site():
    assert _fold([-1, -1, -1, 11, 11, 6], "partial") == ["site-a"]


def test_partial_box_through_a_corner_answers_the_deepest_nodes_it_meets():
    # x 0.5..3.5 meets a1-0 and a1-1 (all of zone-a1) and nothing of zone-a2.
    assert _fold([0.5, 0.5, 0.5, 3.5, 0.6, 0.6], "partial") == ["zone-a1"]
    # x 3.5..6.5 meets a1-1 and a2-0: one item of each zone.
    assert _fold([3.5, 0.5, 0.5, 6.5, 0.6, 0.6], "partial") == ["a1-1", "a2-0"]


def test_partial_box_in_a_gap_between_items_answers_nothing():
    # Inside zone-a1's box (x 1..3) but meeting none of its items.
    assert _fold([1.5, 0.2, 0.2, 2.5, 0.8, 0.8], "partial") == []


def test_partial_box_meeting_a_parents_own_geometry_answers_the_parent():
    # zone-a1 draws a plate of its own across the gap between its items.
    nodes = [{**n, "own_bbox": _box(1, 3)} if n["node_id"] == "zone-a1" else n for n in _nodes()]
    assert _fold([1.5, 0.2, 0.2, 2.5, 0.8, 0.8], "partial", nodes) == ["zone-a1"]
    # Through the plate and one of its items: the parent stands for both.
    assert _fold([2.5, 0.2, 0.2, 3.5, 0.8, 0.8], "partial", nodes) == ["zone-a1"]
    # Its own box out of the query box: the items it meets, as before.
    assert _fold([3.5, 0.5, 0.5, 6.5, 0.6, 0.6], "partial", nodes) == ["a1-1", "a2-0"]


def test_a_box_around_everything_answers_the_sites_not_the_subjectless_world():
    assert _fold([-1, -1, -1, 200, 20, 20], "partial") == ["site-a", "site-b"]
    assert _fold([-1, -1, -1, 200, 20, 20], "within") == ["site-a", "site-b"]


def test_within_takes_only_what_lies_inside():
    # Holds zone-a1 entirely and a2-0, but only part of zone-a2.
    assert _fold([-1, -1, -1, 7.5, 11, 2], "within") == ["zone-a1", "a2-0"]
    # The z range rules out the sites (z up to 5) but not the zones.
    assert _fold([-1, -1, -1, 11, 11, 2], "within") == ["zone-a1", "zone-a2"]


def test_parse_bounds_batch_skips_bad_lines_and_boxes():
    lines = [
        json.dumps({"node_id": "a", "bbox": [0, 0, 0, 1, 1, 1], "depth": 2}).encode(),
        b"{not json",
        json.dumps({"node_id": "b", "bbox": [1, 0, 0, 0, 1, 1]}).encode(),  # inverted
        json.dumps({"node_id": "c", "bbox": [0, 0, 0, 1, 1]}).encode(),  # short
        json.dumps({"node_id": "d", "bbox": [0, 0, 0, 1, 1, float("inf")]}).encode(),
        json.dumps({"bbox": [0, 0, 0, 1, 1, 1]}).encode(),
        json.dumps({"node_id": "e", "bbox": [0, 0, 0, 2, 2, 2], "own_bbox": [0, 0, 0, 1, 1, 1]}).encode(),
        json.dumps({"node_id": "f", "bbox": [0, 0, 0, 2, 2, 2], "own_bbox": [1, 0, 0, 0, 1, 1]}).encode(),
    ]
    records, skipped = nb.parse_bounds_batch(lines, scope="s", provider="p", collection="c")
    assert skipped == 5 and len(records) == 3
    assert records[0][:4] == ("s", "p", "c", "a") and records[0][9] == 2 and records[0][10:16] == (0, 0, 0, 1, 1, 1)
    assert records[0][16:] == (None,) * 6
    # An own box is kept when usable, dropped (the line kept) when not.
    assert records[1][16:] == (0, 0, 0, 1, 1, 1) and records[2][16:] == (None,) * 6


def test_parse_bounds_query():
    q = nb.parse_bounds_query({"box": {"min": [0, 1, 2], "max": [3, 4, 5]}, "collections": ["ABC"], "mode": "within"})
    assert q.box == (0, 1, 2, 3, 4, 5) and q.collections == ("abc",) and q.mode == "within" and q.providers is None
    for bad in (
        {},
        {"box": {"min": [0, 0], "max": [1, 1, 1]}},
        {"box": {"min": [2, 0, 0], "max": [1, 1, 1]}},
        {"box": {"min": [0, 0, 0], "max": [1, 1, 1]}, "mode": "near"},
        {"box": {"min": [0, 0, 0], "max": [1, 1, 1]}, "collections": "abc"},
    ):
        with pytest.raises(nb.BoundsQueryError):
            nb.parse_bounds_query(bad)


def test_header_must_name_the_schema_and_a_collection():
    with pytest.raises(nb.NodeBoundsError):
        nb.parse_bounds_header(json.dumps({"schema": "x", "collection": "c"}).encode())
    with pytest.raises(nb.NodeBoundsError):
        nb.parse_bounds_header(json.dumps({"schema": nb.NODE_BOUNDS_SCHEMA}).encode())
    h = nb.parse_bounds_header(json.dumps({"schema": nb.NODE_BOUNDS_SCHEMA, "collection": " ABC "}).encode())
    assert h["collection"] == "abc" and h["frame"] == {}


def test_readable_scopes_always_include_own_and_shared():
    assert nb.readable_scopes("projects/x", ["users/u", "shared"]) == ["projects/x", "shared", "users/u"]


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

    def _no_publisher(provider):  # a bounds-only publish must never ask for one
        raise AssertionError(f"asset_publisher({provider!r}) was called")

    monkeypatch.setattr(ap, "asset_publisher", _no_publisher)


@pytest.mark.asyncio
async def test_bounds_only_publish_without_a_pool_warns_and_keeps_the_staging(no_audit):
    key = f"assets/_staging/s1/{nb.NODE_BOUNDS_ROLE}"
    storage = _FakeStorage({key: _bounds_bytes("prov", "alpha", _nodes())})
    queue = _FakeQueue()
    job = _publish_job({nb.NODE_BOUNDS_ROLE: key}, provider="prov")
    await ap._run_asset_publish(
        job=job, scope=Scope.shared(), storage=storage, queue=queue, db_pool=None, started_at=time.time()
    )
    assert queue.updates[-1]["status"] == "done", queue.updates
    summary = json.loads(storage.blobs[job.derived_key])
    assert summary["collection"] == "alpha" and summary["revision"] == "r1"
    assert summary["bounds"]["ingested"] is False and summary["bounds"]["frame"] == {"up": "z", "units": "m"}
    assert "no database pool" in summary["warnings"][0]
    assert storage.deleted == []


# --- live Postgres -------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pg_world():
    pool = await dbm.init_pool(POSTGRES_URL)
    scope = f"projects/test-bounds-{uuid.uuid4().hex[:8]}"
    provider = f"test-{uuid.uuid4().hex[:6]}"
    try:
        yield SimpleNamespace(pool=pool, scope=scope, provider=provider)
    finally:
        await pool.execute("DELETE FROM node_bounds_sets WHERE scope = $1", scope)
        left = await pool.fetchval("SELECT COUNT(*) FROM node_bounds WHERE scope = $1", scope)
        await dbm.close_pool(pool)
        assert left == 0, "the test left node bounds behind"


@needs_postgres
@pytest.mark.asyncio
async def test_publish_ingests_bounds_and_the_box_query_answers_them(no_audit):
    async with _pg_world() as pg:
        key = f"assets/_staging/s2/{nb.NODE_BOUNDS_ROLE}"
        dup = json.dumps({**_nodes()[4], "bbox": [50, 50, 50, 51, 51, 51]}).encode()  # a1-0 again: dropped
        storage = _FakeStorage({key: _bounds_bytes(pg.provider, "Alpha", _nodes(), extra=[dup, b"{bad"])})
        queue = _FakeQueue()
        scope = Scope.project(pg.scope.split("/", 1)[1])
        job = _publish_job({nb.NODE_BOUNDS_ROLE: key}, provider=pg.provider)
        await ap._run_asset_publish(
            job=job, scope=scope, storage=storage, queue=queue, db_pool=pg.pool, started_at=time.time()
        )
        assert queue.updates[-1]["status"] == "done", queue.updates
        summary = json.loads(storage.blobs[job.derived_key])
        assert summary["bounds"]["ingested"] is True and summary["bounds"]["rows"] == len(_nodes())
        assert summary["bounds"]["duplicates"] == 1 and summary["bounds"]["skipped"] == 1
        assert storage.deleted == [key]

        scopes = nb.readable_scopes(pg.scope)
        sets = [s for s in await nb.list_bounds_sets(pg.pool, scopes=scopes) if s["provider"] == pg.provider]
        assert [(s["collection"], s["rows"], s["frame"]) for s in sets] == [("alpha", 11, {"up": "z", "units": "m"})]

        async def ask(box, mode):
            q = nb.parse_bounds_query(
                {"box": {"min": box[:3], "max": box[3:]}, "mode": mode, "providers": [pg.provider]}
            )
            res = await nb.query_node_bounds(pg.pool, scopes=scopes, query=q)
            return [h["node_id"] for h in res["hits"]], res

        got, res = await ask([-1, -1, -1, 11, 11, 6], "partial")
        assert got == ["site-a"] and res["truncated"] is None
        assert res["hits"][0]["subject"] == "site-a" and res["hits"][0]["bbox"] == [0, 0, 0, 10, 10, 5]
        assert (await ask([3.5, 0.5, 0.5, 6.5, 0.6, 0.6], "partial"))[0] == ["a1-1", "a2-0"]
        assert (await ask([-1, -1, -1, 7.5, 11, 2], "within"))[0] == ["zone-a1", "a2-0"]
        assert (await ask([-1, -1, -1, 200, 20, 20], "partial"))[0] == ["site-a", "site-b"]

        # A reingest replaces the set whole.
        storage.blobs[key] = _bounds_bytes(pg.provider, "alpha", _nodes()[:2], revision="r2")
        job = _publish_job({nb.NODE_BOUNDS_ROLE: key}, provider=pg.provider)
        await ap._run_asset_publish(
            job=job, scope=scope, storage=storage, queue=queue, db_pool=pg.pool, started_at=time.time()
        )
        assert await pg.pool.fetchval("SELECT COUNT(*) FROM node_bounds WHERE scope = $1", pg.scope) == 2


# --- the bounds request: which sweep answers it ---------------------------------------------------


def _spec(enum, *, kind="sweep"):
    return {
        "slug": "prov",
        "job_options": [{"name": "sweep_target", "type": "enum", "enum": enum}],
        "asset_schedules": [
            {"id": "changes", "kind": "change-check", "options": {"action": "check"}, "collection_option": "project"},
            {
                "id": "project-sweep",
                "kind": kind,
                "options": {"action": "export-project"},
                "collection_option": "project",
                "settings": [{"name": "sweep_target"}],
            },
        ],
    }


def test_bounds_sweep_is_the_sweep_offering_the_bounds_target():
    from ada.comms.rest.routes.attribute_search import bounds_sweep_of

    found = bounds_sweep_of([_spec(["tree", "attributes", "bounds"])])
    assert found is not None and found[1]["id"] == "project-sweep"
    # A provider whose sweep cannot stage bounds, or that declares no sweep, is not offered.
    assert bounds_sweep_of([_spec(["tree", "attributes"])]) is None
    assert bounds_sweep_of([_spec(["bounds"], kind="job")]) is None
    assert bounds_sweep_of([]) is None


def test_attributes_sweep_is_the_sweep_offering_the_attributes_target():
    from ada.comms.rest.routes.attribute_search import ATTRIBUTES_TARGET, sweep_of

    found = sweep_of([_spec(["tree", "attributes"])], ATTRIBUTES_TARGET)
    assert found is not None and found[1]["id"] == "project-sweep"
    assert sweep_of([_spec(["tree", "bounds"])], ATTRIBUTES_TARGET) is None
    assert sweep_of([_spec(["attributes"], kind="job")], ATTRIBUTES_TARGET) is None
