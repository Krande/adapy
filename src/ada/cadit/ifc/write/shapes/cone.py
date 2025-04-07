from __future__ import annotations

from ada import PrimCone
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.geom.solids import Cone


def generate_ifc_cone_geom(shape: PrimCone, f):
    c_geom: Cone = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(c_geom.position, f)
    return f.createIfcRightCircularCone(Position=axis3d, Height=c_geom.height, BottomRadius=c_geom.bottom_radius)
