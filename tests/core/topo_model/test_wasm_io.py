"""The browser's procedural IO entrypoint (``ada.topo_model.wasm_io.run``): the same
engine calls the server's export / import / relocation / export-model jobs make, on a
document handed over, with no database or storage. The Pyodide worker and the node
check scripts call ``run`` exactly as these tests do."""

from __future__ import annotations

import io
import json
import pathlib
import zipfile

import pytest

from ada.topo_model import wasm_io


def _space(name, x):
    return {"NAME": name, "X": x, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3, "FUNCTION": "space", "INCLUDE": True}


def _doc():
    return {"spaces": [_space("Space_01", 0), _space("Space_02", 5)], "equipments": [], "systems": [], "openings": []}


def test_default_engine_workbook_round_trips():
    data = wasm_io.run("export_xlsx", json.dumps({"doc": _doc(), "name": "rt"}))
    assert isinstance(data, bytes) and data[:2] == b"PK"
    doc = json.loads(wasm_io.run("import_xlsx", json.dumps({"engine": None}), data))
    assert doc["engine"] == "adapy-default"
    assert sorted(s["NAME"] for s in doc["spaces"]) == ["Space_01", "Space_02"]
    # Plain JSON: no NaN for the workbook's empty cells (JSON.parse refuses it).
    json.loads(json.dumps(doc), parse_constant=lambda c: pytest.fail(f"non-finite {c} in the doc"))


def test_the_engine_slug_adapy_default_is_the_default_engine():
    data = wasm_io.export_xlsx(_doc(), engine="adapy-default", name="d")
    assert wasm_io.import_xlsx(data, engine="adapy-default")["engine"] == "adapy-default"


_ENGINE = """
import io


def compile(doc, **kwargs):
    raise NotImplementedError


def export_xlsx(doc, name=None):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Cells"
    for i, space in enumerate(doc.get("spaces") or [], start=1):
        wb.active.cell(row=i, column=1, value=space["NAME"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_xlsx(data):
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(data))["Cells"]
    names = [r[0] for r in ws.iter_rows(values_only=True) if r[0]]
    return {"spaces": [{"NAME": n, "X": 0, "Y": 0, "Z": 0, "DX": 1, "DY": 1, "DZ": 1} for n in names]}
"""


@pytest.fixture
def registered_engine(tmp_path: pathlib.Path, monkeypatch):
    """An engine announced in this process the way a baked plugin wheel's register hook
    announces it in the browser: a slug and a compile entrypoint, nothing else."""
    from ada.topo_model import engine_catalog

    (tmp_path / "wheel_engine.py").write_text(_ENGINE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(engine_catalog, "_ENGINE_CAPABILITY_REGISTRY", dict(engine_catalog._ENGINE_CAPABILITY_REGISTRY))
    engine_catalog.register_procedural_engine_capabilities(
        "wheel-engine", name="Wheel", entrypoint="wheel_engine:compile"
    )
    return "wheel-engine"


def test_a_registered_engine_exports_and_imports_through_its_sibling_handlers(registered_engine):
    data = wasm_io.run("export_xlsx", {"doc": _doc(), "engine": registered_engine})
    doc = json.loads(wasm_io.run("import_xlsx", {"engine": registered_engine}, data))
    assert doc["engine"] == registered_engine
    assert [s["NAME"] for s in doc["spaces"]] == ["Space_01", "Space_02"]


def test_an_engine_with_no_excel_format_is_refused(raw_box_engine):
    from ada.topo_model.engines import EngineHasNoExcelFormat

    with pytest.raises(EngineHasNoExcelFormat):
        wasm_io.export_xlsx(_doc(), engine=raw_box_engine)


def test_relocations_answer_the_proposals_document():
    result = json.loads(wasm_io.run("propose_relocations", {"doc": _doc(), "equipment": {}}))
    assert set(result) >= {"proposals", "unresolved"}
    assert result["proposals"] == []


def test_export_model_genie_xml_and_workspace():
    xml = wasm_io.run("export_model", {"doc": _doc(), "format": "gxml", "name": "m"})
    assert b"DNV_structure_concept_protocol" in xml[:2000] or b"<model" in xml
    gnx = wasm_io.run("export_model", {"doc": _doc(), "format": "gnx", "name": "m"})
    with zipfile.ZipFile(io.BytesIO(gnx)) as z:
        assert "modelData.xml" in z.namelist() and "acisGeometry.sat" in z.namelist()


def test_export_model_ifc():
    pytest.importorskip("ifcopenshell")
    data = wasm_io.run("export_model", {"doc": _doc(), "format": "ifc", "name": "m"})
    assert data.lstrip().startswith(b"ISO-10303-21")


def test_unknown_action_and_format_are_refused():
    with pytest.raises(ValueError, match="unknown procedural io action"):
        wasm_io.run("nope", {})
    with pytest.raises(ValueError, match="unsupported export format"):
        wasm_io.run("export_model", {"doc": _doc(), "format": "step"})
