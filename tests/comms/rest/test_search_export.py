"""Attribute-search hits exported as IFC: the leaf functions (``search_export``), the batch job
(``formats/export_search.py``), the fan-out run (``routes/asset_exports.py``), per-file metadata,
and the storage housekeeping of the per-file siblings.

Everything here runs against fakes and temp files; nothing touches a live database or store.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import pathlib
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))
os.environ["ADA_EXPORT_THUMBNAILS"] = "0"

import pytest

import ada
from ada.comms.rest import db as dbm
from ada.comms.rest import search_export as se
from ada.comms.rest import storage_ops
from ada.comms.rest.formats import export_search as job_mod
from ada.comms.rest.routes import asset_exports as ex
from ada.comms.rest.routes import asset_sweeps as sweeps
from ada.comms.rest.run_driver import RunBeat
from ada.comms.rest.scope import Scope

QUERY = {"where": [{"key": "FUNC", "eq": "MS"}, {"key": "GTYP", "in": ["HEB", "IPE"]}]}

HIT = {
    "provider": "web3d",
    "collection": "alpha",
    "node_id": "fn-hit",
    "full_name": "/STRU-1",
    "source_ref": None,
    "path": [
        {"node_id": "fn-site", "full_name": "/SITE-A", "kind": "SITE", "depth": 1},
        {"node_id": "fn-zone", "full_name": "/ZONE-A", "kind": "ZONE", "depth": 2},
    ],
}

ROWS = [
    {"node_id": "fn-site", "full_name": "/SITE-A", "source_ref": "=1/1", "attrs": {"PURP": "STR", "AREA": "12"}},
    {"node_id": "fn-zone", "full_name": "/ZONE-A", "source_ref": "=1/2", "attrs": {"DESC": "Deck zone"}},
    {"node_id": "fn-hit", "full_name": "/STRU-1", "source_ref": "=1/3", "attrs": {"FUNC": "MS", "GTYP": "HEB"}},
    # The beam: found by its E3D ref (the reader stamps RefNo), typed values.
    {
        "node_id": "fn-bm",
        "full_name": "/BM-1",
        "source_ref": "=1/4",
        "attrs": {"GTYP": "HEB", "LENGTH": "1500.5", "NSEG": "3", "MATR": "S355", "CODE": "007"},
    },
    # The plate: found by its full name without the leading slash the reader strips.
    {"node_id": "fn-pl", "full_name": "/PL-1", "source_ref": "=1/5", "attrs": {"THK": 20}},
]


def _geometry_part() -> ada.Part:
    """What a concepts reader hands back for the hit: a part with a sub-part, a beam and a plate."""
    top = ada.Part("STRU-1")
    sub = ada.Part("/FRMW-1")
    top.add_part(sub)
    bm = ada.Beam("BM-1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm.metadata["RefNo"] = "=1/4"
    sub.add_beam(bm)
    pl = ada.Plate("PL-1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.02)
    top.add_plate(pl)
    return top


# --- leaf functions --------------------------------------------------------------------------------


def test_typed_values_are_faithful():
    assert se.typed_value("12") == 12 and isinstance(se.typed_value("12"), int)
    assert se.typed_value("-3") == -3
    assert se.typed_value("1500.5") == 1500.5
    assert se.typed_value("1e3") == 1000.0
    assert se.typed_value("007") == "007"  # an identifier, not a number
    assert se.typed_value("0") == 0 and se.typed_value("0.5") == 0.5
    assert se.typed_value("S355") == "S355"
    assert se.typed_value("nan") == "nan" and se.typed_value("inf") == "inf"
    assert se.typed_value(True) is True and se.typed_value(4) == 4
    assert se.typed_attrs({"A": "", "B": None, "C": "2"}) == {"C": 2}


def test_sidecar_keys_and_file_keys():
    assert se.meta_sidecar_key("x/y/A.ifc") == "x/y/A.meta.json"
    assert se.thumb_sidecar_key("x/y/A.b.glb") == "x/y/A.b.thumb.png"
    assert se.meta_sidecar_key("A") == "A.meta.json"
    taken: set[str] = set()
    a = se.export_file_key("out", "alpha", "/ALPHA/BM 1", "fn-1", taken)
    b = se.export_file_key("out", "alpha", "/ALPHA:BM 1", "fn-2", taken)
    assert a == "out/alpha/ALPHA_BM 1.ifc" and b == "out/alpha/ALPHA_BM 1__fn-2.ifc"
    assert se.clean_folder("/exports//joints/") == "exports/joints"
    for bad in ("", "../x", "_derived/x", "assets/x"):
        with pytest.raises(ValueError):
            se.clean_folder(bad)


def test_matched_attributes_are_the_query_keys():
    assert se.matched_attributes(QUERY, {"FUNC": "MS", "GTYP": "HEB", "X": 1}) == {"FUNC": "MS", "GTYP": "HEB"}


def test_wrap_in_context_roots_the_hit_at_the_top():
    model = se.wrap_in_context(_geometry_part(), HIT)
    asm = model.assembly
    assert asm.name == "/SITE-A"
    zone = asm.parts["/ZONE-A"]
    assert "STRU-1" in zone.parts
    roles = [(e.role, e.node_id or e.full_name) for e in model.elements]
    assert roles[:3] == [("level", "fn-site"), ("level", "fn-zone"), ("hit", "fn-hit")]
    assert ("part", "/FRMW-1") in roles
    members = {e.full_name: e for e in model.elements if e.role == "member"}
    assert members["BM-1"].source_ref == "=1/4"
    assert "/PL-1" in members["PL-1"].lookup_names()


def test_wrap_without_a_path_names_the_assembly_after_the_hit():
    model = se.wrap_in_context(_geometry_part(), {**HIT, "path": []})
    assert model.assembly.name == "/STRU-1" and model.elements[0].role == "hit"


def _psets(ifc_path):
    import ifcopenshell
    import ifcopenshell.util.element as eu

    f = ifcopenshell.open(str(ifc_path))
    return {p.Name: eu.get_psets(p) for p in f.by_type("IfcProduct") if p.Name}, f


def test_ifc_carries_provenance_source_and_typed_attributes_on_every_element(tmp_path):
    model = se.wrap_in_context(_geometry_part(), HIT)
    counts = se.apply_source_attributes(model, ROWS, provider="web3d", collection="alpha", scope="project:p-alpha")
    se.stamp_provenance(
        model,
        provider="web3d",
        collection="alpha",
        provenance={"provider": "web3d", "collection": "alpha", "run_id": "run-1", "query": json.dumps(QUERY)},
        matched={"FUNC": "MS"},
        scope="project:p-alpha",
    )
    # Every element but the sub-part (no row) got attributes.
    assert counts == {"elements": 6, "with_attrs": 5, "without_attrs": 1}
    out = se.write_ifc(model, tmp_path / "hit.ifc")
    psets, f = _psets(out)

    site = psets["/SITE-A"]
    assert site["ADA_Provenance"]["run_id"] == "run-1" and json.loads(site["ADA_Provenance"]["query"]) == QUERY
    assert site["ADA_Provenance"]["source_scope"] == "project:p-alpha"
    assert site["ADA_Source"]["node_id"] == "fn-site"
    # Every ADA_Source -- levels, the hit, matched members -- names the searched scope, so one
    # selected element is enough to find its node in the source tree.
    sources = [p["ADA_Source"] for p in psets.values() if "ADA_Source" in p]
    assert len(sources) == 5 and {s["scope"] for s in sources} == {"project:p-alpha"}
    assert all(s["provider"] == "web3d" and s["collection"] == "alpha" for s in sources)
    # The same sets, listed by IFC name for the sidecar: levels root first, the hit, then members.
    listed = se.element_sources(model)
    assert [(e["name"], e["role"]) for e in listed] == [
        ("/SITE-A", "level"),
        ("/ZONE-A", "level"),
        ("STRU-1", "hit"),
        ("PL-1", "member"),
        ("BM-1", "member"),
    ]
    assert listed[4]["source"] == {
        "provider": "web3d",
        "scope": "project:p-alpha",
        "collection": "alpha",
        "node_id": "fn-bm",
        "full_name": "/BM-1",
        "source_ref": "=1/4",
    }
    assert all(e["guid"] for e in listed)
    assert site["ADA_SourceAttributes"]["AREA"] == 12  # typed
    assert psets["/ZONE-A"]["ADA_SourceAttributes"]["DESC"] == "Deck zone"
    hit = psets["STRU-1"]
    assert hit["ADA_Source"]["full_name"] == "/STRU-1" and hit["ADA_MatchedAttributes"] == {
        "FUNC": "MS",
        "id": hit["ADA_MatchedAttributes"]["id"],
    }
    beam = psets["BM-1"]
    attrs = beam["ADA_SourceAttributes"]
    assert attrs["LENGTH"] == 1500.5 and attrs["NSEG"] == 3 and attrs["MATR"] == "S355" and attrs["CODE"] == "007"
    assert beam["ADA_Source"]["node_id"] == "fn-bm"
    assert psets["PL-1"]["ADA_SourceAttributes"]["THK"] == 20
    # IFC types: integer as IfcInteger, real as IfcReal, text as text.
    by_name = {p.Name: p.NominalValue for p in f.by_type("IfcPropertySingleValue")}
    assert by_name["NSEG"].is_a("IfcInteger") and by_name["LENGTH"].is_a("IfcReal") and by_name["MATR"].is_a("IfcText")

    found = se.parse_ifc_provenance(out)
    assert found["source"]["node_id"] == "fn-hit" and found["matched"]["FUNC"] == "MS"
    assert found["provenance"]["run_id"] == "run-1"


def test_source_attributes_can_be_left_out_but_matching_still_counts(tmp_path):
    model = se.wrap_in_context(_geometry_part(), HIT)
    counts = se.apply_source_attributes(model, ROWS, provider="web3d", collection="alpha", include_attributes=False)
    assert counts["with_attrs"] == 5
    assert all("ADA_SourceAttributes" not in (e.obj.metadata or {}) for e in model.elements)


def test_meta_document_schema():
    doc = se.meta_document(
        file="out/alpha/STRU-1.ifc",
        fmt="ifc",
        created_by="tester",
        source={
            "provider": "web3d",
            "scope": "shared",
            "collection": "alpha",
            "node_id": "fn-hit",
            "path": HIT["path"],
        },
        query=QUERY,
        matched={"FUNC": "MS"},
        run_id="run-1",
        attributes={"elements": 6, "with_attrs": 5, "without_attrs": 1},
        derived={"glb": "out/alpha/STRU-1.glb"},
    )
    assert doc["schema"] == "ada.files/meta@1"
    assert set(doc) >= {
        "schema",
        "file",
        "format",
        "created_at",
        "created_by",
        "source",
        "query",
        "matched",
        "run_id",
        "attributes",
        "derived",
    }
    assert set(doc["source"]) == {
        "provider",
        "scope",
        "collection",
        "node_id",
        "full_name",
        "source_ref",
        "revision",
        "path",
    }
    assert doc["derived"] == {"glb": "out/alpha/STRU-1.glb", "step": None, "thumb": None}


def test_step_header_gets_the_provenance(tmp_path):
    p = tmp_path / "a.step"
    p.write_bytes(
        b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('ada'),'2;1');\nFILE_NAME('a');\nENDSEC;\nDATA;\n#1=X('it''s');\nENDSEC;\n"
    )
    assert se.patch_step_description(p, ["run=r1", "node it's"])
    text = p.read_text()
    assert "FILE_DESCRIPTION(('ada','run=r1','node it''s'),'2;1');" in text and "#1=X('it''s');" in text


# --- resolving geometry ------------------------------------------------------------------------------


def _catalog(spines, exact=None):
    return SimpleNamespace(spines=spines, exact=exact or {}, folded={})


def _spine(subject, node, rows, readable=True):
    return SimpleNamespace(
        subject=subject, node=node, rows=tuple(rows), readable=readable, manifest=SimpleNamespace(subject=subject)
    )


def test_resolve_geometry_by_shared_id_then_narrowest_cover():
    whole = _spine("alpha", "alpha", [("fn-site", "/SITE-A"), ("fn-hit", "/STRU-1"), ("x", "x")])
    narrow = _spine("fn-zone", "fn-zone", [("fn-zone", "/ZONE-A"), ("fn-hit", "/STRU-1")])
    index = SimpleNamespace(catalog=lambda c, p: _catalog([whole, narrow]))
    manifest, node = se.resolve_geometry(index, "alpha", HIT)
    assert manifest.subject == "fn-zone" and node == "fn-hit"
    own = _spine("fn-hit", "fn-hit", [("fn-hit", "/STRU-1")])
    index = SimpleNamespace(catalog=lambda c, p: _catalog([whole, narrow, own]))
    assert se.resolve_geometry(index, "alpha", HIT)[0].subject == "fn-hit"


def test_resolve_geometry_says_why_there_is_none():
    index = SimpleNamespace(catalog=lambda c, p: _catalog([]))
    with pytest.raises(se.NoGeometry, match="published nothing"):
        se.resolve_geometry(index, "alpha", HIT)
    listed_only = _spine("alpha", "alpha", [("fn-hit", "/STRU-1")], readable=False)
    index = SimpleNamespace(catalog=lambda c, p: _catalog([listed_only]))
    with pytest.raises(se.NoGeometry, match="no geometry covering"):
        se.resolve_geometry(index, "alpha", HIT)
    other = _spine("alpha", "alpha", [("fn-other", "/X")])
    index = SimpleNamespace(catalog=lambda c, p: _catalog([other]))
    with pytest.raises(se.NoGeometry, match="has no node"):
        se.resolve_geometry(index, "alpha", HIT)


# --- the batch job -----------------------------------------------------------------------------------


class _Store:
    """Async storage keyed by (scope prefix, key); gzip is not applied (the API reads through)."""

    def __init__(self):
        self.blobs: dict[tuple[str, str], bytes] = {}

    async def put_bytes(self, scope, key, data, *, content_encoding=None, pre_compressed=False):
        self.blobs[(scope.prefix(), key)] = bytes(data)

    async def put_path(self, scope, key, path, *, content_encoding=None, pre_compressed=False):
        self.blobs[(scope.prefix(), key)] = pathlib.Path(path).read_bytes()
        return {}

    async def get_bytes(self, scope, key):
        try:
            data = self.blobs[(scope.prefix(), key)]
        except KeyError:
            raise FileNotFoundError(key) from None
        return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data

    async def exists(self, scope, key):
        return (scope.prefix(), key) in self.blobs

    async def head(self, scope, key):
        data = self.blobs.get((scope.prefix(), key))
        return None if data is None else {"size": len(data)}

    async def stream_to_path(self, scope, key, dest):
        pathlib.Path(dest).write_bytes(await self.get_bytes(scope, key))

    async def list_prefix(self, scope, prefix):
        return [SimpleNamespace(key=k) for (s, k) in self.blobs if s == scope.prefix() and k.startswith(prefix)]

    async def list(self, scope, skip_prefixes=()):
        return [SimpleNamespace(key=k) for (s, k) in self.blobs if s == scope.prefix()]

    async def rename(self, scope, a, b, overwrite=False):
        self.blobs[(scope.prefix(), b)] = self.blobs.pop((scope.prefix(), a))

    async def delete(self, scope, key):
        if (scope.prefix(), key) not in self.blobs:
            raise FileNotFoundError(key)
        del self.blobs[(scope.prefix(), key)]


class _Queue:
    def __init__(self):
        self.updates: list[dict] = []
        self.status = "running"

    async def update(self, job_id, **fields):
        self.updates.append(fields)

    async def get(self, job_id):
        return SimpleNamespace(status=self.status)


def test_the_batch_job_writes_files_sidecars_and_reports_no_geometry(monkeypatch):
    store, queue = _Store(), _Queue()
    source = Scope.shared()

    def fake_resolve(index, collection, hit, **kw):
        if hit["node_id"] == "fn-missing":
            raise se.NoGeometry("provider 'external-csg' has no node '/GONE' in 'alpha'")
        return SimpleNamespace(provider="external-csg", subject="fn-zone", revision="r9"), hit["node_id"]

    monkeypatch.setattr(job_mod, "resolve_geometry", fake_resolve)
    monkeypatch.setattr("ada.assets.concepts.part_for_manifest", lambda m, storage, node: _geometry_part())

    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(job_mod, "_audit_done", no_audit)
    hits = [
        {**HIT, "file": "out/alpha/STRU-1.ifc", "attrs": {"FUNC": "MS"}},
        {**HIT, "node_id": "fn-missing", "full_name": "/GONE", "file": "out/alpha/GONE.ifc"},
    ]
    job = SimpleNamespace(
        job_id="j1",
        derived_key="_derived/search-export/run-1/0.json",
        conversion_options={
            "run_id": "run-1",
            "source_scope": "shared",
            "target_scope": {"kind": "user", "id": "someone"},
            "target_folder": "out",
            "query": QUERY,
            "provider": "web3d",
            "collection": "alpha",
            "hits": hits,
            "also": [],
            "requested_by": "tester",
        },
    )
    ctx = SimpleNamespace(scope=source, storage=store, queue=queue, db_pool=None, started_at=0.0)
    asyncio.run(job_mod.ExportSearchAssetHandler().run(job, ctx))

    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    summary = json.loads(asyncio.run(store.get_bytes(source, job.derived_key)))
    assert summary["counts"] == {"written": 1, "no_geometry": 1, "failed": 0}
    assert set(summary["timings"]) == {
        "resolve_ms",
        "read_ms",
        "attrs_ms",
        "ifc_ms",
        "convert_ms",
        "thumb_ms",
        "upload_ms",
    }
    [ok, gone] = summary["files"]
    assert gone["status"] == "no_geometry" and "has no node" in gone["error"]
    assert ("users/someone", "out/alpha/STRU-1.ifc") in store.blobs
    meta = json.loads(store.blobs[("users/someone", "out/alpha/STRU-1.meta.json")])
    assert meta["schema"] == "ada.files/meta@1" and meta["source"]["node_id"] == "fn-hit"
    assert meta["matched"] == {"FUNC": "MS"} and meta["run_id"] == "run-1"
    assert meta["geometry"]["provider"] == "external-csg"
    assert meta["source"]["scope"] == "shared"
    # No database here, so no member matched a row: the levels and the hit carry their sets.
    assert [e["role"] for e in meta["elements"]] == ["level", "level", "hit"]
    assert any("no database" in w for w in summary["warnings"])
    # The searched scope is in the file itself: on the site's provenance and every ADA_Source.
    ifc = pathlib.Path(tempfile.mkdtemp()) / "STRU-1.ifc"
    ifc.write_bytes(store.blobs[("users/someone", "out/alpha/STRU-1.ifc")])
    psets, _ = _psets(ifc)
    assert psets["/SITE-A"]["ADA_Provenance"]["source_scope"] == "shared"
    assert {p["ADA_Source"]["scope"] for p in psets.values() if "ADA_Source" in p} == {"shared"}


def test_the_source_scope_is_the_jobs_own_scope_in_path_form():
    assert job_mod._source_scope_wire("project:some-slug", Scope.project("p-1")) == "project:p-1"
    assert job_mod._source_scope_wire(None, Scope.shared()) == "shared"
    # A user scope has no wire form for others; the requested segment is kept.
    assert job_mod._source_scope_wire("user:me", Scope.user("someone")) == "user:me"
    assert job_mod._source_scope_wire(None, Scope.user("someone")) is None


# --- the fan-out run ---------------------------------------------------------------------------------


class _ExportWorld:
    def __init__(self, *, skip_position=None, fail_batch=None):
        self.store = _Store()
        self.items: dict[int, dict] = {}
        self.submitted: list = []
        self.cancelled: list[str] = []
        self.finished = None
        self.skip_position = skip_position
        self.skip_cleared = []
        self.fail_batch = fail_batch
        self.jobs: dict[str, str] = {}
        self.ctx = SimpleNamespace(
            jobs=SimpleNamespace(submit=self.submit, status=self.status, cancel=self.cancel),
            storage=self.store,
            audit=self.audit,
        )

    async def audit(self, *a, **k):
        return None

    async def submit(self, req, before_dispatch=None):
        self.submitted.append(req)
        position = req.conversion_options["position"]
        job_id = f"job-{position}"
        if before_dispatch is not None:
            await before_dispatch(SimpleNamespace(job_id=job_id))
        if position == self.skip_position:
            self.jobs[job_id] = "running"  # never finishes on its own
        elif position == self.fail_batch:
            self.jobs[job_id] = "error"
        else:
            hits = req.conversion_options["hits"]
            files = [
                {
                    "file": h["file"],
                    "node_id": h["node_id"],
                    "full_name": h["full_name"],
                    "status": "written",
                    "meta": se.meta_sidecar_key(h["file"]),
                }
                for h in hits
            ]
            files[-1]["status"] = "no_geometry" if len(files) > 1 else files[-1]["status"]
            summary = {
                "files": files,
                "counts": {
                    "written": sum(f["status"] == "written" for f in files),
                    "no_geometry": sum(f["status"] == "no_geometry" for f in files),
                    "failed": 0,
                },
                "timings": {
                    "resolve_ms": 1,
                    "read_ms": 2,
                    "attrs_ms": 3,
                    "ifc_ms": 4,
                    "convert_ms": 0,
                    "thumb_ms": 0,
                    "upload_ms": 5,
                },
                "warnings": [],
            }
            await self.store.put_bytes(req.scope, req.derived_key, gzip.compress(json.dumps(summary).encode()))
            self.jobs[job_id] = "done"
        return SimpleNamespace(job_id=job_id)

    async def status(self, job_id):
        st = self.jobs.get(job_id)
        return None if st is None else SimpleNamespace(status=st, payload={"error": "boom"} if st == "error" else {})

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.jobs[job_id] = "cancelled"
        return True

    async def update_item(self, pool, run_id, position, **fields):
        self.items.setdefault(position, {}).update(fields)

    async def heartbeat(self, pool, run_id):
        return RunBeat("running", self.skip_position, "Tester")

    async def clear_skip(self, pool, run_id, position):
        self.skip_cleared.append(position)
        self.skip_position = None

    async def finish(self, pool, run_id, *, status, error=None):
        self.finished = (status, error)

    async def skip_pending(self, pool, run_id, reason):
        pass


@pytest.fixture
def export_world(monkeypatch):
    def make(**kw):
        w = _ExportWorld(**kw)
        monkeypatch.setattr(dbm, "update_asset_export_item", w.update_item)
        monkeypatch.setattr(dbm, "heartbeat_asset_export_run", w.heartbeat)
        monkeypatch.setattr(dbm, "clear_asset_export_skip", w.clear_skip)
        monkeypatch.setattr(dbm, "finish_asset_export_run", w.finish)
        monkeypatch.setattr(dbm, "skip_pending_asset_export_items", w.skip_pending)
        monkeypatch.setattr(ex, "POLL_INTERVAL_S", 0)
        return w

    return make


def _hits():
    rows = [
        {
            "provider": "web3d",
            "collection": "ALPHA",
            "node_id": "fn-1",
            "full_name": "/A-1",
            "path": [],
            "attrs": {"FUNC": "MS", "X": 1},
        },
        {"provider": "web3d", "collection": "alpha", "node_id": "fn-2", "full_name": "/A-2", "path": []},
        {"provider": "web3d", "collection": "beta", "node_id": "fn-3", "full_name": "/B-1", "path": []},
        {"provider": "external-csg", "collection": "alpha", "node_id": "fn-1", "full_name": "/A-1", "path": []},
        {"provider": "web3d", "collection": "alpha", "node_id": "fn-1", "full_name": "/A-1", "path": []},  # duplicate
    ]
    hits = [ex.clean_hit(r, ["FUNC"]) for r in rows]
    return ex.batch_hits(hits, "out")


def test_hits_are_batched_per_provider_collection_with_unique_file_keys():
    batches = _hits()
    assert [(p, c, len(h)) for p, c, h in batches] == [
        ("web3d", "alpha", 2),
        ("web3d", "beta", 1),
        ("external-csg", "alpha", 1),
    ]
    assert batches[0][2][0]["attrs"] == {"FUNC": "MS"}  # only the matched keys travel
    files = [h["file"] for _, _, hs in batches for h in hs]
    assert len(files) == len(set(files)) and "out/alpha/A-1__fn-1.ifc" in files


def test_every_hit_query_keeps_negative_filters_and_exclusions(monkeypatch):
    """The export pages through the search with the dialog's query as is: exclusions and the
    negative operators reach it, the page size and cursor are the export's own."""
    seen = []

    async def fake_search(pool, *, scope, query):
        seen.append(query)
        return {"rows": [], "next_cursor": None, "counts": {}}

    monkeypatch.setattr(ex.db_module, "search_attributes", fake_search)
    query = {
        **QUERY,
        "where": [*QUERY["where"], {"key": "PURP", "ne": "X"}, {"key": "OWNER", "missing": True}],
        "exclude_collections": ["BETA"],
        "exclude_providers": ["external-csg"],
        "limit": 5,
        "cursor": "stale",
    }
    asyncio.run(ex.all_hits(None, scope="projects/p", query_body=query))
    (q,) = seen
    assert q.exclude_collections == ["beta"] and q.exclude_providers == ["external-csg"]
    assert [w["op"] for w in q.where] == ["in", "in", "not_in", "missing"]
    assert q.limit == 1000 and q.cursor is None


