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
    "provider": "vendor-a",
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
    counts = se.apply_source_attributes(model, ROWS, provider="vendor-a", collection="alpha", scope="project:p-alpha")
    se.stamp_provenance(
        model,
        provider="vendor-a",
        collection="alpha",
        provenance={"provider": "vendor-a", "collection": "alpha", "run_id": "run-1", "query": json.dumps(QUERY)},
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
    assert all(s["provider"] == "vendor-a" and s["collection"] == "alpha" for s in sources)
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
        "provider": "vendor-a",
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
    counts = se.apply_source_attributes(model, ROWS, provider="vendor-a", collection="alpha", include_attributes=False)
    assert counts["with_attrs"] == 5
    assert all("ADA_SourceAttributes" not in (e.obj.metadata or {}) for e in model.elements)


def test_meta_document_schema():
    doc = se.meta_document(
        file="out/alpha/STRU-1.ifc",
        fmt="ifc",
        created_by="tester",
        source={
            "provider": "vendor-a",
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
    with pytest.raises(se.NoGeometry, match="no published project tree for collection"):
        se.resolve_geometry(index, "alpha", HIT)
    listed_only = _spine("alpha", "alpha", [("fn-hit", "/STRU-1")], readable=False)
    index = SimpleNamespace(catalog=lambda c, p: _catalog([listed_only]))
    with pytest.raises(se.NoGeometry, match="no geometry covering"):
        se.resolve_geometry(index, "alpha", HIT)
    other = _spine("alpha", "alpha", [("fn-other", "/X")])
    index = SimpleNamespace(catalog=lambda c, p: _catalog([other]))
    with pytest.raises(se.NoGeometry, match="has no node"):
        se.resolve_geometry(index, "alpha", HIT)


def test_a_hit_outside_node_publishes_wants_the_collections_tree():
    # Only a node of the collection is published: the missing hit is a missing tree, which a
    # tree request can add -- not a node the tree lacks.
    node_only = _spine("fn-other", "fn-other", [("fn-other", "/X")])
    index = SimpleNamespace(catalog=lambda c, p: _catalog([node_only]))
    with pytest.raises(se.NoGeometry, match="not its project tree") as caught:
        se.resolve_geometry(index, "alpha", HIT)
    assert caught.value.reason == "unpublished"
    published = {**HIT, "node_id": "fn-other", "full_name": "/X"}
    assert se.resolve_geometry(index, "alpha", published)[1] == "fn-other"


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
            raise se.NoGeometry("provider 'design-reader' has no node '/GONE' in 'alpha'")
        return SimpleNamespace(provider="design-reader", subject="fn-zone", revision="r9"), hit["node_id"]

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
            "provider": "vendor-a",
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
    assert meta["geometry"]["provider"] == "design-reader"
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


def test_a_batch_of_another_scopes_hits_is_read_there_and_written_to_the_target(monkeypatch):
    """``origin_scope``: the geometry and the provenance are the origin's, the summary the job's own
    scope's, the files the target's."""
    store, queue = _Store(), _Queue()
    own, origin = Scope.user("me"), Scope.project("p-2")
    read_from = []

    def fake_resolve(index, collection, hit, **kw):
        return SimpleNamespace(provider="design-reader", subject="fn-zone", revision="r9"), hit["node_id"]

    def fake_part(m, storage, node):
        read_from.append(storage._scope)
        return _geometry_part()

    monkeypatch.setattr(job_mod, "resolve_geometry", fake_resolve)
    monkeypatch.setattr("ada.assets.concepts.part_for_manifest", fake_part)

    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(job_mod, "_audit_done", no_audit)
    job = SimpleNamespace(
        job_id="j1",
        derived_key="_derived/search-export/run-1/0.json",
        conversion_options={
            "run_id": "run-1",
            "source_scope": "project:p-2",
            "origin_scope": origin.prefix(),
            "target_scope": {"kind": "user", "id": "me"},
            "target_folder": "out",
            "query": QUERY,
            "provider": "vendor-a",
            "collection": "alpha",
            "hits": [{**HIT, "origin": origin.prefix(), "file": "out/alpha/STRU-1.ifc"}],
            "also": [],
        },
    )
    ctx = SimpleNamespace(scope=own, storage=store, queue=queue, db_pool=None, started_at=0.0)
    asyncio.run(job_mod.ExportSearchAssetHandler().run(job, ctx))
    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    assert read_from == [origin]
    assert ("users/me", job.derived_key) in store.blobs
    meta = json.loads(store.blobs[("users/me", "out/alpha/STRU-1.meta.json")])
    assert meta["source"]["scope"] == "project:p-2"
    assert job_mod._origin_of({"origin_scope": "users/me"}, own) == own
    assert job_mod._origin_of({}, own) == own


def test_a_shared_batch_runs_in_the_exports_scope_and_tells_the_job_where_to_read(export_world):
    w = export_world()
    hits = [ex.clean_hit({**HIT, "scope": "shared"}, [], "projects/p-1")]
    batches = ex.batch_hits(hits, "out")
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.project("p-1"),
            target=Scope.shared(),
            batches=batches,
            job_opts={**JOB_OPTS, "source_scope": "project:p-1"},
            user=None,
        )
    )
    [req] = w.submitted
    assert req.scope == Scope.project("p-1")
    assert req.conversion_options["origin_scope"] == "shared" and req.conversion_options["source_scope"] == "shared"
    assert w.finished == ("done", None)


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
            "provider": "vendor-a",
            "collection": "ALPHA",
            "node_id": "fn-1",
            "full_name": "/A-1",
            "path": [],
            "attrs": {"FUNC": "MS", "X": 1},
        },
        {"provider": "vendor-a", "collection": "alpha", "node_id": "fn-2", "full_name": "/A-2", "path": []},
        {"provider": "vendor-a", "collection": "beta", "node_id": "fn-3", "full_name": "/B-1", "path": []},
        {"provider": "design-reader", "collection": "alpha", "node_id": "fn-1", "full_name": "/A-1", "path": []},
        {
            "provider": "vendor-a",
            "collection": "alpha",
            "node_id": "fn-1",
            "full_name": "/A-1",
            "path": [],
        },  # duplicate
    ]
    hits = [ex.clean_hit(r, ["FUNC"]) for r in rows]
    return ex.batch_hits(hits, "out")


def test_hits_are_batched_per_provider_collection_with_unique_file_keys():
    batches = _hits()
    assert [(p, c, len(h)) for p, c, h in batches] == [
        ("vendor-a", "alpha", 2),
        ("vendor-a", "beta", 1),
        ("design-reader", "alpha", 1),
    ]
    assert batches[0][2][0]["attrs"] == {"FUNC": "MS"}  # only the matched keys travel
    files = [h["file"] for _, _, hs in batches for h in hs]
    assert len(files) == len(set(files)) and "out/alpha/A-1__fn-1.ifc" in files


def test_every_hit_query_keeps_negative_filters_and_exclusions(monkeypatch):
    """The export pages through the search with the dialog's query as is: exclusions and the
    negative operators reach it, the page size and cursor are the export's own."""
    seen = []

    async def fake_search(pool, *, scope, query, **kw):
        seen.append(query)
        return {"rows": [], "next_cursor": None, "counts": {}}

    monkeypatch.setattr(ex.db_module, "search_attributes", fake_search)
    query = {
        **QUERY,
        "where": [*QUERY["where"], {"key": "PURP", "ne": "X"}, {"key": "OWNER", "missing": True}],
        "exclude_collections": ["BETA"],
        "exclude_providers": ["design-reader"],
        "limit": 5,
        "cursor": "stale",
    }
    asyncio.run(ex.all_hits(None, scope="projects/p", query_body=query))
    (q,) = seen
    assert q.exclude_collections == ["beta"] and q.exclude_providers == ["design-reader"]
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


