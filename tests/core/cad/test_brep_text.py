"""Reading a planar face back out of the BREP text a kernel serialises it to (Krande/adapy#435).

The reader is checked against what each kernel itself says of the same shape -- vertex points,
plane, area -- on shapes that exercise what the text can carry: faces used reversed in a solid,
located (and twice-located) shapes, holes, and the refusals.
"""

from __future__ import annotations

import re

import numpy as np
import pytest

import ada
from ada.cad import brep_text as bt
from ada.geom import Geometry
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.geom.points import Point


def _box(backend):
    box = geo_so.Box(geo_su.Axis2Placement3D(location=Point(1, 2, 3)), 2.0, 3.0, 4.0)
    return backend.build(Geometry(1, box, None))


def _turn(face: bt.PlanarFace, wire) -> float:
    """Twice the loop's signed area about the face's own normal (the plane axis, through its orientation)."""
    n = face.plane.axis * (-1.0 if face.reversed else 1.0)
    pts = []
    for e in wire:
        ts = np.linspace(e.first, e.last, 33)
        pts += [e.curve.value(t) for t in (ts if e.forward else ts[::-1])[:-1]]
    return float(sum(np.cross(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))) @ n)


def test_every_face_of_a_box_reads_its_outline_round_its_outward_normal(backend):
    """Half a box's faces are used reversed: the text composes that onto the wire and its edges."""
    shape = _box(backend)
    centre = np.array([2.0, 3.5, 5.0])
    reversed_seen = set()
    for handle in backend.faces(shape):
        face = bt.read_planar_face(backend.serialize(handle))
        (wire,) = face.wires
        assert len(wire) == 4
        assert all(a.end == b.start for a, b in zip(wire, wire[1:] + wire[:1]))
        n = face.plane.axis * (-1.0 if face.reversed else 1.0)
        assert float((face.plane.location - centre) @ n) > 0  # the face's normal points out
        assert _turn(face, wire) > 0  # its outline runs counter-clockwise about it
        reversed_seen.add(face.reversed)
    assert reversed_seen == {True, False}


@pytest.mark.parametrize("times", [1, 2])
def test_a_located_face_reads_where_the_kernel_puts_it(backend, times):
    """A shape moved without copying carries a location (two moves: a composed one)."""
    shape = _box(backend)
    c, s = np.cos(0.3), np.sin(0.3)
    about_z = np.array([[c, -s, 0, 10.0], [s, c, 0, -4.0], [0, 0, 1, 0.5], [0, 0, 0, 1]])
    about_x = np.array([[1, 0, 0, -1.0], [0, c, -s, 2.0], [0, s, c, 7.0], [0, 0, 0, 1]])
    for m in (about_z, about_x)[:times]:  # two that do not commute
        shape = backend.transform(shape, m, copy=False)
    for handle in backend.faces(shape):
        text = backend.serialize(handle)
        assert "Locations 0" not in text
        face = bt.read_planar_face(text)
        ours = np.array(
            [e.curve.value(e.first) for e in face.wires[0]] + [e.curve.value(e.last) for e in face.wires[0]]
        )
        kernel = np.array(backend.vertex_points(handle))
        # each of our points is one of the kernel's, to the 15 digits the text writes them with
        dist = np.min(np.linalg.norm(ours[:, None, :] - kernel[None, :, :], axis=2), axis=1)
        assert dist.max() <= 1e-13
        kernel_origin, kernel_normal = backend.face_plane(handle)
        assert np.allclose(np.cross(face.plane.axis, np.asarray(kernel_normal, dtype=float)), 0.0, atol=1e-15)
        assert abs(float((face.plane.location - np.asarray(kernel_origin, dtype=float)) @ face.plane.axis)) <= 1e-13


def test_a_plate_with_a_round_hole_reads_its_circle(backend):
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    (handle,) = backend.faces(backend.build(pl.shell_geom()))
    face = bt.read_planar_face(backend.serialize(handle))
    outer, hole = sorted(face.wires, key=len, reverse=True)
    assert [type(e.curve) for e in outer] == [bt.Line] * 4
    (circle,) = hole
    assert isinstance(circle.curve, bt.Circle) and circle.start == circle.end
    assert np.allclose(circle.curve.centre, (2, 1.5, 0), rtol=0, atol=1e-15) and circle.curve.radius == 0.4
    assert abs(circle.last - circle.first) == pytest.approx(2 * np.pi, abs=5e-15)  # 15 digits
    assert _turn(face, outer) > 0 > _turn(face, [circle])


