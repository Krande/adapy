"""``surface_nodes`` must honour the element face / edge a surface names.

An element-based surface names a side -- ``S3`` on a tetrahedron, ``E1`` on a shell.
Returning every node of every member element instead covers the elements' interiors
and their adjoining faces, which for a tie or a coupling means roughly twice the
nodes the deck asked for, silently tied rigidly.

The face and edge maps under test are Abaqus'; the node identities pinned below were
read off Abaqus' own numbering (see the comments in ``ada.fem.shapes.solids`` /
``shells``) and the C3D10 case matches what Abaqus itself selects on the project
deck this was written for.
"""

from __future__ import annotations

import pytest

import ada
from ada.fem import FemSet, Surface
from ada.fem.surfaces import surface_nodes


def _ids(region) -> list[int]:
    return [n.id for n in surface_nodes(region)]


def _tet10_fem() -> ada.FEM:
    """One C3D10 whose node ids equal their 1-based Abaqus slot number.

    That makes the expected face node ids readable straight off Abaqus'
    published face definitions: S1 = 1-2-3 + mid-side 5-6-7, and so on.
    """
    # corners of a unit tet, then the six mid-side nodes in Abaqus order
    corners = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    mids = [(0.5, 0, 0), (0.5, 0.5, 0), (0, 0.5, 0), (0, 0, 0.5), (0.5, 0, 0.5), (0, 0.5, 0.5)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(corners + mids)]
    fem = ada.FEM("tet", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "TETRA10"))
    return fem


def _shell_fem() -> ada.FEM:
    """One QUAD (node ids 1-4) and one TRI (node ids 3, 4, 5) sharing edge 3-4."""
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 2, 0)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("sh", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes[:4], "QUAD"))
    fem.add_elem(ada.fem.Elem(2, [nodes[2], nodes[3], nodes[4]], "TRIANGLE"))
    return fem


@pytest.mark.parametrize(
    "side, expected",
    [
        ("S1", [1, 2, 3, 5, 6, 7]),
        ("S2", [1, 4, 2, 8, 9, 5]),
        ("S3", [2, 4, 3, 9, 10, 6]),
        ("S4", [3, 4, 1, 10, 8, 7]),
    ],
)
def test_tet10_surface_returns_only_the_named_face(side, expected):
    """Six of the ten nodes, and exactly the six Abaqus puts on that face."""
    fem = _tet10_fem()
    fset = fem.add_set(FemSet(f"_s_{side}", [fem.elements.from_id(1)]))
    surf = Surface(f"s_{side}", Surface.TYPES.ELEMENT, fset, el_face_index=side, parent=fem)

    assert _ids(surf) == expected


