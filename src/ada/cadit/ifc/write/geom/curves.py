from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.utils import to_real
from ada.geom import curves as geo_cu


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    unique_pts, segment_indices = ipc.get_points_and_segment_indices()

    utuples = unique_pts.tolist()

    ifc_point_list = f.createIfcCartesianPointList2D(utuples)
    return f.create_entity("IfcIndexedPolyCurve", ifc_point_list, [to_real(x) for x in segment_indices], False)