RUN = {"id": "run-1", "query": QUERY, "created_at": "2026-01-01T00:00:00+00:00"}
JOB_OPTS = {"run_id": "run-1", "target_folder": "out", "target_scope": {"kind": "shared", "id": None}}


def test_the_fan_out_runs_every_batch_and_writes_the_folder_index(export_world):
    w = export_world()
    batches = _hits()
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=batches,
            job_opts=JOB_OPTS,
            user=None,
        )
    )
    assert w.finished == ("done", None)
    assert len(w.submitted) == 3 and all(r.target_format == "export_search_asset" for r in w.submitted)
    alpha = w.items[0]
    assert alpha["status"] == "partial" and "1 without E3D geometry" in alpha["message"]
    assert alpha["files"] == {"written": 1, "no_geometry": 1, "failed": 0}
    assert alpha["file_errors"][0]["status"] == "no_geometry"
    assert {"queued_ms", "run_ms", "total_ms", "job"} <= set(alpha["timings"]) and alpha["timings"]["job"][
        "ifc_ms"
    ] == 4
    assert w.items[1]["status"] == "done"
    index = json.loads(w.store.blobs[("shared", "out/_export.json")])
    assert index["schema"] == "ada.files/export@1" and index["run_id"] == "run-1"
    assert [f["file"] for f in index["files"]] == ["out/alpha/A-1.ifc", "out/beta/B-1.ifc", "out/alpha/A-1__fn-1.ifc"]


