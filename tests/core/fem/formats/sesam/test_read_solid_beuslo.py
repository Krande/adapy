"""A pressure on a solid face, through a Sesam deck and back: the BEUSLO the writer makes of it is read as the same
face, the same magnitude, and writes out as the same record. Every face of every solid with a side table."""

from __future__ import annotations

import pytest
from .test_solid_sides import _element

import ada
from ada.fem import Bc, FemSection, FemSet, LoadPressure, StepImplicitStatic, Surface
from ada.fem.formats import conversion_report
from ada.fem.formats.sesam.read.read_loads import READ_STEP_NAME
from ada.fem.formats.sesam.write.solid_sides import SESAM_SOLID_SIDES, abaqus_face, sesam_side, side_of_code
from ada.fem.shapes.solids import solid_abaqus_faces
from ada.materials.metals import CarbonSteel

SHAPES = sorted(SESAM_SOLID_SIDES, key=str)


def _faces():
    return [
        pytest.param(s, f, id=f"{s.value.lower()}-S{f + 1}") for s in SHAPES for f in range(len(solid_abaqus_faces[s]))
    ]


@pytest.mark.parametrize("shape", SHAPES)
def test_side_of_code_and_abaqus_face_undo_the_writer(shape):
    el = _element(shape)
    from ada.fem.formats.sesam.write.solid_sides import side_code

    for face in range(len(solid_abaqus_faces[shape])):
        number, _ = sesam_side(el, face)
        assert side_of_code(shape, side_code(shape, number)) == number
        assert abaqus_face(el, number) == face
    assert side_of_code(shape, 11) is None  # two sides at once names no single one


def _deck_lines(path):
    lines = next(path.glob("*T1.FEM")).read_text().splitlines()
    out, keep = [], False
    for line in lines:
        if line[:1] != " ":
            keep = line.startswith("BEUSLO")
        if keep:
            out.append(line)
    return out


@pytest.mark.parametrize("shape, face", _faces())
def test_a_solid_face_pressure_reads_back_as_the_face_it_was_written_on(tmp_path, shape, face):
    el = _element(shape)
    p = ada.Part("P")
    fem = p.fem
    for n in el.nodes:
        fem.nodes.add(n)
    fem.add_elem(el)
    mat = ada.Material("mat", CarbonSteel("S355"))
    p.add_material(mat)
    fem.add_section(FemSection("sec", "solid", fem.add_set(FemSet("solid", [el], "elset")), mat))
    fem.add_bc(Bc("fix", fem.add_set(FemSet("all", list(el.nodes), "nset")), [1, 2, 3]))
    es = fem.add_set(FemSet(f"_loaded_S{face + 1}", [el], "elset"))
    surf = fem.add_surface(Surface("loaded", Surface.TYPES.ELEMENT, [es], el_face_index=[face]))
    a = ada.Assembly("A") / p
    a.fem.add_step(StepImplicitStatic("press")).add_load(LoadPressure("q", 1250.0, surf))
    a.to_fem("deck", "sesam", scratch_dir=tmp_path, overwrite=True)
    written = _deck_lines(tmp_path / "deck")

    with conversion_report.collect() as rep:
        b = ada.from_fem(next((tmp_path / "deck").glob("*T1.FEM")))
    assert not [f.reason for f in rep.findings if f.keyword == "BEUSLO"]
    (part,) = [q for q in b.get_all_parts_in_assembly() if q.fem.steps]
    (step,) = [s for s in part.fem.steps if s.name == READ_STEP_NAME]
    ((_, lc),) = step.load_cases.items()
    (load,) = lc.loads
    assert load.magnitude == 1250.0
    assert load.surface.el_face_index == face
    assert [e.id for e in load.surface.fem_set.members] == [el.id]

    # and out again: the same record
    b.to_fem("again", "sesam", scratch_dir=tmp_path, overwrite=True)
    assert _deck_lines(tmp_path / "again") == written