def _run_with_items(*, merge=False):
    hits = [{"node_id": f"fn-{i}", "file": f"out/a/{i}.glb"} for i in range(3)]
    return {
        "options": {"merge": merge},
        "items": [
            {"position": 0, "provider": "p", "collection": "a", "status": "done", "hit_rows": hits[:1]},
            {
                "position": 1,
                "provider": "p",
                "collection": "b",
                "status": "partial",
                "error": None,
                "files": {"written": 2, "no_geometry": 1},
                "file_errors": [{"node_id": "fn-2", "status": "no_geometry"}],
                "hit_rows": hits,
            },
            {"position": 2, "provider": "p", "collection": "c", "status": "error", "error": "boom", "hit_rows": hits},
            {"position": 3, "provider": "p", "collection": "d", "status": "skipped", "hit_rows": hits[:2]},
        ],
    }


def test_a_rerun_drives_only_what_was_not_written():
    plan = ex.rerun_plan(_run_with_items())
    assert [(pos, c, [h["node_id"] for h in hs], n) for pos, _, c, hs, n in plan] == [
        (1, "b", ["fn-2"], 2),
        (2, "c", ["fn-0", "fn-1", "fn-2"], 0),
        (3, "d", ["fn-0", "fn-1"], 0),
    ]
    # A merged file holds every hit of its collection: the item is driven whole.
    merged = ex.rerun_plan(_run_with_items(merge=True))
    assert [(pos, len(hs), n) for pos, _, _, hs, n in merged] == [(1, 3, 0), (2, 3, 0), (3, 2, 0)]
    assert ex.rerun_plan({"options": {}, "items": [{"position": 0, "status": "done", "hit_rows": [{}]}]}) == []
    # Without retrying failures, only what did not run to the end.
    assert [pos for pos, *_ in ex.rerun_plan(_run_with_items(), retry_failed=False)] == [3]


def test_a_rerun_merges_into_the_folder_index_and_counts_what_was_written(export_world):
    w = export_world()
    [first, second, third] = _hits()
    seed = [
        {"file": "out/alpha/A-1.ifc", "provider": "vendor-a", "collection": "alpha", "old": True},
        {"file": "out/beta/B-1.ifc", "provider": "vendor-a", "collection": "beta"},
    ]
    retry = (first[0], first[1], first[2][1:])  # alpha's second hit only
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=[retry],
            job_opts=JOB_OPTS,
            user=None,
            positions=[0],
            carried={0: 1},
            index_seed=seed,
        )
    )
    assert [r.conversion_options["position"] for r in w.submitted] == [0]
    assert w.items[0]["status"] == "done" and w.items[0]["files"]["written"] == 2
    index = json.loads(w.store.blobs[("shared", "out/_export.json")])
    assert [f["file"] for f in index["files"]] == ["out/alpha/A-1.ifc", "out/alpha/A-2.ifc", "out/beta/B-1.ifc"]
    assert index["files"][0].get("old")  # what the re-run did not write is kept as it was


def test_the_index_replaces_a_rewritten_file_in_place():
    seed = [{"file": "x.glb", "provider": "p", "collection": "a", "hits": 1}, {"file": "y.glb", "provider": "p"}]
    doc = ex.export_index_document(
        RUN, {0: [{"file": "x.glb", "provider": "p", "collection": "a", "hits": 3}]}, "o", seed
    )
    assert [(f["file"], f.get("hits")) for f in doc["files"]] == [("x.glb", 3), ("y.glb", None)]


def _rerun(monkeypatch, run, *, restarted=True, body=None):
    from fastapi import HTTPException  # noqa: F401 - the route raises it

    spawned, restarts = [], []

    async def get_run(pool, run_id, with_hits=False):
        return run

    async def restart(pool, run_id, positions):
        restarts.append(positions)
        return restarted

    async def writable(pool, raw, user):
        return Scope.shared()

    async def permission(ctx, pool, user, provider):
        return {"allowed": True, "available": True}

    monkeypatch.setattr(dbm, "get_asset_export_run", get_run)
    monkeypatch.setattr(dbm, "restart_asset_export_run", restart)
    monkeypatch.setattr(ex, "_writable_scope", writable)
    monkeypatch.setattr(ex, "collection_request_permission", permission)
    monkeypatch.setattr(ex, "spawn", lambda coro, name: (spawned.append(coro), coro.close()))
    ctx = SimpleNamespace(jobs=SimpleNamespace(require=lambda f: None), storage=_Store())
    out = asyncio.run(
        ex.api_search_export_rerun(
            "shared", "run-1", _Request(body or {}), scope_obj=Scope.shared(), user=None, ctx=ctx
        )
    )
    return json.loads(out.body), spawned, restarts


def _stored_run(status="done", **kw):
    run = _run_with_items(**kw)
    return {
        **run,
        "id": "run-1",
        "scope": "shared",
        "status": status,
        "target_scope": "shared",
        "target_folder": "out",
        "query": {},
        "options": {**run["options"], "geometry": "mesh"},
    }


def test_rerunning_a_run_restarts_its_failed_items_once(monkeypatch):
    body, spawned, restarts = _rerun(monkeypatch, _stored_run())
    assert body["rerun"] is True and restarts == [[1, 2, 3]] and len(spawned) == 1
    assert all("hit_rows" not in i for i in body["items"])
    # A second click while it runs, or a lost race for the restart: answered, not started again.
    body, spawned, _ = _rerun(monkeypatch, _stored_run(status="running"))
    assert body["rerun"] is False and "running" in body["reason"] and not spawned
    body, spawned, _ = _rerun(monkeypatch, _stored_run(), restarted=False)
    assert body["rerun"] is False and not spawned


def test_rerunning_a_complete_run_does_nothing(monkeypatch):
    run = _stored_run()
    run["items"] = [run["items"][0]]
    body, spawned, restarts = _rerun(monkeypatch, run)
    assert body["rerun"] is False and "nothing to re-export" in body["reason"] and not spawned and not restarts


def test_removing_a_run_refuses_a_running_one_and_an_unknown_one(monkeypatch):
    from fastapi import HTTPException

    deleted = []

    async def get_run(pool, run_id, with_hits=False):
        return {"scope": "shared"} if run_id != "gone" else None

    async def delete(pool, run_id):
        deleted.append(run_id)
        return run_id != "busy"

    monkeypatch.setattr(dbm, "get_asset_export_run", get_run)
    monkeypatch.setattr(dbm, "delete_asset_export_run", delete)

    def call(run_id):
        return asyncio.run(ex.api_search_export_delete("shared", run_id, _Request({}), scope_obj=Scope.shared()))

    assert json.loads(call("run-1").body) == {"deleted": "run-1"}
    with pytest.raises(HTTPException) as busy:
        call("busy")
    assert busy.value.status_code == 409
    with pytest.raises(HTTPException) as gone:
        call("gone")
    assert gone.value.status_code == 404 and deleted == ["run-1", "busy"]


def test_a_rerun_without_retrying_failures_resumes_only_the_unfinished(monkeypatch):
    _, spawned, restarts = _rerun(monkeypatch, _stored_run(), body={"retry_failed": False})
    assert restarts == [[3]] and len(spawned) == 1
    run = _stored_run()
    run["items"] = run["items"][:3]  # done, partial, error: all ran to the end
    body, spawned, restarts = _rerun(monkeypatch, run, body={"retry_failed": False})
    assert body["rerun"] is False and "Retry failed" in body["reason"] and not spawned and not restarts


def test_item_outcomes():
    assert ex.item_outcome({"counts": {"written": 2}}) == ("done", None)
    assert ex.item_outcome({"counts": {"written": 0, "no_geometry": 2}})[0] == "partial"
    assert ex.item_outcome({"counts": {"written": 0, "failed": 2}})[0] == "error"


