from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom.curves import IndexedPolyCurve
from ada.occ.geom.curves import make_indexed_poly_curve_from_geom


def make_indexed_poly_curve_surface_from_geom(curve: IndexedPolyCurve) -> TopoDS_Shape:
    wire = make_indexed_poly_curve_from_geom(curve)
    return BRepBuilderAPI_MakeFace(wire).Shape()
