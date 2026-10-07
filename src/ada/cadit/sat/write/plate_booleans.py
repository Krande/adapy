"""A :class:`~ada.Plate` with holes cut by its booleans, as the advanced faces the SAT writer authors.

A plate's outline is a polygon and its holes are booleans: primitives subtracted from it. The
SAT writer used to author the outline alone -- one loop per face, ``Loop.next_loop`` never set
(Krande/adapy#410) -- so a hole written to GeniE came back as solid plate, without a word. Here
the CAD backend makes the cut, and what is left of the plate is read back as advanced faces: the
plane, the outer loop and every hole loop, each edge on the line, circle or B-spline the cut put
there. The SAT writer then authors those like any other advanced face, holes and all.

Anything the reading cannot state exactly is refused by name (:class:`PlateBooleanNotAuthored`)
rather than approximated: an edge on another kind of curve, a cut that leaves something other
than plane faces, and any face whose authored area disagrees with the cut's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su

if TYPE_CHECKING:
    from ada import Plate


class PlateBooleanNotAuthored(Exception):
    """A plate's booleans cut it into something the SAT writer cannot state exactly."""


#: How closely the faces read back from the cut must reproduce its area, relative. The
#: comparison is exact geometry against exact geometry (the same lines, circles and splines,
#: integrated adaptively to 1e-9), so this is a floor for the integration, not a tolerance on
#: the shape: a hole wound the wrong way changes the area by twice the hole.
AREA_REL_TOL = 1e-7


def _area(shape) -> float:
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, props, 1e-9)
    return props.Mass()


def _pnt(p) -> ada.Point:
    return ada.Point(p.X(), p.Y(), p.Z())


def _dir(d) -> ada.Direction:
    return ada.Direction(d.X(), d.Y(), d.Z())


def _curve(adaptor, plate_name: str):
    """The adapy curve an OCC edge runs on, in the parameterisation OCC and ACIS share."""
    from OCC.Core.GeomAbs import GeomAbs_BSplineCurve, GeomAbs_Circle, GeomAbs_Line

    kind = adaptor.GetType()
    if kind == GeomAbs_Line:
        line = adaptor.Line()
        return geo_cu.Line(_pnt(line.Location()), _dir(line.Direction()))
    if kind == GeomAbs_Circle:
        circ = adaptor.Circle()
        ax2 = circ.Position()
        position = geo_su.Axis2Placement3D(
            location=_pnt(ax2.Location()), axis=_dir(ax2.Direction()), ref_direction=_dir(ax2.XDirection())
        )
        return geo_cu.Circle(position, circ.Radius())
    if kind == GeomAbs_BSplineCurve:
        bs = adaptor.BSpline()
        poles = [_pnt(bs.Pole(i)) for i in range(1, bs.NbPoles() + 1)]
        knots = [bs.Knot(i) for i in range(1, bs.NbKnots() + 1)]
        mults = [bs.Multiplicity(i) for i in range(1, bs.NbKnots() + 1)]
        common = dict(
            degree=bs.Degree(),
            control_points_list=poles,
            curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=bool(bs.IsClosed()),
            self_intersect=False,
            knot_multiplicities=mults,
            knots=knots,
            knot_spec=geo_cu.KnotType.UNSPECIFIED,
        )
        if bs.IsRational():
            weights = [bs.Weight(i) for i in range(1, bs.NbPoles() + 1)]
            return geo_cu.RationalBSplineCurveWithKnots(**common, weights_data=weights)
        return geo_cu.BSplineCurveWithKnots(**common)
    raise PlateBooleanNotAuthored(f"plate {plate_name!r}: a boolean left an edge on a curve of OCC type {kind}")