# --- the sweep's "skip current" -------------------------------------------------------------------------


def test_a_sweep_skip_mid_fetch_cancels_the_job_and_runs_the_next_collection(monkeypatch):
    from tests.comms.rest.test_asset_sweeps import RUN as SWEEP_RUN
    from tests.comms.rest.test_asset_sweeps import SPEC, SWEEP_ENTRY, _World, install_run_fakes

    w = _World(fetch_summaries={"ALPHA": {"asset_staging_id": "s-a"}, "BETA": {"asset_staging_id": "s-b"}})
    install_run_fakes(monkeypatch, w)  # the collection claim and the worker slots
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
        provider="vendor-a",
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
        glb, se.GlbProvenance({"provider": "vendor-a", "collection": "alpha", "node_id": "fn-hit", "run_id": "r3"})
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


def test_a_merged_files_hit_thumbnails_go_with_it():
    from ada.comms.rest.converters.keys import joint_thumb_keys

    assert se.joint_thumb_key("out/alpha.glb", 3) == "out/alpha.j0003.thumb.png"
    live = {"out/alpha.glb", "out/alpha.j0000.thumb.png", "out/alpha.j0001.thumb.png", "out/alphabet.j0000.thumb.png"}
    assert joint_thumb_keys("out/alpha.glb", live) == ["out/alpha.j0000.thumb.png", "out/alpha.j0001.thumb.png"]

    store = _Store()
    scope = Scope.shared()
    for key in ("out/alpha.glb", "out/alpha.meta.json", "out/alpha.j0000.thumb.png", "out/alpha.j0001.thumb.png"):
        asyncio.run(store.put_bytes(scope, key, b"x"))
    moved = asyncio.run(
        storage_ops.rename_key_cascade(store, scope, "out/alpha.glb", "done/a2.glb", {k for (_, k) in store.blobs})
    )
    keys = {k for (_, k) in store.blobs}
    assert moved["siblings_moved"] == 3
    assert {"done/a2.meta.json", "done/a2.j0000.thumb.png", "done/a2.j0001.thumb.png"} <= keys
    result = asyncio.run(storage_ops.delete_blob_cascade(store, scope, "done/a2.glb"))
    assert {"done/a2.j0000.thumb.png", "done/a2.j0001.thumb.png"} <= set(result["deleted"])


def test_listing_hides_the_siblings():
    from ada.comms.rest.converters.keys import file_sidecar_keys, is_file_sidecar_key

    assert (
        is_file_sidecar_key("x/A.meta.json")
        and is_file_sidecar_key("x/A.thumb.png")
        and is_file_sidecar_key("x/_export.json")
    )
    assert not is_file_sidecar_key("x/A.ifc") and not is_file_sidecar_key("x/export.json")
    assert file_sidecar_keys("x/A.ifc") == ["x/A.meta.json", "x/A.thumb.png"]


# --- the mesh export (vendor-a GLBs) ------------------------------------------------------------------------


def _tiny_glb() -> bytes:
    import struct

    doc = json.dumps({"asset": {"version": "2.0"}, "scenes": [{"nodes": []}], "scene": 0}).encode()
    doc += b" " * (-len(doc) % 4)
    return struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(doc)) + struct.pack("<I4s", len(doc), b"JSON") + doc


def test_mesh_file_keys_are_glbs_and_a_preset_key_is_kept():
    hits = [ex.clean_hit(r, []) for r in ({**HIT, "node_id": "fn-1"}, {**HIT, "node_id": "fn-2", "full_name": "/X"})]
    hits[1]["file"] = "out/alpha/X.ifc"
    [(_, _, batch)] = ex.batch_hits(hits, "out", ext="glb")
    assert [h["file"] for h in batch] == ["out/alpha/STRU-1.glb", "out/alpha/X.ifc"]


def test_a_mesh_export_refuses_derived_formats_and_requests(monkeypatch):
    async def writable(pool, raw, user):
        return Scope.shared()

    async def go(body):
        return await ex.start_export(
            SimpleNamespace(jobs=SimpleNamespace(require=lambda f: None)),
            None,
            scope="shared",
            scope_obj=Scope.shared(),
            body={"query": QUERY, "hits": [HIT], "target_scope": "shared", "target_folder": "out", **body},
            user=None,
        )

    monkeypatch.setattr(ex, "_writable_scope", writable)
    for body in ({"geometry": "mesh", "also": ["step"]}, {"geometry": "mesh", "request_missing_geometry": True}):
        with pytest.raises(ex.ExportRefused) as err:
            asyncio.run(go(body))
        assert err.value.status == 400 and "GLBs only" in err.value.detail
    with pytest.raises(ex.ExportRefused) as err:
        asyncio.run(go({"geometry": "voxels"}))
    assert "'geometry'" in err.value.detail


def test_the_mesh_batch_job_copies_the_built_glb_with_provenance_and_a_sidecar(monkeypatch):
    from ada.assets.build import read_glb_provenance

    store, queue = _Store(), _Queue()
    source = Scope.shared()

    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(job_mod, "_audit_done", no_audit)
    monkeypatch.setattr(job_mod, "_render_thumb", lambda glb, out: "no offscreen renderer")
    glb_key = "_derived/assets/vendor-a/alpha/fn-site/r1/fn-hit/abc/model.glb"
    store.blobs[("shared", glb_key)] = gzip.compress(_tiny_glb())
    mesh = {"glb_key": glb_key, "provider": "vendor-a", "subject": "fn-site", "revision": "r1", "node": "fn-hit"}
    hits = [
        {**HIT, "file": "out/alpha/STRU-1.glb", "attrs": {"FUNC": "MS"}, "mesh": mesh},
        {
            **HIT,
            "node_id": "fn-gone",
            "full_name": "/GONE",
            "file": "out/alpha/GONE.glb",
            "mesh_error": "not in vendor-a",
        },
    ]
    job = SimpleNamespace(
        job_id="j1",
        derived_key="_derived/search-export/run-1/0.json",
        conversion_options={
            "run_id": "run-1",
            "geometry": "mesh",
            "source_scope": "shared",
            "target_scope": {"kind": "user", "id": "someone"},
            "target_folder": "out",
            "query": QUERY,
            "provider": "vendor-a",
            "collection": "alpha",
            "hits": hits,
            "requested_by": "tester",
        },
    )
    ctx = SimpleNamespace(scope=source, storage=store, queue=queue, db_pool=None, started_at=0.0)
    asyncio.run(job_mod.ExportSearchAssetHandler().run(job, ctx))

    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    summary = json.loads(asyncio.run(store.get_bytes(source, job.derived_key)))
    assert summary["geometry"] == "mesh"
    assert summary["counts"] == {"written": 1, "no_geometry": 1, "failed": 0}
    assert summary["files"][1]["error"] == "not in vendor-a"
    assert ex.item_outcome(summary)[1] == "1 of 2 written; 1 without a mesh from vendor-a"
    glb = store.blobs[("users/someone", "out/alpha/STRU-1.glb")]
    prov = read_glb_provenance(glb)
    assert prov["geometry_kind"] == "mesh" and prov["node_id"] == "fn-hit" and prov["source_scope"] == "shared"
    meta = json.loads(store.blobs[("users/someone", "out/alpha/STRU-1.meta.json")])
    assert meta["format"] == "glb" and meta["geometry"]["kind"] == "mesh" and meta["geometry"]["subject"] == "fn-site"
    assert meta["derived"] == {"glb": "out/alpha/STRU-1.glb", "step": None, "thumb": None}
    assert [(e["role"], e["source"]["node_id"]) for e in meta["elements"]] == [
        ("level", "fn-site"),
        ("level", "fn-zone"),
        ("hit", "fn-hit"),
    ]
    assert not any(k[1].endswith(".ifc") for k in store.blobs)