@pytest.mark.adacpp
def test_v2_text_reads_as_v3_does():
    """V2 writes the UV ends of every curve on a surface, V3 does not: both read to the same face."""
    from ada.cad import select_backend

    be = select_backend("adacpp")
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    (handle,) = be.faces(be.build(pl.shell_geom()))
    v3, v2 = be.serialize(handle), be._cad.serialize_brep(handle)
    assert "CASCADE Topology V3" in v3 and "CASCADE Topology V2" in v2
    a, b = bt.read_planar_face(v3), bt.read_planar_face(v2)
    assert [[(type(e.curve), e.first, e.last, e.forward) for e in w] for w in a.wires] == [
        [(type(e.curve), e.first, e.last, e.forward) for e in w] for w in b.wires
    ]


def _edges(face: bt.PlanarFace) -> list:
    return [[(type(e.curve), e.first, e.last, e.forward, e.start, e.end) for e in w] for w in face.wires]


def _seam_plate() -> ada.Plate:
    """A plate in y = 0 cut by a cylinder on z: both kernels put the seam at +x, in the plate's plane."""
    pl = ada.Plate("pl", [(0, 0), (2, 0), (2, 1), (0, 1)], 0.01, origin=(0, 0, 0.5), xdir=(1, 0, 0), normal=(0, 1, 0))
    pl.add_boolean(ada.PrimCyl("c", (0, 0, -1), (0, 0, 1), 0.5))
    return pl


def _seam_face_text(backend) -> str:
    (handle,) = backend.faces(backend.build(_seam_plate().shell_geom()))
    text = backend.serialize(handle)
    assert re.search(r"^3 +\d+ \d+CN ", text, re.M)  # the seam: a curve on a closed surface
    return text


def test_a_seam_edge_reads_as_its_line(backend):
    """A seam record is ``3 pc1 pc2<continuity> surface location first last``: 6 tokens, one glued.

    Read as 7 numbers it raised ``could not convert string to float: '4CN'``. The 3D curve is
    all the reader takes from an edge, so the seam edge is a line like the others.
    """
    (handle,) = backend.faces(backend.build(_seam_plate().shell_geom()))
    face = bt.read_planar_face(_seam_face_text(backend))
    (wire,) = face.wires
    assert all(isinstance(e.curve, bt.Line) for e in wire)
    ours = np.array([e.curve.value(e.first if e.forward else e.last) for e in wire])
    kernel = np.array(backend.vertex_points(handle))
    assert len(ours) == len(kernel)
    assert np.min(np.linalg.norm(ours[:, None, :] - kernel[None, :, :], axis=2), axis=1).max() <= 1e-15


@pytest.mark.adacpp
def test_a_seam_edge_reads_the_same_from_v2_text():
    """V2 follows a seam record, like a curve-on-surface one, with one UV pair (4 numbers) -- not two."""
    from ada.cad import select_backend

    be = select_backend("adacpp")
    (handle,) = be.faces(be.build(_seam_plate().shell_geom()))
    v3, v2 = be.serialize(handle), be._cad.serialize_brep(handle)
    assert "CASCADE Topology V2" in v2 and re.search(r"^3 +\d+ \d+CN ", v2, re.M)
    assert _edges(bt.read_planar_face(v3)) == _edges(bt.read_planar_face(v2))


def test_a_seam_edge_reads_the_same_in_every_format_version(occ_backend, tmp_path):
    """OCCT's own writer, V1, V2 and V3: the same seam face, the same edges."""
    from OCC.Core.BRepTools import breptools
    from OCC.Core.TopTools import (
        TopTools_FormatVersion_VERSION_1,
        TopTools_FormatVersion_VERSION_2,
        TopTools_FormatVersion_VERSION_3,
    )

    (handle,) = occ_backend.faces(occ_backend.build(_seam_plate().shell_geom()))
    read = []
    for k, version in enumerate(
        (TopTools_FormatVersion_VERSION_1, TopTools_FormatVersion_VERSION_2, TopTools_FormatVersion_VERSION_3), 1
    ):
        path = tmp_path / f"seam_v{k}.brep"
        breptools.Write(handle, str(path), False, False, version)
        text = path.read_text()
        assert f"CASCADE Topology V{k}" in text and re.search(r"^3 +\d+ \d+CN ", text, re.M)
        read.append(_edges(bt.read_planar_face(text)))
    assert read[0] == read[1] == read[2]


