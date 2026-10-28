from __future__ import annotations

from ada import PrimExtrude
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_ifcindexpolyline2d,
)
from ada.core.curve_utils import segments_to_indexed_lists


def generate_ifc_prim_extrude_geom(shape: PrimExtrude, f):
    """Create IfcExtrudedAreaSolid from primitive PrimExtrude.

    The profile is the outline in its own plane (2D, as ``IfcProfileDef`` requires; strict readers
    drop a solid swept from a 3D profile), placed by the plane's origin, normal and x-direction, and
    extruded along the plane's normal (local +Z)."""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    poly = shape.poly
    local_points, seg_index = segments_to_indexed_lists(poly.segments)
    points2d = [tuple(float(v) for v in p[:2]) for p in local_points]
    polyline = create_ifcindexpolyline2d(f, points2d, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    position = create_ifc_placement(f, poly.origin, poly.normal, poly.xdir)
    return create_ifcextrudedareasolid(f, profile, position, [0.0, 0.0, 1.0], shape.extrude_depth)