class _Request:
    def __init__(self, body):
        self._body = json.dumps(body).encode()
        self.app = SimpleNamespace(state=SimpleNamespace(db_pool=object()))

    async def body(self):
        return self._body

    async def json(self):
        return json.loads(self._body)


def _request_geometry(monkeypatch, meta, *, may_request=True):
    """Call the route with its collaborators faked; answers (response or exception, start_export kwargs)."""
    from fastapi import HTTPException

    started = {}

    async def fake_meta(storage, scope_obj, key):
        return meta

    async def fake_start(ctx, pool, **kw):
        started.update(kw)
        return {"id": "run-9", "scope": "shared", "hits": 1}

    async def resolve(pool, scope):
        return Scope.shared()

    async def allowed(*a, **k):
        return True

    async def node_request(ctx):
        return SimpleNamespace(plugin_id="p")

    async def permission(pool, user, req):
        return may_request, None if may_request else "admins only"

    monkeypatch.setattr(ex, "file_meta", fake_meta)
    monkeypatch.setattr(ex, "start_export", fake_start)
    monkeypatch.setattr(ex, "resolve_project_scope", resolve)
    monkeypatch.setattr(ex, "parse_scope", lambda raw, user: raw)
    monkeypatch.setattr("ada.comms.rest.scope.can_access", allowed)
    monkeypatch.setattr(ex.greq, "geometry_node_request", node_request)
    monkeypatch.setattr(ex, "request_permission", permission)
    ctx = SimpleNamespace(jobs=SimpleNamespace(require=lambda f: None), storage=None)
    try:
        out = asyncio.run(
            ex.api_file_request_geometry(
                "user:me", _Request({"key": "out/alpha/STRU-1.glb"}), scope_obj=Scope.user("me"), user=None, ctx=ctx
            )
        )
    except HTTPException as exc:
        return exc, started
    return json.loads(out.body), started


MESH_META = {
    "file": "out/alpha/STRU-1.glb",
    "format": "glb",
    "geometry": {"kind": "mesh", "provider": "vendor-a"},
    "query": QUERY,
    "source": {**HIT, "scope": "shared"},
}


def test_requesting_design_geometry_for_a_mesh_file_exports_its_node_beside_it(monkeypatch):
    out, started = _request_geometry(monkeypatch, MESH_META)
    assert out["id"] == "run-9" and out["requesting"] is True
    body = started["body"]
    assert started["scope"] == "shared" and body["target_scope"] == "user:me"
    assert body["geometry"] == "design" and body["request_missing_geometry"] is True
    assert body["target_folder"] == "out/alpha" and body["write_index"] is False
    assert [h["node_id"] for h in body["hits"]] == ["fn-hit"] and body["hits"][0]["path"] == HIT["path"]
    # The IFC takes the mesh's stem, so the two share one sidecar.
    assert started["preset_files"] == {"fn-hit": ("out/alpha/STRU-1.ifc", "out/alpha/STRU-1.glb")}


def test_requesting_design_geometry_without_request_rights_still_exports_what_is_there(monkeypatch):
    out, started = _request_geometry(monkeypatch, MESH_META, may_request=False)
    assert out["requesting"] is False and started["body"]["request_missing_geometry"] is False


def test_a_design_file_cannot_ask_for_its_design_geometry_again(monkeypatch):
    err, started = _request_geometry(monkeypatch, {**MESH_META, "format": "ifc", "geometry": {"kind": "design"}})
    assert err.status_code == 409 and "design geometry already" in err.detail and not started


def test_the_sidecar_names_the_mesh_only_when_there_is_one():
    doc = se.meta_document(
        file="a.ifc",
        fmt="ifc",
        created_by=None,
        source={},
        query=None,
        matched={},
        run_id=None,
        attributes={},
        derived={"glb": None, "mesh": "a.glb"},
    )
    assert doc["derived"]["mesh"] == "a.glb"
    plain = se.meta_document(
        file="a.ifc",
        fmt="ifc",
        created_by=None,
        source={},
        query=None,
        matched={},
        run_id=None,
        attributes={},
        derived={},
    )
    assert "mesh" not in plain["derived"]


def test_a_mesh_item_builds_each_ready_hit_then_submits_its_job(export_world, monkeypatch):
    from ada.comms.rest.routes import assets as assets_routes
    from ada.comms.rest.routes import geometry_requests as greq

    w = export_world()
    hits = [ex.clean_hit(r, []) for r in (HIT, {**HIT, "node_id": "fn-gone", "full_name": "/GONE"})]
    [(provider, collection, batch)] = ex.batch_hits(hits, "out", ext="glb")
    asked = []

    async def classify(storage, scope_obj, groups, provider=None):
        asked.append(provider)
        [(_, hs)] = groups
        return [
            [
                greq.HitState(hit=hs[0], state="ready", node_id="fn-hit", subject="fn-site", revision="r1"),
                greq.HitState(hit=hs[1], state="unknown", message="vendor-a has no node '/GONE'"),
            ]
        ]

    async def build(ctx, scope_obj, *, asked_provider, collection, node, subject, **kw):
        key = f"_derived/assets/vendor-a/{collection}/{subject}/r1/{node}/fp/summary.json"
        summary = {"ok": True, "glb_key": key.replace("summary.json", "model.glb")}
        await w.store.put_bytes(scope_obj, key, json.dumps(summary).encode())
        return {
            "derived_key": key,
            "provider": "vendor-a",
            "subject": subject,
            "revision": "r1",
            "node": node,
            "job_id": None,
        }

    monkeypatch.setattr(greq, "classify_in_scope", classify)
    monkeypatch.setattr(assets_routes, "submit_node_build", build)
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=[(provider, collection, batch)],
            job_opts={**JOB_OPTS, "geometry": "mesh"},
            user=None,
        )
    )
    assert asked == ["vendor-a"]
    [req] = w.submitted
    sent = req.conversion_options["hits"]
    assert sent[0]["mesh"]["glb_key"].endswith("/fn-hit/fp/model.glb") and sent[0]["mesh"]["subject"] == "fn-site"
    assert sent[1]["mesh_error"] == "vendor-a has no node '/GONE'" and "mesh" not in sent[1]
    timings = w.items[0]["timings"]
    assert timings["builds"] == 1 and timings["builds_cached"] == 1 and "build_ms" in timings


# --- merged exports: one file per collection -----------------------------------------------------------

HIT_2 = {**HIT, "node_id": "fn-hit2", "full_name": "/STRU-2"}
HIT_OTHER_ZONE = {
    **HIT,
    "node_id": "fn-hit3",
    "full_name": "/STRU-3",
    "path": [HIT["path"][0], {"node_id": "fn-zone-b", "full_name": "/ZONE-B", "kind": "ZONE", "depth": 2}],
}


def _part(name: str) -> ada.Part:
    p = ada.Part(name)
    p.add_beam(ada.Beam(f"BM-{name}", (0, 0, 0), (1, 0, 0), "IPE300"))
    return p


def test_merged_hits_share_their_levels_in_one_assembly():
    model = se.wrap_all_in_context(
        [(_part("STRU-1"), HIT), (_part("STRU-2"), HIT_2), (_part("STRU-3"), HIT_OTHER_ZONE)], name="alpha"
    )
    asm = model.assembly
    # One site (the shared root IS the assembly), two zones, three hits.
    assert asm.name == "/SITE-A"
    assert set(asm.parts) == {"/ZONE-A", "/ZONE-B"}
    assert set(asm.parts["/ZONE-A"].parts) == {"STRU-1", "STRU-2"} and set(asm.parts["/ZONE-B"].parts) == {"STRU-3"}
    assert [e.node_id for e in model.elements if e.role == "level"] == ["fn-site", "fn-zone", "fn-zone-b"]
    assert [e.node_id for e in model.hits] == ["fn-hit", "fn-hit2", "fn-hit3"]