def test_a_regularity_record_is_skipped(backend):
    """``4 <continuity> surface location surface location``, as OCCT writes it after EncodeRegularity
    (measured on 7.9.3 and 8.0.1: ``4 C0 1 0 2 0``). Put on the seam edge here, it reads the same."""
    text = _seam_face_text(backend)
    plain = bt.read_planar_face(text)
    # after every edge's 3D curve, where OCCT puts it
    with_reg, n = re.subn(r"^(1  \d+ \d+ \S+ \S+)$", r"\1\n4 C0 1 0 1 0", text, flags=re.M)
    assert n == len(plain.wires[0])
    assert _edges(bt.read_planar_face(with_reg)) == _edges(plain)


@pytest.mark.parametrize(
    "bad, why",
    [
        (r"\g<1>XY", "continuity 'XY'"),  # the seam's continuity, not one OCCT writes
        (r"\g<1>", "continuity ''"),  # the continuity left off
    ],
)
def test_a_malformed_seam_record_is_refused_by_name(backend, bad, why):
    text = re.sub(r"^(3 +\d+ \d+)CN", bad, _seam_face_text(backend), count=1, flags=re.M)
    with pytest.raises(bt.BrepTextUnsupported, match=re.escape(why)):
        bt.read_planar_face(text)


def test_a_record_longer_than_its_kind_is_refused_by_name(backend):
    """One token too many in a regularity record would end the edge's list early and read nothing else."""
    text = re.sub(r"^(1  \d+ \d+ \S+ \S+)$", r"\1\n4 C0 1 0 1 0 0", _seam_face_text(backend), count=1, flags=re.M)
    with pytest.raises(bt.BrepTextUnsupported, match="do not end where its record does"):
        bt.read_planar_face(text)


def test_a_number_that_is_not_one_is_refused_by_name(backend):
    text = re.sub(r"^(1  \d+ \d+ )\S+", r"\1nought", _seam_face_text(backend), count=1, flags=re.M)
    with pytest.raises(bt.BrepTextUnsupported, match="'nought' where a number"):
        bt.read_planar_face(text)


@pytest.mark.parametrize("flag", ["i", "e"])
def test_an_internal_or_external_wire_is_refused_by_name(backend, flag):
    """``TopAbs::Compose``: an INTERNAL or EXTERNAL wire makes every edge in it so, whatever their own
    flags. Read with the edges' flags it was a bound -- an imprinted line taken for a hole."""
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    pl.add_boolean(ada.PrimCyl("hole", (2, 1.5, -0.5), (2, 1.5, 0.5), 0.4))
    (handle,) = backend.faces(backend.build(pl.shell_geom()))
    lines = backend.serialize(handle).splitlines()
    k = max(i for i, ln in enumerate(lines) if ln.rstrip().endswith("*"))  # the root face's wires
    wires = lines[k].split()
    assert len(wires) == 5 and wires[2][0] in "+-"  # two wires (ref, location each) and the end
    wires[2] = flag + wires[2][1:]
    lines[k] = " ".join(wires)
    with pytest.raises(bt.BrepTextUnsupported, match="an internal or external wire"):
        bt.read_planar_face("\n".join(lines) + "\n")


@pytest.mark.parametrize(
    "parent, child, composed",
    [(p, c, {"+": c, "-": {"+": "-", "-": "+"}.get(c, c)}.get(p, p)) for p in "+-ie" for c in "+-ie"],
)
def test_orientations_compose_as_topabs_does(parent, child, composed):
    """TopAbs.hxx's table: a forward parent keeps the child's flag, a reversed one reverses + and -,
    an internal or external parent is the result whatever the child."""
    assert bt._compose(parent, child) == composed


def test_a_periodic_b_spline_edge_is_refused_by_name(backend):
    """A periodic B-spline's stored poles and knots wrap round; evaluated as a clamped one it ran
    off its knot vector (``IndexError`` in plate_booleans' outer-wire box). Here the sweep hole's
    B-splines (clamped) with the periodic flag set: the layout of the record is the same."""
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    path = [(2, 1.5, -1), (2.2, 1.4, -0.5), (2.5, 1.5, 0), (2.2, 1.6, 0.5), (2, 1.5, 1)]
    pl.add_boolean(ada.PrimSweep("sw", path, [(0, 0), (0.3, 0), (0.3, 0.3), (0, 0.3)]))
    (handle,) = backend.faces(backend.build(pl.shell_geom()))
    text = backend.serialize(handle)
    assert any(isinstance(e.curve, bt.BSpline) for w in bt.read_planar_face(text).wires for e in w)
    head = re.search(r"^Curves \d+$", text, re.M).end()
    curves, n = re.subn(r"^7 ([01]) 0 ", r"7 \1 1 ", text[head:], flags=re.M)
    assert n > 0
    with pytest.raises(bt.BrepTextUnsupported, match="an edge on a periodic B-spline curve"):
        bt.read_planar_face(text[:head] + curves)


