from __future__ import annotations

from ada import PrimCyl
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.geom.solids import Cylinder


def generate_ifc_cylinder_geom(shape: PrimCyl, f):
    """Create IfcExtrudedAreaSolid from primitive PrimCyl"""
    cyl_geom: Cylinder = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(cyl_geom.position, f)
    return f.createIfcRightCircularCylinder(Position=axis3d, Height=cyl_geom.height, Radius=cyl_geom.radius)
