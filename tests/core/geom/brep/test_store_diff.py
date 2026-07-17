"""Stage 2: the store differ — self-equivalence and meaningful mismatch classes."""

import numpy as np

from ada.geom.brep import BRepStore, LoopKind
from ada.geom.brep.diff import store_equivalence
from ada.geom.curves import Line
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import Plane


def _plane():
    return Plane(Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))


def _line(a, b):
    return Line(Point(*a), Direction(*(np.array(b, float) - np.array(a, float))))


def _face(st, pts, dedup=True):
    loop = st.add_loop(LoopKind.OUTER)
    mk = st.vertex_at if dedup else (lambda p: st.add_vertex(p))
    vs = [mk(Point(*p)) for p in pts]
    for i in range(len(pts)):
        va, vb = vs[i], vs[(i + 1) % len(pts)]
        if dedup:
            edge = st.edge_between(_line(pts[i], pts[(i + 1) % len(pts)]), va, vb)
        else:
            edge = st.add_edge(_line(pts[i], pts[(i + 1) % len(pts)]), va, vb)
        st.add_coedge(edge, edge.start is va, loop)
    return st.add_face(_plane(), True, outer=loop)


A = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
B = [(1, 0, 0), (2, 0, 0), (2, 1, 0), (1, 1, 0)]


def _two_face_welded():
    st = BRepStore()
    _face(st, A)
    _face(st, B)
    st.add_lump([st.add_shell(list(st.faces.values()))])
    return st


def test_self_equivalence_is_zero():
    st = _two_face_welded()
    d = store_equivalence(st, st)
    assert d.is_equivalent, d.report()
    assert d.classify() == {"class2_split_imprint": 0, "class1_weld": 0, "class3_non_derivable": 0}


def test_fragmented_store_flags_class1():
    """A store that mints its own vertices/edges per face (no sharing) vs a welded
    one: extra vertices/edges + ring mismatch -> Class 1."""
    good = _two_face_welded()

    frag = BRepStore()
    _face(frag, A, dedup=False)
    _face(frag, B, dedup=False)
    # two lumps (unwelded) vs one
    frag.add_lump([frag.add_shell([list(frag.faces.values())[0]])])
    frag.add_lump([frag.add_shell([list(frag.faces.values())[1]])])

    d = store_equivalence(good, frag)
    assert not d.is_equivalent
    cls = d.classify()
    assert cls["class1_weld"] > 0, d.report()
    # frag has 8 vertices vs 6, 8 edges vs 7 -> extras; shared edge ring 2 vs 1
    assert sum(d.extra_vertices.values()) == 2, d.report()
    assert d.lump_count_a == 1 and d.lump_count_b == 2


def test_missing_split_flags_class2():
    """Ground truth has a face split in two along x=1; derived has one big face.
    The missing sub-face edges -> Class 2."""
    split = _two_face_welded()  # two 1x1 faces sharing x=1 edge

    merged = BRepStore()
    _face(merged, [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])  # one 2x1 face
    merged.add_lump([merged.add_shell(list(merged.faces.values()))])

    d = store_equivalence(split, merged)
    assert not d.is_equivalent
    cls = d.classify()
    assert cls["class2_split_imprint"] > 0, d.report()
    # the interior split edge (1,0)-(1,1) is present in truth, absent in merged
    assert sum(d.missing_edges.values()) >= 1, d.report()
