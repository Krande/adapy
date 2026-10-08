"""Reading a planar face back out of the BREP text a kernel serialises it to (Krande/adapy#435).

The reader is checked against what each kernel itself says of the same shape -- vertex points,
plane, area -- on shapes that exercise what the text can carry: faces used reversed in a solid,
located (and twice-located) shapes, holes, and the refusals.
"""

from __future__ import annotations

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