def test_merged_hits_under_different_roots_hang_off_an_assembly_named_for_the_batch():
    other_site = {**HIT_2, "path": [{"node_id": "fn-site-b", "full_name": "/SITE-B"}]}
    model = se.wrap_all_in_context([(_part("STRU-1"), HIT), (_part("STRU-2"), other_site)], name="alpha")
    assert model.assembly.name == "alpha" and set(model.assembly.parts) == {"/SITE-A", "/SITE-B"}


def test_a_merged_batch_gives_every_hit_its_collections_one_file():
    hits = [ex.clean_hit(r, []) for r in (HIT, HIT_2, {**HIT, "provider": "other", "node_id": "fn-x"})]
    batches = ex.batch_hits(hits, "out", ext="glb", merge=True)
    assert [(p, c, [h["file"] for h in hs]) for p, c, hs in batches] == [
        ("vendor-a", "alpha", ["out/alpha.glb", "out/alpha.glb"]),
        # Another provider's batch of the same collection does not overwrite the first's file.
        ("other", "alpha", ["out/alpha__other.glb"]),
    ]


def test_a_merged_export_takes_a_higher_cap_and_no_preset_files(monkeypatch):
    async def writable(pool, raw, user):
        return Scope.shared()

    monkeypatch.setattr(ex, "_writable_scope", writable)
    ctx = SimpleNamespace(jobs=SimpleNamespace(require=lambda f: None))
    body = {"query": QUERY, "target_scope": "shared", "target_folder": "out", "merge": True}
    too_many = [{**HIT, "node_id": f"fn-{i}"} for i in range(ex.MAX_MERGED_EXPORT_HITS + 1)]
    with pytest.raises(ex.ExportRefused) as err:
        asyncio.run(
            ex.start_export(
                ctx, None, scope="shared", scope_obj=Scope.shared(), body={**body, "hits": too_many}, user=None
            )
        )
    assert err.value.status == 409 and "merged export takes at most" in err.value.detail
    with pytest.raises(ex.ExportRefused) as err:
        asyncio.run(
            ex.start_export(
                ctx,
                None,
                scope="shared",
                scope_obj=Scope.shared(),
                body={**body, "hits": [HIT]},
                user=None,
                preset_files={"fn-hit": ("a.ifc", None)},
            )
        )
    assert err.value.status == 400 and "preset" in err.value.detail


def _merge_job(monkeypatch, hits, *, geometry=None):
    store, queue = _Store(), _Queue()
    source = Scope.shared()

    async def no_audit(*a, **k):
        return None

    monkeypatch.setattr(job_mod, "_audit_done", no_audit)
    monkeypatch.setattr(job_mod, "_render_thumb", lambda glb, out: "no offscreen renderer")
    monkeypatch.setattr(job_mod, "_thumbnail_unavailable", lambda: "no offscreen renderer")
    job = SimpleNamespace(
        job_id="j1",
        derived_key="_derived/search-export/run-1/0.json",
        conversion_options={
            "run_id": "run-1",
            "merge": True,
            **({"geometry": geometry} if geometry else {}),
            "source_scope": "shared",
            "target_scope": {"kind": "user", "id": "someone"},
            "target_folder": "out",
            "query": QUERY,
            "provider": "vendor-a",
            "collection": "alpha",
            "hits": hits,
            "also": [],
            "requested_by": "tester",
        },
    )
    ctx = SimpleNamespace(scope=source, storage=store, queue=queue, db_pool=None, started_at=0.0)
    return store, queue, job, ctx, source


def test_the_merged_design_job_writes_one_ifc_holding_every_hit(monkeypatch):
    def fake_resolve(index, collection, hit, **kw):
        if hit["node_id"] == "fn-missing":
            raise se.NoGeometry("provider 'design-reader' has no node '/GONE' in 'alpha'")
        return SimpleNamespace(provider="design-reader", subject="fn-zone", revision="r9"), hit["node_id"]

    monkeypatch.setattr(job_mod, "resolve_geometry", fake_resolve)
    monkeypatch.setattr(
        "ada.assets.concepts.part_for_manifest", lambda m, storage, node: _part(node.replace("fn-", "P-"))
    )
    key = "out/alpha.ifc"
    hits = [
        {**HIT, "file": key, "attrs": {"FUNC": "MS"}},
        {**HIT_2, "file": key, "attrs": {"FUNC": "MS"}},
        {**HIT, "node_id": "fn-missing", "full_name": "/GONE", "file": key},
    ]
    store, queue, job, ctx, source = _merge_job(monkeypatch, hits)
    asyncio.run(job_mod.ExportSearchAssetHandler().run(job, ctx))

    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    summary = json.loads(asyncio.run(store.get_bytes(source, job.derived_key)))
    assert summary["counts"] == {"written": 2, "no_geometry": 1, "failed": 0}
    assert summary["merged"]["file"] == key and summary["merged"]["hits"] == 2
    assert [f["file"] for f in summary["files"]] == [key] * 3
    # ONE file in the target, with both hits under the one site and zone.
    written = [k for (s, k) in store.blobs if s == "users/someone" and k.endswith(".ifc")]
    assert written == [key]
    ifc = pathlib.Path(tempfile.mkdtemp()) / "alpha.ifc"
    ifc.write_bytes(store.blobs[("users/someone", key)])
    psets, _ = _psets(ifc)
    assert psets["/SITE-A"]["ADA_Provenance"]["merged_hits"] == 2
    matched = {name for name, p in psets.items() if "ADA_MatchedAttributes" in p}
    assert matched == {"P-hit", "P-hit2"}
    meta = json.loads(store.blobs[("users/someone", "out/alpha.meta.json")])
    assert meta["geometry"]["merged"] is True and meta["geometry"]["nodes"] == ["fn-hit", "fn-hit2"]
    assert meta["source"]["node_id"] is None and meta["source"]["full_name"] == "alpha"
    assert [(e["role"], e["source"]["node_id"]) for e in meta["elements"]] == [
        ("level", "fn-site"),
        ("level", "fn-zone"),
        ("hit", "fn-hit"),
        ("hit", "fn-hit2"),
    ]
    # Its hits, by the names their parts have in the file (what the viewer's tree shows).
    assert [(j["node_id"], j["name"]) for j in meta["joints"]] == [("fn-hit", "P-hit"), ("fn-hit2", "P-hit2")]
    assert summary["merged"]["joints"] == meta["joints"]
    # Each hit keeps its path, so it can be found in the source tree.
    assert [j["path"] for j in meta["joints"]] == [hits[0]["path"], hits[1]["path"]]


def _box_glb(offset: float, hit: str = "/STRU-1") -> bytes:
    """A built GLB as the asset build writes one: mesh node ``node0`` and, in the scene's extras,
    the hit's ancestors and two plates in ``id_hierarchy`` with a draw range each."""
    import trimesh

    box = trimesh.creation.box(extents=(1, 1, 1))  # 12 triangles = 36 indices
    box.apply_translation((offset, 0, 0))
    scene = trimesh.Scene()
    scene.add_geometry(box, node_name="node0", geom_name="node0")
    extras = {
        "id_hierarchy": {
            "1": ["/SITE-A", "*"],
            "2": ["/ZONE-A", "1"],
            "3": [hit, "2"],
            "4": [f"PLATE 1 of {hit}", "3"],
            "5": [f"PLATE 2 of {hit}", "3"],
        },
        "draw_ranges_node0": {"4": [0, 18], "5": [18, 18]},
        "up_axis": "Z",
    }
    return job_mod._with_scene_extras(scene.export(file_type="glb"), extras)


