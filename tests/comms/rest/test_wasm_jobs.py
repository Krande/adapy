"""``ada.comms.rest.wasm_jobs``: the core jobs the browser runs, on the worker handler's own work.

Run here under CPython against an in-memory storage facade -- what the browser's Pyodide worker
runs with blob GETs over the API. That the same calls give the same answers under Pyodide is
checked with node-Pyodide outside this suite (the adacpp wasm wheel cannot load in CPython).
"""

from __future__ import annotations

import io
import json
import pathlib

import pytest

import ada
from ada.comms.rest import browser_jobs, wasm_jobs


class _Store:
    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)

    def get_bytes(self, key):
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]

    def list_keys(self, prefix=""):
        return sorted(k for k in self.files if k.startswith(prefix))

    def fetch_to_path(self, key, dest):
        pathlib.Path(dest).write_bytes(self.get_bytes(key))
        return dest


def _frame() -> ada.Assembly:
    a = ada.Assembly("Frame")
    p = ada.Part("Deck")
    a.add_part(p)
    p.add_beam(ada.Beam("g1", (0, 0, 0), (4, 0, 0), "IPE300"))
    p.add_beam(ada.Beam("g2", (2, 0, 0), (2, 3, 0), "IPE300"))
    return a


@pytest.fixture(scope="module")
def frame_files(tmp_path_factory) -> dict[str, bytes]:
    d = tmp_path_factory.mktemp("frame")
    _frame().to_ifc(d / "frame.ifc")
    buf = io.BytesIO()
    _frame().to_gltf(buf)
    return {"models/frame.ifc": (d / "frame.ifc").read_bytes(), "models/frame.glb": buf.getvalue()}


def test_the_kinds_are_the_ones_the_api_claims():
    assert set(wasm_jobs.KINDS) == set(browser_jobs.BROWSER_KINDS)


def test_an_unknown_kind_is_an_error_not_an_exception():
    res = wasm_jobs.run({"kind": "glb"}, _Store({}))
    assert res["status"] == "error" and "does not run 'glb'" in res["error"] and res["outputs"] == {}


def test_clash_check_over_a_file(frame_files):
    claim = {
        "kind": "clash_check",
        "source_key": "models/frame.ifc",
        "derived_key": "_derived/clash/a/b/result.json",
        "conversion_options": {"source_key": "models/frame.ifc", "options": {}},
    }
    res = wasm_jobs.run(claim, _Store(frame_files))
    assert res["status"] == "done", res
    doc = json.loads(res["outputs"]["result"])
    assert doc["source_key"] == "models/frame.ifc"
    assert doc["counts"]["members"] == 2 and len(doc["joints"]) == 1
    assert doc["provenance"]["executed_in"] == "browser"
    # The document the API checks it against before storing it.
    browser_jobs.validate_output(claim, "result", res["outputs"]["result"])


def test_a_convex_contact_run_with_its_specs(frame_files):
    pytest.importorskip("adacpp.clash")
    target = {"kind": "file", "source_key": "models/frame.ifc"}
    claim = {
        "kind": "clash_contacts",
        "source_key": "models/frame.glb",
        "derived_key": "",
        "conversion_options": {
            "mode": "check",
            "target": target,
            "glb_key": "models/frame.glb",
            "up_axis": "z",
            "options": {},
            "run_id": "r1",
            "annotate": True,
        },
    }
    from ada.clash.sidecar import run_key

    claim["derived_key"] = run_key(target, "r1")
    res = wasm_jobs.run(claim, _Store(frame_files))
    assert res["status"] == "done", res
    assert set(res["outputs"]) >= {"run"}
    browser_jobs.validate_output(claim, "run", res["outputs"]["run"])
    assert browser_jobs.output_target(claim, "run") == (claim["derived_key"], None, "run")


def test_equipment_bbox_of_a_mesh():
    box = ada.Assembly("Pump") / (ada.Part("P") / ada.PrimBox("body", (0, 0, 0), (1.2, 0.8, 1.5)))
    buf = io.BytesIO()
    box.to_gltf(buf)
    claim = {
        "kind": "equipment_bbox",
        "derived_key": "_equipment/t1/preview.glb",
        "conversion_options": {"type_id": "t1", "cad_key": "_equipment/t1/source.glb", "cad_z_up": True},
    }
    res = wasm_jobs.run(claim, _Store({"_equipment/t1/source.glb": buf.getvalue()}))
    assert res["status"] == "done", res
    bbox = json.loads(res["outputs"]["bbox"])
    assert bbox == pytest.approx({"lx": 1.2, "ly": 0.8, "lz": 1.5})
    assert res["outputs"]["preview"][:4] == b"glTF"


def test_a_selection_the_source_does_not_hold_fails_with_its_reason(frame_files):
    pytest.importorskip("ifcopenshell")
    claim = {
        "kind": "export_selection",
        "source_key": "models/frame.ifc",
        "derived_key": "_derived/exports/x.ifc",
        "conversion_options": {"source_key": "models/frame.ifc", "format": "ifc", "element": "nope", "path": []},
    }
    res = wasm_jobs.run(claim, _Store(frame_files))
    assert res["status"] == "error" and "nope" in res["error"]


def test_a_utility_overlay_comes_back_as_an_output(monkeypatch, frame_files):
    from ada.comms.rest.utility import UtilityRegistry

    def _fake(scene, *, storage, scope, on_progress, **kwargs):
        storage.put_bytes("_overlays/m.fake.glb", b"glTF....")
        return {"version": 1, "ops": [{"op": "add_overlay_geometry", "blob_key": "_overlays/m.fake.glb"}]}

    monkeypatch.setitem(UtilityRegistry._entries, "fake", _fake)
    claim = {
        "kind": "utility",
        "source_key": "models/frame.glb",
        "derived_key": "_derived/models/frame.glb.fake.viewops.json",
        "conversion_options": {"utility_name": "fake", "kwargs": {}},
    }
    res = wasm_jobs.run(claim, _Store(frame_files))
    assert res["status"] == "done", res
    assert set(res["outputs"]) == {"payload", "overlay:_overlays/m.fake.glb"}
    assert browser_jobs.output_target(claim, "overlay:_overlays/m.fake.glb") == ("_overlays/m.fake.glb", None, "blob")