def _bound(wire, face, plate_name: str) -> geo_su.FaceBound:
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.BRepTools import BRepTools_WireExplorer
    from OCC.Core.TopAbs import TopAbs_REVERSED

    edges = []
    explorer = BRepTools_WireExplorer(wire, face)
    while explorer.More():
        edge = explorer.Current()
        adaptor = BRepAdaptor_Curve(edge)
        curve = _curve(adaptor, plate_name)
        first, last = adaptor.FirstParameter(), adaptor.LastParameter()
        p_first, p_last = _pnt(adaptor.Value(first)), _pnt(adaptor.Value(last))
        forward = edge.Orientation() != TopAbs_REVERSED
        start, end = (p_first, p_last) if forward else (p_last, p_first)
        t_start, t_end = (first, last) if forward else (last, first)
        edge_curve = geo_cu.EdgeCurve(start=start, end=end, edge_geometry=curve, same_sense=forward)
        edges.append(
            geo_cu.OrientedEdge(
                start=start, end=end, edge_element=edge_curve, orientation=True, t_start=t_start, t_end=t_end
            )
        )
        explorer.Next()
    return geo_su.FaceBound(bound=geo_cu.EdgeLoop(edge_list=edges), orientation=True)


def _to_global(pl: Plate, shape):
    """``shape`` (built in the plate's own frame, as ``shell_geom`` is) moved to global coordinates.

    The same placement :meth:`~ada.Plate.outline_global` pushes the outline through, taken as
    the affine map it is: the images of the origin and the three unit vectors.
    """
    from ada.api.transforms import Placement

    abs_place = pl.placement.get_absolute_placement(include_rotations=True)
    if abs_place.is_identity():
        return shape
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCC.Core.gp import gp_Trsf

    o, ex, ey, ez = abs_place.transform_array_from_other_place(
        np.asarray([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], dtype=float), Placement(), ignore_translation=False
    )
    cols = [ex - o, ey - o, ez - o]
    trsf = gp_Trsf()
    trsf.SetValues(*(v for r in range(3) for v in (cols[0][r], cols[1][r], cols[2][r], o[r])))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


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
    from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
    from OCC.Core.BRepTools import breptools
    from OCC.Core.GeomAbs import GeomAbs_Plane
    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_WIRE
    from OCC.Core.TopExp import TopExp_Explorer
    from OCC.Core.TopoDS import topods

    from ada.occ.geom import geom_to_occ_geom
    from ada.occ.geom.surfaces import make_face_from_geom

    cut = _to_global(pl, geom_to_occ_geom(pl.shell_geom()))
    normal = np.asarray(pl.outline_global()[1], dtype=float)

    faces = []
    exp = TopExp_Explorer(cut, TopAbs_FACE)
    while exp.More():
        face = topods.Face(exp.Current())
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Plane:
            raise PlateBooleanNotAuthored(f"plate {pl.name!r}: its booleans left a face that is not a plane")
        pln = surf.Plane()
        outer = breptools.OuterWire(face)
        bounds = [_bound(outer, face, pl.name)]
        wexp = TopExp_Explorer(face, TopAbs_WIRE)
        while wexp.More():
            wire = topods.Wire(wexp.Current())
            if not wire.IsSame(outer):
                bounds.append(_bound(wire, face, pl.name))
            wexp.Next()
        # The loops run counter-clockwise about the cut face's own normal, and that normal
        # need not be the plate's: measured, the backend's face of a +z plate has its plane
        # axis on -z, FORWARD. The plate's normal is what the XML states, so the face takes
        # it, and the loops are turned round where the cut faced the other way.
        axis = np.asarray(_dir(pln.Axis().Direction()), dtype=float)
        if face.Orientation() == TopAbs_REVERSED:
            axis = -axis
        if float(axis @ normal) < 0:
            bounds = [_reversed(b) for b in bounds]
        plane = geo_su.Plane(
            position=geo_su.Axis2Placement3D(
                location=_pnt(pln.Location()),
                axis=ada.Direction(*normal),
                ref_direction=_dir(pln.XAxis().Direction()),
            )
        )
        advanced = geo_su.AdvancedFace(bounds=bounds, face_surface=plane, same_sense=True)
        expected, got = _area(face), _area(make_face_from_geom(advanced))
        if not abs(got - expected) <= AREA_REL_TOL * expected:
            raise PlateBooleanNotAuthored(
                f"plate {pl.name!r}: the face read back from its booleans' cut measures {got} against the cut's "
                f"{expected}"
            )
        faces.append(advanced)
        exp.Next()
    if not faces:
        raise PlateBooleanNotAuthored(f"plate {pl.name!r}: its booleans leave nothing of it")
    return faces
