from __future__ import annotations

from ada import PrimExtrude
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_ifcindexpolyline,
)
from ada.core.constants import O, X, Z


def generate_ifc_prim_extrude_geom(shape: PrimExtrude, f):
    """Create IfcExtrudedAreaSolid from primitive PrimExtrude"""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    # polyline = self.create_ifcpolyline(self.file, [p[:3] for p in points])
    normal = shape.poly.normal
    h = shape.extrude_depth
    points = [tuple(x.astype(float).tolist()) for x in shape.poly.seg_global_points]
    seg_index = shape.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    opening_axis_placement = create_ifc_placement(f, O, Z, X)
    return create_ifcextrudedareasolid(f, profile, opening_axis_placement, [float(n) for n in normal], h)
