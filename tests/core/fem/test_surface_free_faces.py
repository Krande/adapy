"""An element-surface entry naming no face identifier, and the facets of a surface.

Two things are pinned here.

**The free-face rule.** For a *continuum* element, an element-based surface entry that names
no face identifier -- ``_LinDepPlSurf_,`` in the project deck -- covers the **free faces** of
those elements: faces shared with another element of the mesh are not on the surface, and a
fully interior element contributes nothing at all. Reading it as the whole element instead
pulls in nodes lying on faces buried inside the body, which for a tie or a shell-to-solid
coupling means rigid arms to nodes that are on no surface. For a **shell** the blank side does
mean the whole element -- it names both faces and every node lies on each -- and that is
pinned too, because it is the half of the old behaviour that was right.

**The facet API.** ``surface_facets`` enumerates those facets as ordered node tuples with their
topology, which is what a caller needs to do geometry on the surface -- project a point onto a
facet and evaluate its shape functions -- rather than merely collect its nodes.

The meshes below are conforming C3D10 meshes built corner-first, so the faces really are
shared and the rule has a real mesh to read rather than a hand-written adjacency.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import FemSet, Surface
from ada.fem.formats import conversion_report
from ada.fem.shapes.definitions import LineShapes, ShellShapes
from ada.fem.surfaces import (
    free_face_indices,
    is_blank_side,
    surface_facets,
    surface_nodes,
)

# Abaqus C3D10 mid-side edges, in node order 5..10: 1-2, 2-3, 1-3, 1-4, 2-4, 3-4.
_TET10_MID_EDGES = ((0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3))

# The central tet, and an apex on the far side of each of its four Abaqus faces.
_CORNERS = {
    1: (0.0, 0.0, 0.0),
    2: (1.0, 0.0, 0.0),
    3: (0.0, 1.0, 0.0),
    4: (0.0, 0.0, 1.0),
    5: (0.25, 0.25, -1.0),  # beyond S1 = 1-2-3
    6: (0.25, -1.0, 0.25),  # beyond S2 = 1-4-2
    7: (1.0, 1.0, 1.0),  # beyond S3 = 2-4-3
    8: (-1.0, 0.25, 0.25),  # beyond S4 = 3-4-1
}
_CENTRAL = (1, 2, 3, 4)
#: One neighbour per Abaqus face of the central tet, keyed by that face's number.
_NEIGHBOURS = {1: (1, 2, 3, 5), 2: (1, 4, 2, 6), 3: (2, 4, 3, 7), 4: (3, 4, 1, 8)}


def _tet10_mesh(tets) -> ada.FEM:
    """A conforming C3D10 mesh: ``tets`` are 4-tuples of keys into ``_CORNERS``.

    Mid-side nodes are created once per edge and shared between the elements that use it, so
    two elements listing the same three corners really do share a face of the mesh. Element
    ids are 1-based in the order given, so element 1 is always the first tuple.
    """
    nodes = {}
    mid_ids = {}
    next_mid = [100]

    def node_for(key, point):
        if key not in nodes:
            nodes[key] = ada.Node(point, key if isinstance(key, int) else next_mid[0])
        return nodes[key]

    elems = []
    for tet in tets:
        corner_nodes = [node_for(k, _CORNERS[k]) for k in tet]
        el_nodes = list(corner_nodes)
        for a, b in _TET10_MID_EDGES:
            edge = frozenset((tet[a], tet[b]))
            if edge not in mid_ids:
                next_mid[0] += 1
                mid_ids[edge] = next_mid[0]
                p = tuple((np.asarray(_CORNERS[tet[a]]) + np.asarray(_CORNERS[tet[b]])) / 2.0)
                nodes[edge] = ada.Node(p, mid_ids[edge])
            el_nodes.append(nodes[edge])
        elems.append(el_nodes)

    fem = ada.FEM("m", nodes=ada.api.containers.Nodes(list(nodes.values())))
    for i, el_nodes in enumerate(elems, start=1):
        fem.add_elem(ada.fem.Elem(i, el_nodes, "TETRA10"))
    return fem


def _blank_side_surface(fem: ada.FEM, elements, *, via_id_refs: bool) -> Surface:
    """A surface whose single entry names ``elements`` and no face identifier.

    Both spellings the Abaqus reader produces: ``via_id_refs`` is the multi-row form the
    project deck uses (``_LinDepPlSurf_,``), the other is the single-row form.
    """
    fset = fem.add_set(FemSet("_blank", list(elements)))
    if via_id_refs:
        return Surface("s", Surface.TYPES.ELEMENT, None, id_refs=[(fset.name, "")], parent=fem)
    return Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="", parent=fem)


def _ids(region) -> list[int]:
    return [n.id for n in surface_nodes(region)]


# ---------------------------------------------------------------------------
# the rule


@pytest.mark.parametrize("via_id_refs", [True, False])
def test_sideless_solid_entry_covers_only_the_free_face(via_id_refs):
    """Three of the central tet's four faces are shared, so only S1's six nodes are on it.

    This is the project deck's own shape: each of its 132 side-less C3D10 elements has exactly
    one free face, and six of its ten nodes lie on it. The other four -- the apex and the
    mid-side nodes of the edges running to it -- are inside the casting.
    """
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    central = fem.elements.from_id(1)
    surf = _blank_side_surface(fem, [central], via_id_refs=via_id_refs)

    assert len(central.nodes) == 10
    s1 = [central.nodes[i].id for i in (0, 1, 2, 4, 5, 6)]
    assert _ids(surf) == s1
    assert central.nodes[3].id not in _ids(surf), "the apex is interior to the body"


def test_fully_interior_element_contributes_nothing():
    """All four faces shared -> the element is buried, and a buried element is on no surface."""
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (1, 2, 3, 4)])
    surf = _blank_side_surface(fem, [fem.elements.from_id(1)], via_id_refs=True)

    assert free_face_indices([fem.elements.from_id(1)]) == [[]]
    assert surface_nodes(surf) == []
    assert surface_facets(surf) == []


def test_free_faces_are_shared_with_the_mesh_not_just_the_listed_set():
    """A face is not free because its neighbour was left out of the surface's element set.

    The distinguishing test between the two readings: adjacency is over the mesh, so listing
    only the central tet still hides the face it shares with a neighbour that is not listed.
    Restricting adjacency to the listed elements would call all four faces free and hand back
    every node of the element -- i.e. the bug, with extra steps.
    """
    fem = _tet10_mesh([_CENTRAL, _NEIGHBOURS[1]])
    central = fem.elements.from_id(1)

    assert free_face_indices([central]) == [[1, 2, 3]], "S1 is shared with element 2"
    surf = _blank_side_surface(fem, [central], via_id_refs=True)
    faces = {f.node_indices for f in surface_facets(surf)}
    from ada.fem.shapes.solids import solid_abaqus_faces

    assert faces == {tuple(solid_abaqus_faces[central.type][i]) for i in (1, 2, 3)}


def test_shell_with_a_blank_side_keeps_the_whole_element():
    """A blank side on a shell names both faces, and every node lies on each of them.

    The half of the old behaviour that was correct. A shell is not a volume: it has no
    interior for a free-face rule to exclude.
    """
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("sh", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes[:4], "QUAD"))
    fem.add_elem(ada.fem.Elem(2, [nodes[1], nodes[4], nodes[5], nodes[2]], "QUAD"))  # shares edge 2-3

    surf = _blank_side_surface(fem, [fem.elements.from_id(1)], via_id_refs=True)
    assert _ids(surf) == [1, 2, 3, 4]

    (facet,) = surface_facets(surf)
    assert facet.shape is ShellShapes.QUAD
    assert facet.node_ids == (1, 2, 3, 4)


def test_a_femset_is_still_whole_membership_even_next_to_neighbours():
    """``None`` is not a blank side. A plain set is adapy's own "the members, whole"."""
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    central = fem.elements.from_id(1)
    fset = fem.add_set(FemSet("_all", [central]))

    assert is_blank_side("") and is_blank_side("  ")
    assert not is_blank_side(None) and not is_blank_side("S3") and not is_blank_side(0)
    assert _ids(fset) == [n.id for n in central.nodes]