def test_a_periodic_b_spline_outline_is_refused_by_name(occ_backend):
    """A face on a periodic B-spline as OCCT makes one (``GeomAPI_Interpolate``, periodic)."""
    from OCC.Core.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCC.Core.GeomAPI import GeomAPI_Interpolate
    from OCC.Core.gp import gp_Pln, gp_Pnt
    from OCC.Core.TColgp import TColgp_HArray1OfPnt

    pts = TColgp_HArray1OfPnt(1, 5)
    for i, (x, y) in enumerate([(1, 0), (0.3, 0.9), (-0.8, 0.5), (-0.6, -0.7), (0.5, -0.9)], 1):
        pts.SetValue(i, gp_Pnt(x, y, 0))
    interp = GeomAPI_Interpolate(pts, True, 1e-9)
    interp.Perform()
    assert interp.Curve().IsPeriodic()
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()).Wire()
    face = BRepBuilderAPI_MakeFace(gp_Pln(), wire).Face()
    with pytest.raises(bt.BrepTextUnsupported, match="an edge on a periodic B-spline curve"):
        bt.read_planar_face(occ_backend.serialize(face))


def test_a_face_not_on_a_plane_is_refused_by_name(backend):
    cyl = geo_so.Cylinder(geo_su.Axis2Placement3D(location=Point(0, 0, 0)), 0.5, 1.0)
    faces = backend.faces(backend.build(Geometry(1, cyl, None)))
    kinds = []
    for handle in faces:
        try:
            bt.read_planar_face(backend.serialize(handle))
            kinds.append("plane")
        except bt.BrepTextUnsupported as ex:
            assert "a face on a cylinder, not a plane" in str(ex)
            kinds.append("refused")
    assert sorted(kinds) == ["plane", "plane", "refused"]


def test_an_edge_on_an_ellipse_is_refused_by_name(backend):
    pl = ada.Plate("pl", [(0, 0), (4, 0), (4, 3), (0, 3)], 0.01)
    pl.add_boolean(ada.PrimCyl("slant", (1.5, 1.5, -0.5), (2.5, 1.5, 0.5), 0.3))
    (handle,) = backend.faces(backend.build(pl.shell_geom()))
    with pytest.raises(bt.BrepTextUnsupported, match="an edge on a ellipse"):
        bt.read_planar_face(backend.serialize(handle))


def test_text_that_is_not_brep_is_refused():
    with pytest.raises(bt.BrepTextUnsupported, match="not OCCT BREP"):
        bt.read_planar_face("ISO-10303-21;")


def _closed_circle(r: float) -> bt.FaceEdge:
    z, x = np.array([0.0, 0, 1]), np.array([1.0, 0, 0])
    return bt.FaceEdge(bt.Circle(np.zeros(3), z, x, np.cross(z, x), r), 0.0, 2 * np.pi, True, (1, b""), (1, b""))


def _square(h: float) -> list[bt.FaceEdge]:
    c = [np.array(p, dtype=float) for p in [(-h, -h, 0), (-h, h, 0), (h, h, 0), (h, -h, 0)]]  # clockwise
    return [
        bt.FaceEdge(bt.Line(c[i], c[(i + 1) % 4] - c[i]), 0.0, 1.0, True, (10 + i, b""), (10 + (i + 1) % 4, b""))
        for i in range(4)
    ]


def test_the_outer_wire_is_the_one_whose_box_holds_the_others():
    """``BRepTools::OuterWire``'s rule, whatever order the text stores the wires in.

    A whole circle's two ends are one point: its box comes from where it turns, not its ends.
    """
    from ada.cadit.sat.write.plate_booleans import _outer_index

    plane = bt.Plane(np.zeros(3), np.array([0.0, 0, 1]), np.array([1.0, 0, 0]))
    assert _outer_index(bt.PlanarFace(plane, False, [[_closed_circle(1.0)], _square(0.2)])) == 0
    assert _outer_index(bt.PlanarFace(plane, False, [_square(0.2), [_closed_circle(1.0)]])) == 1


def test_a_location_that_scales_is_refused():
    """A line's parameter would scale with it; the kernels copy rather than locate a scaled shape, so
    this is the guard, called directly."""
    m = np.diag([2.0, 2.0, 2.0, 1.0])
    with pytest.raises(bt.BrepTextUnsupported, match="not a rotation and translation"):
        bt._move(m, bt.Line(np.zeros(3), np.array([1.0, 0, 0])))
