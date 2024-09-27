from typing import Iterable

from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape

from ada.geom import surfaces as geo_su

from .surfaces import iter_faces


def import_geometry_from_step_geom(geom_repr: TopoDS_Compound | TopoDS_Shape) -> Iterable[geo_su.SURFACE_GEOM_TYPES]:
    yield from iter_faces(geom_repr)
