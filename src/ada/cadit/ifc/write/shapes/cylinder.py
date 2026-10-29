from __future__ import annotations

from ada import PrimCyl
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.cadit.ifc.write.shapes.swept_primitives import swept_primitives_wanted
from ada.geom.solids import Cylinder


def generate_ifc_cylinder_geom(shape: PrimCyl, f):
    """IfcRightCircularCylinder from primitive PrimCyl -- or, when the assembly asks for swept
    primitives (``swept_primitives``), the same solid as a circle profile swept along the axis."""
    cyl_geom: Cylinder = shape.solid_geom().geometry
    axis3d = ifc_placement_from_axis3d(cyl_geom.position, f)
    if not swept_primitives_wanted(shape):
        return f.createIfcRightCircularCylinder(Position=axis3d, Height=cyl_geom.height, Radius=cyl_geom.radius)
    origin2d = f.create_entity("IfcAxis2Placement2D", Location=f.create_entity("IfcCartesianPoint", (0.0, 0.0)))
    profile = f.create_entity("IfcCircleProfileDef", ProfileType="AREA", Position=origin2d, Radius=cyl_geom.radius)
    up = f.create_entity("IfcDirection", (0.0, 0.0, 1.0))
    return f.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=profile, Position=axis3d, ExtrudedDirection=up, Depth=cyl_geom.height
    )
