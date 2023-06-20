from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.geom import curves as geo_cu


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    unique_pts, segment_indices = ipc.get_points_and_segment_indices()

    points = unique_pts.tolist()
    if len(points[0]) == 2:
        list_type = "IfcCartesianPointList2D"
    else:
        list_type = "IfcCartesianPointList3D"

    ifc_point_list = f.create_entity(list_type, points)
    s = [
        f.create_entity("IfcArcIndex", i) if len(i) == 3 else f.create_entity("IfcLineIndex", i)
        for i in segment_indices
    ]

    return f.create_entity("IfcIndexedPolyCurve", ifc_point_list, s, False)


def circle_curve(circle: geo_cu.Circle, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Circle to an IFC representation"""
    axis3d = ifc_placement_from_axis3d(circle.position, f)
    return f.create_entity("IfcCircle", axis3d, circle.radius)
