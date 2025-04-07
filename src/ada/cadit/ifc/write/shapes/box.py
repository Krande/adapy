from __future__ import annotations

from ada import PrimBox
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.geom.solids import Box


def generate_ifc_box_geom(shape: PrimBox, f):
    """Create IfcBlock from primitive PrimBox"""
    geom: Box = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(geom.position, f)
    return f.createIfcBlock(Position=axis3d, XLength=geom.x_length, YLength=geom.y_length, ZLength=geom.z_length)