def test_tet10_reader_style_integer_face_index():
    """The Abaqus reader normalises ``S3`` to the 0-based index 2; same answer."""
    fem = _tet10_fem()
    fset = fem.add_set(FemSet("_s", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index=2, parent=fem)

    assert _ids(surf) == [2, 4, 3, 9, 10, 6]


def test_tet10_surface_is_not_every_node_of_the_element():
    """The defect this file exists for: 6 nodes on the face, not all 10."""
    fem = _tet10_fem()
    fset = fem.add_set(FemSet("_s", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="S1", parent=fem)

    assert len(fem.elements.from_id(1).nodes) == 10
    assert len(surface_nodes(surf)) == 6


def test_femset_passed_directly_keeps_every_node():
    """A plain set names no side, so it stays the whole membership."""
    fem = _tet10_fem()
    fset = fem.add_set(FemSet("_all", [fem.elements.from_id(1)]))

    assert _ids(fset) == list(range(1, 11))


def test_node_type_surface_is_unchanged():
    """A NODE surface's members are nodes already -- no face to honour."""
    nodes = [ada.Node((i, 0, 0), i + 1) for i in range(4)]
    fem = ada.FEM("n", nodes=ada.api.containers.Nodes(nodes))
    fem.add_set(FemSet("a", nodes[:2], "nset"))
    fem.add_set(FemSet("b", nodes[2:], "nset"))
    surf = Surface("s", Surface.TYPES.NODE, None, id_refs=[("a", ""), ("b", "")], parent=fem)

    assert sorted(_ids(surf)) == [1, 2, 3, 4]


def test_shell_edge_surface_returns_only_the_edge():
    """``E2`` on a QUAD is corner nodes 2-3, not all four."""
    fem = _shell_fem()
    fset = fem.add_set(FemSet("_e2", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="E2", parent=fem)

    assert _ids(surf) == [2, 3]


def test_shell_edge_surface_over_mixed_quad_and_tri():
    """One set, two element types: each looks its own edge table up."""
    fem = _shell_fem()
    fset = fem.add_set(FemSet("_e2", [fem.elements.from_id(1), fem.elements.from_id(2)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="E2", parent=fem)

    # QUAD E2 = nodes 2-3; TRI E2 = its 2nd and 3rd nodes, i.e. 4 and 5
    assert _ids(surf) == [2, 3, 4, 5]


def test_shell_face_sides_cover_the_whole_element():
    """SPOS / SNEG are faces of the shell -- every node lies on them."""
    fem = _shell_fem()
    fset = fem.add_set(FemSet("_f", [fem.elements.from_id(1)]))

    for side in ("SPOS", "SNEG", 1, -1):
        surf = Surface(f"s{side}", Surface.TYPES.ELEMENT, fset, el_face_index=side, parent=fem)
        assert _ids(surf) == [1, 2, 3, 4]


def test_multi_face_surface_unions_its_faces_in_first_seen_order():
    """A surface holding a list of sets pairs each with its own face index."""
    fem = _tet10_fem()
    el = fem.elements.from_id(1)
    fs1 = fem.add_set(FemSet("_a", [el]))
    fs2 = fem.add_set(FemSet("_b", [el]))
    surf = Surface("s", Surface.TYPES.ELEMENT, [fs1, fs2], el_face_index=["S1", "S2"], parent=fem)

    # S1 = 1,2,3,5,6,7 then S2 = 1,4,2,8,9,5 contributing 4,8,9 as new
    assert _ids(surf) == [1, 2, 3, 5, 6, 7, 4, 8, 9]


def test_unknown_face_index_raises_naming_type_and_side():
    """Never a silent fall back to every node -- that is the bug."""
    fem = _tet10_fem()
    fset = fem.add_set(FemSet("_s", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="S9", parent=fem)

    with pytest.raises(ValueError, match="TETRA10.*S9"):
        surface_nodes(surf)


def test_solid_face_side_on_a_shell_element_raises():
    """A shell has no ``S3``; refuse rather than return the whole element."""
    fem = _shell_fem()
    fset = fem.add_set(FemSet("_s", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="S3", parent=fem)

    with pytest.raises(ValueError, match="QUAD.*S3"):
        surface_nodes(surf)


ABAQUS_MULTI_FACE_DECK = """*Heading
** multi-row element surfaces, the shape the project deck writes
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 0., 1., 0.
4, 0., 0., 1.
5, 0.5, 0., 0.
6, 0.5, 0.5, 0.
7, 0., 0.5, 0.
8, 0., 0., 0.5
9, 0.5, 0., 0.5
10, 0., 0.5, 0.5
*Element, type=C3D10
1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
*Elset, elset=_TET_SURF_S1
1,
*Elset, elset=_TET_SURF_S2
1,
*Surface, type=ELEMENT, name=TET_SURF
_TET_SURF_S1, S1
_TET_SURF_S2, S2
*Solid Section, elset=_TET_SURF_S1, material=Steel
*Material, name=Steel
*Elastic
2.1e11, 0.3
"""


def test_multi_row_surface_read_from_an_inp_honours_each_row(tmp_path):
    """The path the project deck takes: the reader keeps the side labels in
    ``id_refs`` (``fem_set`` and ``el_face_index`` stay None), so the face has to
    be read off the row rather than off ``el_face_index``."""
    inp = tmp_path / "surf.inp"
    inp.write_text(ABAQUS_MULTI_FACE_DECK)

    a = ada.from_fem(inp, "abaqus")
    (part,) = [p for p in a.get_all_parts_in_assembly(True) if p.fem.surfaces]
    surf = part.fem.surfaces["TET_SURF"]

    assert surf.fem_set is None and surf.el_face_index is None  # it is all in id_refs
    assert sorted(_ids(surf)) == [1, 2, 3, 4, 5, 6, 7, 8, 9]  # S1 u S2; node 10 is not on either


def _wedge_fem() -> ada.FEM:
    """One WEDGE, node ids 1..6 equal to their 1-based Abaqus slot.

    Triangles 1-2-3 at y=0 and 4-5-6 at y=1, so each Abaqus face is a distinct set of
    nodes and the face a surface lands on is unambiguous.
    """
    pts = [(0, 0, 0), (1, 0, 0), (0, 0, 1), (0, 1, 0), (1, 1, 0), (0, 1, 1)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("wedge", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "WEDGE"))
    return fem


def test_wedge_surface_is_named_with_an_abaqus_face_number():
    """The face number written for a solid surface must be Abaqus's, not the index of the
    visualisation face sequence.

    The two agree for tets and hexes by coincidence. They cannot for a wedge: the viz
    table triangulates the three quad faces, giving six entries for a five-faced element.
    Taking the viz index labelled Abaqus face S2 (the 4-5-6 triangle) as "S2" only by
    luck, and could name a face "S6", which no wedge has.
    """
    from ada.fem.shapes.solids import solid_abaqus_faces
    from ada.fem.surfaces import create_surface_from_nodes, solid_abaqus_face_index

    fem = _wedge_fem()
    el = fem.elements.from_id(1)

    # every Abaqus face of this wedge is found, and found as itself
    for index, slots in enumerate(solid_abaqus_faces[el.type]):
        face_nodes = [el.nodes[s] for s in slots]
        assert solid_abaqus_face_index(el, face_nodes) == index

    # ... and the quad face S4 (2-3-6-5) is named S4 in the surface it produces
    s4_nodes = [el.nodes[s] for s in solid_abaqus_faces[el.type][3]]
    surf = create_surface_from_nodes("quad_face", s4_nodes, fem)
    assert surf.el_face_index == [3], "S4 is Abaqus face index 3"
    assert any("_S4" in fs.name for fs in surf.fem_set)
    # the face really does resolve back to those four nodes
    assert {n.id for n in surface_nodes(surf)} == {n.id for n in s4_nodes}


def test_wedge_never_gets_a_face_number_it_does_not_have():
    """A wedge has five faces. The viz sequence has six entries, and its last one would
    have been written as "S6"."""
    from ada.fem.shapes.solids import solid_abaqus_faces
    from ada.fem.surfaces import solid_abaqus_face_index

    fem = _wedge_fem()
    el = fem.elements.from_id(1)
    assert len(solid_abaqus_faces[el.type]) == 5
    assert len(el.shape.faces_seq) == 6, "the viz table still triangulates; that is why this matters"
    for nodes in ([el.nodes[s] for s in slots] for slots in solid_abaqus_faces[el.type]):
        assert solid_abaqus_face_index(el, nodes) < 5


def test_tet10_surface_face_number_is_unchanged():
    """The tet path must be untouched by the wedge fix: its viz and Abaqus tables agree."""
    from ada.fem.shapes.solids import solid_abaqus_faces
    from ada.fem.surfaces import solid_abaqus_face_index

    fem = _tet10_fem()
    el = fem.elements.from_id(1)
    for index, slots in enumerate(solid_abaqus_faces[el.type]):
        corners = [el.nodes[s] for s in slots[:3]]
        assert solid_abaqus_face_index(el, corners) == index
