"""Which element type a writer writes: the caller's rules, the source formulation, the default.

A writer for the element's own format writes back the type it was read as. A writer for another
format cannot, and says so in the conversion report. The caller can decide instead, with a
mapping or a function passed to ``to_fem(..., formulations=...)``.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import Elem, FemSection, FemSet
from ada.fem.formats import conversion_report
from ada.fem.formulations import ABAQUS, formulation
from ada.fem.shapes.definitions import ShellShapes


def _model(source=None) -> ada.Assembly:
    a = ada.Assembly("A")
    p = a.add_part(ada.Part("P"))
    mat = p.add_material(ada.Material("S355"))
    fem = p.fem
    nodes = [fem.nodes.add(ada.Node(xyz, i)) for i, xyz in enumerate([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 1)]
    el = fem.add_elem(Elem(1, nodes, ShellShapes.QUAD, el_formulation_override=source))
    es = fem.add_set(FemSet("plate", [el], "elset"))
    fem.add_section(FemSection("sec", "shell", es, mat, thickness=0.01))
    return a


def _written_type(a, tmp_path, **kwargs) -> tuple[str, conversion_report.ConversionReport]:
    with conversion_report.collect() as report:
        a.to_fem("m", "abaqus", scratch_dir=tmp_path, overwrite=True, write_input_files_only=True, **kwargs)
    text = "\n".join(p.read_text() for p in tmp_path.rglob("*.inp"))
    header = next(ln for ln in text.splitlines() if ln.upper().startswith("*ELEMENT"))
    return header.split("type=")[1].split(",")[0].strip(), report


def test_an_element_made_in_adapy_gets_the_writers_default(tmp_path):
    written, report = _written_type(_model(), tmp_path)
    assert written == "S4"
    assert not report.findings or all(f.keyword != "Formulation" for f in report.findings)


def test_the_source_formulation_of_the_same_family_is_written_back(tmp_path):
    written, _ = _written_type(_model(formulation(ABAQUS, "S4R")), tmp_path)
    assert written == "S4R"


def test_a_source_formulation_of_another_family_is_reported_when_the_default_replaces_it(tmp_path):
    written, report = _written_type(_model(formulation("sesam", "24")), tmp_path)
    assert written == "S4"
    (finding,) = [f for f in report.findings if f.keyword == "Formulation"]
    assert finding.kind == "approximated" and finding.subject == "sesam:24 -> S4"


def test_a_mapping_keyed_by_the_source_formulation_decides(tmp_path):
    written, report = _written_type(_model(formulation("sesam", "24")), tmp_path, formulations={("sesam", "24"): "S4R"})
    assert written == "S4R"
    (finding,) = [f for f in report.findings if f.keyword == "Formulation"]
    assert finding.kind == "note"


def test_a_mapping_may_be_keyed_by_shape(tmp_path):
    written, _ = _written_type(_model(), tmp_path, formulations={ShellShapes.QUAD: "M3D4"})
    assert written == "M3D4"


def test_a_function_decides_and_none_falls_through_to_the_next_rule(tmp_path):
    seen = []

    def rule(elem, source, target):
        seen.append((elem.type, source, target))
        return None

    written, _ = _written_type(_model(formulation(ABAQUS, "S4R")), tmp_path, formulations=[rule, {"S4R": "CPS4R"}])
    assert written == "CPS4R"
    assert seen == [(ShellShapes.QUAD, formulation(ABAQUS, "S4R"), "abaqus")]


def test_a_rule_naming_a_type_of_another_shape_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="not a abaqus type"):
        _written_type(_model(), tmp_path, formulations={ShellShapes.QUAD: "C3D8"})


def test_the_formulation_survives_the_array_store(tmp_path):
    """Packing object elements into the array store kept only string formulations."""
    a = ada.from_fem(_write_s4r_deck(tmp_path), "abaqus")
    (el,) = list(a.get_all_parts_in_assembly()[0].fem.elements)
    assert el.formulation == formulation(ABAQUS, "S4R")


def _write_s4r_deck(tmp_path):
    written, _ = _written_type(_model(formulation(ABAQUS, "S4R")), tmp_path / "src")
    return next((tmp_path / "src").rglob("m.inp"))