def test_skipping_one_batch_cancels_its_job_and_the_run_goes_on(export_world):
    w = export_world(skip_position=1)
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=_hits(),
            job_opts=JOB_OPTS,
            user=None,
        )
    )
    assert w.items[1]["status"] == "skipped" and w.items[1]["message"] == "skipped by Tester"
    assert "job-1" in w.cancelled and w.skip_cleared == [1]
    assert w.items[0]["status"] == "partial" and w.items[2]["status"] == "done"
    assert w.finished == ("done", None)


def test_a_failed_batch_fails_the_run_not_the_others(export_world):
    w = export_world(fail_batch=2)
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=_hits(),
            job_opts=JOB_OPTS,
            user=None,
        )
    )
    assert w.items[2]["status"] == "error" and w.items[2]["error"] == "boom"
    assert w.finished == ("error", "1 of 3 batch(es) failed")


def test_item_outcomes():
    assert ex.item_outcome({"counts": {"written": 2}}) == ("done", None)
    assert ex.item_outcome({"counts": {"written": 0, "no_geometry": 2}})[0] == "partial"
    assert ex.item_outcome({"counts": {"written": 0, "failed": 2}})[0] == "error"


# --- the sweep's "skip current" -------------------------------------------------------------------------


def test_a_sweep_skip_mid_fetch_cancels_the_job_and_runs_the_next_collection(monkeypatch):
    from tests.comms.rest.test_asset_sweeps import RUN as SWEEP_RUN
    from tests.comms.rest.test_asset_sweeps import SPEC, SWEEP_ENTRY, _World

    w = _World(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}, "BETA": {"asset_staging_id": "s-b"}})
    original_enqueue = w.enqueue

    async def enqueue(ctx, **kw):
        job_id = await original_enqueue(ctx, **kw)
        if kw["options"]["project"] == "ALPHA":
            w.jobs_status[job_id] = "running"  # a long fetch, skipped while it runs
        return job_id

    state = {"skip": 0}

    async def heartbeat(pool, run_id):
        return RunBeat("running", state["skip"], "Tester")

    async def clear(pool, run_id, position):
        state["skip"] = None

    monkeypatch.setattr(sweeps, "enqueue_plugin_job", enqueue)
    monkeypatch.setattr(sweeps, "submit_asset_publish", w.publish)
    monkeypatch.setattr(dbm, "update_asset_sweep_item", w.update_item)
    monkeypatch.setattr(dbm, "heartbeat_asset_sweep_run", heartbeat)
    monkeypatch.setattr(dbm, "clear_asset_sweep_skip", clear)
    monkeypatch.setattr(dbm, "finish_asset_sweep_run", w.finish)
    monkeypatch.setattr(dbm, "skip_pending_asset_sweep_items", w.skip)
    monkeypatch.setattr(dbm, "asset_sweep_item_status", w.item_status)
    monkeypatch.setattr(sweeps, "POLL_INTERVAL_S", 0)
    asyncio.run(
        sweeps._drive(
            w.ctx,
            None,
            SWEEP_RUN,
            scope_obj=Scope.shared(),
            spec=SPEC,
            entry=SWEEP_ENTRY,
            collections=["ALPHA", "BETA"],
            user=None,
        )
    )
    assert w.jobs_status["fetch-1"] == "cancelled"
    a, b = w.items[0], w.items[1]
    assert a["status"] == "skipped" and a["message"] == "skipped by Tester" and "total_ms" in a["timings"]
    assert b["status"] == "done" and len(w.published) == 1
    assert w.finished == ("done", None)