def test_a_named_face_is_unaffected_by_the_free_face_rule():
    """``S1, S1`` covers S1 whether or not another element is on the other side of it."""
    fem = _tet10_mesh([_CENTRAL, _NEIGHBOURS[1]])
    central = fem.elements.from_id(1)
    fset = fem.add_set(FemSet("_s1", [central]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="S1", parent=fem)

    assert _ids(surf) == [central.nodes[i].id for i in (0, 1, 2, 4, 5, 6)]


# ---------------------------------------------------------------------------
# when the free faces cannot be determined, it is reported


def test_unknown_element_faces_are_reported_and_fall_back_to_the_whole_element():
    """No Abaqus face numbering for the type -> say so; do not invent a boundary.

    Falling back to the whole element keeps a superset, which for a coupling still contains
    the real surface; falling back to nothing would discard real surface, which is measurably
    worse. Either way the engineer is told, which is the point.
    """
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0.5, 1)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("p", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "PYRAMID5"))
    surf = _blank_side_surface(fem, [fem.elements.from_id(1)], via_id_refs=True)

    with conversion_report.collect() as report:
        assert _ids(surf) == [1, 2, 3, 4, 5]

    (finding,) = report.of_kind(conversion_report.SUSPECT)
    assert finding.keyword == "*SURFACE"
    assert "free" in finding.reason and "interior" in finding.reason
    assert finding.details["n_elements"] == 1


