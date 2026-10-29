from __future__ import annotations

import math

from ada import PrimRevolve
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_ifcindexpolyline2d,
    create_ifcrevolveareasolid,
)
from ada.core.curve_utils import segments_to_indexed_lists


def generate_ifc_prim_revolve_geom(shape: PrimRevolve, f):
    """Create IfcRevolveAreaSolid from primitive PrimRevolve.

    The profile is the outline in its own plane (2D, as ``IfcProfileDef`` requires; strict readers
    drop a solid swept from a 3D profile), placed by the plane's origin, normal and x-direction. The
    revolve axis is the plane's y-axis through its origin, so in that placement it is the local y-axis."""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    # 8.8.3.28 IfcRevolvedAreaSolid
    poly = shape.poly
    local_points, seg_index = segments_to_indexed_lists(poly.segments)
    points2d = [tuple(float(v) for v in p[:2]) for p in local_points]
    polyline = create_ifcindexpolyline2d(f, points2d, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    position = create_ifc_placement(f, poly.origin, poly.normal, poly.xdir)
    return create_ifcrevolveareasolid(
        f,
        profile,
        position,
        [0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        # Degrees on the primitive; the file's plane angle unit is the radian.
        math.radians(shape.revolve_angle),
    )