# --- per-file metadata -------------------------------------------------------------------------------


def test_meta_answers_the_sidecar_then_in_file_provenance_then_nothing(tmp_path):
    store = _Store()
    scope = Scope.shared()
    doc = se.meta_document(
        file="out/a/X.ifc",
        fmt="ifc",
        created_by=None,
        source={"node_id": "n"},
        query={},
        matched={},
        run_id="r",
        attributes={},
        derived={},
    )
    asyncio.run(store.put_bytes(scope, "out/a/X.meta.json", json.dumps(doc).encode()))
    asyncio.run(store.put_bytes(scope, "out/a/X.thumb.png", b"png"))
    got = asyncio.run(ex.file_meta(store, scope, "out/a/X.glb"))  # any file of the stem
    assert got["origin"] == "sidecar" and got["derived"]["thumb"] == "out/a/X.thumb.png"

    model = se.wrap_in_context(_geometry_part(), HIT)
    se.stamp_provenance(
        model,
        provider="web3d",
        collection="alpha",
        provenance={"run_id": "r2", "query": json.dumps(QUERY)},
        matched={"FUNC": "MS"},
        scope="project:p-alpha",
    )
    ifc = se.write_ifc(model, tmp_path / "Y.ifc")
    asyncio.run(store.put_bytes(scope, "loose/Y.ifc", ifc.read_bytes()))
    got = asyncio.run(ex.file_meta(store, scope, "loose/Y.ifc"))
    assert got["origin"] == "ifc" and got["source"]["node_id"] == "fn-hit" and got["run_id"] == "r2"
    assert got["query"] == QUERY and got["matched"]["FUNC"] == "MS"
    # Read back from the file alone: the searched scope, and every element's source set by name.
    assert got["source"]["scope"] == "project:p-alpha"
    by_name = {e["name"]: e for e in got["elements"]}
    assert by_name["/ZONE-A"]["source"]["node_id"] == "fn-zone" and by_name["STRU-1"]["role"] == "hit"
    assert {e["source"]["scope"] for e in got["elements"]} == {"project:p-alpha"}

    from ada.assets.build import patch_glb_provenance

    glb = b"glTF" + (2).to_bytes(4, "little") + (0).to_bytes(4, "little")
    body = json.dumps({"asset": {"version": "2.0"}}).encode()
    body += b" " * (-len(body) % 4)
    glb = (
        b"glTF"
        + (2).to_bytes(4, "little")
        + (20 + len(body)).to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + b"JSON"
        + body
    )
    glb = patch_glb_provenance(
        glb, se.GlbProvenance({"provider": "web3d", "collection": "alpha", "node_id": "fn-hit", "run_id": "r3"})
    )
    asyncio.run(store.put_bytes(scope, "loose/Z.glb", glb))
    got = asyncio.run(ex.file_meta(store, scope, "loose/Z.glb"))
    assert got["origin"] == "glb" and got["source"]["node_id"] == "fn-hit" and got["run_id"] == "r3"

    asyncio.run(store.put_bytes(scope, "loose/plain.step", b"x"))
    assert asyncio.run(ex.file_meta(store, scope, "loose/plain.step")) is None


