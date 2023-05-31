from OCC.Core.TopoDS import TopoDS_Shape

from ada.geom import Geometry
from ada.geom.solids import Box, ExtrudedAreaSolid
from ada.occ.geom.solids import make_box_from_geom, make_extruded_area_shape_from_geom


def geom_to_occ_geom(geom: Geometry) -> TopoDS_Shape:
    if isinstance(geom.geometry, Box):
        occ_geom = make_box_from_geom(geom.geometry)
    elif isinstance(geom.geometry, ExtrudedAreaSolid):
        occ_geom = make_extruded_area_shape_from_geom(geom.geometry)
    else:
        raise NotImplementedError()

    return occ_geom
