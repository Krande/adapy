"""Stage 1: BRep store core — shared identity, partner rings, dedup, hole loops."""

import numpy as np

from ada.geom.brep import BRepStore, LoopKind
from ada.geom.curves import Line
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import Plane


def _plane(z=0.0):
    return Plane(Axis2Placement3D(Point(0, 0, z), Direction(0, 0, 1), Direction(1, 0, 0)))


def _line(a, b):
    return Line(Point(*a), Direction(*(np.array(b, float) - np.array(a, float))))


def test_two_faces_share_one_edge():
    """Two quads sharing an edge weld to ONE BEdge with a two-coedge partner ring."""
    st = BRepStore()

    # square A: (0,0)-(1,0)-(1,1)-(0,1); square B shares the (1,0)-(1,1) edge.
    A = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    B = [(1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)]

    def build_face(pts):
        loop = st.add_loop(LoopKind.OUTER)
        vs = [st.vertex_at(Point(*p)) for p in pts]
        for i in range(len(pts)):
            va, vb = vs[i], vs[(i + 1) % len(pts)]
            edge = st.edge_between(_line(pts[i], pts[(i + 1) % len(pts)]), va, vb)
            sense = edge.start is va  # forward if the edge runs our way
            st.add_coedge(edge, sense, loop)
        return st.add_face(_plane(), True, outer=loop)

    fa = build_face(A)
    fb = build_face(B)

    s = st.summary()
    # 6 unique corners (2 shared), 7 unique edges (1 shared), 2 faces
    assert s["vertices"] == 6, s
    assert s["edges"] == 7, s
    assert s["faces"] == 2

    # the shared edge is the vertical one at x=1: it must have TWO coedges
    shared = [e for e in st.edges.values()
              if {tuple(e.start.point)[:3], tuple(e.end.point)[:3]} ==
              {(1.0, 0.0, 0.0), (1.0, 1.0, 0.0)}]
    assert len(shared) == 1, "the (1,0)-(1,1) edge must be a single shared object"
    ring = st.coedges_on(shared[0])
    assert len(ring) == 2, "shared edge must carry both faces' coedges"
    assert {c.loop.face.id for c in ring} == {fa.id, fb.id}


def test_vertex_dedup_within_tol():
    st = BRepStore(dedup_nd=6)
    v1 = st.vertex_at(Point(1.0, 2.0, 3.0))
    v2 = st.vertex_at(Point(1.0, 2.0, 3.0 + 1e-8))  # below dedup rounding
    v3 = st.vertex_at(Point(1.0, 2.0, 3.001))  # distinct
    assert v1 is v2
    assert v3 is not v1
    assert len(st.vertices) == 2


def test_arc_and_chord_are_distinct_edges():
    from ada.geom.curves import Circle

    st = BRepStore()
    a, b = (1, 0, 0), (0, 1, 0)
    va, vb = st.vertex_at(Point(*a)), st.vertex_at(Point(*b))
    chord = st.edge_between(_line(a, b), va, vb)
    arc = st.edge_between(
        Circle(Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), 1.0), va, vb
    )
    assert chord is not arc, "an arc and its chord between the same corners are different edges"
    assert len(st.edges) == 2


def test_inner_hole_loop():
    st = BRepStore()
    outer = st.add_loop(LoopKind.OUTER)
    inner = st.add_loop(LoopKind.INNER)
    face = st.add_face(_plane(), True, outer=outer, inner=[inner])
    assert face.outer is outer
    assert face.inner == [inner]
    assert len(face.loops) == 2
    assert inner.kind is LoopKind.INNER


def test_lump_grouping_and_summary():
    st = BRepStore()
    f = st.add_face(_plane(), True, outer=st.add_loop())
    shell = st.add_shell([f])
    lump = st.add_lump([shell])
    assert f.shell is shell
    assert shell.lump is lump
    assert st.summary()["lumps"] == 1
