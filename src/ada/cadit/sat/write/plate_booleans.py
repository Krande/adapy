"""A :class:`~ada.Plate` with holes cut by its booleans, as the advanced faces the SAT writer authors.

A plate's outline is a polygon and its holes are booleans: primitives subtracted from it. The
SAT writer used to author the outline alone -- one loop per face, ``Loop.next_loop`` never set
(Krande/adapy#410) -- so a hole written to GeniE came back as solid plate, without a word. Here
the CAD backend makes the cut, and what is left of the plate is read back as advanced faces: the
plane, the outer loop and every hole loop, each edge on the line, circle or B-spline the cut put
there. The SAT writer then authors those like any other advanced face, holes and all.

Every step goes through :mod:`ada.cad`'s backend, so this runs on adacpp and pythonocc alike
(Krande/adapy#435 first walked the cut with pythonocc, which the default environments do not
carry): the backend builds, places and measures the cut, and each face is read back out of the
BREP text the backend serialises it to (:mod:`ada.cad.brep_text`) -- the one form of a shape
both kernels write.

Anything the reading cannot state exactly is refused by name (:class:`PlateBooleanNotAuthored`)
rather than approximated: an edge on another kind of curve, a cut that leaves something other
than plane faces, and any face whose authored area disagrees with the cut's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.cad import brep_text as bt
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

if TYPE_CHECKING:
    from ada import Plate


class PlateBooleanNotAuthored(Exception):
    """A plate's booleans cut it into something the SAT writer cannot state exactly."""


#: How closely the faces read back from the cut must reproduce its area, relative. Both areas
#: are the backend's, of exact geometry (the same lines, circles and splines), so this is a
#: floor for the integration, not a tolerance on the shape: a hole wound the wrong way changes
#: the area by twice the hole.
AREA_REL_TOL = 1e-7

#: A B-spline whose end poles are closer than this is closed (``closed_curve``).
_BSPLINE_CLOSED = 1e-7


def _pnt(p) -> ada.Point:
    return ada.Point(*(float(c) for c in p))


def _dir(d) -> ada.Direction:
    return ada.Direction(*(float(c) for c in d))


def _curve(c, plate_name: str):
    """The adapy curve an edge runs on, in the parameterisation the kernel and ACIS share."""
    if isinstance(c, bt.Line):
        return geo_cu.Line(_pnt(c.location), _dir(c.direction))
    if isinstance(c, bt.Circle):
        position = geo_su.Axis2Placement3D(location=_pnt(c.centre), axis=_dir(c.axis), ref_direction=_dir(c.x_dir))
        return geo_cu.Circle(position, c.radius)
    if isinstance(c, bt.BSpline):
        if c.periodic:
            raise PlateBooleanNotAuthored(f"plate {plate_name!r}: a boolean left an edge on a periodic B-spline")
        common = dict(
            degree=c.degree,
            control_points_list=[_pnt(p) for p in c.poles],
            curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=bool(np.linalg.norm(c.poles[0] - c.poles[-1]) <= _BSPLINE_CLOSED),
            self_intersect=False,
            knot_multiplicities=list(c.multiplicities),
            knots=list(c.knots),
            knot_spec=geo_cu.KnotType.UNSPECIFIED,
        )
        if c.weights is not None:
            return geo_cu.RationalBSplineCurveWithKnots(**common, weights_data=[float(w) for w in c.weights])
        return geo_cu.BSplineCurveWithKnots(**common)
    raise PlateBooleanNotAuthored(f"plate {plate_name!r}: a boolean left an edge on a {type(c).__name__}")


def _bound(wire: list[bt.FaceEdge], plate_name: str) -> geo_su.FaceBound:
    edges = []
    for e in wire:
        curve = _curve(e.curve, plate_name)
        p_first, p_last = _pnt(e.curve.value(e.first)), _pnt(e.curve.value(e.last))
        start, end = (p_first, p_last) if e.forward else (p_last, p_first)
        t_start, t_end = (e.first, e.last) if e.forward else (e.last, e.first)
        edge_curve = geo_cu.EdgeCurve(start=start, end=end, edge_geometry=curve, same_sense=e.forward)
        edges.append(
            geo_cu.OrientedEdge(
                start=start, end=end, edge_element=edge_curve, orientation=True, t_start=t_start, t_end=t_end
            )
        )
    return geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)


