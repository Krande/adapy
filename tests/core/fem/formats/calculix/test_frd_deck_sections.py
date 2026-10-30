"""A CalculiX result's beams drawn as solids, from the input deck beside the ``.frd``.

``files/rect_b31_2d.{inp,frd}``: two B31 elements of a RECT section, solved with ccx 2.23 with
``*NODE FILE, OUTPUT=2D`` / ``*EL FILE, OUTPUT=2D`` -- the ``.frd`` then holds the line elements
under their deck numbers. (Without OUTPUT=2D ccx writes each beam as the brick it expanded it
into, with new nodes: the result is already a solid, and the graft finds no line to match.)

``files/two_steps_u1.frd``: two U1 beams, which ccx leaves out of the ``.frd`` entirely.
"""

import json
import pathlib
import shutil

import numpy as np

import ada
from ada.api.fem_tasks import _with_line_sections
from ada.fem import Elem, FemSection, FemSet
from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto
from ada.fem.results.artefacts import (
    BeamSolidInstances,
    FEAResultStreamAdapter,
    bake_artefacts,
)
from ada.fem.shapes.definitions import LineShapes
from ada.sections.categories import BaseTypes

FILES = pathlib.Path(__file__).parent / "files"


def _copy(tmp_path, stem, *suffixes):
    for suffix in suffixes:
        shutil.copyfile(FILES / f"{stem}{suffix}", tmp_path / f"model{suffix}")
    return tmp_path / "model.frd"


def test_frd_takes_the_sections_of_the_deck_beside_it(tmp_path):
    res = read_from_frd_file_proto(_copy(tmp_path, "rect_b31_2d", ".frd", ".inp"))
    assert sorted(int(r[0]) for r in res.mesh.elem_data) == [1, 2]
    (sec,) = {s.name: s for s in res.mesh.sections.values()}.values()
    assert sec.type == BaseTypes.FLATBAR and (sec.w_top, sec.h) == (0.05, 0.1)
    elem = res.mesh.get_line_elems()[0]
    # n1 = +Y on the deck: the RECT's height (local 2 = n2 = t x n1) along +Z.
    assert np.allclose(elem.fem_sec.local_z, [0, 0, 1])

    beams = FEAResultStreamAdapter(res).try_solid_beams(format="compact")
    assert isinstance(beams, BeamSolidInstances) and beams.n_beams == 2


def test_bake_offers_beam_solids(tmp_path):
    res = read_from_frd_file_proto(_copy(tmp_path, "rect_b31_2d", ".frd", ".inp"))
    bake = bake_artefacts(FEAResultStreamAdapter(res), tmp_path / "out", src="model")
    mesh = json.loads(bake.manifest_path.read_text())["mesh"]
    assert mesh["beam_solids_compact_url"] and mesh["n_beam_solids"] == 2


def test_without_a_deck_there_are_no_sections(tmp_path):
    res = read_from_frd_file_proto(_copy(tmp_path, "rect_b31_2d", ".frd"))
    assert not res.mesh.sections


def test_a_deck_of_other_elements_gives_nothing(tmp_path):
    frd = _copy(tmp_path, "rect_b31_2d", ".frd", ".inp")
    deck = frd.with_suffix(".inp")
    deck.write_text(deck.read_text().replace("1, 0., 0., 0.", "1, 0., 0., 0.25"))  # element 1 moved
    res = read_from_frd_file_proto(frd)
    assert sorted(int(r[0]) for r in res.mesh.elem_data) == [2]


def test_a_general_section_deck_is_not_read(tmp_path):
    frd = _copy(tmp_path, "rect_b31_2d", ".frd", ".inp")
    deck = frd.with_suffix(".inp")
    deck.write_text(deck.read_text().replace("SECTION=RECT\n0.05, 0.1", "SECTION=GENERAL\n0.005, 4e-6, 0., 1e-6, 1e-6"))
    assert not read_from_frd_file_proto(frd).mesh.sections


def test_u1_beams_left_out_of_the_frd_are_added_back_from_the_solved_model(tmp_path):
    res = read_from_frd_file_proto(_copy(tmp_path, "two_steps_u1", ".frd"))
    assert not res.mesh.elements  # ccx writes no U1 element

    # The solved model, numbered as the deck that produced the .frd was.
    p = ada.Part("P")
    fem = p.fem
    nodes = [fem.nodes.add(ada.Node((x, 0, 0), nid)) for nid, x in ((1, 0.0), (2, 0.5), (3, 1.0))]
    els = [fem.add_elem(Elem(i, [nodes[i - 1], nodes[i]], "LINE")) for i in (1, 2)]
    fs = fem.add_set(FemSet("beams", els, "elset"))
    mat = ada.Material("S355")
    p.add_material(mat)
    fem.add_section(
        FemSection("bsec", "line", fs, mat, section=ada.Section("IPE300", from_str="IPE300"), local_z=(0, 0, 1))
    )
    a = ada.Assembly("a") / p

    _with_line_sections(res, a)
    (block,) = res.mesh.elements
    assert isinstance(block.elem_info.type, LineShapes)
    assert sorted(block.identifiers.tolist()) == [1, 2]
    beams = FEAResultStreamAdapter(res).try_solid_beams(format="compact")
    assert beams.n_beams == 2


def test_a_model_numbered_differently_adds_nothing(tmp_path):
    res = read_from_frd_file_proto(_copy(tmp_path, "two_steps_u1", ".frd"))
    # Beam.to_fem_obj numbers the middle node 3: same ids, other places -- not the solved model.
    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE300")
    a = ada.Assembly("a") / (ada.Part("P") / bm)
    a.get_part("P").fem = bm.to_fem_obj(0.5, "line")
    _with_line_sections(res, a)
    assert not res.mesh.elements and not res.mesh.sections