# --- housekeeping --------------------------------------------------------------------------------------


def test_rename_and_delete_carry_the_per_file_siblings():
    store = _Store()
    scope = Scope.shared()
    for key in (
        "out/A.ifc",
        "out/A.meta.json",
        "out/A.thumb.png",
        "_derived/out/A.ifc.glb",
        "out/B.ifc",
        "out/B.glb",
        "out/B.meta.json",
    ):
        asyncio.run(store.put_bytes(scope, key, b"x"))
    live = {k for (_, k) in store.blobs}
    moved = asyncio.run(storage_ops.rename_key_cascade(store, scope, "out/A.ifc", "done/A2.ifc", live))
    keys = {k for (_, k) in store.blobs}
    assert moved["siblings_moved"] == 3
    assert {"done/A2.ifc", "done/A2.meta.json", "done/A2.thumb.png", "_derived/done/A2.ifc.glb"} <= keys
    assert not keys & {"out/A.meta.json", "out/A.thumb.png"}

    # B.ifc shares its sidecar with B.glb: moving or deleting one leaves it for the other.
    asyncio.run(storage_ops.rename_key_cascade(store, scope, "out/B.ifc", "done/B.ifc", {k for (_, k) in store.blobs}))
    assert "out/B.meta.json" in {k for (_, k) in store.blobs}
    result = asyncio.run(storage_ops.delete_blob_cascade(store, scope, "out/B.glb"))
    assert "out/B.meta.json" in result["deleted"]  # the last file of that stem took it along
    result = asyncio.run(storage_ops.delete_blob_cascade(store, scope, "done/A2.ifc"))
    assert {"done/A2.meta.json", "done/A2.thumb.png"} <= set(result["deleted"])


def test_listing_hides_the_siblings():
    from ada.comms.rest.converters.keys import file_sidecar_keys, is_file_sidecar_key

    assert (
        is_file_sidecar_key("x/A.meta.json")
        and is_file_sidecar_key("x/A.thumb.png")
        and is_file_sidecar_key("x/_export.json")
    )
    assert not is_file_sidecar_key("x/A.ifc") and not is_file_sidecar_key("x/export.json")
    assert file_sidecar_keys("x/A.ifc") == ["x/A.meta.json", "x/A.thumb.png"]