def test_missing_node_back_references_are_reported_not_taken_as_no_neighbours():
    """Adjacency is read off ``Node.refs``; an empty ``refs`` is unknown, not "no neighbour".

    A ``FEM`` rebuilt from a pickle drops ``Node._refs`` (``Node.__getstate__`` clears it).
    Treating that as "nothing is adjacent" would call every face free and quietly turn a
    buried element into a surface.
    """
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (1, 2, 3, 4)])
    central = fem.elements.from_id(1)
    for n in fem.nodes:
        n.refs.clear()

    assert free_face_indices([central]) is None
    surf = _blank_side_surface(fem, [central], via_id_refs=True)
    with conversion_report.collect() as report:
        assert _ids(surf) == [n.id for n in central.nodes]
    assert len(report.of_kind(conversion_report.SUSPECT)) == 1


def test_determined_free_faces_report_nothing():
    """The finding is news, not noise: a surface that resolves cleanly is silent."""
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    surf = _blank_side_surface(fem, [fem.elements.from_id(1)], via_id_refs=True)

    with conversion_report.collect() as report:
        surface_nodes(surf)
        surface_facets(surf)
    assert report.findings == []


# ---------------------------------------------------------------------------
# the facet API


def test_facets_of_a_sideless_solid_entry_are_its_free_faces():
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    central = fem.elements.from_id(1)
    surf = _blank_side_surface(fem, [central], via_id_refs=True)

    (facet,) = surface_facets(surf)
    assert facet.element is central
    assert facet.shape is ShellShapes.TRI6
    assert facet.side == ""
    assert facet.node_indices == (0, 1, 2, 4, 5, 6)
    assert facet.node_ids == tuple(central.nodes[i].id for i in facet.node_indices)


def test_a_free_face_facet_keeps_abaqus_order_too():
    """Not just the named-face path: a free face is an Abaqus face and carries its ordering.

    The free face here is S3, whose slots are ``(1, 3, 2, 8, 9, 5)`` -- deliberately not in
    ascending order, unlike S1's, so sorting the tuple somewhere in the free-face path shows up.
    """
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (1, 2, 4)])
    central = fem.elements.from_id(1)
    surf = _blank_side_surface(fem, [central], via_id_refs=True)

    (facet,) = surface_facets(surf)
    assert facet.node_indices == (1, 3, 2, 8, 9, 5), "Abaqus S3 = 2-4-3 + mids 9-10-6"
    assert facet.node_ids == tuple(central.nodes[i].id for i in (1, 3, 2, 8, 9, 5))
    assert _ids(surf) == list(facet.node_ids)


