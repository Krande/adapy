"""The design model's beam sections are grafted onto a result mesh that has none.

An Abaqus ODB dump or a Code_Aster MED result is points and connectivity only, so their bundles
could not draw beams as solids. The graft carries the solved model's sections across by element
id -- and only where the result's element really is the same element.
"""

import numpy as np

import ada
from ada.fem.results.common import ElementBlock, ElementInfo, FemNodes, Mesh
from ada.fem.results.line_sections import graft_line_sections, line_section_tables
from ada.fem.shapes.definitions import LineShapes


def _design():
    bm = ada.Beam("bm", (0, 0, 0), (3, 0, 0), "IPE300")
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(1.0, "line")
    return p.fem


def _result_mesh(fem, shift_ids=0, move=0.0):
    """A result mesh as a reader would build it: the design's nodes and line elements, by id."""
    nodes = sorted(fem.nodes, key=lambda n: n.id)
    coords = np.array([n.p for n in nodes], dtype=float)
    coords[:, 1] += move
    lines = list(fem.elements.lines)
    block = ElementBlock(
        elem_info=ElementInfo(type=LineShapes.LINE, source_software=None, source_type="B31"),
        node_refs=np.array([[n.id for n in el.nodes] for el in lines], dtype=int),
        identifiers=np.array([el.id + shift_ids for el in lines], dtype=int),
    )
    return Mesh(elements=[block], nodes=FemNodes(coords=coords, identifiers=np.array([n.id for n in nodes])))


def test_sections_are_grafted_by_id_and_position():
    fem = _design()
    tables = line_section_tables(fem.elements.lines)
    mesh = _result_mesh(fem)

    assert graft_line_sections(mesh, tables) == len(list(fem.elements.lines))
    (elem, *_) = mesh.get_line_elems()
    assert elem.fem_sec.section.name == "IPE300"
    assert elem.fem_sec.local_z is not None


def test_a_renumbered_result_gets_no_sections_rather_than_wrong_ones():
    fem = _design()
    tables = line_section_tables(fem.elements.lines)
    # Same ids, elements somewhere else: not the same elements.
    moved = _result_mesh(fem, move=1.0)
    assert graft_line_sections(moved, tables) == 0
    assert not moved.sections
    # Shifted ids: nothing to match.
    shifted = _result_mesh(fem, shift_ids=1000)
    assert graft_line_sections(shifted, tables) == 0


def test_a_mesh_with_sections_of_its_own_keeps_them():
    fem = _design()
    tables = line_section_tables(fem.elements.lines)
    mesh = _result_mesh(fem)
    mesh.sections = {1: "the reader's own"}
    assert graft_line_sections(mesh, tables) == 0
    assert mesh.sections == {1: "the reader's own"}


def test_to_mesh_still_carries_the_tables():
    fem = _design()
    mesh = fem.to_mesh()
    assert len(mesh.elem_data) == len(list(fem.elements.lines))
    assert {s.name for s in mesh.sections.values()} == {"IPE300"}