def _run_mesh_merge(monkeypatch, *, thumbs: bool = False):
    key = "out/alpha.glb"
    hits = []
    store_blobs = {}
    for n, hit in enumerate((HIT, HIT_2)):
        glb_key = f"_derived/assets/vendor-a/alpha/fn-site/r1/{hit['node_id']}/abc/model.glb"
        store_blobs[("shared", glb_key)] = gzip.compress(_box_glb(n * 3.0, hit["full_name"]))
        mesh = {
            "glb_key": glb_key,
            "provider": "vendor-a",
            "subject": "fn-site",
            "revision": "r1",
            "node": hit["node_id"],
        }
        hits.append({**hit, "file": key, "attrs": {"FUNC": "MS"}, "mesh": mesh})
    hits.append({**HIT, "node_id": "fn-gone", "full_name": "/GONE", "file": key, "mesh_error": "not in vendor-a"})
    store, queue, job, ctx, source = _merge_job(monkeypatch, hits, geometry="mesh")
    rendered: list[str] = []
    if thumbs:

        def render(glb, out):
            rendered.append(pathlib.Path(glb).name)
            pathlib.Path(out).write_bytes(b"png")
            return None

        monkeypatch.setattr(job_mod, "_thumbnail_unavailable", lambda: None)
        monkeypatch.setattr(job_mod, "_render_thumb", render)
    store.blobs.update(store_blobs)
    asyncio.run(job_mod.ExportSearchAssetHandler().run(job, ctx))
    return key, store, queue, job, source, rendered


def test_the_merged_mesh_job_writes_one_glb_with_a_node_per_hit(monkeypatch):
    import struct

    key, store, queue, job, source, _ = _run_mesh_merge(monkeypatch)

    assert queue.updates[-1]["status"] == "done", queue.updates[-1]
    summary = json.loads(asyncio.run(store.get_bytes(source, job.derived_key)))
    assert summary["geometry"] == "mesh" and summary["counts"] == {"written": 2, "no_geometry": 1, "failed": 0}
    assert ex.item_outcome(summary)[1] == "2 of 3 written; 1 without a mesh from vendor-a"
    glb = store.blobs[("users/someone", key)]
    (json_len,) = struct.unpack_from("<I", glb, 12)
    doc = json.loads(glb[20 : 20 + json_len])
    # One mesh per hit, named as the viewer pairs meshes with their draw ranges.
    assert sorted(n["name"] for n in doc["nodes"] if "mesh" in n) == ["node1", "node2"]
    assert len(doc["meshes"]) == 2
    extras = doc["scenes"][doc.get("scene", 0)]["extras"]
    assert extras["up_axis"] == "Z"
    tree = extras["id_hierarchy"]
    by_name = {name: (gid, parent) for gid, (name, parent) in tree.items()}
    # The ancestors the two joints share are ONE entry each; each joint and its plates are its own.
    assert [name for name, _ in tree.values()].count("/SITE-A") == 1
    assert by_name["/STRU-1"][1] == by_name["/STRU-2"][1] == by_name["/ZONE-A"][0]
    # Every plate is still its own clickable element: a range per plate, in its own mesh.
    for node, hit in (("node1", "/STRU-1"), ("node2", "/STRU-2")):
        ranges = extras[f"draw_ranges_{node}"]
        assert ranges == {by_name[f"PLATE 1 of {hit}"][0]: [0, 18], by_name[f"PLATE 2 of {hit}"][0]: [18, 18]}
    meta = json.loads(store.blobs[("users/someone", "out/alpha.meta.json")])
    assert meta["format"] == "glb" and meta["geometry"]["merged"] is True
    assert [(e["role"], e["source"]["node_id"]) for e in meta["elements"]] == [
        ("level", "fn-site"),
        ("level", "fn-zone"),
        ("hit", "fn-hit"),
        ("hit", "fn-hit2"),
    ]
    # The hits inside the file, each with the run of ids that are its objects -- exactly its
    # plates' ranges, none of the shared ancestors -- in the sidecar and in the batch's merged entry.
    joints = summary["merged"]["joints"]
    assert meta["joints"] == joints
    assert [(j["node_id"], j["name"], j["thumb"]) for j in joints] == [
        ("fn-hit", "/STRU-1", None),
        ("fn-hit2", "/STRU-2", None),
    ]
    assert all(j["path"] for j in joints)
    for joint, node in zip(joints, ("node1", "node2")):
        lo, hi = joint["ids"]
        assert {gid for gid in tree if lo <= int(gid) <= hi} >= set(extras[f"draw_ranges_{node}"])
        assert by_name["/SITE-A"][0] not in {str(i) for i in range(lo, hi + 1)}
    assert joints[0]["ids"][1] < joints[1]["ids"][0]


def test_a_merged_mesh_file_has_a_thumbnail_per_hit(monkeypatch):
    key, store, queue, job, source, rendered = _run_mesh_merge(monkeypatch, thumbs=True)
    summary = json.loads(asyncio.run(store.get_bytes(source, job.derived_key)))
    joints = summary["merged"]["joints"]
    assert [j["thumb"] for j in joints] == ["out/alpha.j0000.thumb.png", "out/alpha.j0001.thumb.png"]
    for j in joints:
        assert store.blobs[("users/someone", j["thumb"])] == b"png"
    # Rendered from each hit's own GLB, and once from the merged file for the file's own picture.
    assert sorted(rendered) == ["alpha.glb", "hit-0.glb", "hit-1.glb"]
    assert summary["merged"]["thumb"] == "out/alpha.thumb.png"
    # Each hit's row names its own picture.
    assert [f.get("thumb") for f in summary["files"] if f["status"] == "written"] == [j["thumb"] for j in joints]


def test_a_hit_is_cut_out_of_a_merged_glb_by_its_name(tmp_path):
    import trimesh

    parts = []
    for n, hit in enumerate(("/STRU-1", "/STRU-2")):
        path = tmp_path / f"hit-{n}.glb"
        path.write_bytes(_box_glb(n * 3.0, hit))
        parts.append(({"full_name": hit}, path))
    merged = tmp_path / "alpha.glb"
    job_mod._merge_glbs(parts, merged, "alpha")
    cuts = job_mod._cut_subtrees(merged, ["/STRU-2", "PLATE 1 of /STRU-1", "/NOPE"], tmp_path)
    assert cuts[2] is None
    whole = trimesh.load(str(cuts[0]), file_type="glb", force="mesh", process=False)
    assert len(whole.faces) == 12 and whole.bounds[0][0] > 2.0  # the second box, all of it
    plate = trimesh.load(str(cuts[1]), file_type="glb", force="mesh", process=False)
    assert len(plate.faces) == 6 and plate.bounds[1][0] < 1.0  # one plate of the first


def test_a_merged_file_cannot_request_design_geometry(monkeypatch):
    merged = {**MESH_META, "geometry": {"kind": "mesh", "merged": True, "nodes": ["fn-hit", "fn-hit2"]}}
    err, started = _request_geometry(monkeypatch, merged)
    assert err.status_code == 409 and "merges 2 nodes" in err.detail and not started


# --- the mesh preflight and the tree request ----------------------------------------------------------

VENDOR_SPEC = {
    "slug": "vendor-a-plugin",
    "asset_provider_id": "vendor-a",
    "asset_collections_field": "projects",
    "projects": ["ALPHA", "BETA"],
    "asset_collection_request": {"options": {"action": "stage-tree"}, "collection_option": "project", "label": "Tree"},
}


def _mesh_preflight_world(monkeypatch, *, admin_only=False, sweep=None, missing="unpublished"):
    async def classify(storage, scope_obj, groups, provider="design-reader"):
        out = []
        for collection, hits in groups:
            state = "ready" if collection == "alpha" else missing
            out.append([ex.greq.HitState(hit=h, state=state, message=f"{collection}: {state}") for h in hits])
        return out

    async def advertised(kind):
        return {"w": VENDOR_SPEC}

    async def requires_admin(plugin_id, pool, spec):
        return admin_only

    async def running(pool, provider):
        return sweep

    monkeypatch.setattr(ex.greq, "classify_in_scope", classify)
    monkeypatch.setattr("ada.comms.rest.routes.plugin_jobs.plugin_job_requires_admin", requires_admin)
    monkeypatch.setattr(dbm, "running_asset_sweep", running)
    return SimpleNamespace(storage=None, jobs=SimpleNamespace(advertised_specs=advertised))