def test_facet_nodes_are_in_abaqus_face_order():
    """Ordered, not a set: a caller evaluating shape functions on the facet depends on it.

    Abaqus lists a face's corners in winding order and then the mid-side node of each of
    those edges in the same order, so a TRI6 facet reads corner, corner, corner, mid(1-2),
    mid(2-3), mid(3-1) -- and the geometry has to agree with that.
    """
    fem = _tet10_mesh([_CENTRAL, _NEIGHBOURS[1]])
    central = fem.elements.from_id(1)
    fset = fem.add_set(FemSet("_s3", [central]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="S3", parent=fem)

    (facet,) = surface_facets(surf)
    assert facet.node_indices == (1, 3, 2, 8, 9, 5), "Abaqus S3 = 2-4-3 + mids 9-10-6"
    p = facet.points
    assert p.shape == (6, 3)
    for corner, mid in ((0, 3), (1, 4), (2, 5)):
        nxt = (corner + 1) % 3
        assert np.allclose(p[mid], (p[corner] + p[nxt]) / 2.0), "mid-side node follows its two corners"


def test_shell_edge_facets_are_line_facets():
    """A shell edge is a one-dimensional facet, and its shape says whether it has a mid node."""
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.5, 0, 0), (1, 0.5, 0), (0.5, 1, 0), (0, 0.5, 0)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("sh", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "QUAD8"))
    fset = fem.add_set(FemSet("_e2", [fem.elements.from_id(1)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, fset, el_face_index="E2", parent=fem)

    (facet,) = surface_facets(surf)
    assert facet.shape is LineShapes.LINE3
    assert facet.node_ids == (2, 3, 6)


def test_shell_face_side_gives_one_facet_over_the_whole_element():
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("sh", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "QUAD"))
    fset = fem.add_set(FemSet("_f", [fem.elements.from_id(1)]))

    for side in ("SPOS", "SNEG", 1, -1):
        surf = Surface(f"s{side}", Surface.TYPES.ELEMENT, fset, el_face_index=side, parent=fem)
        (facet,) = surface_facets(surf)
        assert facet.shape is ShellShapes.QUAD
        assert facet.node_ids == (1, 2, 3, 4)
        assert facet.side == side


def test_regions_without_facets_yield_none():
    """A node set has no facets, and neither has a solid element a region names with no side.

    ``surface_nodes`` still answers for both -- the two are different questions.
    """
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    central = fem.elements.from_id(1)
    assert surface_facets(fem.add_set(FemSet("_all", [central]))) == []
    assert _ids(FemSet("_all2", [central])) == [n.id for n in central.nodes]

    nset = fem.add_set(FemSet("_n", list(central.nodes), "nset"))
    surf = Surface("ns", Surface.TYPES.NODE, nset, parent=fem)
    assert surface_facets(surf) == []
    assert len(surface_nodes(surf)) == 10


def test_facets_and_nodes_are_the_same_reading_of_the_surface():
    """The two must not drift: every facet node is a surface node and vice versa.

    A surface with a side-less solid entry *and* named faces, which is the project deck's
    shape, so the union is over both kinds of entry.
    """
    fem = _tet10_mesh([_CENTRAL] + [_NEIGHBOURS[f] for f in (2, 3, 4)])
    central = fem.elements.from_id(1)
    blank = fem.add_set(FemSet("_blank", [central]))
    named = fem.add_set(FemSet("_named", [fem.elements.from_id(2)]))
    surf = Surface("s", Surface.TYPES.ELEMENT, None, id_refs=[(blank.name, ""), (named.name, "S1")], parent=fem)

    facet_nodes = {n.id for f in surface_facets(surf) for n in f.nodes}
    assert facet_nodes == {n.id for n in surface_nodes(surf)}
    assert len(surface_facets(surf)) == 2


# ---------------------------------------------------------------------------
# the face-shape tables


def test_face_shape_tables_line_up_with_the_face_tables():
    """One shape per face, for every type that has faces -- a gap here is a KeyError later."""
    from ada.fem.shapes.shells import shell_abaqus_edge_shapes, shell_abaqus_edges
    from ada.fem.shapes.solids import solid_abaqus_face_shapes, solid_abaqus_faces

    for faces, shapes in (
        (solid_abaqus_faces, solid_abaqus_face_shapes),
        (shell_abaqus_edges, shell_abaqus_edge_shapes),
    ):
        assert set(faces) == set(shapes)
        for key in faces:
            assert len(faces[key]) == len(shapes[key]), key


def test_wedge_faces_are_two_triangles_and_three_quads():
    """The shape is per *face*, not per element: a wedge has both kinds."""
    from ada.fem.shapes.definitions import SolidShapes
    from ada.fem.shapes.solids import solid_abaqus_face_shapes

    assert solid_abaqus_face_shapes[SolidShapes.WEDGE] == (
        ShellShapes.TRI,
        ShellShapes.TRI,
        ShellShapes.QUAD,
        ShellShapes.QUAD,
        ShellShapes.QUAD,
    )
    assert solid_abaqus_face_shapes[SolidShapes.WEDGE15] == (
        ShellShapes.TRI6,
        ShellShapes.TRI6,
        ShellShapes.QUAD8,
        ShellShapes.QUAD8,
        ShellShapes.QUAD8,
    )


def test_a_wedge_face_facet_reports_the_shape_of_that_face():
    pts = [(0, 0, 0), (1, 0, 0), (0, 0, 1), (0, 1, 0), (1, 1, 0), (0, 1, 1)]
    nodes = [ada.Node(p, i + 1) for i, p in enumerate(pts)]
    fem = ada.FEM("w", nodes=ada.api.containers.Nodes(nodes))
    fem.add_elem(ada.fem.Elem(1, nodes, "WEDGE"))
    fset = fem.add_set(FemSet("_w", [fem.elements.from_id(1)]))

    for side, shape, n_nodes in (("S1", ShellShapes.TRI, 3), ("S4", ShellShapes.QUAD, 4)):
        surf = Surface(f"s{side}", Surface.TYPES.ELEMENT, fset, el_face_index=side, parent=fem)
        (facet,) = surface_facets(surf)
        assert facet.shape is shape
        assert len(facet.nodes) == n_nodes


# ---------------------------------------------------------------------------
# reading and writing a blank side


SIDELESS_SOLID_DECK = """*Heading
** one C3D10 whose surface entry names no face identifier
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
*Elset, elset=TET
1,
*Surface, type=ELEMENT, name=TET_SURF
TET,
*Solid Section, elset=TET, material=Steel
*Material, name=Steel
*Elastic
2.1e11, 0.3
"""


def test_a_single_row_sideless_solid_surface_is_read_as_a_blank_side(tmp_path):
    """``TET,`` used to reach ``int("") - 1`` and take the whole ``*Surface`` down.

    A lone element has no neighbour, so all four faces are free and every node is on the
    surface -- which is the point: the free-face rule agrees with the old answer exactly when
    the elements really are exposed.
    """
    inp = tmp_path / "sideless.inp"
    inp.write_text(SIDELESS_SOLID_DECK)

    a = ada.from_fem(inp, "abaqus")
    (part,) = [p for p in a.get_all_parts_in_assembly(True) if p.fem.surfaces]
    surf = part.fem.surfaces["TET_SURF"]

    assert surf.el_face_index == "", "the blank side is kept, not guessed at"
    assert sorted(_ids(surf)) == list(range(1, 11))
    assert len(surface_facets(surf)) == 4


def test_a_blank_side_is_written_back_as_a_blank_side(tmp_path):
    """Writing a face number here would narrow a free-face surface to one named face."""
    from ada.fem.formats.abaqus.write.write_surfaces import surface_str

    inp = tmp_path / "sideless.inp"
    inp.write_text(SIDELESS_SOLID_DECK)
    a = ada.from_fem(inp, "abaqus")
    (part,) = [p for p in a.get_all_parts_in_assembly(True) if p.fem.surfaces]

    written = surface_str(part.fem.surfaces["TET_SURF"], False)
    assert written.splitlines()[-1].endswith(",")
    assert ", S" not in written
