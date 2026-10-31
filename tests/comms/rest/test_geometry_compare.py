"""Geometry comparison (migration 051, routes/geometry_compare.py, formats/geometry_compare.py): the
worker job over two sides' GLBs, the kind's wiring into the sweep driver, the gallery's state table
(live Postgres, skipped unless ADA_TEST_POSTGRES_URL is set) and the picture guard."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import pathlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from ada.comms.rest import db as dbm
from ada.comms.rest.formats import geometry_compare as fmt
from ada.comms.rest.routes import asset_schedules as sched
from ada.comms.rest.routes import geometry_compare as route
from ada.geom_compare import glb

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

_CUBE_V = [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1]
_CUBE_T = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7]


def _cube(at=(0.0, 0.0, 0.0)):
    return [at[i % 3] + v for i, v in enumerate(_CUBE_V)], list(_CUBE_T)


def _glb(tmp_path: pathlib.Path, meshes: dict, name: str, node_ids: dict | None = None) -> bytes:
    path = tmp_path / name
    glb.write(meshes, path)
    if node_ids:
        js, binary = glb.parse_glb(path.read_bytes())
        js["scenes"][0]["extras"]["node_ids"] = node_ids
        glb.write_glb(js, binary, path)
    return path.read_bytes()


class _Storage:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    async def get_bytes(self, scope, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    async def put_bytes(self, scope, key, data, **kw):
        self.blobs[key] = data


class _Queue:
    def __init__(self):
        self.updates: list[dict] = []

    async def update(self, job_id, **fields):
        self.updates.append(fields)


def test_the_worker_job_joins_a_build_by_its_spine_names_and_writes_rows_and_a_summary(tmp_path, monkeypatch):
    storage = _Storage()
    # Side A: a core build -- entries named by the writer, mapped to published nodes (node_ids),
    # whose spine names them by full name. Side B: another provider's GLB named by full name.
    storage.blobs["a.glb"] = _glb(
        tmp_path, {"beam-1": _cube(), "beam-2": _cube((5, 0, 0))}, "a.glb", {"1": "n1", "2": "n2"}
    )
    storage.blobs["a-spine.json"] = json.dumps(
        {
            "cols": ["id", "parent", "label", "kind", "leaf", "delivery", "full_name"],
            "rows": [["n1", None, "B1", "x", 1, "", "/S/ONE"], ["n2", None, "B2", "x", 1, "", "/S/TWO"]],
        }
    ).encode()
    storage.blobs["b.glb"] = _glb(tmp_path, {"S/ONE": _cube(), "S/TWO": _cube((5.2, 0, 0))}, "b.glb")
    queue = _Queue()
    monkeypatch.setattr(fmt, "_audit_done", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr("ada.geom_compare.render.unavailable", lambda: "no renderer in tests")
    job = SimpleNamespace(
        job_id="j1",
        derived_key="out/summary.json",
        conversion_options={
            "collection": "col",
            "provider_a": "pa",
            "provider_b": "pb",
            "sources_a": [{"glb_key": "a.glb", "hierarchy_key": "a-spine.json", "subject": "s1"}],
            "sources_b": [{"glb_key": "b.glb"}],
            "rows_key": "out/rows.ndjson.gz",
            "pictures_prefix": "out/pictures/",
        },
    )
    ctx = SimpleNamespace(queue=queue, storage=storage, scope="providers", db_pool=None, started_at=0.0)
    asyncio.run(fmt._run(job, ctx))
    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    summary = json.loads(storage.blobs["out/summary.json"])
    assert summary["schema"] == fmt.RESULT_SCHEMA and summary["counts"]["compared"] == 2
    assert summary["counts"]["pass"] == 1 and summary["counts"]["fail"] == 1
    assert any("no renderer" in w for w in summary["warnings"])
    rows = [json.loads(x) for x in gzip.decompress(storage.blobs["out/rows.ndjson.gz"]).decode().splitlines()]
    by = {r["key"]: r for r in rows}
    assert set(by) == {"S/ONE", "S/TWO"} and by["S/TWO"]["verdict"] == "fail"
    assert by["S/ONE"]["a"]["subject"] == "s1" and by["S/ONE"]["a"]["summary"]["triangles"] == 12


def test_a_job_missing_its_inputs_fails_and_says_which(monkeypatch):
    queue = _Queue()
    monkeypatch.setattr(fmt, "_audit_done", lambda *a, **k: asyncio.sleep(0))
    job = SimpleNamespace(job_id="j", derived_key="k", conversion_options={"collection": "c"})
    asyncio.run(fmt._run(job, SimpleNamespace(queue=queue, storage=_Storage(), scope="s", db_pool=None, started_at=0)))
    assert queue.updates[-1]["status"] == "error" and "provider_a" in queue.updates[-1]["error"]


def test_spine_names_prefer_the_full_name_then_the_label():
    doc = {"cols": ["id", "label", "full_name"], "rows": [["a", "A", "/X/A"], ["b", "B", None]]}
    assert fmt.spine_names(doc) == {"a": "/X/A", "b": "B"}


def test_the_kind_is_driven_and_offers_no_prebuild():
    assert sched.GEOMETRY_COMPARE in sched.KINDS and sched.GEOMETRY_COMPARE in sched.DRIVEN_KINDS
    spec = {"job_options": [{"name": "compare_with", "type": "enum", "enum": ["other"]}]}
    entry = {"id": "compare", "kind": sched.GEOMETRY_COMPARE, "settings": [{"name": "compare_with"}]}
    names = [s["name"] for s in sched.entry_settings(spec, entry)]
    assert names == ["compare_with"], names


def test_the_picture_route_serves_only_comparison_pictures():
    ctx = SimpleNamespace(storage=_Storage())
    for bad in ("assets/x/secret.json", "_derived/geometry-compare/../x.png", "_derived/geometry-compare/x.json"):
        with pytest.raises(HTTPException) as err:
            asyncio.run(route.admin_geometry_compare_picture(bad, ctx))
        assert err.value.status_code == 400
    key = "_derived/geometry-compare/pa/pb/c/pictures/abc/f1_f2-a.png"
    ctx.storage.blobs[key] = b"\x89PNG"
    assert asyncio.run(route.admin_geometry_compare_picture(key, ctx)).body == b"\x89PNG"


@needs_postgres
def test_the_state_is_replaced_per_collection_and_listed_for_the_gallery():
    async def go():
        pool = await dbm.init_pool(POSTGRES_URL)
        pa, pb = f"pa-{uuid.uuid4().hex[:6]}", f"pb-{uuid.uuid4().hex[:6]}"
        try:
            rows = [
                {
                    "key": "S/ONE",
                    "name": "/S/ONE",
                    "verdict": "pass",
                    "metrics": {"box_delta_mm": 0.1},
                    "fp_a": "1",
                    "fp_b": "2",
                },
                {
                    "key": "S/TWO",
                    "name": "/S/TWO",
                    "verdict": "fail",
                    "metrics": {"box_delta_mm": 200.0},
                    "fp_a": "3",
                    "fp_b": "4",
                    "a": {"subject": "s1"},
                    "picture_a": "p.png",
                },
                {"key": "S/THREE", "verdict": "uncertain", "metrics": {"box_delta_mm": 5.0, "hausdorff_mm": 4.0}},
            ]
            assert (
                await dbm.replace_geometry_compare(pool, provider_a=pa, provider_b=pb, collection="Col", rows=rows) == 3
            )
            prev = await dbm.geometry_compare_previous(pool, provider_a=pa, provider_b=pb, collection="col")
            assert prev["S/TWO"]["fp_b"] == "4" and prev["S/TWO"]["picture_a"] == "p.png"
            page = await dbm.list_geometry_compare(
                pool, provider_a=pa, verdicts=["fail", "uncertain"], sort="box_delta_mm"
            )
            assert [i["key"] for i in page["items"]] == ["S/TWO", "S/THREE"] and page["total"] == 2
            assert page["counts"] == {"pass": 1, "fail": 1, "uncertain": 1}
            assert page["items"][0]["a"] == {"subject": "s1"}
            assert (await dbm.list_geometry_compare(pool, provider_a=pa, query="three"))["total"] == 1
            # Replaced, not merged: an element gone from the comparison is gone from the state.
            await dbm.replace_geometry_compare(pool, provider_a=pa, provider_b=pb, collection="col", rows=rows[:1])
            assert (await dbm.list_geometry_compare(pool, provider_a=pa))["total"] == 1
        finally:
            await pool.execute("DELETE FROM geometry_compare_elements WHERE provider_a = $1", pa)
            await dbm.close_pool(pool)

    asyncio.run(go())