#: An administrator: an asset provider's jobs (tree and geometry requests) are theirs alone.
ADMIN = SimpleNamespace(sub="admin-1", is_admin=True)


def test_the_mesh_preflight_names_the_collections_without_a_tree_and_offers_their_request(monkeypatch):
    ctx = _mesh_preflight_world(monkeypatch)
    hits = [HIT, HIT_2, {**HIT, "collection": "beta", "node_id": "fn-b"}]
    pre = asyncio.run(
        ex.export_preflight(
            ctx, None, scope_obj=Scope.shared(), body={"query": QUERY, "hits": hits, "geometry": "mesh"}, user=ADMIN
        )
    )
    assert pre["geometry"] == "mesh"
    assert (pre["hits"], pre["ready"], pre["no_tree"], pre["unknown"]) == (3, 2, 1, 0)
    assert [(c["collection"], c["ready"], c["no_tree"]) for c in pre["collections"]] == [
        ("alpha", 2, 0),
        ("beta", 0, 1),
    ]
    [req] = pre["tree_requests"]
    assert req["provider"] == "vendor-a" and req["collections"] == ["beta"]
    assert req["available"] and req["allowed"] and req["label"] == "Tree" and req["sweep_running"] is None


def test_the_mesh_preflight_tells_a_member_that_provider_trees_are_an_admins(monkeypatch):
    """An asset provider's tree request writes provider data: a member is told so up front."""
    ctx = _mesh_preflight_world(monkeypatch)
    hits = [{**HIT, "collection": "beta", "node_id": "fn-b"}]
    pre = asyncio.run(
        ex.export_preflight(
            ctx, None, scope_obj=Scope.shared(), body={"query": QUERY, "hits": hits, "geometry": "mesh"}, user=None
        )
    )
    [req] = pre["tree_requests"]
    assert req["available"] and req["allowed"] is False and "administrators" in req["reason"]


def test_the_mesh_preflight_offers_a_fresh_tree_for_hits_outside_the_published_one(monkeypatch):
    ctx = _mesh_preflight_world(monkeypatch, missing="unknown")
    hits = [HIT, {**HIT, "collection": "beta", "node_id": "fn-b"}]
    pre = asyncio.run(
        ex.export_preflight(
            ctx, None, scope_obj=Scope.shared(), body={"query": QUERY, "hits": hits, "geometry": "mesh"}, user=None
        )
    )
    assert (pre["no_tree"], pre["unknown"]) == (0, 1)
    [req] = pre["tree_requests"]
    assert req["collections"] == ["beta"]


def test_the_mesh_preflight_says_when_the_tree_request_is_not_this_users(monkeypatch):
    ctx = _mesh_preflight_world(monkeypatch, admin_only=True, sweep={"id": "s1", "created_at": "earlier"})
    hits = [{**HIT, "collection": "beta"}]
    pre = asyncio.run(
        ex.export_preflight(
            ctx, None, scope_obj=Scope.shared(), body={"query": QUERY, "hits": hits, "geometry": "mesh"}, user=None
        )
    )
    [req] = pre["tree_requests"]
    assert req["allowed"] is False and "administrators" in req["reason"]
    assert req["sweep_running"] == {"id": "s1", "created_at": "earlier"}


def _request_trees(monkeypatch, body, *, admin_only=False, refuse=None, user=ADMIN):
    from fastapi import HTTPException

    from ada.comms.rest.routes import asset_sweeps as sweeps

    ctx = _mesh_preflight_world(monkeypatch, admin_only=admin_only)
    started = {}

    async def start(ctx, pool, **kw):
        if refuse:
            raise sweeps.SweepRefused(refuse)
        started.update(kw)
        return {"id": "run-t", "job": kw["job"], "collections": kw["collections"]}

    monkeypatch.setattr(sweeps, "start_sweep", start)
    try:
        out = asyncio.run(
            ex.api_search_export_request_trees("shared", _Request(body), scope_obj=Scope.shared(), user=user, ctx=ctx)
        )
    except HTTPException as exc:
        return exc, started
    return json.loads(out.body), started


def test_requesting_the_trees_starts_one_collection_request_run(monkeypatch):
    out, started = _request_trees(monkeypatch, {"provider": "vendor-a", "collections": ["beta"]})
    assert out["id"] == "run-t"
    assert started["job"] == "collection-request" and started["collections"] == ["beta"] and started["via"] == "export"


def test_the_tree_request_is_gated_like_the_providers_job(monkeypatch):
    # An asset provider's request is an admin's, whatever its own `requires_admin` says.
    err, started = _request_trees(monkeypatch, {"provider": "vendor-a", "collections": ["beta"]}, user=None)
    assert err.status_code == 403 and not started and "administrators" in err.detail
    err, _ = _request_trees(monkeypatch, {"provider": "vendor-a", "collections": []})
    assert err.status_code == 400
    err, _ = _request_trees(
        monkeypatch, {"provider": "vendor-a", "collections": ["beta"]}, refuse="a vendor-a sweep is already running"
    )
    assert err.status_code == 409 and "already running" in err.detail


# --- request trees, then export (one run) --------------------------------------------------------------


def _tree_item_world(export_world, monkeypatch, *, why_not=None, state="unpublished"):
    from ada.comms.rest.routes import assets as assets_routes
    from ada.comms.rest.routes import geometry_requests as greq

    w = export_world()
    asked_trees = []
    published = {"done": False}

    async def classify(storage, scope_obj, groups, provider=None):
        [(_, hs)] = groups
        if published["done"]:
            return [
                [
                    greq.HitState(hit=h, state="ready", node_id=h["node_id"], subject="fn-site", revision="r1")
                    for h in hs
                ]
            ]
        return [[greq.HitState(hit=h, state=state, message="no tree") for h in hs]]

    async def request_tree(ctx, pool, run_id, position, provider, collection, **kw):
        asked_trees.append((provider, collection))
        published["done"] = why_not is None
        return why_not

    async def build(ctx, scope_obj, *, asked_provider, collection, node, subject, **kw):
        key = f"_derived/assets/vendor-a/{collection}/{subject}/r1/{node}/fp/summary.json"
        await w.store.put_bytes(scope_obj, key, json.dumps({"ok": True, "glb_key": key + ".glb"}).encode())
        return {
            "derived_key": key,
            "provider": "vendor-a",
            "subject": subject,
            "revision": "r1",
            "node": node,
            "job_id": None,
        }

    monkeypatch.setattr(greq, "classify_in_scope", classify)
    monkeypatch.setattr(ex, "request_collection_tree", request_tree)
    monkeypatch.setattr(assets_routes, "submit_node_build", build)
    hits = [ex.clean_hit(HIT, [])]
    [(provider, collection, batch)] = ex.batch_hits(hits, "out", ext="glb")
    asyncio.run(
        ex.drive_export(
            w.ctx,
            None,
            RUN,
            scope_obj=Scope.shared(),
            target=Scope.shared(),
            batches=[(provider, collection, batch)],
            job_opts={**JOB_OPTS, "geometry": "mesh", "request_trees": True},
            user=None,
        )
    )
    return w, asked_trees


def test_a_collection_with_no_tree_asks_for_it_then_builds_its_meshes(export_world, monkeypatch):
    w, asked = _tree_item_world(export_world, monkeypatch)
    assert asked == [("vendor-a", "alpha")]
    [req] = w.submitted
    assert req.conversion_options["hits"][0]["mesh"]["glb_key"].endswith(".glb")


