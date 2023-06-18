import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom.points import Point


def get_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.CURVE_GEOM_TYPES:
    if ifc_entity.is_a("IfcIndexedPolyCurve"):
        return indexed_poly_curve(ifc_entity)
    elif ifc_entity.is_a("IfcPolyline"):
        return poly_line(ifc_entity)
    else:
        raise NotImplementedError(f"Geometry type {ifc_entity.is_a()} not implemented")


def poly_line(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.PolyLine:
    return geo_cu.PolyLine([Point(x.Coordinates) for x in ifc_entity.Points])


def indexed_poly_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.IndexedPolyCurve:
    pts = ifc_entity.Points.CoordList
    segments = []
    for segment in ifc_entity.Segments:
        value = [x - 1 for x in segment.wrappedValue]
        if segment.is_a("IfcLineIndex"):
            segments.append(geo_cu.Line(pts[value[0]], pts[value[1]]))
        else:
            segments.append(geo_cu.ArcLine(pts[value[0]], pts[value[1]], pts[value[2]]))

    return geo_cu.IndexedPolyCurve(segments, ifc_entity.SelfIntersect)
