from __future__ import annotations

import math

import numpy as np

from ada import PrimCone
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.cadit.ifc.write.shapes.swept_primitives import swept_primitives_wanted
from ada.geom.solids import Cone


def generate_ifc_cone_geom(shape: PrimCone, f):
    """IfcRightCircularCone from primitive PrimCone -- or, when the assembly asks for swept
    primitives (``swept_primitives``), the same solid as a right triangle revolved about its axis."""
    c_geom: Cone = shape.solid_geom().geometry
    if not swept_primitives_wanted(shape):
        axis3d = ifc_placement_from_axis3d(c_geom.position, f)
        return f.createIfcRightCircularCone(Position=axis3d, Height=c_geom.height, BottomRadius=c_geom.bottom_radius)

    # The profile's plane holds the cone's x-direction as its local x and the cone's axis as its
    # local y; its normal is x cross y.
    pos = c_geom.position
    axis = np.asarray(pos.axis, dtype=float)
    ref = np.asarray(pos.ref_direction, dtype=float)
    axis = axis / np.linalg.norm(axis)
    ref = ref / np.linalg.norm(ref)
    normal = np.cross(ref, axis)

    def direction(v):
        return f.create_entity("IfcDirection", tuple(float(x) for x in v))

    origin = f.create_entity("IfcCartesianPoint", tuple(float(x) for x in pos.location))
    position = f.create_entity(
        "IfcAxis2Placement3D", Location=origin, Axis=direction(normal), RefDirection=direction(ref)
    )
    r, h = float(c_geom.bottom_radius), float(c_geom.height)
    points = f.create_entity("IfcCartesianPointList2D", CoordList=((0.0, 0.0), (r, 0.0), (0.0, h)))
    segments = [f.create_entity("IfcLineIndex", seg) for seg in ((1, 2), (2, 3), (3, 1))]
    outline = f.create_entity("IfcIndexedPolyCurve", Points=points, Segments=segments, SelfIntersect=False)
    profile = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=outline)
    about = f.create_entity(
        "IfcAxis1Placement",
        Location=f.create_entity("IfcCartesianPoint", (0.0, 0.0, 0.0)),
        Axis=direction((0.0, 1.0, 0.0)),
    )
    return f.create_entity("IfcRevolvedAreaSolid", SweptArea=profile, Position=position, Axis=about, Angle=2 * math.pi)
