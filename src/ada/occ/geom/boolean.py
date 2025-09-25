from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom.booleans import BooleanOperation, BoolOpEnum
from ada.geom.surfaces import HalfSpaceSolid, Plane


def apply_geom_booleans(geom: TopoDS_Shape, booleans: list[BooleanOperation]) -> TopoDS_Shape:
    from ada.occ.geom import geom_to_occ_geom

    for boolean in booleans:
        if isinstance(boolean.second_operand.geometry, HalfSpaceSolid):
            solid_geom = boolean.second_operand.geometry
            plane: Plane = solid_geom.base_surface
            origin = plane.position.location
            normal = plane.position.axis

            # Create a plane from origin and normal
            gp_origin = gp_Pnt(origin.x, origin.y, origin.z)
            gp_normal = gp_Dir(normal.x, normal.y, normal.z)
            plane = gp_Pln(gp_origin, gp_normal)

            # Create a face from the plane (you'll need to create a bounded face)

            face_maker = BRepBuilderAPI_MakeFace(plane)
            face = face_maker.Face()

            # Create reference point (slightly offset from the plane in the direction of the normal)
            # This determines which side of the plane is the "solid" side
            offset = 1.0 if not solid_geom.agreement_flag else -1.0
            ref_point = gp_Pnt(origin.x + normal.x * offset, origin.y + normal.y * offset, origin.z + normal.z * offset)

            # Create the half-space solid
            half_space_maker = BRepPrimAPI_MakeHalfSpace(face, ref_point)
            half_space = half_space_maker.Solid()

            # Apply the boolean cut operation (half-space is typically used for cutting)
            geom = BRepAlgoAPI_Cut(geom, half_space).Shape()

            continue
        if boolean.operator == BoolOpEnum.DIFFERENCE:
            geom = BRepAlgoAPI_Cut(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        elif boolean.operator == BoolOpEnum.UNION:
            geom = BRepAlgoAPI_Fuse(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        elif boolean.operator == BoolOpEnum.INTERSECTION:
            geom = BRepAlgoAPI_Common(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        else:
            raise NotImplementedError(f"Boolean operation {boolean.operator} not implemented")

    return geom