def _outer_index(face: bt.PlanarFace) -> int:
    """``BRepTools::OuterWire``: the first wire, displaced by any later one whose box holds its box.

    The boxes are in the plane's own (u, v), as OCCT's are in the face's parameter space: a
    line's from its ends, an arc's from its ends and the angles where u or v turns, a
    B-spline's from 257 points along it.
    """
    origin = face.plane.location
    u = face.plane.x_dir / np.linalg.norm(face.plane.x_dir)
    v = np.cross(face.plane.axis / np.linalg.norm(face.plane.axis), u)

    def box(wire):
        pts = []
        for e in wire:
            lo, hi = min(e.first, e.last), max(e.first, e.last)
            ts = [e.first, e.last]
            if isinstance(e.curve, bt.Circle):
                for a in (u, v):
                    t0 = np.arctan2(e.curve.y_dir @ a, e.curve.x_dir @ a)
                    k0 = int(np.ceil((lo - t0) / np.pi))
                    ts += [t0 + k * np.pi for k in range(k0, k0 + 3) if t0 + k * np.pi <= hi]
            elif isinstance(e.curve, bt.BSpline):
                ts += list(np.linspace(lo, hi, 257))
            pts += [e.curve.value(t) - origin for t in ts]
        uv = np.array([(p @ u, p @ v) for p in pts])
        return uv.min(axis=0), uv.max(axis=0)

    best = 0
    b_lo, b_hi = box(face.wires[0])
    for k in range(1, len(face.wires)):
        lo, hi = box(face.wires[k])
        if np.all(lo <= b_lo) and np.all(hi >= b_hi):
            best, b_lo, b_hi = k, lo, hi
    return best


def _to_global(be, pl: Plate, shape):
    """``shape`` (built in the plate's own frame, as ``shell_geom`` is) moved to global coordinates.

    The same placement :meth:`~ada.Plate.outline_global` pushes the outline through, taken as
    the affine map it is: the images of the origin and the three unit vectors.
    """
    from ada.api.transforms import Placement

    abs_place = pl.placement.get_absolute_placement(include_rotations=True)
    if abs_place.is_identity():
        return shape
    o, ex, ey, ez = abs_place.transform_array_from_other_place(
        np.asarray([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], dtype=float), Placement(), ignore_translation=False
    )
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = ex - o, ey - o, ez - o, o
    return be.transform(shape, m, copy=True)


def _reversed(bound: geo_su.FaceBound) -> geo_su.FaceBound:
    """The same loop run the other way: edges in reverse order, each from its end to its start."""
    edges = []
    for oe in reversed(bound.bound.edge_list):
        ec = oe.edge_element
        flipped = geo_cu.EdgeCurve(
            start=oe.end, end=oe.start, edge_geometry=ec.edge_geometry, same_sense=not ec.same_sense
        )
        edges.append(
            geo_cu.OrientedEdge(
                start=oe.end, end=oe.start, edge_element=flipped, orientation=True, t_start=oe.t_end, t_end=oe.t_start
            )
        )
    return geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)


def plate_faces_after_booleans(pl: Plate) -> list[geo_su.AdvancedFace]:
    """What the plate's booleans leave of it, as advanced faces in global coordinates.

    One face for a plate with holes; several where the booleans cut it apart. Each face is
    checked against the cut it was read from -- rebuilt from the advanced face, its area must be
    the cut's -- so a misread loop or winding is refused, not written.
    """
    from ada.cad import active_backend
    from ada.geom import Geometry

    be = active_backend()
    cut = _to_global(be, pl, be.build(pl.shell_geom()))
    normal = np.asarray(pl.outline_global()[1], dtype=float)

    faces = []
    for handle in be.faces(cut):
        try:
            face = bt.read_planar_face(be.serialize(handle))
        except bt.BrepTextUnsupported as ex:
            raise PlateBooleanNotAuthored(f"plate {pl.name!r}: its booleans left {ex}") from ex
        outer = _outer_index(face)
        bounds = [_bound(face.wires[k], pl.name) for k in [outer] + [k for k in range(len(face.wires)) if k != outer]]
        # The loops run counter-clockwise about the cut face's own normal, and that normal
        # need not be the plate's: measured, the backend's face of a +z plate has its plane
        # axis on -z, FORWARD. The plate's normal is what the XML states, so the face takes
        # it, and the loops are turned round where the cut faced the other way.
        axis = face.plane.axis / np.linalg.norm(face.plane.axis)
        if face.reversed:
            axis = -axis
        if float(axis @ normal) < 0:
            bounds = [_reversed(b) for b in bounds]
        plane = geo_su.Plane(
            position=geo_su.Axis2Placement3D(
                location=_pnt(face.plane.location),
                axis=ada.Direction(*normal),
                ref_direction=_dir(face.plane.x_dir),
            )
        )
        advanced = geo_su.AdvancedFace(bounds=bounds, face_surface=plane, same_sense=True)
        expected, got = be.area(handle), be.area(be.build(Geometry(pl.guid, advanced, pl.color)))
        if not abs(got - expected) <= AREA_REL_TOL * expected:
            raise PlateBooleanNotAuthored(
                f"plate {pl.name!r}: the face read back from its booleans' cut measures {got} against the cut's "
                f"{expected}"
            )
        faces.append(advanced)
    if not faces:
        raise PlateBooleanNotAuthored(f"plate {pl.name!r}: its booleans leave nothing of it")
    return faces