def test_a_tree_without_the_hit_is_requested_afresh(export_world, monkeypatch):
    w, asked = _tree_item_world(export_world, monkeypatch, state="unknown")
    assert asked == [("vendor-a", "alpha")]
    [req] = w.submitted
    assert req.conversion_options["hits"][0]["mesh"]["glb_key"].endswith(".glb")


def test_a_tree_the_provider_could_not_give_is_every_hits_reason(export_world, monkeypatch):
    w, asked = _tree_item_world(export_world, monkeypatch, why_not="vendor-a had no tree to give for ALPHA: no sites")
    assert asked == [("vendor-a", "alpha")]
    [req] = w.submitted
    assert req.conversion_options["hits"][0]["mesh_error"] == "vendor-a had no tree to give for ALPHA: no sites"


def _request_tree_world(monkeypatch, summary):
    from ada.comms.rest.routes import assets as assets_routes
    from ada.comms.rest.routes import plugin_jobs

    store, items, enqueued, published = _Store(), {}, [], []

    async def advertised(kind):
        return {"w": VENDOR_SPEC}

    async def status(job_id):
        return SimpleNamespace(status="done", payload={})

    async def enqueue(ctx, **kw):
        enqueued.append(kw)
        # An asset provider's job runs in the provider store (`provider_store.job_scope`), so its
        # summary lands there -- and the request reads it, and publishes its answer, there too.
        await store.put_bytes(kw["scope_obj"], kw["derived_key"], json.dumps(summary).encode())
        return "fetch-1"

    async def staged(ctx, scope_obj, staging_id):
        assert scope_obj == Scope.providers()
        return {"tree": f"assets/_staging/{staging_id}/tree.json"}

    async def publish(ctx, scope_obj, *, provider, staged, user):
        assert scope_obj == Scope.providers()
        published.append((provider, staged))
        return "publish-1", "_derived/pub.json"

    async def update(pool, run_id, position, **fields):
        items.update(fields)

    async def beat(pool, run_id):
        return RunBeat("running", None, "Tester")

    monkeypatch.setattr(plugin_jobs, "enqueue_plugin_job", enqueue)
    monkeypatch.setattr(assets_routes, "staged_by_role", staged)
    monkeypatch.setattr(assets_routes, "submit_asset_publish", publish)
    monkeypatch.setattr(dbm, "update_asset_export_item", update)
    monkeypatch.setattr(dbm, "heartbeat_asset_export_run", beat)
    monkeypatch.setattr(ex, "POLL_INTERVAL_S", 0)
    ctx = SimpleNamespace(storage=store, jobs=SimpleNamespace(advertised_specs=advertised, status=status))
    timings: dict = {}
    why_not = asyncio.run(
        ex.request_collection_tree(
            ctx, None, "run-1", 0, "vendor-a", "alpha", scope_obj=Scope.shared(), user=None, timings=timings
        )
    )
    return why_not, enqueued, published, items, timings


def test_the_tree_request_asks_with_the_providers_own_options_and_publishes_the_answer(monkeypatch):
    why_not, enqueued, published, items, timings = _request_tree_world(monkeypatch, {"asset_staging_id": "stg-1"})
    assert why_not is None
    [job] = enqueued
    # The provider's spelling of the collection, under its collection option, as a click would send.
    assert job["options"]["action"] == "stage-tree" and job["options"]["project"] == "ALPHA"
    assert job["scope_obj"] == Scope.providers()
    assert published == [("vendor-a", {"tree": "assets/_staging/stg-1/tree.json"})]
    assert items["status"] == "requesting" and "tree_request_ms" in timings


def test_a_tree_request_with_nothing_staged_says_so(monkeypatch):
    why_not, _, published, _, _ = _request_tree_world(monkeypatch, {"asset_nothing": True, "message": "no sites"})
    assert why_not == "vendor-a had no tree to give for ALPHA: no sites" and not published


def test_requesting_trees_is_for_mesh_exports_and_gated_like_the_providers_job(monkeypatch):
    async def writable(pool, raw, user):
        return Scope.shared()

    monkeypatch.setattr(ex, "_writable_scope", writable)
    _mesh_preflight_world(monkeypatch, admin_only=True)

    async def advertised(kind):
        return {"w": VENDOR_SPEC}

    ctx = SimpleNamespace(jobs=SimpleNamespace(require=lambda f: None, advertised_specs=advertised))
    base = {"query": QUERY, "hits": [HIT], "target_scope": "shared", "target_folder": "out", "request_trees": True}
    with pytest.raises(ex.ExportRefused) as err:
        asyncio.run(ex.start_export(ctx, None, scope="shared", scope_obj=Scope.shared(), body=base, user=None))
    assert err.value.status == 400 and "mesh exports" in err.value.detail
    with pytest.raises(ex.ExportRefused) as err:
        asyncio.run(
            ex.start_export(
                ctx, None, scope="shared", scope_obj=Scope.shared(), body={**base, "geometry": "mesh"}, user=None
            )
        )
    assert err.value.status == 403 and "administrators" in err.value.detail


def test_a_component_hit_resolves_to_its_nearest_leaf_ancestor():
    # A component row of the attribute index (a primitive below an equipment): no spine lists it,
    # its `path` (root first) does. Its geometry is the equipment's -- a LEAF of the spine.
    spine = _spine("alpha", "alpha", [("fn-site", "/SITE-A"), ("fn-equi", "/EQUI-1")])
    spine.leaves = frozenset({"fn-equi"})
    component = {
        "node_id": "cmp-1",
        "full_name": "/EQUI-1/PRIM-3",
        "path": [{"node_id": "fn-site", "full_name": "/SITE-A"}, {"node_id": "fn-equi", "full_name": "/EQUI-1"}],
    }
    index = SimpleNamespace(catalog=lambda c, p: _catalog([spine]))
    got = se.resolve_geometry(index, "alpha", component)
    manifest, node = got
    assert manifest.subject == "alpha" and node == "fn-equi" and got.via == "fn-equi"
    # The nearest listed ancestor is a BRANCH: its geometry is more than the component -- no geometry.
    spine.leaves = frozenset()
    with pytest.raises(se.NoGeometry):
        se.resolve_geometry(index, "alpha", component)
    # A hit the spine lists itself is not resolved through an ancestor.
    spine.leaves = frozenset({"fn-equi"})
    assert se.resolve_geometry(index, "alpha", {"node_id": "fn-equi", "full_name": "/EQUI-1"}).via is None


def test_spine_leaves_come_from_the_leaf_column_or_from_who_is_a_parent(monkeypatch):
    import ada.assets.projection as proj
    from ada.clash.geometry_source import GeometrySourceIndex

    published = {
        # The hierarchy's own `leaf` column: only `b` is a leaf.
        "r1": [
            {"id": "a", "parent": None, "label": "A", "leaf": False},
            {"id": "b", "parent": "a", "label": "B", "leaf": True},
        ],
        # No column: a row nobody names as its parent is a leaf.
        "r2": [{"id": "a", "parent": None, "label": "A"}, {"id": "b", "parent": "a", "label": "B"}],
    }
    gsi = GeometrySourceIndex(storage=SimpleNamespace(get_bytes=lambda key: key.encode(), list_keys=lambda p: []))
    monkeypatch.setattr(gsi, "_spine_key", lambda m: m.revision)
    monkeypatch.setattr(proj, "parse_hierarchy", lambda raw: SimpleNamespace(records=lambda: published[raw.decode()]))
    for rev in ("r1", "r2"):
        assert gsi.spine(SimpleNamespace(collection="alpha", subject="alpha", revision=rev)) is not None
        assert gsi._leaves[("alpha", "alpha", rev)] == frozenset({"b"})
