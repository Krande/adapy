from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.utils import to_real
from ada.geom import curves as geo_cu


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    unique_pts, segment_indices = ipc.get_points_and_segment_indices()

    utuples = unique_pts.tolist()
    ifc_point_list = f.create_entity("IfcCartesianPointList2D", utuples)
    s = [
        f.create_entity("IfcArcIndex", i) if len(i) == 3 else f.create_entity("IfcLineIndex", i)
        for i in segment_indices
    ]

    return f.create_entity("IfcIndexedPolyCurve", ifc_point_list, s, False)
