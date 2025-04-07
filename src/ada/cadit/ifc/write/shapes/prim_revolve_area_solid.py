from __future__ import annotations

from ada import PrimRevolve
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_ifcindexpolyline,
    create_ifcrevolveareasolid,
)
from ada.core.constants import O, X, Z


def generate_ifc_prim_revolve_geom(shape: PrimRevolve, f):
    """Create IfcRevolveAreaSolid from primitive PrimRevolve"""
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
    # 8.8.3.28 IfcRevolvedAreaSolid

    revolve_axis = [float(n) for n in shape.revolve_axis]
    revolve_origin = [float(x) for x in shape.revolve_origin]
    revolve_angle = shape.revolve_angle
    points = [tuple(x.astype(float).tolist()) for x in shape.poly.seg_global_points]
    seg_index = shape.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
    opening_axis_placement = create_ifc_placement(f, O, Z, X)
    return create_ifcrevolveareasolid(
        f,
        profile,
        opening_axis_placement,
        revolve_origin,
        revolve_axis,
        revolve_angle,
    )
