from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse, BRepAlgoAPI_Common
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom import BooleanOperation


def apply_geom_booleans(geom: TopoDS_Shape, booleans: list[BooleanOperation]) -> TopoDS_Shape:
    from ada.geom import BoolOpEnum
    from ada.occ.geom import geom_to_occ_geom

    for boolean in booleans:
        if boolean.operator == BoolOpEnum.DIFFERENCE:
            geom = BRepAlgoAPI_Cut(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        elif boolean.operator == BoolOpEnum.UNION:
            geom = BRepAlgoAPI_Fuse(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        elif boolean.operator == BoolOpEnum.INTERSECTION:
            geom = BRepAlgoAPI_Common(geom, geom_to_occ_geom(boolean.second_operand)).Shape()
        else:
            raise NotImplementedError(f"Boolean operation {boolean.operator} not implemented")

    return geom
